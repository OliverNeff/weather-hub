import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from app.adapter.buinradar import fetch_buienradar_weather
from app.adapter.openmeteo import fetch_openmeteo_weather
from app.adapter.wetterdienst_dwd import fetch_wetterdienst_weather

logger = logging.getLogger(__name__)  # noqa
from app.models.weather_data import WeatherData

router = APIRouter(
    prefix="/weather/data",
    tags=["weather-data"]
)

# Fields that can come from any adapter.
_MERGEABLE_FIELDS = [
    "wind_speed",
    "wind_gust",
    "precipitation_intensity",
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
    "precipitation_intensity",
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


def _pick_first(data: tuple, field: str) -> tuple:
    """Return the first non-None value for *field* and its adapter."""
    for wd in data:
        val = getattr(wd, field)
        if val is not None:
            return val, wd
    return None, None


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
    except Exception as e:
        logger.error("adapter %s failed: %s", func.__name__, e, exc_info=True)
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

    # Resolve temperature first — feels_like must come from the same adapter.
    temp_val, temp_adapter = _pick_first(fresh, "temperature")
    setattr(merged, "temperature", temp_val)

    for field in _MERGEABLE_FIELDS:
        if field in ("time", "temperature", "feels_like"):
            continue
        if field in _CONSERVATIVE_FIELDS:
            # Wind + precipitation: highest value across all adapters.
            val = _pick_max(all_data, field)
        else:
            # uv_index, sun data: freshest source.
            val, _ = _pick_first(fresh, field)
        setattr(merged, field, val)

    # feels_like: prefer the adapter that supplied temperature, fallback to freshest.
    if temp_adapter is not None:
        fl = getattr(temp_adapter, "feels_like", None)
    else:
        fl = None
    if fl is None:
        fl, _ = _pick_first(fresh, "feels_like")
    setattr(merged, "feels_like", fl)

    # Compute precipitation_now: True if max measured intensity across adapters > 0.
    # DWD adapter already filters out stale observation data (>2h old).
    precip_intensity = merged.precipitation_intensity
    if precip_intensity is not None:
        merged.precipitation_now = precip_intensity > 0

    # Collect stations from whichever adapter returned data.
    for wd in (dwd, buienradar, openmeteo):
        if wd.stations:
            merged.stations.extend(wd.stations)

    return merged