from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
from haversine import haversine, Unit

from wetterdienst.provider.dwd.observation import DwdObservationRequest
from wetterdienst.provider.dwd.mosmix import DwdMosmixRequest

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation

# ---------------------------------------------------------------------------
# MosMix-Parameter (DWD-MOSMIX-Messmodelldaten)
# ---------------------------------------------------------------------------
_MOSMIX_TEMPERATURE = "temperature_air_mean_2m"
_MOSMIX_WIND_SPEED = "wind_speed"
_MOSMIX_WIND_GUST = "wind_speed_max"
# Niederschlagsmenge signifikanten Wetter (mm) pro Stunde
_MOSMIX_PRECIPITATION = "precipitation_height_significant_weather_last_1h"
# Globalstrahlung in J/m² — für UV-Index-Approximation
_MOSMIX_RADIATION = "radiation_global"

# ---------------------------------------------------------------------------
# DWD-Observation-Parameter (Met stationsweiser Beobachtung)
# ---------------------------------------------------------------------------
_OBS_TEMPERATURE = "temperature_air_mean_2m"
_OBS_WIND_SPEED = "wind_speed"
_OBS_WIND_GUST = "wind_gust_max"
_OBS_PRECIPITATION = "precipitation_height"


async def fetch_wetterdienst_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt aktuelle Wetterdaten von DWD Observation und Vorhersage von
    MOSMIX Small. Die nächstgelegene Station wird über Haversine ermittelt.

    Niederschlagsmenge (amount) ist in mm, Intensität (intensity) in mm/h.
    MosMix liefert bereits mm/h — kein weiterer Umrechnungsschritt nötig.
    """
    obs = _fetch_observation(latitude, longitude)
    fc = _fetch_forecast(latitude, longitude)

    weather_station = WeatherStation(
        source="dwd",
        name=obs["station_name"],
        lat=obs["lat"],
        lon=obs["lon"],
    )

    def _precip_bool(key: str) -> bool | None:
        """
        True wenn Niederschlag erwartet wird, False wenn nicht.
        None wenn keine Daten verfügbar sind.
        """
        val = fc[key]
        if val is None:
            return None
        return val > 0

    weather_data = WeatherData(
        time=obs.get("time") or datetime.now(timezone.utc),
        # Wind (m/s — DWD liefert bereits m/s)
        wind_speed=obs["wind_speed"],
        wind_gust=obs["wind_gust"],
        # Regen aktuell (mm – DWD Beobachtung: letzte 10 min als Gesamtmenge)
        precipitation_rate=obs["precipitation"],
        # Regenvorhersage (MosMix, mm / mm/h)
        precipitation_next_30m=_precip_bool("precip_30m"),
        precipitation_amount_next_30m=fc["precip_30m"],
        precipitation_intensity_next_30m=fc["intensity_30m"],
        precipitation_next_1h=_precip_bool("precip_1h"),
        precipitation_amount_next_1h=fc["precip_1h"],
        precipitation_intensity_next_1h=fc["intensity_1h"],
        precipitation_next_2h=_precip_bool("precip_2h"),
        precipitation_amount_next_2h=fc["precip_2h"],
        precipitation_intensity_next_2h=fc["intensity_2h"],
        # Temperatur (°C — DWD Observation liefert °C)
        temperature=obs["temperature"],
        feels_like=None,  # DWD liefert keine gefühlte Temperatur
        # UV-Index (approximiert aus Globalstrahlung in J/m²)
        uv_index=fc["uv_index"],
        sun_elevation=None,
    )

    weather_data.stations.append(weather_station)
    return weather_data


# ---------------------------------------------------------------------------
# Beobachtung (DWD Met stationsweise)
# ---------------------------------------------------------------------------


def _fetch_observation(lat: float, lon: float) -> dict[str, Any]:
    """
    Holt die neuesten Messwerte der nächstgelegenen DWD-Station.

    Verwendet Auflösung ``10_minutes`` mit Periode ``recent``, weil diese
    Kombination die benötigten Parameter (Temperatur, Wind, Regen) enthält.
    """
    request = DwdObservationRequest(
        parameters=[
            _OBS_TEMPERATURE,
            _OBS_WIND_SPEED,
            _OBS_WIND_GUST,
            _OBS_PRECIPITATION,
        ],
        periods="recent",  # type: ignore[arg-type]
    )

    # Nächste Station ermitteln
    stations = request.filter_by_distance(
        lat=lat, lon=lon, rank=5
    ).stations.df.to_dicts()

    if not stations:
        return _empty_observation()

    nearest = min(
        stations,
        key=lambda s: haversine((lat, lon), (s["latitude"], s["longitude"]), Unit.KILOMETERS),
    )
    station_id = nearest["station_id"]

    # Daten abrufen
    values = (
        request.filter_by_station_id(station_id=station_id)
        .values.all()
        .df.to_dicts()
    )

    if not values:
        return _empty_observation()

    # Neuesten Zeitstempel nehmen (sortiert absteigend)
    latest = values[-1]

    return {
        "station_name": nearest["name"],
        "lat": nearest["latitude"],
        "lon": nearest["longitude"],
        "time": latest.get("date"),  # datetime UTC
        "temperature": _to_float(latest, "temperature_air_mean_2m"),
        "wind_speed": _to_float(latest, "wind_speed"),
        "wind_gust": _to_float(latest, "wind_gust_max"),
        "precipitation": _to_float(latest, "precipitation_height"),
    }


# ---------------------------------------------------------------------------
# Vorhersage (DWD MOSMIX Small)
# ---------------------------------------------------------------------------


def _fetch_forecast(lat: float, lon: float) -> dict[str, Any]:
    """
    Holt die nächsten 2 Stunden aus DWD MOSMIX Small.

    MosMix liefert stündliche Prognosen; wir mappen sie auf 30 / 60 / 120 min.
    Jeder MosMix-Zeitpunkt hat einen eigenen Niederschlagswert in mm/h.
    Der Mittelwert der Werte im Fenster ist die durchschnittliche Intensität.
    """
    request = DwdMosmixRequest(
        parameters=[
            _MOSMIX_TEMPERATURE,
            _MOSMIX_WIND_SPEED,
            _MOSMIX_PRECIPITATION,
            _MOSMIX_RADIATION,
            _MOSMIX_WIND_GUST,  # für evtl. spätere Nutzung
        ],
    )

    # Nächste Station ermitteln (SINGLE_STATIONS = MosMix Small)
    stations = request.filter_by_distance(
        lat=lat, lon=lon, rank=5
    ).stations.df.to_dicts()

    if not stations:
        return _empty_forecast()

    nearest = min(
        stations,
        key=lambda s: haversine((lat, lon), (s["latitude"], s["longitude"]), Unit.KILOMETERS),
    )
    station_id = nearest["station_id"]

    # MosMix Small — Daten abrufen
    values = (
        request.filter_by_station_id(station_id=station_id)
        .values.all()
        .df.to_dicts()
    )

    if not values:
        return _empty_forecast()

    # Nach Zeitstempel sortieren (aufsteigend)
    values.sort(key=lambda v: str(v["datetime"]))

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    forecast: dict[str, Any] = {
        "precip_30m": None,
        "intensity_30m": None,
        "precip_1h": None,
        "intensity_1h": None,
        "precip_2h": None,
        "intensity_2h": None,
        "uv_index": None,
    }

    # Prognose-Fenster (nächste 2 Stunden)
    windows: dict[str, tuple[datetime, datetime]] = {
        "30m": (now, now + timedelta(minutes=30)),
        "1h":  (now, now + timedelta(hours=1)),
        "2h":  (now, now + timedelta(hours=2)),
    }

    for label, (t_start, t_end) in windows.items():
        precip_values: list[float] = []
        radiation_values: list[float] = []

        for v in values:
            dt = _parse_datetime(v["datetime"])
            if dt is None or dt < t_start or dt >= t_end:
                continue
            precip_values.append(_to_float(v, "precipitation_height_significant_weather_last_1h"))
            radiation_values.append(_to_float(v, "radiation_global"))

        # Mittelwert der Regenwerte im Fenster (mm/h — MosMix liefert bereits mm/h)
        valid_precip = [p for p in precip_values if p is not None]
        if valid_precip:
            mean_intensity = sum(valid_precip) / len(valid_precip)
            forecast[f"precip_{label}"] = round(mean_intensity, 2)
            forecast[f"intensity_{label}"] = round(mean_intensity, 2)

    # UV-Index aus Globalstrahlung (J/m²): approx. * 0.019 → Index 0–16+
    valid_rad: list[float] = []
    for v in values:
        r = _to_float(v, "radiation_global")
        if r is not None:
            valid_rad.append(r)

    if valid_rad and forecast["uv_index"] is None:
        mean_rad_jm2 = sum(valid_rad) / len(valid_rad)
        uv_approx = round(mean_rad_jm2 * 0.019, 1)
        forecast["uv_index"] = min(max(uv_approx, 0), 16)

    return forecast


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _empty_observation() -> dict[str, Any]:
    return {
        "station_name": None,
        "lat": None,
        "lon": None,
        "time": None,
        "temperature": None,
        "wind_speed": None,
        "wind_gust": None,
        "precipitation": None,
    }


def _empty_forecast() -> dict[str, Any]:
    return {
        "precip_30m": None,
        "intensity_30m": None,
        "precip_1h": None,
        "intensity_1h": None,
        "precip_2h": None,
        "intensity_2h": None,
        "uv_index": None,
    }


def _to_float(record: dict[str, Any], key: str) -> float | None:
    """Key aus Dictionary lesen und nach float konvertieren (NaN → None)."""
    val = record.get(key)
    if val is None:
        return None
    f = float(val)
    return None if (f != f) else f  # NaN check


def _parse_datetime(dt_val: Any) -> datetime | None:
    """Verschiedene Datumsformate aus MosMix/Beobachtung parsen."""
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        return dt_val.replace(tzinfo=timezone.utc) if dt_val.tzinfo is None else dt_val.astimezone(timezone.utc)
    try:
        parsed = pd.Timestamp(dt_val).to_pydatetime()
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
