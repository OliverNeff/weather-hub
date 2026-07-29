import math
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

from wetterdienst.provider.dwd.observation import DwdObservationRequest
from wetterdienst.provider.dwd.mosmix import DwdMosmixRequest
from wetterdienst.settings import Settings

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation

# Disable wetterdienst's fsspec cache — stale listings cause
# "does not have a list of files" errors when the DWD server adds
# new station zips between cache refreshes.
_SETTINGS = Settings(cache_disable=True)

# ---------------------------------------------------------------------------
# MosMix-Parameter (DWD-MOSMIX-Messmodelldaten)
# Format: resolution/dataset/parameter
# ---------------------------------------------------------------------------
_MOSMIX_PRECIPITATION = "hourly/small/precipitation_height_significant_weather_last_1h"
_MOSMIX_RADIATION = "hourly/small/radiation_global"

# ---------------------------------------------------------------------------
# DWD-Observation-Parameter (Met stationsweise Beobachtung)
# Format: resolution/dataset/parameter
# ---------------------------------------------------------------------------
_OBS_TEMPERATURE = "10_minutes/temperature_air/temperature_air_mean_2m"
_OBS_WIND_SPEED = "10_minutes/wind/wind_speed"
_OBS_WIND_GUST = "10_minutes/wind_extreme/wind_gust_max"
_OBS_PRECIPITATION = "10_minutes/precipitation/precipitation_height"


async def fetch_wetterdienst_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt aktuelle Wetterdaten von DWD Observation und Vorhersage von
    MOSMIX Small. Jeder Parameter sucht die naechstgelegene Station
    separat — verschiedene DWD-Datasets decken unterschiedliche Stationen ab.
    """
    obs = _fetch_observation(latitude, longitude)
    fc = _fetch_forecast(latitude, longitude)

    # _fetch_observation returns {"_primary": best_station, "_all": [all stations]}
    primary = obs.get("_primary") or _empty_observation()
    all_stations = obs.get("_all", [])

    sun_elevation = None  # Provided by openmeteo adapter

    def _precip_bool(key: str) -> bool | None:
        val = fc[key]
        if val is None:
            return None
        return val > 0

    weather_data = WeatherData(
        wind_speed=primary["wind_speed"],
        wind_gust=primary["wind_gust"],
        precipitation_rate=primary["precipitation"],
        precipitation_next_30m=_precip_bool("precip_30m"),
        precipitation_amount_next_30m=fc["precip_30m"],
        precipitation_intensity_next_30m=fc["intensity_30m"],
        precipitation_next_1h=_precip_bool("precip_1h"),
        precipitation_amount_next_1h=fc["precip_1h"],
        precipitation_intensity_next_1h=fc["intensity_1h"],
        precipitation_next_2h=_precip_bool("precip_2h"),
        precipitation_amount_next_2h=fc["precip_2h"],
        precipitation_intensity_next_2h=fc["intensity_2h"],
        temperature=primary["temperature"],
        feels_like=None,
        uv_index=fc["uv_index"],
        sun_elevation=sun_elevation,
        sunrise=None,
        sunset=None,
    )

    # Add all DWD stations that contributed data, not just the best one
    for station_data in all_stations:
        weather_data.stations.append(WeatherStation(
            source="dwd",
            name=station_data["station_name"],
            lat=station_data["lat"],
            lon=station_data["lon"],
            time=station_data.get("time"),
        ))

    return weather_data


# ---------------------------------------------------------------------------
# Beobachtung (DWD Met stationsweise)
# ---------------------------------------------------------------------------

def _fetch_observation(lat: float, lon: float) -> dict[str, Any]:
    """
    Holt die neuesten Messwerte von den 3 nächsten Stationen.
    Jeder Parameter wird separat abgefragt, weil verschiedene DWD-Datasets
    unterschiedliche Stationen abdecken. Am Ende wird die Station mit dem
    frischesten Messzeitpunkt bevorzugt.
    """
    _PARAMS = [
        # (obs_spec, result_key)
        (_OBS_TEMPERATURE, "temperature"),
        (_OBS_WIND_SPEED, "wind_speed"),
        (_OBS_WIND_GUST, "wind_gust"),
        (_OBS_PRECIPITATION, "precipitation"),
    ]

    # Per-station cache: station_id → {info, values_by_key}
    station_cache: dict[str, dict] = {}

    for obs_param, key in _PARAMS:
        req = DwdObservationRequest(
            parameters=[obs_param],
            periods="recent",
            settings=_SETTINGS,
        )
        try:
            station_df = req.filter_by_distance(latlon=(lat, lon), distance=50.0).df
        except Exception:
            continue

        if len(station_df) == 0:
            continue

        # Look at up to 3 nearest stations
        for row_idx in range(min(3, len(station_df))):
            first = station_df.row(row_idx, named=True)
            station_id = first["station_id"]

            # Lazily load station values
            if station_id not in station_cache:
                station_cache[station_id] = {
                    "info": {
                        "name": first["name"],
                        "lat": first["latitude"],
                        "lon": first["longitude"],
                    },
                    "values": {},
                    "latest_time": None,
                }
                try:
                    values_df = req.filter_by_station_id(station_id=station_id).values.all().df
                    values_dicts = values_df.to_dicts()
                    if values_dicts:
                        values_dicts.sort(key=lambda r: str(r["date"]), reverse=True)
                        station_cache[station_id]["latest_time"] = values_dicts[0]["date"]
                except Exception:
                    pass

            values_dicts_cached = station_cache[station_id]["values"].get(key)
            if values_dicts_cached is None:
                try:
                    values_df = req.filter_by_station_id(station_id=station_id).values.all().df
                    values_dicts = values_df.to_dicts()
                    values_dicts.sort(key=lambda r: str(r["date"]), reverse=True)
                    station_cache[station_id]["values"][key] = values_dicts
                    if station_cache[station_id]["latest_time"] is None and values_dicts:
                        station_cache[station_id]["latest_time"] = values_dicts[0]["date"]
                except Exception:
                    pass

            values_dicts = station_cache[station_id]["values"].get(key) or []
            if not values_dicts:
                continue

            val = _to_float_value(values_dicts[0])
            if val is not None:
                station_cache[station_id]["values"][key] = values_dicts

    # Find the station with the freshest data
    if not station_cache:
        return _empty_observation()

    # Build a list of all stations with their data
    all_stations = []
    for sid, sc in station_cache.items():
        station = _empty_observation()
        station["station_name"] = sc["info"]["name"]
        station["lat"] = sc["info"]["lat"]
        station["lon"] = sc["info"]["lon"]
        station["time"] = sc["latest_time"]
        for obs_param, key in _PARAMS:
            values_dicts = sc["values"].get(key)
            if values_dicts:
                val = _to_float_value(values_dicts[0])
                if val is not None:
                    station[key] = val
        all_stations.append(station)

    # Primary values come from the station with the freshest data
    best_station = max(
        all_stations,
        key=lambda s: s.get("time") or "",
    )

    return {"_primary": best_station, "_all": all_stations}


# ---------------------------------------------------------------------------
# Vorhersage (DWD MOSMIX Small)
# ---------------------------------------------------------------------------

def _fetch_forecast(lat: float, lon: float) -> dict[str, Any]:
    """
    Holt die naechsten 2 Stunden aus DWD MOSMIX Small.
    """
    param_precipitation = "precipitation_height_significant_weather_last_1h"
    param_radiation = "radiation_global"

    request = DwdMosmixRequest(
        parameters=[
            _MOSMIX_PRECIPITATION,
            _MOSMIX_RADIATION,
        ],
        settings=_SETTINGS,
    )

    try:
        station_df = request.filter_by_distance(latlon=(lat, lon), distance=50.0).df
    except Exception:
        return _empty_forecast()

    if len(station_df) == 0:
        return _empty_forecast()

    first = station_df.row(0, named=True)
    station_id = first["station_id"]

    try:
        values_dicts = request.filter_by_station_id(station_id=station_id).values.all().df.to_dicts()
    except Exception:
        return _empty_forecast()

    if not values_dicts:
        return _empty_forecast()

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

    windows = {
        "30m": (now, now + timedelta(minutes=30)),
        "1h": (now, now + timedelta(hours=1)),
        "2h": (now, now + timedelta(hours=2)),
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

        if precip_values:
            mean_intensity = sum(precip_values) / len(precip_values)
            forecast[f"precip_{label}"] = round(mean_intensity, 2)
            forecast[f"intensity_{label}"] = round(mean_intensity, 2)

    # UV-Index aus Globalstrahlung (J/m^2)
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


def _to_float_value(record: dict[str, Any]) -> float | None:
    """Value-Spalte aus Long-Format-Dict lesen und nach float konvertieren."""
    val = record.get("value")
    if val is None:
        return None
    f = float(val)
    return None if math.isnan(f) else f


def _parse_datetime(dt_val: Any) -> datetime | None:
    """Verschiedene Datumsformate parsen."""
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        return dt_val.replace(tzinfo=timezone.utc) if dt_val.tzinfo is None else dt_val.astimezone(timezone.utc)
    try:
        parsed = pd.Timestamp(dt_val).to_pydatetime()
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None