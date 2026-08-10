from datetime import datetime

from pydantic import BaseModel, Field

from app.models.weather_station import WeatherStation


class WeatherData(BaseModel):
    """Merged weather data from DWD, Open-Meteo and Buienradar."""

    # --- Status ---
    status: str | None = Field(
        default=None,
        description="Home Assistant weather status (sunny, cloudy, rainy, pouring, fog, snowy, "
        "snowy-rainy, hail, lightning, lightning-rainy, windy, windy-variant, clear-night, partlycloudy)",
    )
    weather_code: int | None = Field(
        default=None,
        description="WMO weather code from Open-Meteo (0-99)",
    )
    cloud_cover: int | None = Field(
        default=None,
        description="Cloud cover percentage (0-100)",
    )

    # --- Wind ---
    wind_speed: float | None = Field(
        default=None,
        description="Current wind speed in m/s (DWD > Open-Meteo > Buienradar)",
    )
    wind_gust: float | None = Field(
        default=None,
        description="Maximum wind gust in m/s (DWD > Open-Meteo > Buienradar)",
    )

    # --- Precipitation (current) ---
    precipitation_now: bool | None = Field(
        default=None,
        description="True if precipitation is currently being measured",
    )
    precipitation_intensity: float | None = Field(
        default=None,
        description="Current precipitation intensity in mm/h (max across providers)",
    )

    # --- Precipitation (forecast) ---
    precipitation_next_30m: bool | None = Field(
        default=None,
        description="True if precipitation is expected in the next 30 minutes",
    )
    precipitation_amount_next_30m: float | None = Field(
        default=None,
        description="Expected precipitation amount in mm for the next 30 minutes",
    )
    precipitation_intensity_next_30m: float | None = Field(
        default=None,
        description="Maximum expected precipitation intensity in mm/h for the next 30 minutes",
    )
    precipitation_next_1h: bool | None = Field(
        default=None,
        description="True if precipitation is expected in the next hour",
    )
    precipitation_amount_next_1h: float | None = Field(
        default=None,
        description="Expected precipitation amount in mm for the next hour",
    )
    precipitation_intensity_next_1h: float | None = Field(
        default=None,
        description="Maximum expected precipitation intensity in mm/h for the next hour",
    )
    precipitation_next_2h: bool | None = Field(
        default=None,
        description="True if precipitation is expected in the next 2 hours",
    )
    precipitation_amount_next_2h: float | None = Field(
        default=None,
        description="Expected precipitation amount in mm for the next 2 hours",
    )
    precipitation_intensity_next_2h: float | None = Field(
        default=None,
        description="Maximum expected precipitation intensity in mm/h for the next 2 hours",
    )

    # --- Precipitation (end) ---
    precipitation_stops_at: datetime | None = Field(
        default=None,
        description="UTC time when current precipitation is expected to stop",
    )

    # --- Temperature ---
    temperature: float | None = Field(
        default=None,
        description="Current temperature in °C",
    )
    feels_like: float | None = Field(
        default=None,
        description="Apparent temperature in °C",
    )

    # --- UV / Sun ---
    uv_index: float | None = Field(
        default=None,
        description="UV index (0-16+)",
    )
    sun_elevation: float | None = Field(
        default=None,
        description="Sun elevation in degrees (negative when below horizon)",
    )
    sunrise: datetime | None = Field(
        default=None,
        description="Sunrise time today (UTC)",
    )
    sunset: datetime | None = Field(
        default=None,
        description="Sunset time today (UTC)",
    )

    # --- Stations ---
    stations: list[WeatherStation] = Field(
        default_factory=list,
        description="Weather stations that contributed data",
    )
