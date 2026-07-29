import math
from datetime import datetime, timedelta, timezone

import niquests
import openmeteo_requests

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation

# ---------------------------------------------------------------------------
# Open-Meteo variable IDs
# ---------------------------------------------------------------------------
# Current
_VAR_TEMPERATURE = 47
_VAR_APPARENT_TEMPERATURE = 1
_VAR_WIND_SPEED = 59      # km/h
_VAR_WIND_GUSTS = 58      # km/h
_VAR_PRECIPITATION = 24
_VAR_UV_INDEX = 52

# Daily (int64)
_VAR_SUNRISE = 40
_VAR_SUNSET = 41

# km/h → m/s
_KMH_TO_MS = 1 / 3.6

# ---------------------------------------------------------------------------
# Shared session + client — reused across all requests for connection pooling.
# ---------------------------------------------------------------------------
_session = niquests.AsyncSession()
_client = openmeteo_requests.AsyncClient(session=_session)


async def fetch_openmeteo_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt aktuelle Wetterdaten + Vorhersage + sunrise/sunset von Open-Meteo.
    """
    try:
        responses = await _client.weather_api(
            url="https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": (
                    "temperature_2m,apparent_temperature,"
                    "wind_speed_10m,wind_gusts_10m,precipitation,uv_index"
                ),
                "hourly": "precipitation,uv_index",
                "daily": "sunrise,sunset",
                "timezone": "UTC",
                "forecast_days": 1,
            },
        )
    except Exception:
        return _empty(latitude, longitude)

    try:
        resp = responses[0]
    except IndexError:
        return _empty(latitude, longitude)

    current = resp.Current()
    sunrise_dt, sunset_dt, sun_elevation = _parse_sun(current, resp.Daily(), latitude, longitude)

    # Build forecast windows from hourly precipitation
    now = datetime.now(timezone.utc)
    forecast = _parse_hourly_precipitation(resp.Hourly(), now)

    weather_data = WeatherData(
        time=datetime.fromtimestamp(current.Time(), tz=timezone.utc),
        temperature=_safe(current, _VAR_TEMPERATURE),
        feels_like=_safe(current, _VAR_APPARENT_TEMPERATURE),
        wind_speed=_kmh(_safe(current, _VAR_WIND_SPEED)),
        wind_gust=_kmh(_safe(current, _VAR_WIND_GUSTS)),
        precipitation_rate=_safe(current, _VAR_PRECIPITATION),
        uv_index=_safe(current, _VAR_UV_INDEX),
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
        )
    )

    return weather_data


def _empty(lat: float, lon: float) -> WeatherData:
    """Return an empty WeatherData when the API call failed entirely."""
    data = WeatherData(time=datetime.now(timezone.utc))
    data.stations.append(
        WeatherStation(source="openmeteo", name="computed", lat=lat, lon=lon)
    )
    return data


# ---------------------------------------------------------------------------
# Helpers — Current variables
# ---------------------------------------------------------------------------

def _safe(current, var_id: int) -> float | None:
    """Extract a single float value from a Current VariablesWithTime by var_id."""
    for i in range(current.VariablesLength()):
        v = current.Variables(i)
        if v.Variable() == var_id and not v.ValuesIsNone():
            val = v.Value()
            return None if (val != val) else round(val, 1)  # NaN guard
    return None


def _kmh(kmh: float | None) -> float | None:
    """Convert km/h to m/s."""
    if kmh is None:
        return None
    return round(kmh * _KMh_TO_MS, 1)


# ---------------------------------------------------------------------------
# Helpers — Sunrise / Sunset + elevation
# ---------------------------------------------------------------------------

def _parse_sun(
    current, daily, lat: float, lon: float
) -> tuple[datetime | None, datetime | None, float | None]:
    sunrise_ts = None
    sunset_ts = None

    for i in range(daily.VariablesLength()):
        var = daily.Variables(i)
        var_id = var.Variable()
        length = var.ValuesInt64Length()
        if var_id == _VAR_SUNRISE and length > 0:
            sunrise_ts = var.ValuesInt64(0)
        elif var_id == _VAR_SUNSET and length > 0:
            sunset_ts = var.ValuesInt64(0)

    sunrise_dt = (
        datetime.fromtimestamp(sunrise_ts, tz=timezone.utc)
        if sunrise_ts is not None
        else None
    )
    sunset_dt = (
        datetime.fromtimestamp(sunset_ts, tz=timezone.utc)
        if sunset_ts is not None
        else None
    )

    if sunrise_dt is None or sunset_dt is None:
        return sunrise_dt, sunset_dt, None

    current_ts = current.Time()
    now = datetime.fromtimestamp(current_ts, tz=timezone.utc)

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


# ---------------------------------------------------------------------------
# Helpers — Hourly precipitation forecast windows
# ---------------------------------------------------------------------------

def _parse_hourly_precipitation(
    hourly, now: datetime
) -> dict:
    """
    Build 30m / 1h / 2h forecast fields from hourly precipitation data.

    Open-Meteo returns hourly totals (mm per hour). We average the values
    that fall within each window for amount and intensity.
    """
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

    # Find precipitation variable in hourly
    precip_var = None
    for i in range(hourly.VariablesLength()):
        v = hourly.Variables(i)
        if v.Variable() == _VAR_PRECIPITATION:
            precip_var = v
            break

    if precip_var is None:
        return result

    # Get all hourly timestamps and values
    start_ts = hourly.Time()
    n = precip_var.ValuesLength()
    if n == 0:
        return result

    windows = {
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "2h": timedelta(hours=2),
    }

    now_utc = datetime.now(timezone.utc)

    for label, duration in windows.items():
        window_end = now_utc + duration
        values: list[float] = []

        for j in range(n):
            # Each hour is spaced by 3600s from start
            ts = start_ts + (j * 3600)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if dt >= now_utc and dt < window_end:
                val = precip_var.Values(j)
                if val is not None and val > 0:
                    values.append(val)

        if values:
            mean = sum(values) / len(values)
            result[f"precipitation_amount_next_{label}"] = round(mean, 2)
            result[f"precipitation_intensity_next_{label}"] = round(mean, 2)
            result[f"precipitation_next_{label}"] = True

    return result