from pydantic import BaseModel, Field
from datetime import datetime

from app.models.weather_station import WeatherStation

class WeatherData(BaseModel):
    # Zeitpunkt der Messung (UTC)
    time: datetime

    # Wind
    wind_speed: float | None = None                    # m/s – aktuelle Windgeschwindigkeit
    wind_gust: float | None = None                     # m/s – maximale Böe

    # Regen (aktuell)
    precipitation_rate: float | None = None            # mm/h – gemessene Niederschlagsintensität der Station

    # Regen (Vorhersage aus Buienradar-Raincontent)

    precipitation_next_30m: bool | None = None
    # Regenwahrscheinlichkeit für die nächsten 30 Minuten (binär).
    # False = kein Regen erwartet (alle 5‑Minuten‑Werte sind 0)
    # True  = Regen sicher (mindestens ein Wert > 0)

    precipitation_amount_next_30m: float | None = None
    # mm – aufsummierte Niederschlagsmenge der nächsten 30 Minuten

    precipitation_intensity_next_30m: float | None = None
    # mm/h – stärkste erwartete Niederschlagsintensität der nächsten 30 Minuten
    # (max aus den ersten 6 Werten des 5‑Minuten‑Rasters)

    precipitation_next_1h: bool | None = None
    # Regenwahrscheinlichkeit für die nächsten 60 Minuten (binär).
    # False = kein Regen erwartet
    # True  = Regen sicher

    precipitation_amount_next_1h: float | None = None
    # mm – aufsummierte Niederschlagsmenge der nächsten 60 Minuten

    precipitation_intensity_next_1h: float | None = None
    # mm/h – stärkste erwartete Niederschlagsintensität der nächsten 60 Minuten

    precipitation_next_2h: bool | None = None
    # Regenwahrscheinlichkeit für die nächsten 120 Minuten (binär).
    # False = kein Regen erwartet
    # True  = Regen sicher

    precipitation_amount_next_2h: float | None = None
    # mm – aufsummierte Niederschlagsmenge der nächsten 120 Minuten

    precipitation_intensity_next_2h: float | None = None
    # mm/h – stärkste erwartete Niederschlagsintensität der nächsten 120 Minuten

    # Temperatur
    temperature: float | None = None                   # °C – aktuelle Temperatur
    feels_like: float | None = None                    # °C – gefühlte Temperatur

    # UV / Sonne
    uv_index: float | None = None                      # UV‑Index (0–11+), aus Globalstrahlung approximiert

    # Sonnenstand (aus Home Assistant)
    sun_elevation: float | None = None                 # Grad – Sonnenhöhe über dem Horizont

    # Messstationen
    stations: list[WeatherStation] = Field(default_factory=list)