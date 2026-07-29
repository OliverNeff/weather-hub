import math
from datetime import datetime, timezone

import openmeteo_requests

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation

# Open-Meteo variable IDs
VAR_SUNRISE = 40
VAR_SUNSET = 41


async def fetch_openmeteo_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt sunrise/sunset von Open-Meteo und berechnet daraus die
    aktuelle Sonnenelevation (NOAA formula).
    """
    sunrise_dt, sunset_dt, sun_elevation = await _fetch_sun_data(
        latitude, longitude
    )

    weather_data = WeatherData(
        time=datetime.now(timezone.utc),
        sun_elevation=sun_elevation,
        sunrise=sunrise_dt,
        sunset=sunset_dt,
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


async def _fetch_sun_data(
    lat: float, lon: float
) -> tuple[datetime | None, datetime | None, float | None]:
    """
    Fetch sunrise/sunset from Open-Meteo, compute elevation via NOAA formula.
    Returns (sunrise, sunset, elevation) — any component may be None.
    """
    try:
        client = openmeteo_requests.AsyncClient()
        responses = await client.weather_api(
            url="https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "sunrise,sunset",
                "timezone": "UTC",
                "forecast_days": 1,
            },
        )
    except Exception:
        return None, None, None

    try:
        daily = responses[0].Daily()
    except (IndexError, AttributeError):
        return None, None, None

    sunrise_ts = None
    sunset_ts = None

    for i in range(daily.VariablesLength()):
        var = daily.Variables(i)
        var_id = var.Variable()
        length = var.ValuesInt64Length()
        if var_id == VAR_SUNRISE and length > 0:
            sunrise_ts = var.ValuesInt64(0)
        elif var_id == VAR_SUNSET and length > 0:
            sunset_ts = var.ValuesInt64(0)

    sunrise_dt = datetime.fromtimestamp(sunrise_ts, tz=timezone.utc) if sunrise_ts is not None else None
    sunset_dt = datetime.fromtimestamp(sunset_ts, tz=timezone.utc) if sunset_ts is not None else None

    if sunrise_dt is None or sunset_dt is None:
        return sunrise_dt, sunset_dt, None

    now = datetime.now(timezone.utc)

    # Before sunrise or after sunset -> below horizon.
    if now < sunrise_dt or now > sunset_dt:
        return sunrise_dt, sunset_dt, None

    # NOAA solar elevation formula.
    day_of_year = now.timetuple().tm_yday
    dec = math.radians(23.45 * math.sin(2 * math.pi * (284 + day_of_year) / 365))
    lat_r = math.radians(lat)

    solar_noon_utc_h = 12.0 - lon / 15.0
    ha_deg = 15.0 * (now.hour + now.minute / 60.0 + now.second / 3600.0 - solar_noon_utc_h)
    ha = math.radians(ha_deg)

    sin_el = math.sin(lat_r) * math.sin(dec) + math.cos(lat_r) * math.cos(dec) * math.cos(ha)
    sin_el = max(-1.0, min(1.0, sin_el))
    elevation = math.degrees(math.asin(sin_el))

    return sunrise_dt, sunset_dt, round(elevation, 1)