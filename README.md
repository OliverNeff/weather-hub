# Weather Hub

FastAPI microservice that aggregates weather data from multiple providers and exposes it via a single REST endpoint.

## Quick Start

```bash
uv sync
uv run uvicorn app.main:app --reload
curl "http://127.0.0.1:8000/weather/data?lat=49.87&lon=8.93"
```

[OpenAPI docs](http://127.0.0.1:8000/docs) are available at `/docs` when the server is running.

## Tech Stack

- **Runtime**: Python 3.12+ (managed by [`uv`](https://github.com/astral-sh/uv))
- **Framework**: FastAPI
- **Dependencies**: buienradar, wetterdienst, openmeteo-requests, httpx, pandas, polars

## Endpoint

```
GET /weather/data?lat=<latitude>&lon=<longitude>
```

Returns current conditions plus 30m/1h/2h precipitation forecast as JSON.

### Response Schema

| Field | Type | Description |
|---|---|---|
| **Wind** | | |
| `wind_speed` | `float \| null` | Current wind speed in m/s |
| `wind_gust` | `float \| null` | Maximum wind gust in m/s |
| **Precipitation (current)** | | |
| `precipitation_now` | `bool \| null` | `true` if precipitation is currently measured |
| `precipitation_intensity` | `float \| null` | Current precipitation rate in mm/h |
| **Precipitation (forecast)** | | |
| `precipitation_next_30m` | `bool \| null` | Rain expected in the next 30 minutes |
| `precipitation_amount_next_30m` | `float \| null` | Expected precipitation in mm (next 30m) |
| `precipitation_intensity_next_30m` | `float \| null` | Peak intensity in mm/h (next 30m) |
| `precipitation_next_1h` | `bool \| null` | Rain expected in the next hour |
| `precipitation_amount_next_1h` | `float \| null` | Expected precipitation in mm (next 1h) |
| `precipitation_intensity_next_1h` | `float \| null` | Peak intensity in mm/h (next 1h) |
| `precipitation_next_2h` | `bool \| null` | Rain expected in the next 2 hours |
| `precipitation_amount_next_2h` | `float \| null` | Expected precipitation in mm (next 2h) |
| `precipitation_intensity_next_2h` | `float \| null` | Peak intensity in mm/h (next 2h) |
| **Temperature** | | |
| `temperature` | `float \| null` | Current temperature in °C |
| `feels_like` | `float \| null` | Apparent temperature in °C |
| **UV / Sun** | | |
| `uv_index` | `float \| null` | UV index (0–16+) |
| `sun_elevation` | `float \| null` | Sun elevation in degrees (negative when below horizon) |
| `sunrise` | `datetime \| null` | Today's sunrise (UTC) |
| `sunset` | `datetime \| null` | Today's sunset (UTC) |
| **Stations** | | |
| `stations` | `list[WeatherStation]` | All contributing weather stations (see below) |

All fields are nullable — a `null` means the adapter did not return data for that field.

### Station Object

| Field | Type | Description |
|---|---|---|
| `source` | `str` | Adapter name: `dwd`, `openmeteo`, or `buienradar` |
| `name` | `str` | Station name |
| `lat` | `float` | Station latitude |
| `lon` | `float` | Station longitude |
| `time` | `datetime \| null` | Timestamp of the measurement (UTC) |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  GET /weather/data?lat=49.87&lon=8.93                              │
│                              │                                      │
│          ┌───────────────────┼───────────────────┐                  │
│          ▼                   ▼                   ▼                  │
│    ┌───────────┐     ┌───────────┐     ┌──────────────┐            │
│    │   DWD     │     │  Open-    │     │  Buienradar  │            │
│    │ (parallel)│     │  Meteo    │     │ (parallel)   │            │
│    │           │     │           │     │              │            │
│    │ Obs: temp │     │ current:  │     │ radar grid:  │            │
│    │ Obs: wind │     │ temp/uv   │     │ precip 30m   │            │
│    │ Obs: rain │     │ wind      │     │ precip 1h    │            │
│    │ forecast: │     │ sunrise   │     │ precip 2h    │            │
│    │  precip   │     │ feels_like│     │ temp (NL)    │            │
│    │  uv (est) │     │ sun elev  │     │ wind (NL)    │            │
│    └───────────┘     └───────────┘     └──────────────┘            │
│          │                   │                   │                  │
│          └───────────────────┼───────────────────┘                  │
│                              ▼                                      │
│                    Merge strategy (see below)                       │
│                              │                                      │
│                              ▼                                      │
│                      Single JSON response                           │
└─────────────────────────────────────────────────────────────────────┘
```

All 3 adapters run in parallel. A single adapter failure does not take down the response.

## Providers

| Provider | Strengths | Coverage | Typical latency |
|---|---|---|---|
| **Open-Meteo** | UV index (accurate), sunrise/sunset, feels_like, global coverage | Worldwide | < 1s |
| **DWD** | German station data (temperature, wind, precipitation observation) | Germany | ~7s cold, ~0.3s cached |
| **Buienradar** | Radar-based precipitation forecast (30m/1h/2h), excellent real-time radar | NL stations + DE radar | < 1s |

### DWD (`wetterdienst_dwd.py`)

Combines two DWD sources:

**Observation** — Fetches temperature, wind_speed, wind_gust, and precipitation from the `recent` period (10-minute resolution). Each parameter is fetched in a separate request targeting the nearest station that reports it. The 4 requests run in parallel via `ThreadPoolExecutor`.

**Forecast** — Hourly MosMix Small prognoses for precipitation and radiation. Builds 30m/1h/2h windows from now, averages the precip values in each window for amount and intensity. UV index is approximated from global radiation with `* 0.019`, clamped to 0–16 (rough estimate).

**Caching** — Two layers:
1. **In-memory MosMix cache** — 10-minute TTL, keyed by `{lat:.2f},{lon:.2f}`. MosMix updates every 1–3 hours.
2. **fsspec disk cache** — Controlled by `DWD_CACHE` env var. When enabled, speeds up repeated requests from ~7s to ~0.3s. Returns empty results are retried once after clearing the cache.

### Open-Meteo (`openmeteo.py`)

Plain HTTP/JSON client (`httpx`), no FFI bindings. Returns current temperature, apparent_temperature, wind_speed, wind_gust, precipitation, uv_index, sunrise, sunset.

Sun elevation is computed using the NOAA formula. Returns negative values when the sun is below the horizon (nighttime).

### Buienradar (`buinradar.py`)

- **Radar grid** for precipitation forecast (30m/1h/2h windows) — works for Germany too (grid-based, not station-based)
- **Station measurements** (temperature, wind, current precipitation) are Netherlands-only
- Station data is **ignored when the nearest NL station is >100km away** — temperature and feels_like from a distant station would be irrelevant for the requested location
- Rain forecast uses 5-minute intervals, converted via `10 ** ((code - 109) / 32)` to mm/h

## Merge Strategy

After all adapters return, the router (`weather_data.py`) merges the results:

| Fields | Strategy | Rationale |
|---|---|---|
| Wind speed / gust | `max()` across all adapters | Over-reporting is safer than under-reporting |
| Precipitation rate + 30m/1h/2h windows | `max()` across all adapters | Missing rain is worse than over-reporting it |
| Feels-like temperature | Same adapter as temperature, fallback to freshest | Keeps temperature and feels_like consistent |
| Temperature | Freshest source (newest timestamp first) | Most recent data is most accurate. Buienradar is excluded when station >100km away |
| UV index | Freshest source | Open-Meteo provides accurate real-time UV; DWD's is a rough estimate |
| Sunrise / sunset / sun elevation | Freshest source | Only Open-Meteo provides these |
| Stations | All stations from all adapters that returned data | Shows which sources contributed |

## Configuration

Create a `.env` file in the project root:

```env
# Enable wetterdienst fsspec disk cache (DWD data).
# true  = cache enabled (~0.3s warm, ~7s cold)
# false = cache disabled (always fresh from DWD)
DWD_CACHE=false
```

## Known Issues

- **`fastapi dev` crashes on Windows**: The `rich_toolkit` console in `fastapi_cli` uses the system encoding (cp1252 on German Windows). Station names with non-ASCII characters cause `UnicodeEncodeError`. Use `uvicorn app.main:app --reload` instead.
- **DWD observation data can be stale**: DWD `recent` period data for small stations can be 12+ hours old. The merge layer prefers Open-Meteo's fresher data for temperature and UV.
- **DWD MosMix UV index is approximate**: The `* 0.019` factor from global radiation gives a rough estimate. Open-Meteo provides the accurate UV index and takes precedence via the freshness-based merge.

## Windows SSL Configuration

`uv` uses rustls on Windows. `SSL_CERT_FILE` and `NODE_EXTRA_CA_CERTS` pointing to a single corporate cert break TLS chain validation against PyPI.

- `uv.toml` contains `system-certs = true` — uses Windows Certificate Store
- Do not set `SSL_CERT_FILE` or `NODE_EXTRA_CA_CERTS` as Windows env vars
- If `uv lock --upgrade` fails with `UnknownIssuer`, unset them:
  ```powershell
  $env:SSL_CERT_FILE=""
  $env:NODE_EXTRA_CA_CERTS=""
  ```