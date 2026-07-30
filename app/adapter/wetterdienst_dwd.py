"""DWD weather adapter — observation via plain httpx, forecast via wetterdienst."""

import asyncio
import csv
import io
import logging
import math
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
import polars as pl

from wetterdienst.provider.dwd.mosmix import DwdMosmixRequest
from wetterdienst.settings import Settings

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DWD base URL
# ---------------------------------------------------------------------------
DWD_BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes"

# ---------------------------------------------------------------------------
# Dataset definitions
#   (dataset_dir, station_prefix, zip_prefix, csv_value_column)
#   station_prefix: used in station description TSV (zehn_min_{prefix}_Beschreibung_Stationen.txt)
#   zip_prefix: pattern in directory listing (10minutenwerte_{zip_prefix}_{station_id}_akt.zip)
#   csv_value_column: column name in the extracted CSV
# ---------------------------------------------------------------------------
_DATASETS = {
    # air_temperature has no station TSV — stations discovered via directory listing
    "temperature": {
        "dir": "air_temperature",
        "zip_prefix": "TU",
        "zip_pattern": re.compile(r"10minutenwerte_TU_(\d{5})_akt\.zip"),
        "csv_column": "TT_10",
        "has_station_tsv": False,
    },
    "wind_speed": {
        "dir": "wind",
        "station_prefix": "ff",
        "zip_prefix": "wind",
        "zip_pattern": re.compile(r"10minutenwerte_wind_(\d{5})_akt\.zip"),
        "csv_column": "FF_10",
        "has_station_tsv": True,
    },
    "wind_gust": {
        "dir": "extreme_wind",
        # extreme_wind has no TSV — stations discovered via directory listing
        "zip_prefix": "extrema_wind",
        "zip_pattern": re.compile(r"10minutenwerte_extrema_wind_(\d{5})_akt\.zip"),
        "csv_column": "FX_10",
        "has_station_tsv": False,
    },
    "precipitation": {
        "dir": "precipitation",
        "station_prefix": "rr",
        "zip_prefix": "nieder",
        "zip_pattern": re.compile(r"10minutenwerte_nieder_(\d{5})_akt\.zip"),
        "csv_column": "RWS_10",
        "has_station_tsv": True,
    },
}

# Distance limit for station search (km)
_MAX_DISTANCE = 50.0

# ---------------------------------------------------------------------------
# Wetterdienst settings (for MosMix forecast only)
# ---------------------------------------------------------------------------
_SETTINGS = Settings(_env_file=None, cache_disable=True)


# ---------------------------------------------------------------------------
# In-process cache for MosMix forecasts (TTL 10min)
# ---------------------------------------------------------------------------
_mosmix_cache: dict[str, tuple[datetime, pl.DataFrame]] = {}
_MOSMIX_CACHE_TTL = timedelta(minutes=10)

# ---------------------------------------------------------------------------
# Lightweight in-memory cache for station lists (TTL 5min)
# Avoids re-fetching directory listings / TSVs on every request.
# ---------------------------------------------------------------------------
_station_cache: dict[str, list[dict[str, Any]]] = {}
_STATION_CACHE_TTL = timedelta(minutes=5)
_station_cache_time: dict[str, datetime] = {}

# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _http_get_bytes(url: str) -> bytes:
    """Fetch binary data from URL."""
    resp = httpx.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _http_get_text(url: str) -> str:
    """Fetch text from URL, decode as latin-1 (DWD encoding)."""
    raw = _http_get_bytes(url)
    return raw.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Station discovery
# ---------------------------------------------------------------------------

def _parse_station_tsv(text: str) -> list[dict[str, Any]]:
    """Parse the DWD station description file.

    Format (space-separated columns with padding):
    Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname ...
    """
    stations = []
    pattern = re.compile(
        r"^(\d{5})\s+"          # station id (5 digits)
        r"\d{8}\s+"             # von_datum (skip)
        r"\d{8}\s+"             # bis_datum (skip)
        r"\d+\s+"               # Stationshoehe (skip)
        r"([\d.]+)\s+"          # geoBreite
        r"([\d.]+)\s+"          # geoLaenge
        r"(\S+[\S\s]*?)"       # Stationsname (rest of meaningful content)
        r"\s{2,}",              # Bundesland separator (2+ spaces before state name)
    )
    for line in text.strip().split("\n")[2:]:  # skip header + separator
        m = pattern.match(line)
        if m:
            sid = m.group(1)
            lat = float(m.group(2))
            lon = float(m.group(3))
            name = m.group(4).strip()
            stations.append({"id": sid, "lat": lat, "lon": lon, "name": name})
            continue

        # Fallback: simpler pattern for lines without Bundesland
        m = re.match(
            r"^(\d{5})\s+\d{8}\s+\d{8}\s+\d+\s+([\d.]+)\s+([\d.]+)\s+(.+)",
            line,
        )
        if m:
            sid = m.group(1)
            lat = float(m.group(2))
            lon = float(m.group(3))
            name = m.group(4).strip()
            stations.append({"id": sid, "lat": lat, "lon": lon, "name": name})
    return stations


def _parse_directory_listing(html: str, pattern: re.Pattern) -> list[str]:
    """Extract station IDs from DWD directory listing HTML."""
    return list(set(pattern.findall(html)))


def _get_stations_for_param(param_key: str) -> list[dict[str, Any]]:
    """Get station list for a parameter, with in-memory caching.

    For datasets with a station TSV: parse it.
    For datasets without: extract IDs from directory listing, then
    look up metadata from the precipitation TSV (largest, most complete).
    """
    now = datetime.now(timezone.utc)
    if param_key in _station_cache:
        age = now - _station_cache_time.get(param_key, now)
        if age < _STATION_CACHE_TTL:
            return _station_cache[param_key]

    ds = _DATASETS[param_key]

    if ds["has_station_tsv"]:
        # Parse the station description TSV
        url = (
            f"{DWD_BASE}/{ds['dir']}/recent/"
            f"zehn_min_{ds['station_prefix']}_Beschreibung_Stationen.txt"
        )
        text = _http_get_text(url)
        stations = _parse_station_tsv(text)
    else:
        # No TSV — get IDs from directory listing, look up metadata from
        # the precipitation TSV (best coverage).
        html = _http_get_text(f"{DWD_BASE}/{ds['dir']}/recent/")
        station_ids = _parse_directory_listing(html, ds["zip_pattern"])

        # Build a lookup from precipitation TSV (most stations)
        if not hasattr(_get_stations_for_param, "_precip_lookup"):
            try:
                precip_text = _http_get_text(
                    f"{DWD_BASE}/precipitation/recent/zehn_min_rr_Beschreibung_Stationen.txt"
                )
                precip_stations = _parse_station_tsv(precip_text)
                _get_stations_for_param._precip_lookup = {
                    s["id"]: s for s in precip_stations
                }
            except Exception:
                _get_stations_for_param._precip_lookup = {}

        lookup = _get_stations_for_param._precip_lookup
        stations = []
        for sid in station_ids:
            if sid in lookup:
                stations.append(lookup[sid].copy())
            else:
                # Fallback: we have the ID but no coords — skip (unlikely)
                logger.debug("dwd: no metadata for station %s", sid)

    _station_cache[param_key] = stations
    _station_cache_time[param_key] = now
    return stations


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute haversine distance in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _find_nearest_stations(
    stations: list[dict[str, Any]],
    lat: float,
    lon: float,
    count: int = 3,
) -> list[dict[str, Any]]:
    """Find the *count* nearest stations within _MAX_DISTANCE km."""
    with_dist = []
    for s in stations:
        d = _haversine_km(lat, lon, s["lat"], s["lon"])
        if d < _MAX_DISTANCE:
            s_copy = dict(s)
            s_copy["distance"] = d
            with_dist.append(s_copy)
    with_dist.sort(key=lambda x: x["distance"])
    return with_dist[:count]


# ---------------------------------------------------------------------------
# Station data fetcher
# ---------------------------------------------------------------------------

def _parse_csv_for_column(raw_csv: str, column_name: str) -> tuple[float | None, datetime | None]:
    """Parse DWD CSV for a specific column, return (latest_value, timestamp)."""
    reader = csv.reader(raw_csv.splitlines(), delimiter=";")
    header = next(reader, None)
    if not header:
        return None, None

    header = [h.strip() for h in header]
    date_idx = header.index("MESS_DATUM") if "MESS_DATUM" in header else None
    col_idx = header.index(column_name) if column_name in header else None

    if date_idx is None or col_idx is None:
        return None, None

    latest_val = None
    latest_dt = None

    for row in reader:
        if len(row) <= max(date_idx, col_idx):
            continue

        ts_str = row[date_idx].strip()
        if len(ts_str) != 12:
            continue
        try:
            dt = datetime.strptime(ts_str, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        raw_val = row[col_idx].strip()
        if raw_val == "-999" or raw_val == "":
            continue
        try:
            val = float(raw_val)
            if math.isnan(val):
                continue
            if latest_dt is None or dt > latest_dt:
                latest_val = val
                latest_dt = dt
        except ValueError:
            continue

    return latest_val, latest_dt


def _fetch_station_value(
    station_id: str,
    zip_prefix: str,
    csv_column: str,
    dataset_dir: str,
) -> tuple[float | None, datetime | None]:
    """Download ZIP for a station, extract CSV, return (value, timestamp)."""
    url = f"{DWD_BASE}/{dataset_dir}/recent/10minutenwerte_{zip_prefix}_{station_id}_akt.zip"
    zip_data = _http_get_bytes(url)

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        with zf.open(zf.namelist()[0]) as f:
            raw_csv = f.read().decode("latin-1", errors="replace")

    return _parse_csv_for_column(raw_csv, csv_column)


# ---------------------------------------------------------------------------
# Observation fetch
# ---------------------------------------------------------------------------

def _fetch_observation(lat: float, lon: float) -> dict[str, Any]:
    """
    Fetch the latest measurement for each parameter from the nearest
    station that provides it. All 4 parameters are fetched in parallel.
    """
    logger.info("dwd: fetching observation for lat=%.2f, lon=%.2f", lat, lon)

    param_keys = ["temperature", "wind_speed", "wind_gust", "precipitation"]

    def _fetch_one(param_key: str) -> list[tuple[str, float | None, datetime | None, dict | None]]:
        """
        Fetch a single parameter from up to 3 nearest stations.
        Returns a list of (param_key, value, timestamp, station_info) tuples,
        sorted by station distance (nearest first).
        """
        ds = _DATASETS[param_key]
        results: list[tuple[str, float | None, datetime | None, dict | None]] = []
        try:
            stations = _get_stations_for_param(param_key)
            targets = _find_nearest_stations(stations, lat, lon, count=3)
            if not targets:
                logger.warning("dwd: no station found for %s within %s km", param_key, _MAX_DISTANCE)
                return results

            # Fetch all 3 stations in parallel
            def _try_station(st):
                try:
                    val, dt = _fetch_station_value(
                        st["id"],
                        ds["zip_prefix"],
                        ds["csv_column"],
                        ds["dir"],
                    )
                    return (param_key, val, dt, {
                        "id": st["id"],
                        "station_name": st["name"],
                        "lat": st["lat"],
                        "lon": st["lon"],
                        "distance": st["distance"],
                    })
                except Exception:
                    return (param_key, None, None, None)

            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                futs = [pool.submit(_try_station, st) for st in targets]
                results = [f.result() for f in futs]

            # Log the best result
            for pk, val, dt, si in results:
                if val is not None and si is not None:
                    logger.info(
                        "dwd: %s = %s from station %s (%s, %.1fkm) at %s",
                        param_key, val, si["id"], si["station_name"], si["distance"], dt,
                    )
        except httpx.HTTPError as e:
            logger.warning("dwd: HTTP error fetching %s: %s", param_key, e)
        except Exception as e:
            logger.error("dwd: failed to fetch %s: %s", param_key, e, exc_info=True)
        return results

    # Fetch all 4 parameters in parallel
    all_results = []
    with ThreadPoolExecutor(max_workers=len(param_keys)) as pool:
        futs = [pool.submit(_fetch_one, pk) for pk in param_keys]
        for f in futs:
            all_results.extend(f.result())

    # DWD recent observation data can be 12+ hours old for small stations.
    # Discard precipitation data older than 2h to avoid reporting stale values.
    stale_threshold = datetime.now(timezone.utc) - timedelta(hours=2)
    discard_precip = False
    for pk, val, dt, _si in all_results:
        if pk == "precipitation" and dt and dt < stale_threshold:
            discard_precip = True
            logger.warning("dwd: discarding stale precipitation data (timestamp %s)", dt)
            break

    # Build primary values dict — for each param pick the value from the nearest station
    primary = _empty_observation()
    stations_seen: dict[str, dict] = {}

    for pk, val, dt, station_info in all_results:
        if val is None:
            continue
        if discard_precip and pk == "precipitation":
            continue
        # Take the first non-None value (stations are ordered by distance)
        if primary.get(pk) is None:
            primary[pk] = val
        if dt and (primary.get("time") is None or dt > primary.get("time")):
            primary["time"] = dt

        if station_info:
            sid = station_info["id"]
            if sid not in stations_seen:
                stations_seen[sid] = {
                    "station_name": station_info["station_name"],
                    "lat": station_info["lat"],
                    "lon": station_info["lon"],
                    "distance": station_info["distance"],
                    "time": None,
                    "temperature": None,
                    "wind_speed": None,
                    "wind_gust": None,
                    "precipitation": None,
                }
            stations_seen[sid]["time"] = dt
            if not (discard_precip and pk == "precipitation"):
                stations_seen[sid][pk] = val

    # Log which stations contributed data
    if stations_seen:
        logger.info(
            "dwd: used %d station(s) for observation: %s",
            len(stations_seen),
            ", ".join(
                f"{v['station_name']}({v.get('distance', 0):.1f}km)"
                for v in sorted(stations_seen.values(), key=lambda s: s.get("distance", 0))
            ),
        )

    all_stations = sorted(stations_seen.values(), key=lambda s: s.get("distance", 0))

    # Return only if we have at least temperature or wind
    if primary["temperature"] is None and primary["wind_speed"] is None:
        return _empty_observation()

    return {"_primary": primary, "_all": all_stations}


# ---------------------------------------------------------------------------
# Forecast (DWD MOSMIX Small) — still uses wetterdienst
# ---------------------------------------------------------------------------

_MOSMIX_PRECIPITATION = "hourly/small/precipitation_height_significant_weather_last_1h"
_MOSMIX_RADIATION = "hourly/small/radiation_global"


def _fetch_forecast(lat: float, lon: float) -> dict[str, Any]:
    """
    Fetch the next 2 hours from DWD MOSMIX Small.
    Uses polars-native filtering and in-process cache (TTL 10min).
    """
    cache_key = f"{lat:.2f},{lon:.2f}"
    now = datetime.now(timezone.utc)

    # Check cache
    cached = _mosmix_cache.get(cache_key)
    if cached and now - cached[0] < _MOSMIX_CACHE_TTL:
        return _process_forecast_df(cached[1], now)

    request = DwdMosmixRequest(
        parameters=[
            _MOSMIX_PRECIPITATION,
            _MOSMIX_RADIATION,
        ],
        settings=_SETTINGS,
    )

    station_df = None
    try:
        station_df = request.filter_by_distance(latlon=(lat, lon), distance=50.0).df
    except Exception as e:
        logger.error("dwd: forecast station lookup failed: %s", e)
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

    for label, delta in [
        ("30m", timedelta(minutes=30)),
        ("1h", timedelta(hours=1)),
        ("2h", timedelta(hours=2)),
    ]:
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
# Public entry point
# ---------------------------------------------------------------------------

async def fetch_wetterdienst_weather(
    latitude: float,
    longitude: float,
) -> WeatherData:
    """
    Fetch current weather from DWD Observation and forecast from
    MOSMIX Small. Observation and Forecast run in parallel.
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
# Helpers
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