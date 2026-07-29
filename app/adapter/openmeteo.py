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

    # Solar declination (degrees) — needed to scale the sinusoidal curve.
    day_of_year = now.timetuple().tm_yday
    declination = math.radians(23.45 * math.sin(2 * math.pi * (284 + day_of_year) / 365))

    # Maximum elevation at solar noon: 90° - latitude + declination
    lat_rad = math.radians(lat)
    max_elevation = math.degrees(math.pi / 2 - lat_rad + declination)

    # Sinusoidal curve scaled to the actual max elevation.
    day_length = (sunset_dt - sunrise_dt).total_seconds()
    elapsed = (now - sunrise_dt).total_seconds()
    fraction = elapsed / day_length
    elevation = math.sin(fraction * math.pi) * max_elevation

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