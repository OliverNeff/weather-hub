import math
from datetime import datetime, timezone

import httpx

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation


async def fetch_openmeteo_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt sunrise/sunset von Open-Meteo und berechnet daraus die
    aktuelle Sonnenelevation (sinusoidal approximation).
    """
    sun_elevation = await _get_sun_elevation(latitude, longitude)

    weather_data = WeatherData(
        time=datetime.now(timezone.utc),
        sun_elevation=sun_elevation,
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


async def _get_sun_elevation(lat: float, lon: float) -> float | None:
    """
    Fetch sunrise/sunset from Open-Meteo for today in UTC, compute sun
    elevation via sinusoidal approximation scaled to the actual maximum
    elevation for the given latitude.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "sunrise,sunset",
                    "timezone": "UTC",
                    "forecast_days": 1,
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    try:
        daily = data["daily"]
        sunrise_str = daily["sunrise"][0]
        sunset_str = daily["sunset"][0]
    except (KeyError, IndexError):
        return None

    sunrise_dt = _parse_utc(sunrise_str)
    sunset_dt = _parse_utc(sunset_str)
    if sunrise_dt is None or sunset_dt is None:
        return None

    now = datetime.now(timezone.utc)

    # Before sunrise or after sunset -> below horizon.
    if now < sunrise_dt or now > sunset_dt:
        return None

    # NOAA solar elevation formula: sin(el) = sin(lat)*sin(dec) + cos(lat)*cos(dec)*cos(HA)
    day_of_year = now.timetuple().tm_yday
    # Solar declination in radians
    dec = math.radians(23.45 * math.sin(2 * math.pi * (284 + day_of_year) / 365))
    lat_r = math.radians(lat)

    # Hour angle in degrees: 15° per hour from solar noon at this longitude.
    solar_noon_utc_h = 12.0 - lon / 15.0
    ha_deg = 15.0 * (now.hour + now.minute / 60.0 + now.second / 3600.0 - solar_noon_utc_h)
    ha = math.radians(ha_deg)

    sin_el = math.sin(lat_r) * math.sin(dec) + math.cos(lat_r) * math.cos(dec) * math.cos(ha)
    sin_el = max(-1.0, min(1.0, sin_el))  # clamp
    elevation = math.degrees(math.asin(sin_el))

    if elevation < 0:
        return None

    return round(elevation, 1)


def _parse_utc(iso_str: str) -> datetime:
    """Parse an ISO 8601 datetime string, always return UTC."""
    try:
        parsed = datetime.fromisoformat(iso_str)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))