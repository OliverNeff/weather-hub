"""DWD weather adapter — observation via plain httpx, forecast via wetterdienst."""

import asyncio
import io
import logging
import math
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from re import Pattern
from typing import Any, cast

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
_DATASETS: dict[str, dict[str, Any]] = {
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

# Distance limit for station search (km) (configurable via DWD_MAX_DISTANCE env var)
_MAX_DISTANCE = int(os.environ.get("DWD_MAX_DISTANCE", "50"))

# Number of nearest stations to use (configurable via DWD_STATIONS env var)
_NUM_STATIONS = int(os.environ.get("DWD_STATIONS", "3"))

# ---------------------------------------------------------------------------
# Wetterdienst settings (for MosMix forecast only)
# ---------------------------------------------------------------------------
_SETTINGS = Settings(
    _env_file=None,
    cache_disable=os.environ.get("DWD_CACHE", "true").lower() != "true",
)


# ---------------------------------------------------------------------------
# In-process cache for MosMix forecasts (TTL 10min)
# ---------------------------------------------------------------------------
_mosmix_cache: dict[str, tuple[datetime, pl.DataFrame]] = {}
_MOSMIX_CACHE_TTL = timedelta(minutes=10)

# ---------------------------------------------------------------------------
# In-process cache for DWD observation ZIP data (TTL 8min)
# DWD updates every 10 minutes; caching avoids re-downloading ~700KB ZIPs
# when we only need ~30 lines of CSV.
# ---------------------------------------------------------------------------
_zip_cache: dict[str, tuple[datetime, bytes]] = {}
_ZIP_CACHE_TTL = timedelta(minutes=8)

# ---------------------------------------------------------------------------
# Lightweight in-memory cache for station lists (TTL 5min)
# Avoids re-fetching directory listings / TSVs on every request.
# ---------------------------------------------------------------------------
_station_cache: dict[str, Any] = {}
_STATION_CACHE_TTL = timedelta(minutes=5)
_station_cache_time: dict[str, datetime] = {}

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_http_client = httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)


def _http_get_bytes(url: str) -> bytes:
    """Fetch binary data from URL."""
    resp = _http_client.get(url)
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
        r"^(\d{5})\s+"  # station id (5 digits)
        r"\d{8}\s+"  # von_datum (skip)
        r"\d{8}\s+"  # bis_datum (skip)
        r"\d+\s+"  # Stationshoehe (skip)
        r"([\d.]+)\s+"  # geoBreite
        r"([\d.]+)\s+"  # geoLaenge
        r"(\S+[\S\s]*?)"  # Stationsname (rest of meaningful content)
        r"\s{2,}",  # Bundesland separator (2+ spaces before state name)
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


def _parse_directory_listing(html: str, pattern: Pattern[str]) -> list[str]:
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
            return cast(list[dict[str, Any]], _station_cache[param_key])

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
        station_ids = _parse_directory_listing(html, cast(Pattern[str], ds["zip_pattern"]))

        # Build a lookup from precipitation TSV (most stations)
        cache_key = "precipitation_lookup"
        cached = _station_cache.get(cache_key)
        cached_time = _station_cache_time.get(cache_key)
        if cached is None or (cached_time and now - cached_time >= _STATION_CACHE_TTL):
            try:
                precip_text = _http_get_text(
                    f"{DWD_BASE}/precipitation/recent/zehn_min_rr_Beschreibung_Stationen.txt"
                )
                precip_stations = _parse_station_tsv(precip_text)
            except Exception:
                precip_stations = []

            _station_cache[cache_key] = {s["id"]: s for s in precip_stations}
            _station_cache_time[cache_key] = now

        lookup = _station_cache.get(cache_key, {})
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
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
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


def _parse_csv_tail(raw_bytes: bytes, column_name: str) -> tuple[float | None, datetime | None]:
    """Parse only the last ~30 lines of a DWD CSV to find the latest value.

    DWD files contain ~79K rows (a full year of 10-min data).  We only need
    the last measurement, so we rsplit the raw bytes instead of decoding +
    iterating the full file.  This is ~5-7× faster.
    """
    parts = raw_bytes.rsplit(b"\n", 31)
    if not parts:
        return None, None
    header = [h.strip().decode("latin-1") for h in parts[0].split(b";")]
    if "MESS_DATUM" not in header or column_name not in header:
        return None, None
    date_idx = header.index("MESS_DATUM")
    col_idx = header.index(column_name)
    for line_bytes in reversed(parts[1:]):
        if not line_bytes:
            continue
        fields = line_bytes.split(b";")
        if len(fields) <= max(date_idx, col_idx):
            continue
        ts = fields[date_idx].strip().decode("latin-1")
        if len(ts) != 12:
            continue
        try:
            dt = datetime.strptime(ts, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        val_str = fields[col_idx].strip().decode("latin-1")
        if val_str in ("-999", ""):
            continue
        try:
            val = float(val_str)
            if math.isnan(val):
                continue
            return val, dt
        except ValueError:
            continue
    return None, None


def _fetch_station_value(
    station_id: str,
    zip_prefix: str,
    csv_column: str,
    dataset_dir: str,
) -> tuple[float | None, datetime | None]:
    """Get decompressed CSV from ZIP (cached), extract last value."""
    raw_csv = _get_csv_from_zip(station_id, zip_prefix, dataset_dir)

    return _parse_csv_tail(raw_csv, csv_column)


def _get_csv_from_zip(
    station_id: str,
    zip_prefix: str,
    dataset_dir: str,
) -> bytes:
    """Get decompressed CSV from a DWD observation ZIP, using in-memory cache.

    DWD updates data every 10 minutes.  A full ZIP is ~500–800 KB while we
    only need ~30 lines of CSV.  Caching the raw ZIP for 8 minutes avoids
    re-downloading on subsequent requests.
    """
    cache_key = f"{station_id}:{zip_prefix}"
    now = datetime.now(timezone.utc)

    # Purge stale entries
    _purge_zip_cache()

    cached = _zip_cache.get(cache_key)
    if cached and now - cached[0] < _ZIP_CACHE_TTL:
        zip_raw = cached[1]
        logger.debug("dwd: ZIP cache hit %s", cache_key)
    else:
        url = f"{DWD_BASE}/{dataset_dir}/recent/10minutenwerte_{zip_prefix}_{station_id}_akt.zip"
        zip_raw = _http_get_bytes(url)
        _zip_cache[cache_key] = (now, zip_raw)
        logger.debug("dwd: ZIP cache miss %s (%d bytes)", cache_key, len(zip_raw))

    with zipfile.ZipFile(io.BytesIO(zip_raw)) as zf:
        return zf.read(zf.namelist()[0])


def _purge_zip_cache() -> None:
    """Remove ZIP cache entries older than 15 minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    stale = [k for k, (ts, _) in _zip_cache.items() if ts < cutoff]
    if stale:
        for k in stale:
            del _zip_cache[k]
        logger.debug("dwd: purged %d stale ZIP entries", len(stale))


# ---------------------------------------------------------------------------
# Observation fetch
# ---------------------------------------------------------------------------


def _get_all_stations() -> list[dict[str, Any]]:
    """Get a deduplicated list of all stations across all datasets.

    Each station is annotated with ``coverage`` — the number of datasets
    (temperature, wind, gust, precip) that it provides.  Stations with
    more coverage are preferred.
    """
    seen: dict[str, dict[str, Any]] = {}
    for pk in _DATASETS:
        for s in _get_stations_for_param(pk):
            if s["id"] not in seen:
                seen[s["id"]] = dict(s, coverage=1)
            else:
                seen[s["id"]]["coverage"] = seen[s["id"]]["coverage"] + 1
    return list(seen.values())


def _fetch_param_from_stations(
    param_key: str,
    stations: list[dict[str, Any]],
) -> list[tuple[str, float | None, datetime | None, dict[str, Any] | None]]:
    """
    Fetch a single parameter from the given stations.
    Returns a list of (param_key, value, timestamp, station_info) tuples.
    """
    ds = _DATASETS[param_key]
    results: list[tuple[str, float | None, datetime | None, dict[str, Any] | None]] = []
    try:
        # Only try stations that have data for this parameter
        available = _get_stations_for_param(param_key)
        available_ids = {s["id"] for s in available}

        stations_to_try = [s for s in stations if s["id"] in available_ids]
        if not stations_to_try:
            return results

        def _try_one(
            st: dict[str, Any],
        ) -> tuple[str, float | None, datetime | None, dict[str, Any] | None]:
            try:
                val, dt = _fetch_station_value(
                    st["id"],
                    ds["zip_prefix"],
                    ds["csv_column"],
                    ds["dir"],
                )
                return (
                    param_key,
                    val,
                    dt,
                    {
                        "id": st["id"],
                        "station_name": st["name"],
                        "lat": st["lat"],
                        "lon": st["lon"],
                        "distance": st["distance"],
                    },
                )
            except Exception:
                return (param_key, None, None, None)

        with ThreadPoolExecutor(max_workers=len(stations_to_try)) as pool:
            futs = [pool.submit(_try_one, st) for st in stations_to_try]
            results = [f.result() for f in futs]

        for pk, val, dt, si in results:
            if val is not None and si is not None:
                logger.info(
                    "dwd: %s = %s from station %s (%s, %.1fkm) at %s",
                    pk,
                    val,
                    si["id"],
                    si["station_name"],
                    si["distance"],
                    dt,
                )
    except httpx.HTTPError as e:
        logger.warning("dwd: HTTP error fetching %s: %s", param_key, e)
    except Exception as e:
        logger.error("dwd: failed to fetch %s: %s", param_key, e, exc_info=True)
    return results


def _fetch_observation(lat: float, lon: float) -> dict[str, Any]:
    """
    Fetch the latest measurement for each parameter.

    For each parameter the _NUM_STATIONS nearest stations that report
    it are fetched — precipitation from the nearest rain gauges, wind
    from the nearest anemometers, etc. This way a station 5 km away
    with only precipitation is still used for rain, while a station
    14 km away with all parameters covers temperature and wind.
    """
    logger.info("dwd: fetching observation for lat=%.2f, lon=%.2f", lat, lon)

    param_keys = ["temperature", "wind_speed", "wind_gust", "precipitation"]
    try:
        all_stations = _get_all_stations()
        candidates = _find_nearest_stations(all_stations, lat, lon, count=len(all_stations))
    except Exception as e:
        logger.error("dwd: failed to get station list: %s", e, exc_info=True)
        return _empty_observation()

    if not candidates:
        logger.warning("dwd: no station found within %s km", _MAX_DISTANCE)
        return _empty_observation()

    # Build per-param pools: nearest stations that have this parameter.
    param_id_lookup: dict[str, set[str]] = {}
    for pk in param_keys:
        param_id_lookup[pk] = {s["id"] for s in _get_stations_for_param(pk)}

    # Stations sorted by distance (nearest first).
    candidates.sort(key=lambda s: s["distance"])

    all_results: list[tuple[str, float | None, datetime | None, dict[str, Any] | None]] = []
    stations_seen: dict[str, dict[str, Any]] = {}
    stale_threshold = datetime.now(timezone.utc) - timedelta(hours=2)

    with ThreadPoolExecutor(max_workers=len(param_keys)) as pool:
        futs = []
        for pk in param_keys:
            available_ids = param_id_lookup[pk]
            pk_candidates = [s for s in candidates if s["id"] in available_ids]
            if pk_candidates:
                futs.append(pool.submit(_fetch_param_from_stations, pk, pk_candidates[:_NUM_STATIONS]))

        for f in futs:
            all_results.extend(f.result())

    for pk, val, dt, station_info in all_results:
        if val is None:
            continue
        if pk == "precipitation" and dt and dt < stale_threshold:
            logger.warning(
                "dwd: discarding stale precip for %s (timestamp %s)",
                station_info.get("station_name", "?") if station_info else "?",
                dt,
            )
            continue
        sid = station_info["id"] if station_info else None
        if sid is None or station_info is None:
            continue
        # Initialize station entry on first encounter
        if sid not in stations_seen:
            stations_seen[sid] = {
                "station_name": station_info.get("station_name", sid),
                "lat": station_info.get("lat"),
                "lon": station_info.get("lon"),
                "distance": station_info.get("distance", 0),
                "time": None,
                "temperature": None,
                "wind_speed": None,
                "wind_gust": None,
                "precipitation": None,
            }
        stations_seen[sid]["time"] = dt
        stations_seen[sid][pk] = val

    # Build primary values from all results — pick freshest per parameter.
    primary = _empty_observation()
    for pk, val, dt, station_info in all_results:
        if val is None:
            continue
        if pk == "precipitation" and dt and dt < stale_threshold:
            continue
        if primary.get(pk) is None or dt is None or dt > primary.get("time", dt):
            primary[pk] = val
        if dt is not None:
            existing_time = primary.get("time")
            if existing_time is None or dt > existing_time:
                primary["time"] = dt

    # Keep only stations that reported at least one value, trim to nearest _NUM_STATIONS.
    with_data = [s for s in stations_seen.values() if s["time"] is not None]
    trimmed = sorted(with_data, key=lambda s: s.get("distance", 0))[:_NUM_STATIONS]

    # Log which stations contributed data
    if trimmed:
        logger.info(
            "dwd: used %d station(s) for observation: %s",
            len(trimmed),
            ", ".join(
                f"{v['station_name']}({v.get('distance', 0):.1f}km)"
                for v in trimmed
            ),
        )

    all_stations = trimmed

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
        result = _process_forecast_df(cached[1], now)
        result["_cache_miss"] = False
        return result

    request = DwdMosmixRequest(
        parameters=[
            _MOSMIX_PRECIPITATION,
            _MOSMIX_RADIATION,
        ],
        settings=_SETTINGS,
    )

    station_df = None
    try:
        station_df = request.filter_by_distance(latlon=(lat, lon), distance=_MAX_DISTANCE).df
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

    result = _process_forecast_df(vals_df, now)
    result["_cache_miss"] = True
    return result


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
        "precipitation_stops_at": None,
    }

    # Find nearest future forecast entry as anchor. MosMix data is hourly,
    # so the 30m window may be too narrow to catch any entries on its own.
    future = precip.filter(precip["date"] >= now_rounded)

    anchor: datetime | None = None
    gap_minutes: float = 999
    if len(future) > 0:
        anchor = cast(datetime, future["date"].min())
        gap_minutes = (anchor - now_rounded).total_seconds() / 60

    for label, delta in [
        ("30m", timedelta(minutes=30)),
        ("1h", timedelta(hours=1)),
        ("2h", timedelta(hours=2)),
    ]:
        # For 30m: include the nearest future entry if it's within the next hour
        # (MosMix is hourly; a 30m window alone would miss all entries).
        if label == "30m" and gap_minutes <= 60 and anchor is not None:
            window = future.filter(future["date"] == anchor)
        else:
            window = precip.filter(
                (precip["date"] >= now_rounded) & (precip["date"] < now_rounded + delta)
            )
        if len(window) > 0:
            mean = window["value"].mean()
            if mean is not None and isinstance(mean, (int, float)) and not math.isnan(float(mean)):
                val = round(mean, 2)
                forecast[f"precip_{label}"] = val
                forecast[f"intensity_{label}"] = val

    # UV-Index aus Globalstrahlung (J/m^2)
    if len(rad) > 0:
        mean_rad = rad["value"].mean()
        if (
            mean_rad is not None
            and isinstance(mean_rad, (int, float))
            and not math.isnan(float(mean_rad))
        ):
            uv_approx = round(mean_rad * 0.019, 1)
            forecast["uv_index"] = min(max(uv_approx, 0), 16)

    # Calculate when precipitation stops
    forecast["precipitation_stops_at"] = _precipitation_stops_at(future, now_rounded)

    return forecast


def _precipitation_stops_at(
    precip: pl.DataFrame, now: datetime
) -> datetime | None:
    """Find when current rain stops from MosMix hourly forecast.

    Scans forward from now and finds the first gap (rain → no rain).
    Returns the datetime of the last hourly entry with precipitation > 0
    before that gap, or None if no rain data.
    """
    if len(precip) == 0:
        return None

    rows = precip.sort("date").iter_rows(named=True)
    last_rain_dt: datetime | None = None
    is_currently_raining = False

    for row in rows:
        dt = row["date"]
        if dt < now:
            continue
        val = row["value"]
        is_rain = val is not None and not math.isnan(float(val)) and val > 0

        if is_rain:
            last_rain_dt = dt
            if not is_currently_raining:
                is_currently_raining = True
        elif is_currently_raining:
            return last_rain_dt

    return last_rain_dt


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
        val = fc.get(key)
        if val is None:
            return None
        return float(val) > 0

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
        precipitation_stops_at=fc["precipitation_stops_at"],
        temperature=primary["temperature"],
        feels_like=None,
        uv_index=fc["uv_index"],
        sun_elevation=None,
        sunrise=None,
        sunset=None,
    )

    for station_data in all_stations:
        weather_data.stations.append(
            WeatherStation(
                source="dwd",
                name=station_data["station_name"],
                lat=station_data["lat"],
                lon=station_data["lon"],
                time=station_data.get("time"),
            )
        )

    # Publish via MQTT if MosMix cache was cold (longest TTL among DWD caches).
    was_cache_miss = fc.get("_cache_miss", False)
    if was_cache_miss:
        try:
            from app.mqtt import publish_weather
            await publish_weather(weather_data)
        except Exception:
            logger.warning("mqtt: failed to publish weather data", exc_info=True)

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
        "precipitation_stops_at": None,
        "_cache_miss": False,
    }
