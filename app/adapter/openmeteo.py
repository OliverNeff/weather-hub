import math
from datetime import datetime, timezone

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation


async def fetch_openmeteo_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt Sonnenstand-Daten fuer die gegebenen Koordinaten.
    Berechnet die aktuelle Sonnenelevation aus astronomischer Formel.
    """
    sun_elevation = _get_sun_elevation(latitude, longitude)

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


def _get_sun_elevation(lat: float, lon: float) -> float | None:
    """
    Calculates sun elevation angle using simplified solar position
    algorithm. Returns None if sun is below horizon.
    """
    try:
        now = datetime.now(timezone.utc)
        day_of_year = now.timetuple().tm_yday

        # Solar declination (degrees)
        declination = 23.45 * math.sin(
            2 * math.pi * (284 + day_of_year) / 365
        )

        # Hour angle: 15 deg per hour from solar noon at location.
        solar_noon_utc = 12.0 - lon / 15.0
        hour_angle = 15.0 * (now.hour + now.minute / 60.0 + now.second / 3600.0 - solar_noon_utc)

        lat_rad = math.radians(lat)
        dec_rad = math.radians(declination)
        ha_rad = math.radians(hour_angle)

        sin_elevation = (
            math.sin(lat_rad) * math.sin(dec_rad)
            + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
        )

        # Clamp to [-1, 1] for asin safety
        sin_elevation = max(-1.0, min(1.0, sin_elevation))
        elevation = math.degrees(math.asin(sin_elevation))

        # Below horizon -> None
        if elevation < 0:
            return None

        return round(elevation, 1)
    except Exception:
        return None