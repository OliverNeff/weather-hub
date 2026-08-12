import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

from app.adapter.buinradar import fetch_buienradar_weather
from app.adapter.openmeteo import fetch_openmeteo_weather
from app.adapter.wetterdienst_dwd import fetch_wetterdienst_weather
from app.models.weather_data import WeatherData

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/weather/data", tags=["weather-data"])

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
    "precipitation_stops_at",
]

# Precipitation: take the max across all adapters.
# Missing rain is worse than over-reporting it.
_CONSERVATIVE_FIELDS = {
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

# Wind fields: prefer DWD (station measurements) > Open-Meteo (model) > Buienradar (NL-only).
# Buienradar stations are NL-only and can be 200km+ away for DE coords.
_WIND_ORDER = ("dwd", "openmeteo", "buienradar")


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


def _pick_by_source_order(
    dwd: WeatherData,
    buienradar: WeatherData,
    openmeteo: WeatherData,
    field: str,
    order: tuple[str, ...],
    stale_minutes: float = 30.0,
) -> float | None:
    """Return the first non-None value for *field* from adapters in source priority order.

    If the preferred adapter's data is older than *stale_minutes*, skip to the next.
    This avoids stale station snapshots (e.g. DWD 10-min intervals can be hours old).
    """
    mapping: dict[str, WeatherData] = {"dwd": dwd, "buienradar": buienradar, "openmeteo": openmeteo}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=stale_minutes)

    for source in order:
        wd = mapping.get(source)
        if wd is not None:
            val = getattr(wd, field)
            if val is not None:
                # Check freshness: skip stale data by checking the
                # newest timestamp across all stations for this adapter
                if wd.stations:
                    times = [s.time for s in wd.stations if s.time is not None]
                    if times:
                        freshest = max(times)
                        if freshest < cutoff:
                            continue
                return val  # type: ignore[no-any-return]
    return None


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


def _wmo_to_condition(code: int | None) -> str | None:
    """Map WMO weather code to Home Assistant condition string."""
    mapping = {
        0: "clear-night",
        1: "clear-night",
        2: "partlycloudy",
        3: "cloudy",
        45: "fog",
        48: "fog",
    }
    if code in mapping:
        return mapping[code]
    if code in (51, 53, 55, 56, 61, 63, 65, 80, 81, 82):
        return "rainy"
    if code in (52, 54, 64):
        return "pouring"
    if code in (66, 67, 86):
        return "snowy-rainy"
    if code in (71, 73, 75, 77, 85, 87):
        return "snowy"
    if code == 95:
        return "lightning"
    if code in (96, 99):
        return "lightning-rainy"
    if code is not None:
        return "cloudy"
    return None


@router.get("", response_model=WeatherData, responses={200: {"model": WeatherData}})
async def get_weather_data(
    lat: float = Query(
        ..., description="Latitude of the location (decimal degrees)", ge=-90, le=90
    ),
    lon: float = Query(
        ..., description="Longitude of the location (decimal degrees)", ge=-180, le=180
    ),
) -> WeatherData:
    """Current weather and short-term precipitation forecast.

    Fetches data from three providers in parallel (DWD, Open-Meteo, Buienradar)
    and merges the results. A single provider failure doesn't affect the response.

    **Merge strategy**

    - Wind: source priority (DWD > Open-Meteo > Buienradar)
    - Precipitation: maximum value across all providers
    - Temperature / feels-like: freshest measurement
    - UV / sun data: freshest source (Open-Meteo provides accurate UV)
    - Status: derived from merged data (Home Assistant compatible)

    All response fields are nullable — `null` means the provider had no data.
    """
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
        if field in ("time", "temperature", "feels_like", "precipitation_stops_at"):
            continue
        if field in ("wind_speed", "wind_gust"):
            # Prefer DWD station measurements over model data.
            val = _pick_by_source_order(dwd, buienradar, openmeteo, field, _WIND_ORDER)
        elif field in _CONSERVATIVE_FIELDS:
            # Precipitation: highest value across all adapters.
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

    # If there's no current rain, clear precipitation_stops_at (meaningless
    # without an active rain session). Keep the forecast bool/amount/intensity
    # fields as-is — they still reflect adapter data (false = "no rain expected",
    # null = "no data available").
    if merged.precipitation_now is not True:
        merged.precipitation_stops_at = None
    else:
        # precipitation_stops_at: prefer most granular source.
        # Buienradar (5min) > Open-Meteo (15min/hourly) > DWD MosMix (1h).
        stops_at = getattr(buienradar, "precipitation_stops_at", None)
        if stops_at is None:
            stops_at, _ = _pick_first(fresh, "precipitation_stops_at")
        merged.precipitation_stops_at = stops_at

    # Derive HA weather status from WMO code.
    merged.status = _wmo_to_condition(merged.weather_code)

    # Collect stations from whichever adapter returned data.
    for wd in (dwd, buienradar, openmeteo):
        if wd.stations:
            merged.stations.extend(wd.stations)

    return merged
