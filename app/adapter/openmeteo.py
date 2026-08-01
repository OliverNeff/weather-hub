import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation

# ---------------------------------------------------------------------------
# km/h -> m/s
# ---------------------------------------------------------------------------
_KMH_TO_MS = 1 / 3.6

# ---------------------------------------------------------------------------
# Shared async session - reused across requests for connection pooling.
# ---------------------------------------------------------------------------
_session = httpx.AsyncClient(timeout=10)


async def fetch_openmeteo_weather(latitude: float, longitude: float) -> WeatherData:
    """
    Holt aktuelle Wetterdaten + Vorhersage + sunrise/sunset von Open-Meteo.
    """
    try:
        r = await _session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,wind_speed_10m,wind_gusts_10m,precipitation,uv_index,weather_code,cloud_cover",
                "minutely_15": "precipitation",
                "hourly": "precipitation,uv_index",
                "daily": "sunrise,sunset",
                "timezone": "UTC",
                "forecast_days": 1,
            },
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return _empty(latitude, longitude)

    current = data.get("current", {})
    minutely_15 = data.get("minutely_15", {})
    hourly = data.get("hourly", {})
    daily = data.get("daily", {})

    sunrise_dt, sunset_dt, sun_elevation = _parse_sun(daily, latitude, longitude)

    now = datetime.now(timezone.utc)
    forecast = _parse_minutely_precipitation(minutely_15, hourly, now)
    weather_time = _parse_iso(current.get("time"))

    # Compute current precipitation intensity from minutely_15 data (mm/15min → mm/h).
    # Falls back to current.precipitation (hourly sum, less granular).
    precip = _current_precip_from_minutely(minutely_15, now)
    if precip is None:
        precip = _sf(current, "precipitation")

    weather_data = WeatherData(
        temperature=_sf(current, "temperature_2m"),
        feels_like=_sf(current, "apparent_temperature"),
        wind_speed=_kmh(_sf(current, "wind_speed_10m")),
        wind_gust=_kmh(_sf(current, "wind_gusts_10m")),
        precipitation_intensity=precip,
        uv_index=_sf(current, "uv_index"),
        sun_elevation=sun_elevation,
        sunrise=sunrise_dt,
        sunset=sunset_dt,
        weather_code=_si(current, "weather_code"),
        cloud_cover=_si(current, "cloud_cover"),
        **forecast,
    )

    weather_data.stations.append(
        WeatherStation(
            source="openmeteo",
            name="computed",
            lat=latitude,
            lon=longitude,
            time=weather_time or datetime.now(timezone.utc),
        )
    )

    return weather_data


def _empty(lat: float, lon: float) -> WeatherData:
    data = WeatherData()
    data.stations.append(
        WeatherStation(
            source="openmeteo", name="computed", lat=lat, lon=lon, time=datetime.now(timezone.utc)
        )
    )
    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sf(d: dict[str, Any], key: str) -> float | None:
    val = d.get(key)
    if val is None:
        return None
    f = float(val)
    return None if math.isnan(f) else round(f, 1)


def _si(d: dict[str, Any], key: str) -> int | None:
    val = d.get(key)
    if val is None:
        return None
    return int(val)


def _kmh(kmh: float | None) -> float | None:
    if kmh is None:
        return None
    return round(kmh * _KMH_TO_MS, 1)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _parse_sun(daily: dict[str, Any], lat: float, lon: float) -> tuple[datetime | None, datetime | None, float | None]:
    sunrise_dt = None
    sunset_dt = None

    for ts in daily.get("sunrise", []):
        sunrise_dt = _parse_iso(ts)
        break
    for ts in daily.get("sunset", []):
        sunset_dt = _parse_iso(ts)
        break

    now = datetime.now(timezone.utc)
    elevation = _noaa_elevation(lat, lon, now)
    return sunrise_dt, sunset_dt, elevation


def _noaa_elevation(lat: float, lon: float, now: datetime) -> float | None:
    day_of_year = now.timetuple().tm_yday
    dec = math.radians(23.45 * math.sin(2 * math.pi * (284 + day_of_year) / 365))
    lat_r = math.radians(lat)

    solar_noon_utc_h = 12.0 - lon / 15.0
    ha_deg = 15.0 * (now.hour + now.minute / 60.0 + now.second / 3600.0 - solar_noon_utc_h)
    ha = math.radians(ha_deg)

    sin_el = math.sin(lat_r) * math.sin(dec) + math.cos(lat_r) * math.cos(dec) * math.cos(ha)
    sin_el = max(-1.0, min(1.0, sin_el))
    elevation = math.degrees(math.asin(sin_el))

    return round(elevation, 1)


def _current_precip_from_minutely(minutely_15: dict[str, Any], now: datetime) -> float | None:
    """Compute current precipitation intensity (mm/h) from minutely_15 data.

    Takes the nearest 15-minute interval in the future (within 30min) and converts
    mm/15min → mm/h (multiply by 4). Returns None if no data or value is 0.
    """
    times = minutely_15.get("time", [])
    values = minutely_15.get("precipitation", [])
    if not times or not values:
        return None

    best_val: float | None = None
    best_dt: datetime | None = None
    for i, ts in enumerate(times):
        dt = _parse_iso(ts)
        if dt is None:
            continue
        val = values[i]
        if val is None:
            continue
        # Find the nearest future interval within 30 minutes
        diff = (dt - now).total_seconds()
        if 0 <= diff <= 1800:
            if best_dt is None or diff < (best_dt - now).total_seconds():
                best_val = val
                best_dt = dt

    if best_val is not None and best_val > 0:
        return round(best_val * 4, 1)  # mm per 15min → mm/h
    return None


def _build_precip_result() -> dict[str, bool | float | None]:
    return {
        "precipitation_next_30m": None,
        "precipitation_amount_next_30m": None,
        "precipitation_intensity_next_30m": None,
        "precipitation_next_1h": None,
        "precipitation_amount_next_1h": None,
        "precipitation_intensity_next_1h": None,
        "precipitation_next_2h": None,
        "precipitation_amount_next_2h": None,
        "precipitation_intensity_next_2h": None,
    }


def _parse_minutely_precipitation(
    minutely_15: dict[str, Any], hourly: dict[str, Any], now: datetime
) -> dict[str, bool | float | None]:
    """Build 30m / 1h / 2h forecast fields from minutely_15 precipitation data.

    Falls back to hourly data if minutely_15 is not available.
    """
    result = _build_precip_result()

    times = minutely_15.get("time", [])
    values = minutely_15.get("precipitation", [])

    # Fallback to hourly if minutely_15 not available
    if not times or not values:
        return _parse_hourly_precipitation(hourly, now)

    windows = {
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "2h": timedelta(hours=2),
    }

    for label, duration in windows.items():
        window_end = now + duration
        window_vals: list[float] = []
        has_data = False
        for i, ts in enumerate(times):
            dt = _parse_iso(ts)
            if dt is None:
                continue
            if dt >= now and dt < window_end:
                has_data = True
                val = values[i]
                if val is not None and val > 0:
                    # Convert mm/15min → mm/h for intensity
                    window_vals.append(val * 4)

        if has_data:
            if window_vals:
                total = sum(window_vals) / len(window_vals)  # average intensity in mm/h
                result[f"precipitation_amount_next_{label}"] = round(total, 2)
                result[f"precipitation_intensity_next_{label}"] = round(total, 2)
                result[f"precipitation_next_{label}"] = True
            else:
                # Data available but all values are 0 — no rain expected
                result[f"precipitation_next_{label}"] = False
                result[f"precipitation_amount_next_{label}"] = 0.0
                result[f"precipitation_intensity_next_{label}"] = 0.0

    return result


def _parse_hourly_precipitation(
    hourly: dict[str, Any], now: datetime
) -> dict[str, bool | float | None]:
    """Build 30m / 1h / 2h forecast fields from hourly precipitation data."""
    result = _build_precip_result()

    times = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    if not times or not precip:
        return result

    windows = {
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "2h": timedelta(hours=2),
    }

    for label, duration in windows.items():
        window_end = now + duration
        values: list[float] = []
        has_data = False
        for i, ts in enumerate(times):
            dt = _parse_iso(ts)
            if dt is None:
                continue
            if dt >= now and dt < window_end:
                has_data = True
                val = precip[i]
                if val is not None and val > 0:
                    values.append(val)

        if has_data:
            if values:
                mean = sum(values) / len(values)
                result[f"precipitation_amount_next_{label}"] = round(mean, 2)
                result[f"precipitation_intensity_next_{label}"] = round(mean, 2)
                result[f"precipitation_next_{label}"] = True
            else:
                # Data available but all values are 0 — no rain expected
                result[f"precipitation_next_{label}"] = False
                result[f"precipitation_amount_next_{label}"] = 0.0
                result[f"precipitation_intensity_next_{label}"] = 0.0

    return result