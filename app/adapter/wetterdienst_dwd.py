import asyncio
import logging
import math
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Any

import polars as pl

from wetterdienst.provider.dwd.observation import DwdObservationRequest
from wetterdienst.provider.dwd.mosmix import DwdMosmixRequest
from wetterdienst.settings import Settings

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation

logger = logging.getLogger(__name__)

# DWD cache: controlled by DWD_CACHE env var (.env).
# Defaults to disabled — stale fsspec cache causes
# "does not have a list of files" errors when the DWD server adds
# new station zips between cache refreshes.
_DWD_CACHE_ENABLED = os.environ.get("DWD_CACHE", "false").lower() == "true"

# Subclass Settings with env_file=None so pydantic-settings doesn't
# scan .env itself (which would reject our DWD_CACHE var as extra).
class _WdSettings(Settings):
    model_config = {"env_file": None}

_SETTINGS = _WdSettings(cache_disable=not _DWD_CACHE_ENABLED)


def _clear_dwd_cache():
    """Remove wetterdienst's fsspec cache directory so the next request fetches fresh."""
    cache_dir = _SETTINGS.cache_dir
    if cache_dir and os.path.isdir(cache_dir):
        try:
            shutil.rmtree(cache_dir)
            logger.info("dwd: cleared stale fsspec cache at %s", cache_dir)
        except Exception as e:
            logger.warning("dwd: failed to clear cache dir: %s", e)

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

    precip_val = primary["precipitation"]
    # DWD precipitation_height is mm per 10 min; convert to mm/h.
    precip_mmh = round(precip_val * 6, 1) if precip_val is not None else None
    weather_data = WeatherData(
        wind_speed=primary["wind_speed"],
        wind_gust=primary["wind_gust"],
        precipitation_intensity=precip_mmh,
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
    Holt die neuesten Messwerte für jeden Parameter von der jeweils
    nächstgelegenen Station die diesen Parameter bereitstellt.
    Alle 4 Parameter werden parallel abgerufen.
    """
    logger.info("dwd: fetching observation for lat=%.2f, lon=%.2f", lat, lon)
    start_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _fetch_param(ds, result_key, param_name):
        """Find nearest station with this dataset, return latest value."""
        for attempt in range(2):
            try:
                req = DwdObservationRequest(
                    parameters=[f"10_minutes/{ds}"],
                    periods="recent",
                    start_date=start_date,
                    end_date=end_date,
                    settings=_SETTINGS,
                )
                sdf = req.filter_by_distance(latlon=(lat, lon), distance=50.0).df
                if len(sdf) == 0:
                    return (result_key, None)
                row = sdf.sort("distance").row(0, named=True)
                sid = row["station_id"]
                vals = req.filter_by_station_id(station_id=sid).values.all().df
                sub = vals.filter(vals["parameter"] == param_name)
                if len(sub) == 0:
                    return (result_key, None)
                latest = sub.sort("date", descending=True).row(0, named=True)
                val = _to_float_value({"value": latest["value"]})
                return (result_key, val, latest["date"], sid, row["name"],
                        row["latitude"], row["longitude"], row["distance"])
            except Exception as e:
                if attempt == 0:
                    logger.warning("dwd: %s attempt 1 failed: %s — retrying", result_key, e)
                    _clear_dwd_cache()
                    continue
                logger.error("dwd: failed to fetch %s: %s", result_key, e)
                return (result_key, None)

    results = []
    with ThreadPoolExecutor(max_workers=len(_OBS_DATASETS)) as pool:
        futs = [pool.submit(_fetch_param, ds, rk, pm) for ds, rk, pm in _OBS_DATASETS]
        results = [f.result() for f in futs]

    # If cache is enabled and all params returned None, cache is likely stale.
    # Clear it and retry once.
    if _DWD_CACHE_ENABLED and all(r[1] is None for r in results):
        logger.warning("dwd: all 4 params returned None — clearing cache and retrying")
        _clear_dwd_cache()
        with ThreadPoolExecutor(max_workers=len(_OBS_DATASETS)) as pool:
            futs = [pool.submit(_fetch_param, ds, rk, pm) for ds, rk, pm in _OBS_DATASETS]
            results = [f.result() for f in futs]

    # DWD recent observation data can be 12+ hours old for small stations.
    # Discard precipitation data older than 2h to avoid reporting stale values.
    _stale_threshold = datetime.now(timezone.utc) - timedelta(hours=2)
    _discard_precip = False
    for r in results:
        if r[0] == "precipitation" and len(r) >= 4 and r[2] and r[2] < _stale_threshold:
            _discard_precip = True
            logger.warning("dwd: discarding stale precipitation data (timestamp %s)", r[2])
            break

    # Collect values and station info
    primary = _empty_observation()
    stations_seen: dict[str, dict] = {}
    for r in results:
        result_key = r[0]
        if len(r) < 2 or r[1] is None:
            continue
        # Skip stale precipitation data
        if _discard_precip and result_key == "precipitation":
            continue
        primary[result_key] = r[1]
        if r[2] and (primary.get("time") is None or r[2] > primary.get("time")):
            primary["time"] = r[2]
        if len(r) >= 8:
            sid = r[3]
            if sid not in stations_seen:
                stations_seen[sid] = {
                    "station_name": r[4],
                    "lat": r[5],
                    "lon": r[6],
                    "distance": r[7],
                    "time": None,
                    "temperature": None,
                    "wind_speed": None,
                    "wind_gust": None,
                    "precipitation": None,
                }
            stations_seen[sid]["time"] = r[2]
            if not (_discard_precip and result_key == "precipitation"):
                stations_seen[sid][result_key] = r[1]

    # Log which stations contributed data
    if stations_seen:
        logger.info(
            "dwd: used %d station(s) for observation: %s",
            len(stations_seen),
            ", ".join(f"{v['station_name']}({v.get('distance', 0):.1f}km)" for v in sorted(stations_seen.values(), key=lambda s: s.get('distance', 0))),
        )

    all_stations = sorted(stations_seen.values(), key=lambda s: s.get('distance', 0))
    if primary["temperature"] is None and primary["wind_speed"] is None:
        return _empty_observation()

    return {"_primary": primary, "_all": all_stations}


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

    for attempt in range(2):
        try:
            station_df = request.filter_by_distance(latlon=(lat, lon), distance=50.0).df
            break
        except Exception as e:
            if attempt == 0:
                logger.warning("dwd: forecast station lookup failed: %s — clearing cache and retrying", e)
                _clear_dwd_cache()
                continue
            logger.error("dwd: forecast station lookup failed: %s", e)
            return _empty_forecast()
    else:
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