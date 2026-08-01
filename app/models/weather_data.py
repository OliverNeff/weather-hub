from pydantic import BaseModel, Field
from datetime import datetime

from app.models.weather_station import WeatherStation


class WeatherData(BaseModel):

    # Status (Home Assistant compatible)
    status: str | None = None

    # WMO weather code (from Open-Meteo)
    weather_code: int | None = None
    cloud_cover: int | None = None

    # Wind
    wind_speed: float | None = None                    # m/s – aktuelle Windgeschwindigkeit
    wind_gust: float | None = None                     # m/s – maximale Böe

    # Regen (aktuell)
    precipitation_now: bool | None = None              # True wenn gerade Niederschlag gemessen oder sofort erwartet wird
    precipitation_intensity: float | None = None       # mm/h – gemessene Regenintensität

    # Regen (Vorhersage)

    precipitation_next_30m: bool | None = None
    # Regenwahrscheinlichkeit für die nächsten 30 Minuten (binär).
    # False = kein Regen erwartet, True = Regen sicher

    precipitation_amount_next_30m: float | None = None
    # mm – aufsummierte Niederschlagsmenge der nächsten 30 Minuten

    precipitation_intensity_next_30m: float | None = None
    # mm/h – stärkste erwartete Niederschlagsintensität der nächsten 30 Minuten

    precipitation_next_1h: bool | None = None
    # Regenwahrscheinlichkeit für die nächsten 60 Minuten (binär).

    precipitation_amount_next_1h: float | None = None
    # mm – aufsummierte Niederschlagsmenge der nächsten 60 Minuten

    precipitation_intensity_next_1h: float | None = None
    # mm/h – stärkste erwartete Niederschlagsintensität der nächsten 60 Minuten

    precipitation_next_2h: bool | None = None
    # Regenwahrscheinlichkeit für die nächsten 120 Minuten (binär).

    precipitation_amount_next_2h: float | None = None
    # mm – aufsummierte Niederschlagsmenge der nächsten 120 Minuten

    precipitation_intensity_next_2h: float | None = None
    # mm/h – stärkste erwartete Niederschlagsintensität der nächsten 120 Minuten

    # Temperatur
    temperature: float | None = None                   # °C – aktuelle Temperatur
    feels_like: float | None = None                    # °C – gefühlte Temperatur

    # UV / Sonne
    uv_index: float | None = None                      # UV‑Index (0–16+), aus Globalstrahlung approximiert
    sun_elevation: float | None = None                 # Grad – Sonnenhöhe über dem Horizont
    sunrise: datetime | None = None                    # UTC – Sonnenaufgang heute
    sunset: datetime | None = None                     # UTC – Sonnenuntergang heute

    # Messstationen
    stations: list[WeatherStation] = Field(default_factory=list)