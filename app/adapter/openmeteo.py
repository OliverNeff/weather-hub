import httpx
from datetime import datetime, timezone, timedelta
import math

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


async def fetch_openmeteo_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt aktuelle Wetterdaten + Vorhersage + sunrise/sunset von Open-Meteo.
    """
    try:
        r = await _session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,wind_speed_10m,wind_gusts_10m,precipitation,uv_index",
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
    hourly = data.get("hourly", {})
    daily = data.get("daily", {})

    sunrise_dt, sunset_dt, sun_elevation = _parse_sun(daily, latitude, longitude)

    now = datetime.now(timezone.utc)
    forecast = _parse_hourly_precipitation(hourly, now)
    weather_time = _parse_iso(current.get("time"))

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
        WeatherStation(source="openmeteo", name="computed", lat=lat, lon=lon, time=datetime.now(timezone.utc))
    )
    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sf(d: dict, key: str):
    """Extract a single float value, None if missing or NaN."""
    val = d.get(key)
    if val is None:
        return None
    f = float(val)
    return None if math.isnan(f) else round(f, 1)


def _kmh(kmh):
    """Convert km/h to m/s."""
    if kmh is None:
        return None
    return round(kmh * _KMH_TO_MS, 1)


def _parse_iso(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_sun(daily: dict, lat: float, lon: float):
    sunrise_dt = None
    sunset_dt = None

    for ts in daily.get("sunrise", []):
        sunrise_dt = _parse_iso(ts)
        break
    for ts in daily.get("sunset", []):
        sunset_dt = _parse_iso(ts)
        break

    if sunrise_dt is None or sunset_dt is None:
        return sunrise_dt, sunset_dt, None

    now = datetime.now(timezone.utc)
    if now < sunrise_dt or now > sunset_dt:
        return sunrise_dt, sunset_dt, None

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


def _parse_hourly_precipitation(hourly: dict, now: datetime):
    """Build 30m / 1h / 2h forecast fields from hourly precipitation data."""
    result = {
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
        values = []
        for i, ts in enumerate(times):
            dt = _parse_iso(ts)
            if dt is None:
                continue
            if dt >= now and dt < window_end:
                val = precip[i]
                if val is not None and val > 0:
                    values.append(val)

        if values:
            mean = sum(values) / len(values)
            result[f"precipitation_amount_next_{label}"] = round(mean, 2)
            result[f"precipitation_intensity_next_{label}"] = round(mean, 2)
            result[f"precipitation_next_{label}"] = True

    return result