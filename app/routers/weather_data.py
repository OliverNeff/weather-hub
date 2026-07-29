import asyncio
from datetime import datetime, timezone
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
# Priority: DWD (nearest German station) > Buienradar > OpenMeteo.
_MERGEABLE_FIELDS = [
    "time",
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


def _pick_first(data: tuple, field: str):
    """Return the first non-None value for *field* across adapters."""
    for wd in data:
        val = getattr(wd, field)
        if val is not None:
            return val
    return None


@router.get("", response_model=WeatherData)
async def get_weather_data(
    lat: float,
    lon: float
) -> WeatherData:
    # Fetch all three adapters in parallel.
    dwd, buienradar, openmeteo = await asyncio.gather(
        fetch_wetterdienst_weather(latitude=lat, longitude=lon),
        fetch_buienradar_weather(latitude=lat, longitude=lon),
        fetch_openmeteo_weather(latitude=lat, longitude=lon),
    )

    # Resolve time from first adapter that has it.
    time_val = _pick_first((dwd, buienradar, openmeteo), "time")
    if time_val is None:
        time_val = datetime.now(timezone.utc)

    # Build merged WeatherData — start with time (required field).
    merged = WeatherData(time=time_val)

    # For each mergeable field, take first non-None across adapters.
    for field in _MERGEABLE_FIELDS:
        if field == "time":
            continue
        val = _pick_first((dwd, buienradar, openmeteo), field)
        setattr(merged, field, val)

    # Collect stations from whichever adapter returned data.
    for wd in (dwd, buienradar, openmeteo):
        if wd.stations:
            merged.stations.extend(wd.stations)

    return merged