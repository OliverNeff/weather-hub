import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Any

import polars as pl

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
# ---------------------------------------------------------------------------
_MOSMIX_PRECIPITATION = "hourly/small/precipitation_height_significant_weather_last_1h"
_MOSMIX_RADIATION = "hourly/small/radiation_global"

# ---------------------------------------------------------------------------
# DWD-Observation-Parameter (Met stationsweise Beobachtung)
# Format: resolution/dataset (wetterdienst v2)
# ---------------------------------------------------------------------------
_OBS_DATASETS = [
    ("temperature_air", "temperature", "temperature_air_mean_2m"),
    ("wind", "wind_speed", "wind_speed"),
    ("wind_extreme", "wind_gust", "wind_gust_max"),
    ("precipitation", "precipitation", "precipitation_height"),
]

# ---------------------------------------------------------------------------
# In-process cache for MosMix forecasts (TTL 10min)
# Forecasts update every 1-3 hours; fresh requests take ~7s to fetch.
# ---------------------------------------------------------------------------
_mosmix_cache: dict[str, tuple[datetime, pl.DataFrame]] = {}
_MOSMIX_CACHE_TTL = timedelta(minutes=10)


async def fetch_wetterdienst_weather(
    latitude: float, longitude: float
) -> WeatherData:
    """
    Holt aktuelle Wetterdaten von DWD Observation und Vorhersage von
    MOSMIX Small. Observation und Forecast laufen parallel.
    """
    obs, fc = await asyncio.gather(
        asyncio.to_thread(_fetch_observation, latitude, longitude),
        asyncio.to_thread(_fetch_forecast, latitude, longitude),
    )

    primary = obs.get("_primary") or _empty_observation()
    all_stations = obs.get("_all", [])

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
        sun_elevation=None,
        sunrise=None,
        sunset=None,
    )

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
    Holt die neuesten Messwerte von 3 nächsten Stationen.
    Alle 4 Datasets werden in einem combined Request abgefragt.
    Die Werte der Stationen werden parallel abgerufen.
    """
    start_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        req = DwdObservationRequest(
            parameters=[f"10_minutes/{ds}" for ds, _, _ in _OBS_DATASETS],
            periods="recent",
            start_date=start_date,
            end_date=end_date,
            settings=_SETTINGS,
        )
        station_df = req.filter_by_distance(latlon=(lat, lon), distance=50.0).df
    except Exception:
        return _empty_observation()

    if len(station_df) == 0:
        return _empty_observation()

    # Pick 3 unique stations: prefer those with more datasets, then nearest
    grouped = station_df.group_by("station_id").agg(
        pl.col("distance").min().alias("min_distance"),
        pl.col("dataset").n_unique().alias("num_datasets"),
    )
    top = grouped.sort(["num_datasets", "min_distance"], descending=[True, False]).head(3)
    selected_ids = [row["station_id"] for row in top.iter_rows(named=True)]

    # Build station info map (from the combined station list)
    station_info: dict[str, dict] = {}
    for row in station_df.sort("distance").iter_rows(named=True):
        sid = row["station_id"]
        if sid not in station_info:
            station_info[sid] = {
                "name": row["name"],
                "lat": row["latitude"],
                "lon": row["longitude"],
            }

    def _fetch_station_values(sid: str) -> tuple[str, dict]:
        """Fetch all values for a station, keyed by parameter name."""
        try:
            vals = req.filter_by_station_id(station_id=sid).values.all().df
            if len(vals) == 0:
                return (sid, {})
            return (sid, {row["parameter"]: row for row in vals.sort("date", descending=True).iter_rows(named=True)})
        except Exception:
            return (sid, {})

    results = []
    with ThreadPoolExecutor(max_workers=len(selected_ids)) as pool:
        futs = [pool.submit(_fetch_station_values, sid) for sid in selected_ids]
        results = [f.result() for f in futs]
    station_values = dict(results)

    # Extract values per station
    station_cache: dict[str, dict] = {}
    for sid in selected_ids:
        vals_dict = station_values.get(sid, {})
        if not vals_dict:
            continue

        info = station_info.get(sid, {})
        cache = {
            "info": {"name": info.get("name"), "lat": info.get("lat"), "lon": info.get("lon")},
            "values": {},
            "latest_time": None,
        }

        for _, result_key, param_name in _OBS_DATASETS:
            if param_name in vals_dict:
                row = vals_dict[param_name]
                val = _to_float_value(row)
                if val is not None:
                    cache["values"][result_key] = val
                if cache["latest_time"] is None:
                    cache["latest_time"] = row.get("date")

        if cache["info"].get("name"):
            station_cache[sid] = cache

    if not station_cache:
        return _empty_observation()

    # Build station list with extracted values
    all_stations = []
    for sid, sc in station_cache.items():
        station = _empty_observation()
        station["station_name"] = sc["info"]["name"]
        station["lat"] = sc["info"]["lat"]
        station["lon"] = sc["info"]["lon"]
        station["time"] = sc["latest_time"]
        for key in ["temperature", "wind_speed", "wind_gust", "precipitation"]:
            if key in sc["values"]:
                station[key] = sc["values"][key]
        all_stations.append(station)

    best_station = max(all_stations, key=lambda s: s.get("time") or "")

    return {"_primary": best_station, "_all": all_stations}


# ---------------------------------------------------------------------------
# Vorhersage (DWD MOSMIX Small)
# ---------------------------------------------------------------------------

def _fetch_forecast(lat: float, lon: float) -> dict[str, Any]:
    """
    Holt die naechsten 2 Stunden aus DWD MOSMIX Small.
    Uses polars-native filtering and in-process cache (TTL 10min).
    """
    cache_key = f"{lat:.2f},{lon:.2f}"
    now = datetime.now(timezone.utc)

    # Check cache
    cached = _mosmix_cache.get(cache_key)
    if cached and now - cached[0] < _MOSMIX_CACHE_TTL:
        return _process_forecast_df(cached[1], now)

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

    station_id = station_df.row(0, named=True)["station_id"]

    try:
        vals_df = request.filter_by_station_id(station_id=station_id).values.all().df
    except Exception:
        return _empty_forecast()

    if len(vals_df) == 0:
        return _empty_forecast()

    # Cache the raw forecast data
    _mosmix_cache[cache_key] = (now, vals_df)

    return _process_forecast_df(vals_df, now)


def _process_forecast_df(vals_df: pl.DataFrame, now: datetime) -> dict[str, Any]:
    """Extract precip windows and UV index from MosMix forecast DataFrame."""
    param_precipitation = "precipitation_height_significant_weather_last_1h"
    param_radiation = "radiation_global"

    precip = vals_df.filter(vals_df["parameter"] == param_precipitation)
    rad = vals_df.filter(vals_df["parameter"] == param_radiation)

    now_rounded = now.replace(second=0, microsecond=0)
    forecast: dict[str, Any] = {
        "precip_30m": None,
        "intensity_30m": None,
        "precip_1h": None,
        "intensity_1h": None,
        "precip_2h": None,
        "intensity_2h": None,
        "uv_index": None,
    }

    for label, delta in [("30m", timedelta(minutes=30)), ("1h", timedelta(hours=1)), ("2h", timedelta(hours=2))]:
        window = precip.filter(
            (precip["date"] >= now_rounded) & (precip["date"] < now_rounded + delta)
        )
        if len(window) > 0:
            mean = window["value"].mean()
            if mean is not None and not math.isnan(mean):
                val = round(mean, 2)
                forecast[f"precip_{label}"] = val
                forecast[f"intensity_{label}"] = val

    # UV-Index aus Globalstrahlung (J/m^2)
    if len(rad) > 0:
        mean_rad = rad["value"].mean()
        if mean_rad is not None and not math.isnan(mean_rad):
            uv_approx = round(mean_rad * 0.019, 1)
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