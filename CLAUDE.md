# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Weather Hub is a FastAPI microservice that fetches weather data (current conditions + short-term forecast) from multiple providers and exposes it via a single REST endpoint.

## Tech Stack

- **Runtime**: Python 3.14+ (managed by `uv`)
- **Framework**: FastAPI [standard](pyproject.toml) (includes uvicorn, starlette, pydantic)
- **Weather providers**: `buienradar` (1.0.9), `wetterdienst` (DWD/MosMix)
- **Utilities**: `haversine` (distance), `pandas` (MosMix datetime parsing)
- **Package manager**: `uv` (see `uv.lock`)

## Directory Structure

```
app/
├── main.py                        # FastAPI app entry point, mounts routers
├── routers/
│   └── weather_data.py            # GET /weather/data?lat=&lon=
├── models/
│   ├── weather_data.py            # WeatherData Pydantic response model
│   └── weather_station.py         # WeatherStation Pydantic model
└── adapter/
    ├── buinradar.py               # Buienradar API client (fetch + parse)
    └── wetterdienst_dwd.py        # DWD client (Observation + MosMix forecast)
```

## Key Commands

```bash
# Install deps and run the server (dev mode with auto-reload)
uv run fastapi dev app/main.py

# Or directly with uvicorn
uv run uvicorn app.main:app --reload

# Run a quick API smoke test
uv run python -c "import httpx; print(httpx.get('http://127.0.0.1:8000/weather/data?lat=52.37&lon=4.89').json())"
```

## Architecture

The app follows a thin layered architecture:

1. **Router** (`app/routers/`) — HTTP layer. Receives `lat`/`lon` query params, delegates to an adapter, returns a Pydantic model.
2. **Adapter** (`app/adapter/`) — external-API clients. Each provider has its own adapter module. They fetch raw data, parse it, and map to `WeatherData`.
3. **Models** (`app/models/`) — Pydantic `BaseModel` classes that define the API response schema. All fields are nullable (`float | None`) because weather stations don't always report every measurement.

The only public endpoint is `GET /weather/data?lat=...&lon=...`.

### Adapters

The router currently delegates to `fetch_wetterdienst_weather` (DWD). A Buienradar adapter exists (`fetch_buienradar_weather`) but is not currently routed.

**DWD Adapter** (`wetterdienst_dwd.py`): Combines two DWD sources:
- **Observation** (`DwdObservationRequest`): Current measurements (temperature, wind, precipitation) from `recent` period at `10_minutes` resolution. Finds nearest station via `filter_by_distance(rank=5)` + haversine.
- **Forecast** (`DwdMosmixRequest`): Hourly MosMix Small prognoses for precipitation and radiation. Builds 30m/1h/2h windows from now, averages the precip values in each window for amount and intensity. UV index is approximated from global radiation (J/m²) with `* 0.019` factor, clamped to 0-16.

**Buienradar Adapter** (`buinradar.py`): Calls `buienradar.buienradar.get_data()`, parses the JSON `content` string and `raincontent` grid. Rain content uses integer codes per 5-minute interval, converted via `to_mmh()`: `10 ** ((code - 109) / 32)`. Station lookup uses Euclidean distance (not haversine).

### Models

`WeatherData` contains: current conditions (wind, precipitation, temperature, feels_like, uv_index, sun_elevation), plus forecast windows for 30m/1h/2h (boolean `precipitation_next_X`, float `precipitation_amount_next_X`, float `precipitation_intensity_next_X`).

`WeatherData.stations` is a `list[WeatherStation]` (not a single field), populated via `append()` after model construction.

## Important Details

- Buienradar's `content` field is a JSON string (not already-parsed dict) — it must be `json.loads()`-ed before use (see `app/adapter/buinradar.py:25`).
- `WeatherData.stations` is a `list[WeatherStation]`, populated via `append()` after model construction.
- Stations with no data should return `None` fields (not 0), preserving the distinction between "no data" and "zero precipitation".
- DWD `_to_float()` helper handles NaN values by checking `f != f` (see `app/adapter/wetterdienst_dwd.py:284`).
- MosMix datetime parsing falls back through `pd.Timestamp()` for various formats (see `_parse_datetime()`).