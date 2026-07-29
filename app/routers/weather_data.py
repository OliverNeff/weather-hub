import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter

from app.adapter.buinradar import fetch_buienradar_weather
from app.adapter.openmeteo import fetch_openmeteo_weather
from app.adapter.wetterdienst_dwd import fetch_wetterdienst_weather
from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation

router = APIRouter(
    prefix="/weather/data",
    tags=["weather-data"]
)

# Fields that can come from any adapter.
# Priority: DWD (nearest German station) > Buienradar > OpenMeteo.
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

# Precipitation fields where we take the conservative (max) value across
# all adapters — if *any* adapter reports rain, we report it.
_PRECIP_FIELDS = {
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
    """Return the max non-None value for *field* — used for precipitation."""
    best = None
    for wd in data:
        val = getattr(wd, field)
        if val is not None:
            if best is None or val > best:
                best = val
    return best


def _station_time(wd: WeatherData) -> datetime | None:
    """Return the time of the first station, if any."""
    if wd.stations:
        return wd.stations[0].time
    return None


def _sorted_by_time(data: tuple) -> list:
    """Sort adapters newest first. Adapters without a time go to the end."""
    now = datetime.now(timezone.utc)
    def _sort_key(wd):
        t = _station_time(wd)
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

    # Order adapters by freshness — newest data wins.
    fresh = _sorted_by_time((dwd, buienradar, openmeteo))

    # For each mergeable field, take first non-None across adapters.
    # Precipitation fields use max (conservative: rain > no rain).
    # feels_like prefers openmeteo (apparent_temperature) over buienradar.
    for field in _MERGEABLE_FIELDS:
        if field == "time":
            continue
        if field in _PRECIP_FIELDS:
            val = _pick_max(fresh, field)
        elif field == "feels_like":
            openmeteo_first = _pick_first([a for a in fresh if a.stations and a.stations[0].source == "openmeteo"], field)
            val = openmeteo_first if openmeteo_first is not None else _pick_first(fresh, field)
        else:
            val = _pick_first(fresh, field)
        setattr(merged, field, val)

    # Collect stations from whichever adapter returned data.
    for wd in (dwd, buienradar, openmeteo):
        if wd.stations:
            merged.stations.extend(wd.stations)

    return merged