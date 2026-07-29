import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from app.adapter.buinradar import fetch_buienradar_weather
from app.adapter.openmeteo import fetch_openmeteo_weather
from app.adapter.wetterdienst_dwd import fetch_wetterdienst_weather
from app.models.weather_data import WeatherData

router = APIRouter(
    prefix="/weather/data",
    tags=["weather-data"]
)

# Fields that can come from any adapter.
_MERGEABLE_FIELDS = [
    "wind_speed",
    "wind_gust",
    "precipitation_rate",
    "precipitation_next_30m",
    "precipitation_amount_next_30m",
    "precipitation_intensity_next_30m",
    "precipitation_next_1h",
    "precipitation_amount_next_1h",
    "precipitation_intensity_next_1h",
    "precipitation_next_2h",
    "precipitation_amount_next_2h",
    "precipitation_intensity_next_2h",
    "temperature",
    "feels_like",
    "uv_index",
    "sun_elevation",
    "sunrise",
    "sunset",
]

# Wind + precipitation: take the max across all adapters.
# Missing rain or under-reported wind is worse than over-reporting it.
_CONSERVATIVE_FIELDS = {
    "wind_speed",
    "wind_gust",
    "precipitation_rate",
    "precipitation_next_30m",
    "precipitation_amount_next_30m",
    "precipitation_intensity_next_30m",
    "precipitation_next_1h",
    "precipitation_amount_next_1h",
    "precipitation_intensity_next_1h",
    "precipitation_next_2h",
    "precipitation_amount_next_2h",
    "precipitation_intensity_next_2h",
}


def _pick_first(data: tuple, field: str):
    """Return the first non-None value for *field* across adapters."""
    for wd in data:
        val = getattr(wd, field)
        if val is not None:
            return val
    return None


def _pick_max(data: tuple, field: str):
    """Return the max non-None value for *field*."""
    best = None
    for wd in data:
        val = getattr(wd, field)
        if val is not None:
            if best is None or val > best:
                best = val
    return best


def _sorted_by_freshness(data: tuple) -> list:
    """Sort adapters newest first. Adapters without a time go to the end."""
    now = datetime.now(timezone.utc)
    def _sort_key(wd):
        if not wd.stations:
            return now - timedelta(days=1)
        t = wd.stations[0].time
        if t is None:
            return now - timedelta(days=1)
        return t
    return sorted(data, key=_sort_key, reverse=True)


async def _safe_fetch(func, lat, lon):
    """Call an adapter; on failure return an empty WeatherData."""
    try:
        return await func(latitude=lat, longitude=lon)
    except Exception:
        return WeatherData()


@router.get("", response_model=WeatherData)
async def get_weather_data(
    lat: float,
    lon: float
) -> WeatherData:
    # Fetch all three adapters in parallel — each wrapped so one failure
    # doesn't take down the whole request.
    dwd, buienradar, openmeteo = await asyncio.gather(
        _safe_fetch(fetch_wetterdienst_weather, lat, lon),
        _safe_fetch(fetch_buienradar_weather, lat, lon),
        _safe_fetch(fetch_openmeteo_weather, lat, lon),
    )

    merged = WeatherData()

    # Order adapters by freshness (newest data first) for accurate fields.
    fresh = _sorted_by_freshness((dwd, buienradar, openmeteo))
    all_data = (dwd, buienradar, openmeteo)

    for field in _MERGEABLE_FIELDS:
        if field == "time":
            continue
        if field in _CONSERVATIVE_FIELDS:
            # Wind + precipitation: highest value across all adapters.
            val = _pick_max(all_data, field)
        elif field == "feels_like":
            # Prefer openmeteo (apparent_temperature), fallback to any.
            om = _pick_first(
                [a for a in fresh if a.stations and a.stations[0].source == "openmeteo"],
                field,
            )
            val = om if om is not None else _pick_first(fresh, field)
        else:
            # uv_index, temperature, sun data: freshest source.
            val = _pick_first(fresh, field)
        setattr(merged, field, val)

    # Collect stations from whichever adapter returned data.
    for wd in (dwd, buienradar, openmeteo):
        if wd.stations:
            merged.stations.extend(wd.stations)

    return merged