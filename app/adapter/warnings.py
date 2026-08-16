"""DWD weather warnings adapter — CAP (Common Alerting Protocol) alerts.

Data source: DWD OPeNDATA publishes the current state of all active German
weather warnings as CAP 1.2 XML files in a zip (same data as the NINA/EEW
apps). The LATEST symlink always points to the newest state file — no API
key required, plain HTTPS.

Warnings are regional (per DWD warning district), so the requested lat/lon
is matched against each warning's polygons via point-in-polygon.
"""

import asyncio
import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.models.weather_data import Alert, WeatherData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source URL (LATEST state of all active warnings, DE, district granularity)
# ---------------------------------------------------------------------------
_CAP_URL = (
    "https://opendata.dwd.de/weather/alerts/cap/DISTRICT_EVENT_STAT/"
    "Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMEVENT_DISTRICT_DE.zip"
)

_CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

# ---------------------------------------------------------------------------
# In-process cache — warnings change rarely; a 10-minute TTL matches the
# MosMix cache / MQTT timer interval and keeps request latency near zero.
# ---------------------------------------------------------------------------
_cache: tuple[datetime, list[dict[str, Any]]] | None = None
_CACHE_TTL = timedelta(minutes=10)

_http_client = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=True)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_polygon(text: str | None) -> list[tuple[float, float]]:
    """Parse a CAP <polygon> ("lat,lon lat,lon ...") into (lon, lat) pairs."""
    if not text:
        return []
    points: list[tuple[float, float]] = []
    for pair in text.split():
        try:
            lat_s, lon_s = pair.split(",")
            points.append((float(lon_s), float(lat_s)))
        except ValueError:
            continue
    return points


def _parse_iso(s: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp (e.g. 2026-08-16T19:00:00+02:00) to UTC."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _text(parent: ET.Element, tag: str) -> str | None:
    """Get stripped text of a namespaced child element, or None if empty."""
    val = parent.findtext(f"cap:{tag}", "", _CAP_NS)
    if val is None:
        return None
    val = val.strip()
    return val or None


def _parse_cap_bytes(blob: bytes) -> list[dict[str, Any]]:
    """Parse all CAP alerts from a zip into per-area records.

    One alert can contain multiple <area> elements (e.g. Kreis and Stadt as
    separate polygons), so the result has one record per area.
    """
    records: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in zf.namelist():
            if not name.endswith(".xml"):
                continue
            try:
                root = ET.fromstring(zf.read(name))
            except ET.ParseError:
                logger.debug("warnings: skipping unparseable CAP file %s", name)
                continue

            status = (root.findtext("cap:status", "", _CAP_NS) or "").strip().lower()
            if status and status != "actual":
                continue

            info = root.find("cap:info", _CAP_NS)
            if info is None:
                continue

            event = _text(info, "event")
            if not event:
                continue

            for area in info.findall("cap:area", _CAP_NS):
                polygons = [
                    p
                    for poly in area.findall("cap:polygon", _CAP_NS)
                    if (p := _parse_polygon(poly.text))
                ]
                if not polygons:
                    continue
                records.append(
                    {
                        "event": event,
                        "severity": _text(info, "severity") or "Unknown",
                        "description": _text(info, "description"),
                        "instruction": _text(info, "instruction"),
                        "area": _text(area, "areaDesc") or "Unbekanntes Gebiet",
                        "onset": _parse_iso(info.findtext("cap:onset", None, _CAP_NS)),
                        "expires": _parse_iso(info.findtext("cap:expires", None, _CAP_NS)),
                        "polygons": polygons,
                    }
                )
    return records


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def point_in_polygon(lat: float, lon: float, polygon: list[tuple[float, float]]) -> bool:
    """Even-odd ray casting. *polygon* is a list of (lon, lat) pairs."""
    inside = False
    j = len(polygon) - 1
    for i, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[j]
        if ((y1 > lat) != (y2 > lat)) and (lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Fetch + cache
# ---------------------------------------------------------------------------


def _get_cached_areas(now: datetime) -> list[dict[str, Any]]:
    """Return active warning areas, downloading from DWD when the cache is stale."""
    global _cache
    if _cache is not None and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    resp = _http_client.get(_CAP_URL)
    resp.raise_for_status()
    areas = _parse_cap_bytes(resp.content)
    _cache = (now, areas)
    logger.info("warnings: downloaded %d active warning area(s) from DWD", len(areas))
    return areas


def _fetch_warnings(latitude: float, longitude: float) -> list[Alert]:
    """Return all active DWD warnings whose polygons contain (latitude, longitude)."""
    now = datetime.now(timezone.utc)
    try:
        areas = _get_cached_areas(now)
    except Exception:
        logger.error("warnings: failed to fetch DWD CAP alerts", exc_info=True)
        return []

    alerts: list[Alert] = []
    for rec in areas:
        if not any(point_in_polygon(latitude, longitude, p) for p in rec["polygons"]):
            continue
        expires = rec["expires"]
        if expires is not None and expires < now - timedelta(minutes=5):
            continue
        alerts.append(
            Alert(
                event=rec["event"],
                severity=rec["severity"],
                description=rec["description"],
                instruction=rec["instruction"],
                area=rec["area"],
                onset=rec["onset"],
                expires=expires,
            )
        )
    if alerts:
        logger.info(
            "warnings: %d active warning(s) for lat=%.2f lon=%.2f: %s",
            len(alerts),
            latitude,
            longitude,
            ", ".join(f"{a.event} ({a.severity})" for a in alerts),
        )
    return alerts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def fetch_warnings(latitude: float, longitude: float) -> WeatherData:
    """Fetch active DWD weather warnings for the given coordinates.

    Returns a WeatherData with only the ``alerts`` field set. A download
    failure yields an empty list instead of an error, so alerts never
    break the weather response.
    """
    alerts = await asyncio.to_thread(_fetch_warnings, latitude, longitude)
    data = WeatherData()
    data.alerts = alerts
    return data
