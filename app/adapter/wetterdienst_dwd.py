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
# Format: resolution/dataset/parameter
# ---------------------------------------------------------------------------
_MOSMIX_TEMPERATURE = "hourly/small/temperature_air_mean_2m"
_MOSMIX_WIND_SPEED = "hourly/small/wind_speed"
_MOSMIX_WIND_GUST = "hourly/small/wind_gust_max_last_1h"
# Niederschlagsmenge signifikanten Wetter (mm) pro Stunde
_MOSMIX_PRECIPITATION = "hourly/small/precipitation_height_significant_weather_last_1h"
# Globalstrahlung in J/m^2 -- fuer UV-Index-Approximation
_MOSMIX_RADIATION = "hourly/small/radiation_global"

# ---------------------------------------------------------------------------
# DWD-Observation-Parameter (Met stationsweise Beobachtung)
# Format: resolution/dataset/parameter
# ---------------------------------------------------------------------------
_OBS_TEMPERATURE = "10_minutes/temperature_air/temperature_air_mean_2m"
_OBS_WIND_SPEED = "10_minutes/wind/wind_speed"
_OBS_WIND_GUST = "10_minutes/wind/wind_gust_max"
_OBS_PRECIPITATION = "10_minutes/precipitation/precipitation_height"


async def fetch_wetterdienst_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt aktuelle Wetterdaten von DWD Observation und Vorhersage von
    MOSMIX Small. Die naechstgelegene Station wird ueber Haversine ermittelt.

    Niederschlagsmenge (amount) ist in mm, Intensitaet (intensity) in mm/h.
    MosMix liefert bereits mm/h -- kein weiterer Umrechnungsschritt noetig.
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
        None wenn keine Daten verfuegbar sind.
        """
        val = fc[key]
        if val is None:
            return None
        return val > 0

    weather_data = WeatherData(
        time=obs.get("time") or datetime.now(timezone.utc),
        # Wind (m/s -- DWD liefert bereits m/s)
        wind_speed=obs["wind_speed"],
        wind_gust=obs["wind_gust"],
        # Regen aktuell (mm -- DWD Beobachtung: letzte 10 min als Gesamtmenge)
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
        # Temperatur (C -- DWD Observation liefert C)
        temperature=obs["temperature"],
        feels_like=None,  # DWD liefert keine gefuehlte Temperatur
        # UV-Index (approximiert aus Globalstrahlung in J/m^2)
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
    Holt die neuesten Messwerte der naechstgelegenen DWD-Station.

    Verwendet Aufloesung ``10_minutes`` mit Periode ``recent``, weil diese
    Kombination die benoetigten Parameter (Temperatur, Wind, Regen) enthaelt.

    DWD Observation liefert Daten im Long-Format:
    Jede Zeile hat {parameter, date, value} — nicht als separate Spalten pro Parameter.
    """
    param_temperature = "temperature_air_mean_2m"
    param_wind_speed = "wind_speed"
    param_wind_gust = "wind_gust_max"
    param_precipitation = "precipitation_height"

    request = DwdObservationRequest(
        parameters=[
            _OBS_TEMPERATURE,
            _OBS_WIND_SPEED,
            _OBS_WIND_GUST,
            _OBS_PRECIPITATION,
        ],
        periods="recent",
    )

    stations_result = request.filter_by_rank(
        latlon=(lat, lon), rank=5
    )

    station_df = stations_result.df
    if len(station_df) == 0:
        return _empty_observation()

    nearest = _find_nearest_station(station_df, lat, lon)
    station_id = nearest["station_id"]

    # Daten abrufen — long format: {parameter, date, value}
    values_request = request.filter_by_station_id(station_id=station_id)
    values_dicts = values_request.values.all().df.to_dicts()

    if not values_dicts:
        return _empty_observation()

    # Long-Format: durch parameter-Name filtern, jeweils den neuesten Wert nehmen
    latest_time = None
    result = _empty_observation()

    for param, key in [
        (param_temperature, "temperature"),
        (param_wind_speed, "wind_speed"),
        (param_wind_gust, "wind_gust"),
        (param_precipitation, "precipitation"),
    ]:
        rows = [r for r in values_dicts if r["parameter"] == param]
        if rows:
            rows.sort(key=lambda r: str(r["date"]), reverse=True)
            val = _to_float_value(rows[0])
            if val is not None:
                result[key] = val
            if latest_time is None or str(rows[0]["date"]) > str(latest_time):
                latest_time = rows[0]["date"]

    result["station_name"] = nearest.get("name")
    result["lat"] = nearest.get("latitude")
    result["lon"] = nearest.get("longitude")
    result["time"] = latest_time

    return result


# ---------------------------------------------------------------------------
# Vorhersage (DWD MOSMIX Small)
# ---------------------------------------------------------------------------


def _fetch_forecast(lat: float, lon: float) -> dict[str, Any]:
    """
    Holt die naechsten 2 Stunden aus DWD MOSMIX Small.

    MosMix liefert stuendliche Prognosen; wir mappen sie auf 30 / 60 / 120 min.
    Jeder MosMix-Zeitpunkt hat einen eigenen Niederschlagswert in mm/h.
    Der Mittelwert der Werte im Fenster ist die durchschnittliche Intensitaet.

    DWD MosMix liefert Daten im Long-Format:
    Jede Zeile hat {parameter, date, value} — nicht als separate Spalten pro Parameter.
    """
    param_precipitation = "precipitation_height_significant_weather_last_1h"
    param_radiation = "radiation_global"

    request = DwdMosmixRequest(
        parameters=[
            _MOSMIX_TEMPERATURE,
            _MOSMIX_WIND_SPEED,
            _MOSMIX_PRECIPITATION,
            _MOSMIX_RADIATION,
            _MOSMIX_WIND_GUST,  # fuer evtl. spaetere Nutzung
        ],
    )

    stations_result = request.filter_by_rank(
        latlon=(lat, lon), rank=5
    )

    station_df = stations_result.df
    if len(station_df) == 0:
        return _empty_forecast()

    nearest = _find_nearest_station(station_df, lat, lon)
    station_id = nearest["station_id"]

    # MosMix Small — Daten abrufen, long format: {parameter, date, value}
    values_request = request.filter_by_station_id(station_id=station_id)
    values_dicts = values_request.values.all().df.to_dicts()

    if not values_dicts:
        return _empty_forecast()

    # Long-Format: nach Parameter filtern
    precip_rows = [r for r in values_dicts if r["parameter"] == param_precipitation]
    radiation_rows = [r for r in values_dicts if r["parameter"] == param_radiation]

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

    # Prognose-Fenster (naechste 2 Stunden)
    windows: dict[str, tuple[datetime, datetime]] = {
        "30m": (now, now + timedelta(minutes=30)),
        "1h":  (now, now + timedelta(hours=1)),
        "2h":  (now, now + timedelta(hours=2)),
    }

    for label, (t_start, t_end) in windows.items():
        precip_values: list[float] = []

        for r in precip_rows:
            dt = _parse_datetime(r["date"])
            if dt is None or dt < t_start or dt >= t_end:
                continue
            val = _to_float_value(r)
            if val is not None:
                precip_values.append(val)

        # Mittelwert der Regenwerte im Fenster (mm/h -- MosMix liefert bereits mm/h)
        if precip_values:
            mean_intensity = sum(precip_values) / len(precip_values)
            forecast[f"precip_{label}"] = round(mean_intensity, 2)
            forecast[f"intensity_{label}"] = round(mean_intensity, 2)

    # UV-Index aus Globalstrahlung (J/m^2): approx. * 0.019 -> Index 0-16+
    radiation_values: list[float] = []
    for r in radiation_rows:
        val = _to_float_value(r)
        if val is not None:
            radiation_values.append(val)

    if radiation_values and forecast["uv_index"] is None:
        mean_rad_jm2 = sum(radiation_values) / len(radiation_values)
        uv_approx = round(mean_rad_jm2 * 0.019, 1)
        forecast["uv_index"] = min(max(uv_approx, 0), 16)

    return forecast


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _find_nearest_station(df, lat: float, lon: float) -> dict:
    """Finde die naechste Station via Haversine."""
    rows = df.to_dicts()
    nearest = min(
        rows,
        key=lambda s: haversine(
            (lat, lon), (s["latitude"], s["longitude"]), Unit.KILOMETERS
        ),
    )
    return nearest


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
    """Key aus Dictionary lesen und nach float konvertieren (NaN -> None)."""
    val = record.get(key)
    if val is None:
        return None
    f = float(val)
    return None if (f != f) else f  # NaN check


def _to_float_value(record: dict[str, Any]) -> float | None:
    """Value-Spalte aus Long-Format-Dict lesen und nach float konvertieren."""
    val = record.get("value")
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