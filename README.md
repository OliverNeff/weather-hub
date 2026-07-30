# Weather Hub

FastAPI microservice that aggregates weather data from multiple providers and exposes it via a single REST endpoint.

## Endpoint

```
GET /weather/data?lat=<latitude>&lon=<longitude>
```

Returns current conditions plus 30m/1h/2h precipitation forecast as JSON.

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
│    │  uv (est) │     │           │     │ wind (NL)    │            │
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

## Provider strengths

| Provider | Strengths | Coverage | Typical latency |
|---|---|---|---|
| **Open-Meteo** | UV index (accurate), sunrise/sunset, feels_like, global coverage | Worldwide | < 1s |
| **DWD** | German station data (temperature, wind, precipitation observation) | Germany | ~7s cold, ~0.3s cached |
| **Buienradar** | Radar-based precipitation forecast (30m/1h/2h), excellent real-time radar | Netherlands + DE radar | < 1s |

### Per-parameter detail

**DWD Observation** (`wetterdienst_dwd.py`):
- Fetches temperature, wind_speed, wind_gust, precipitation from `recent` period (10-minute resolution)
- Each parameter uses a **separate request** targeting the nearest station that reports it (not all stations report all parameters)
- The 4 parameter requests run in parallel via `ThreadPoolExecutor`
- Station selection: 3 nearest stations by **distance** (not dataset count)
- UV index from MosMix forecast: approximated via `global_radiation * 0.019`, clamped 0–16 (rough estimate, not real UV)
- MosMix forecast is cached in-memory (10 min TTL), and on disk via fsspec (configurable via `DWD_CACHE` env var)
- If the disk cache returns empty results, it is automatically cleared and retried once

**Open-Meteo** (`openmeteo.py`):
- Plain HTTP/JSON client (`httpx`), no FFI bindings
- Returns current temperature, apparent_temperature, wind_speed, wind_gust, precipitation, uv_index, sunrise, sunset
- Provides the most accurate UV index and sunrise/sunset data

**Buienradar** (`buinradar.py`):
- Radar grid for precipitation forecast (30m/1h/2h windows) — works for Germany too
- Station measurements (temperature, wind, current precipitation) are Netherlands-only (nearest NL station)
- Rain forecast uses 5-minute intervals, converted via `10 ** ((code - 109) / 32)` to mm/h

## Merge strategy

After all adapters return, the router (`weather_data.py`) merges the results:

| Fields | Strategy | Rationale |
|---|---|---|
| Wind speed / gust | `max()` across all adapters | Over-reporting is safer than under-reporting |
| Precipitation rate + 30m/1h/2h windows | `max()` across all adapters | Missing rain is worse than over-reporting it |
| Feels-like temperature | Open-Meteo first, then freshest source | Open-Meteo's `apparent_temperature` is most reliable |
| Temperature | Freshest source (newest timestamp first) | Most recent data is most accurate |
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

## Quick start

```bash
uv run uvicorn app.main:app --reload
curl "http://127.0.0.1:8000/weather/data?lat=49.87&lon=8.93"
```