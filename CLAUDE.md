# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Weather Hub is a FastAPI microservice that fetches Dutch weather data (current conditions + short-term forecast) from the Buienradar API and exposes it via a single REST endpoint.

## Tech Stack

- **Runtime**: Python 3.14+ (managed by `uv`)
- **Framework**: FastAPI [standard](pyproject.toml) (includes uvicorn, starlette, pydantic)
- **Weather data**: `buienradar` (1.0.9), `wetterdienst` (0.128.0)
- **Package manager**: `uv` (see `uv.lock`)

## Directory Structure

```
app/
├── main.py                  # FastAPI app entry point, mounts routers
├── routers/
│   └── weather_data.py      # GET /weather/data?lat=&lon=
├── models/
│   ├── weather_data.py      # WeatherData Pydantic response model
│   └── weather_station.py   # WeatherStation Pydantic model
└── adapter/
    └── buinradar.py         # Buienradar API client (fetch + parse)
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

1. **Router** (`app/routers/`) — HTTP layer. Receives `lat`/`lon` query params, delegates to the adapter, returns a Pydantic model.
2. **Adapter** (`app/adapter/`) — external-API client. Calls `buienradar.buienradar.get_data()`, parses the JSON content and raincontent string, maps raw station data → `WeatherData`.
3. **Models** (`app/models/`) — Pydantic `BaseModel` classes that define the API response schema. All fields are nullable (`float | None`) because weather stations don't always report every measurement.

The only public endpoint is `GET /weather/data?lat=...&lon=...`. It returns the nearest Buienradar station's current conditions plus a 2-hour precipitation forecast derived from the raincontent grid.

## Important Details

- Buienradar's `content` field is a JSON string (not already-parsed dict) — it must be `json.loads()`-ed before use (see [buinradar.py:28](app/adapter/buinradar.py#L28)).
- Rain content is encoded as integer codes per 5-minute interval. Use `to_mmh()` to convert: `10 ** ((code - 109) / 32)`.
- UV index is approximated from sunpower (`sunpower * 0.008`).
- Sun elevation is not available from Buienradar — always `None` for now.
- Stations with no data should return `None` fields (not 0), preserving the distinction between "no data" and "zero precipitation".
