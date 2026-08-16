# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Weather Hub is a FastAPI microservice that fetches weather data (current conditions + short-term forecast + active DWD warnings) from multiple providers and exposes it via a single REST endpoint.

## Tech Stack

- **Runtime**: Python 3.14+ (managed by `uv`)
- **Framework**: FastAPI [standard](pyproject.toml) (includes uvicorn, starlette, pydantic)
- **Weather providers**: `buienradar` (1.0.9), `wetterdienst` (DWD/MosMix), `httpx` (Open-Meteo)
- **Warnings**: DWD OPeNDATA CAP 1.2 XML zips (plain `httpx` + stdlib `zipfile`/`xml.etree`)
- **MQTT**: `fastmqtt` (publishes merged weather data on timer)
- **Utilities**: `haversine` (distance), `polars` (data processing)
- **Package manager**: `uv` (see `uv.lock`)

## Directory Structure

```
app/
├── main.py                        # FastAPI app entry point, logging setup, MQTT lifespan
├── mqtt.py                        # MQTT push client (FastMQTT + StubClient)
├── mqtt_discovery.py              # Home Assistant MQTT Discovery configs
├── routers/
│   ├── weather_data.py            # GET /weather/data?lat=&lon=
│   └── alerts.py                  # GET /weather/alerts?lat=&lon=
├── models/
│   ├── weather_data.py            # WeatherData Pydantic response schema + Alert model
│   ├── weather_station.py         # WeatherStation Pydantic model
│   └── alerts.py                  # AlertsResponse (dedicated /weather/alerts)
└── adapter/
    ├── buinradar.py               # Buienradar API client
    ├── wetterdienst_dwd.py        # DWD client (Observation + MosMix forecast)
    ├── openmeteo.py               # Open-Meteo client (plain httpx, no FFI)
    └── warnings.py                # DWD weather warnings (CAP alerts, OPeNDATA)
```

## Key Commands

```bash
# Run the server (use uvicorn, not fastapi dev — see "Known issues")
uv run uvicorn app.main:app --reload

# Run a quick API smoke test
uv run python -c "import httpx; print(httpx.get('http://127.0.0.1:8000/weather/data?lat=49.87&lon=8.93').json())"

# Smoke-test the dedicated alerts endpoint
uv run python -c "import httpx; print(httpx.get('http://127.0.0.1:8000/weather/alerts?lat=49.87&lon=8.93').json())"
```

## Architecture

The app follows a thin layered architecture:

1. **Router** (`app/routers/`) — HTTP layer. Fetches all adapters in parallel, merges results, returns a Pydantic model.
2. **Adapter** (`app/adapter/`) — external-API clients. Each provider has its own adapter module. They fetch raw data, parse it, and map to `WeatherData`.
3. **Models** (`app/models/`) — Pydantic `BaseModel` classes that define the API response schema. All fields are nullable (`float | None`) because weather stations don't always report every measurement.

Two public endpoints:

- `GET /weather/data?lat=...&lon=...` — merged weather + forecast + active warnings (`WeatherData`).
- `GET /weather/alerts?lat=...&lon=...` — lightweight endpoint returning **only** active DWD warnings (`AlertsResponse`: `count` + `alerts`, most severe first). Reuses the warnings adapter (10-minute cache), so it's cheap to poll. Intended for consumers that display alerts on separate devices (e.g. a pixel display) without the full weather payload.

### Adapters

Four adapters run in parallel per request. A single adapter failure doesn't take down the response.

**DWD Adapter** (`wetterdienst_dwd.py`): Combines two DWD sources:
- **Observation**: Fetches temperature, wind speed, wind gust, and precipitation from `recent` period at `10_minutes` resolution. Each parameter is fetched from its own pool of nearest stations — precipitation from the nearest rain gauges, wind from the nearest anemometers, etc. (Not all stations report all parameters). The 4 parameter requests run in parallel via `ThreadPoolExecutor`. Only stations with fresh data (>2h threshold for precipitation) appear in the result, trimmed to the 3 nearest.
- **Forecast** (`DwdMosmixRequest`): Hourly MosMix Small prognoses for precipitation and radiation. Builds 30m/1h/2h windows from now, averages the precip values in each window for amount and intensity. UV index is approximated from global radiation (J/m²) with `* 0.019` factor, clamped to 0-16 — this is a rough estimate, not a real UV measurement.

**Open-Meteo Adapter** (`openmeteo.py`): Calls the Open-Meteo forecast API via plain `httpx` (not the `openmeteo_requests` FFI client). Provides current temperature, apparent temperature, wind, precipitation, UV index (accurate), plus sunrise/sunset with sun elevation. Typical response time: <1s.

**Buienradar Adapter** (`buinradar.py`): Calls `buienradar.buienradar.get_data()`, parses the JSON `content` string and `raincontent` grid. Rain content uses integer codes per 5-minute interval, converted via `to_mmh()`: `10 ** ((code - 109) / 32)`. Station lookup uses Euclidean distance (not haversine). Radar precipitation forecast (30m/1h/2h) works for Germany too — it's grid-based, not station-based. Only temperature/wind/current-precipitation are NL-only (nearest NL station).

**Warnings Adapter** (`warnings.py`): Fetches active DWD weather warnings from OPeNDATA as CAP 1.2 XML zips — the same data the NINA/EEW apps use, no API key required. The `LATEST` symlink at `opendata.dwd.de/weather/alerts/cap/DISTRICT_EVENT_STAT/Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMEVENT_DISTRICT_DE.zip` always points to the current state of all German warnings (~100 KB). Warnings are regional (per DWD warning district), so the requested lat/lon is matched against each warning's `<polygon>` via point-in-polygon (even-odd ray casting). One alert can contain multiple `<area>` elements (Kreis + Stadt as separate polygons) — all must be checked. In-memory cache with 10-minute TTL (warnings change rarely). Expired alerts are dropped (5 min grace). On download failure the adapter returns an empty list — alerts never break the weather response.

### Response merging (`weather_data.py`)

The router fetches all 4 adapters in parallel, then merges:

| Fields | Strategy | Why |
|---|---|---|
| Wind speed/gust | DWD > Open-Meteo > Buienradar | DWD = station measurement (if <30min stale); fallback Open-Meteo model; Buienradar NL-only |
| Precipitation rate + 30m/1h/2h | `max()` across all adapters | Missing rain is worse than over-reporting |
| Feels like | Same adapter as temperature, then freshest | Keeps temperature and feels_like consistent |
| Temperature | Freshest source (newest timestamp first) | Most recent data is most accurate |
| UV index | Freshest source | Open-Meteo provides accurate real-time UV; DWD's is rough |
| Sunrise/sunset/sun elevation | Freshest source | Only Open-Meteo provides these |
| Alerts | Warnings adapter only (point-in-polygon) | Regional data, single source |
| Stations | All stations from all adapters | Shows which sources contributed |

### Status computation (`weather_data.py`)

`status` is a Home Assistant-compatible condition string (`sunny`, `rainy`, `cloudy`, etc.) derived from all merged fields via `_compute_status()`. Priority: thunder > snow > measured precipitation > wind > fog > cloud. When WMO `weather_code` indicates rain/snow/thunder but measured values are 0, the model data takes precedence — it may reflect conditions not yet captured by sensors.

The response has two weather status fields:
- **`status`** — HA condition string (derived from all merged data)
- **`weather_code`** — raw WMO code from Open-Meteo (0-99)

### No-rain guard (`weather_data.py`)

When no adapter reports current rain (`precipitation_intensity > 0`),
`precipitation_stops_at` is set to `None` — without an active rain session the
"stop time" is meaningless. Forecast bool/amount/intensity fields are preserved
from adapter data: `false` means "no rain expected" (data available), `null`
means "no data". The adapter helpers (`_precipitation_stops_at`) have a 2h guard
that prevents far-future rain events from leaking through.

### DWD caching

Two layers:
1. **In-memory MosMix cache** (`_mosmix_cache`) — 10-minute TTL, cache key `{lat:.2f},{lon:.2f}`. MosMix updates every 1-3 hours; a fresh fetch takes ~11s.
2. **fsspec disk cache** (wetterdienst default) — controlled by `DWD_CACHE` env var in `.env`. When enabled, speeds up repeated requests from ~7s to ~0.3s. If the cache returns empty results, it is automatically cleared and retried once. Default: disabled (stale cache can cause "does not have a list of files" errors).

### MQTT Push (`app/mqtt.py`)

When `MQTT_LAT` and `MQTT_LON` are set, a background timer fetches weather data every 10 minutes (configurable via `MQTT_INTERVAL`) and publishes the merged result via MQTT to `weather-hub/state`.

- **Real broker**: Set `MQTT_BROKER` env var. Uses `fastmqtt.FastMQTT` with `JsonEncoder`.
- **Stub mode** (default): When `MQTT_BROKER` is empty, uses `StubClient` that logs what would be published. No broker needed for dev.
- **Lifecycle**: MQTT client is created/connected in FastAPI `lifespan`, disconnected on shutdown.
- **Payload**: All `WeatherData` fields (already designed for Home Assistant compatibility).
- **Config**: `MQTT_BROKER`, `MQTT_PORT` (1883), `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_CLIENT_ID` (weather-hub), `MQTT_TOPIC` (weather-hub/state), `MQTT_LAT`, `MQTT_LON`, `MQTT_INTERVAL` (600s).

### MQTT Discovery (`app/mqtt_discovery.py`)

On startup with a real broker, publishes Home Assistant MQTT Discovery configs with `retain=true`. Skipped in stub mode.

- `discover_all(client)` — entry point, called from lifespan, publishes 24 entities
- `build_sensor_config()` / `build_binary_sensor_config()` — generic sensor/binary sensor builders
- `build_alerts_config()` — weather warnings sensor; Jinja2 `value_template` renders the alert list (e.g. "STARKE HITZE; STARKES GEWITTER (2)"), "unknown" when none
- `DISCOVERY_PREFIX` configurable via `HA_DISCOVERY_PREFIX` (default: `homeassistant`)
- Entities: 20 sensors + 4 binary sensors, all under device "Weather Hub"

## Windows SSL Configuration

On Windows, `uv` uses rustls (not OpenSSL). The env vars `SSL_CERT_FILE` and `NODE_EXTRA_CA_CERTS`
pointing to a single corporate cert break uv's TLS chain validation against pypi.org.

- **`uv.toml`** contains `system-certs = true` — uses Windows Certificate Store.
- **Do not set** `SSL_CERT_FILE` or `NODE_EXTRA_CA_CERTS` as Windows User/Machine env vars.
- If `uv lock --upgrade` or `uv sync` fails with `UnknownIssuer`, unset those vars:
  ```powershell
  # Remove from user environment (requires new shell)
  [System.Environment]::SetEnvironmentVariable('SSL_CERT_FILE', $null, 'User')
  [System.Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', $null, 'User')
  # Or unset for current session only
  $env:SSL_CERT_FILE=""
  $env:NODE_EXTRA_CA_CERTS=""
  ```
- In this repo, `SSL_CERT_FILE="" NODE_EXTRA_CA_CERTS="" uv lock --upgrade` works around the issue.

## Important Details

- Buienradar's `content` field is a JSON string (not already-parsed dict) — it must be `json.loads()`-ed before use (see `app/adapter/buinradar.py:25`).
- `WeatherData.stations` is a `list[WeatherStation]`, populated via `append()` after model construction.
- Stations with no data should return `None` fields (not 0), preserving the distinction between "no data" and "zero precipitation".
- DWD `_to_float()` helper handles NaN values by checking `f != f` (see `app/adapter/wetterdienst_dwd.py`).

## Known issues

- **`fastapi dev` crashes on Windows**: the `rich_toolkit` console in `fastapi_cli` uses the system encoding (cp1252 on German Windows). Station names with non-ASCII characters (`Großostheim`) cause a `UnicodeEncodeError`. Use `uv run uvicorn app.main:app --reload` instead.
- **DWD observation data is stale**: DWD `recent` period data for small stations can be 12+ hours old. Temperature/wind may only be available from stations 20km+ away. The merge layer prefers Open-Meteo's fresher data for temperature and UV.
- **DWD per-parameter station selection**: In areas with many precip-only stations (e.g., Eifel), the old distance-only approach would pick rain gauges that miss temperature/wind. Now each parameter fetches from its own pool of nearest stations, so local rain data is used when available while temperature/wind come from full-coverage stations.
- **DWD MosMix UV index is inaccurate**: the `* 0.019` factor from global radiation gives a rough approximation (e.g., returns 1.6 when real UV is 6). Open-Meteo provides the accurate UV index and takes precedence via the freshness-based merge.
- **DWD `.values.all()` loads full station ZIPs**: even when requesting a single parameter, wetterdienst downloads the entire station data file. The per-parameter approach (4 parallel requests) is faster than one combined request with 3 station ZIPs (~4s vs ~40s).