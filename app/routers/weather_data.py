import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter

from app.adapter.buinradar import fetch_buienradar_weather
from app.adapter.openmeteo import fetch_openmeteo_weather
from app.adapter.wetterdienst_dwd import fetch_wetterdienst_weather
from app.models.weather_data import WeatherData

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/weather/data", tags=["weather-data"])

# Home Assistant weather status values
_STATUS_CLEAR_NIGHT = "clear-night"
_STATUS_CLOUDY = "cloudy"
_STATUS_FOG = "fog"
_STATUS_HAIL = "hail"
_STATUS_LIGHTNING = "lightning"
_STATUS_LIGHTNING_RAINY = "lightning-rainy"
_STATUS_PARTLYCLOUDY = "partlycloudy"
_STATUS_POURING = "pouring"
_STATUS_RAINY = "rainy"
_STATUS_SNOWY = "snowy"
_STATUS_SNOWY_RAINY = "snowy-rainy"
_STATUS_SUNNY = "sunny"
_STATUS_WINDY = "windy"
_STATUS_WINDY_VARIANT = "windy-variant"

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
    "weather_code",
    "cloud_cover",
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


def _pick_first(data: Sequence[WeatherData], field: str) -> tuple[Any, Any]:
    """Return the first non-None value for *field* and its adapter."""
    for wd in data:
        val = getattr(wd, field)
        if val is not None:
            return val, wd
    return None, None


def _pick_max(data: Sequence[WeatherData], field: str) -> float | None:
    """Return the max non-None value for *field*."""
    best: float | None = None
    for wd in data:
        val = getattr(wd, field)
        if val is not None and (best is None or val > best):
            best = val
    return best


def _sorted_by_freshness(data: Sequence[WeatherData]) -> list[WeatherData]:
    """Sort adapters newest first. Adapters without a time go to the end."""
    now = datetime.now(timezone.utc)

    def _sort_key(wd: WeatherData) -> datetime:
        if not wd.stations:
            return now - timedelta(days=1)
        t = wd.stations[0].time
        if t is None:
            return now - timedelta(days=1)
        return t

    return sorted(data, key=_sort_key, reverse=True)


async def _safe_fetch(func: Any, lat: float, lon: float) -> WeatherData:
    """Call an adapter; on failure return an empty WeatherData."""
    try:
        result: WeatherData = await func(latitude=lat, longitude=lon)
        return result
    except Exception as e:
        logger.error("adapter %s failed: %s", func.__name__, e, exc_info=True)
        return WeatherData()


def _compute_status(wd: WeatherData) -> str | None:
    """Derive a Home Assistant-compatible weather status from merged data.

    Priority: precipitation > thunder > snow > wind > cloud > clear.

    Uses merged values (max across adapters for wind/precip) plus
    Open-Meteo weather_code and cloud_cover for conditions not directly
    measurable (fog, snow, thunder, cloud cover).

    When merged precipitation is 0 but weather_code indicates rain/snow,
    the weather_code takes precedence — it reflects the latest model data
    which may not yet be captured by station measurements or radar.
    """
    precip = wd.precipitation_intensity
    wind = wd.wind_speed
    code = wd.weather_code
    cloud = wd.cloud_cover
    temp = wd.temperature
    sun_el = wd.sun_elevation

    # Night check — sun below horizon
    is_night = sun_el is not None and sun_el < 0

    # Thunder (WMO 95-96) — always from model, independent of measured precip
    if code == 96:
        return _STATUS_LIGHTNING_RAINY
    if code == 95:
        return _STATUS_LIGHTNING

    # Snow / freezing rain from WMO codes — model-based, always trust
    if code in (66, 67, 86):
        return _STATUS_SNOWY_RAINY

    # Hail: WMO 77 (snow grains) can indicate hail when temp is low.
    # Must come before generic snowy check — 77 is ambiguous.
    if code == 77 and temp is not None and temp <= 2:
        return _STATUS_HAIL

    if code in (71, 73, 75, 85):
        return _STATUS_SNOWY
    if code == 77:
        return _STATUS_SNOWY

    # Measured precipitation (merged max across all adapters — includes
    # Buienradar radar which overrides stale station data)
    if precip is not None and precip > 5:
        return _STATUS_POURING
    if precip is not None and precip > 0:
        return _STATUS_RAINY

    # WMO-based rain/shower — model says rain but stations/radar report 0.
    # This can happen when rain just started and station data is stale.
    if code in (51, 52, 53, 54, 55, 61, 63, 64, 80, 81, 82):
        return _STATUS_RAINY

    # Wind (uses merged max across all adapters)
    if wind is not None and wind >= 15:
        return _STATUS_WINDY_VARIANT
    if wind is not None and wind >= 10:
        return _STATUS_WINDY

    # Fog (WMO 45, 48)
    if code in (45, 48):
        return _STATUS_FOG

    # Cloud cover from WMO code
    if code is not None:
        if code in (0, 1):
            return _STATUS_CLEAR_NIGHT if is_night else _STATUS_SUNNY
        if code == 2:
            return _STATUS_PARTLYCLOUDY
        if 3 <= code <= 44:
            return _STATUS_CLOUDY

    # Fallback: derive status from cloud_cover when weather_code unavailable
    if cloud is not None:
        if cloud <= 10:
            return _STATUS_CLEAR_NIGHT if is_night else _STATUS_SUNNY
        if cloud <= 50:
            return _STATUS_PARTLYCLOUDY
        return _STATUS_CLOUDY

    return None


@router.get("", response_model=WeatherData)
async def get_weather_data(lat: float, lon: float) -> WeatherData:
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
    merged.temperature = temp_val

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
    fl = getattr(temp_adapter, "feels_like", None) if temp_adapter is not None else None
    if fl is None:
        fl, _ = _pick_first(fresh, "feels_like")
    merged.feels_like = fl

    # Compute precipitation_now: True if max measured intensity across adapters > 0.
    # DWD adapter already filters out stale observation data (>2h old).
    precip_intensity = merged.precipitation_intensity
    if precip_intensity is not None:
        merged.precipitation_now = precip_intensity > 0

    # Derive Home Assistant-compatible status from merged data.
    merged.status = _compute_status(merged)

    # Collect stations from whichever adapter returned data.
    for wd in (dwd, buienradar, openmeteo):
        if wd.stations:
            merged.stations.extend(wd.stations)

    return merged
