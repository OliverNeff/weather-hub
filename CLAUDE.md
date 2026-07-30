# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Weather Hub is a FastAPI microservice that fetches weather data (current conditions + short-term forecast) from multiple providers and exposes it via a single REST endpoint.

## Tech Stack

- **Runtime**: Python 3.14+ (managed by `uv`)
- **Framework**: FastAPI [standard](pyproject.toml) (includes uvicorn, starlette, pydantic)
- **Weather providers**: `buienradar` (1.0.9), `wetterdienst` (DWD/MosMix), `httpx` (Open-Meteo)
- **Utilities**: `haversine` (distance), `polars` (data processing)
- **Package manager**: `uv` (see `uv.lock`)

## Directory Structure

```
app/
├── main.py                        # FastAPI app entry point, logging setup
├── routers/
│   └── weather_data.py            # GET /weather/data?lat=&lon=
├── models/
│   ├── weather_data.py            # WeatherData Pydantic response schema
│   └── weather_station.py         # WeatherStation Pydantic model
└── adapter/
    ├── buinradar.py               # Buienradar API client
    ├── wetterdienst_dwd.py        # DWD client (Observation + MosMix forecast)
    └── openmeteo.py               # Open-Meteo client (plain httpx, no FFI)
```

## Key Commands

```bash
# Run the server (use uvicorn, not fastapi dev — see "Known issues")
uv run uvicorn app.main:app --reload

# Run a quick API smoke test
uv run python -c "import httpx; print(httpx.get('http://127.0.0.1:8000/weather/data?lat=49.87&lon=8.93').json())"
```

## Architecture

The app follows a thin layered architecture:

1. **Router** (`app/routers/`) — HTTP layer. Fetches all adapters in parallel, merges results, returns a Pydantic model.
2. **Adapter** (`app/adapter/`) — external-API clients. Each provider has its own adapter module. They fetch raw data, parse it, and map to `WeatherData`.
3. **Models** (`app/models/`) — Pydantic `BaseModel` classes that define the API response schema. All fields are nullable (`float | None`) because weather stations don't always report every measurement.

The only public endpoint is `GET /weather/data?lat=...&lon=...`.

### Adapters

Three adapters run in parallel per request. A single adapter failure doesn't take down the response.

**DWD Adapter** (`wetterdienst_dwd.py`): Combines two DWD sources:
- **Observation** (`DwdObservationRequest`): Fetches temperature, wind speed, wind gust, and precipitation from `recent` period at `10_minutes` resolution. Each parameter is fetched in a separate request targeting the nearest station that reports it (not all stations report all parameters). The 4 parameter requests run in parallel via `ThreadPoolExecutor`.
- **Forecast** (`DwdMosmixRequest`): Hourly MosMix Small prognoses for precipitation and radiation. Builds 30m/1h/2h windows from now, averages the precip values in each window for amount and intensity. UV index is approximated from global radiation (J/m²) with `* 0.019` factor, clamped to 0-16 — this is a rough estimate, not a real UV measurement.
- **Station selection**: picks the 3 nearest stations by distance, not by dataset count. Proximity matters most for local rain/wind accuracy. Logs which stations are selected and which are skipped.

**Open-Meteo Adapter** (`openmeteo.py`): Calls the Open-Meteo forecast API via plain `httpx` (not the `openmeteo_requests` FFI client). Provides current temperature, apparent temperature, wind, precipitation, UV index (accurate), plus sunrise/sunset with sun elevation. Typical response time: <1s.

**Buienradar Adapter** (`buinradar.py`): Calls `buienradar.buienradar.get_data()`, parses the JSON `content` string and `raincontent` grid. Rain content uses integer codes per 5-minute interval, converted via `to_mmh()`: `10 ** ((code - 109) / 32)`. Station lookup uses Euclidean distance (not haversine). Radar precipitation forecast (30m/1h/2h) works for Germany too — it's grid-based, not station-based. Only temperature/wind/current-precipitation are NL-only (nearest NL station).

### Response merging (`weather_data.py`)

The router fetches all 3 adapters in parallel, then merges:

| Fields | Strategy | Why |
|---|---|---|
| Wind speed/gust | `max()` across all adapters | Over-reporting is safer |
| Precipitation rate + 30m/1h/2h | `max()` across all adapters | Missing rain is worse than over-reporting |
| Feels like | Open-Meteo first, then freshest | Open-Meteo's apparent_temperature is most reliable |
| Temperature | Freshest source (newest timestamp first) | Most recent data is most accurate |
| UV index | Freshest source | Open-Meteo provides accurate real-time UV; DWD's is rough |
| Sunrise/sunset/sun elevation | Freshest source | Only Open-Meteo provides these |
| Stations | All stations from all adapters | Shows which sources contributed |

### DWD caching

Two layers:
1. **In-memory MosMix cache** (`_mosmix_cache`) — 10-minute TTL, cache key `{lat:.2f},{lon:.2f}`. MosMix updates every 1-3 hours; a fresh fetch takes ~11s.
2. **fsspec disk cache** (wetterdienst default) — controlled by `DWD_CACHE` env var in `.env`. When enabled, speeds up repeated requests from ~7s to ~0.3s. If the cache returns empty results, it is automatically cleared and retried once. Default: disabled (stale cache can cause "does not have a list of files" errors).

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
- **DWD MosMix UV index is inaccurate**: the `* 0.019` factor from global radiation gives a rough approximation (e.g., returns 1.6 when real UV is 6). Open-Meteo provides the accurate UV index and takes precedence via the freshness-based merge.
- **DWD `.values.all()` loads full station ZIPs**: even when requesting a single parameter, wetterdienst downloads the entire station data file. The per-parameter approach (4 parallel requests) is faster than one combined request with 3 station ZIPs (~4s vs ~40s).