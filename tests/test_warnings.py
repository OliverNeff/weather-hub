"""Tests for the DWD CAP warnings adapter."""

import io
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.adapter import warnings
from app.adapter.warnings import (
    _parse_cap_bytes,
    _parse_iso,
    _parse_polygon,
    point_in_polygon,
)
from app.models.weather_data import WeatherData

# ---------------------------------------------------------------------------
# Test fixtures — synthetic CAP alert
# ---------------------------------------------------------------------------

# A 1x1 degree square: lon 8..9, lat 49..50 (Groß-Umstadt area).
_SQUARE = "49.0,8.0 50.0,8.0 50.0,9.0 49.0,9.0 49.0,8.0"

_CAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>test-id</identifier>
  <sender>opendata@dwd.de</sender>
  <sent>2026-08-16T14:18:00+02:00</sent>
  <status>{status}</status>
  <msgType>Update</msgType>
  <source>PVW</source>
  <scope>Public</scope>
  <info>
    <language>de-DE</language>
    <event>STARKES GEWITTER</event>
    <severity>Moderate</severity>
    <onset>2026-08-16T16:19:00+02:00</onset>
    <expires>{expires}</expires>
    <description>Es treten Gewitter auf.</description>
    <instruction>Schutz suchen.</instruction>
    <area>
      <areaDesc>Kreis Darmstadt-Dieburg und Stadt Darmstadt</areaDesc>
      <polygon>{square}</polygon>
    </area>
  </info>
</alert>
"""


def _cap_xml(expires: datetime, status: str = "Actual") -> str:
    return _CAP_TEMPLATE.format(square=_SQUARE, expires=expires.isoformat(), status=status)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _cap_zip(*xml_blobs: str) -> bytes:
    files = {f"alert-{i}.xml": blob.encode("utf-8") for i, blob in enumerate(xml_blobs)}
    return _zip_bytes(files)


def _fake_response(content: bytes) -> MagicMock:
    """Minimal stand-in for httpx.Response (the adapter reads .raise_for_status/.content)."""
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# _parse_polygon
# ---------------------------------------------------------------------------


class TestParsePolygon:
    def test_simple(self):
        pts = _parse_polygon("49.0,8.0 50.0,9.0")
        assert pts == [(8.0, 49.0), (9.0, 50.0)]

    def test_empty(self):
        assert _parse_polygon("") == []
        assert _parse_polygon(None) == []

    def test_bad_pair_skipped(self):
        pts = _parse_polygon("49.0,8.0 garbage 50.0,9.0")
        assert pts == [(8.0, 49.0), (9.0, 50.0)]


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


class TestParseIso:
    def test_with_offset(self):
        dt = _parse_iso("2026-08-16T17:15:00+02:00")
        assert dt == datetime(2026, 8, 16, 15, 15, tzinfo=timezone.utc)

    def test_naive_becomes_utc(self):
        assert _parse_iso("2026-08-16T17:15:00") == datetime(
            2026, 8, 16, 17, 15, tzinfo=timezone.utc
        )

    def test_invalid(self):
        assert _parse_iso("nope") is None
        assert _parse_iso("") is None
        assert _parse_iso(None) is None


# ---------------------------------------------------------------------------
# point_in_polygon
# ---------------------------------------------------------------------------


class TestPointInPolygon:
    @classmethod
    def setup_class(cls) -> None:
        cls.square = _parse_polygon(_SQUARE)

    def test_inside(self):
        assert point_in_polygon(49.5, 8.5, self.square)

    def test_outside(self):
        assert not point_in_polygon(50.5, 8.5, self.square)
        assert not point_in_polygon(49.5, 9.5, self.square)


# ---------------------------------------------------------------------------
# _parse_cap_bytes
# ---------------------------------------------------------------------------


class TestParseCapBytes:
    def test_single_alert_single_area(self):
        records = _parse_cap_bytes(_cap_zip(_cap_xml(datetime(2026, 8, 16, 15, 15))))
        assert len(records) == 1
        r = records[0]
        assert r["event"] == "STARKES GEWITTER"
        assert r["severity"] == "Moderate"
        assert r["area"] == "Kreis Darmstadt-Dieburg und Stadt Darmstadt"
        assert r["description"] == "Es treten Gewitter auf."
        assert r["instruction"] == "Schutz suchen."
        assert r["expires"] == datetime(2026, 8, 16, 15, 15, tzinfo=timezone.utc)
        assert len(r["polygons"]) == 1

    def test_alert_with_two_areas(self):
        two_areas = _cap_xml(datetime(2026, 8, 16, 15, 15)).replace(
            "<area>\n      <areaDesc>Kreis Darmstadt-Dieburg und Stadt Darmstadt</areaDesc>\n"
            f"      <polygon>{_SQUARE}</polygon>\n    </area>",
            "<area>\n      <areaDesc>Kreis Darmstadt-Dieburg</areaDesc>\n"
            f"      <polygon>{_SQUARE}</polygon>\n    </area>\n    <area>\n"
            "      <areaDesc>Stadt Darmstadt</areaDesc>\n"
            f"      <polygon>{_SQUARE}</polygon>\n    </area>",
        )
        records = _parse_cap_bytes(_cap_zip(two_areas))
        assert len(records) == 2
        assert {r["area"] for r in records} == {
            "Kreis Darmstadt-Dieburg",
            "Stadt Darmstadt",
        }

    def test_non_actual_status_skipped(self):
        cancelled = _cap_xml(datetime(2026, 8, 16, 15, 15), status="Cancelled")
        assert _parse_cap_bytes(_cap_zip(cancelled)) == []

    def test_malformed_xml_skipped(self):
        assert _parse_cap_bytes(_cap_zip("<broken")) == []

    def test_area_without_polygon_skipped(self):
        no_poly = _cap_xml(datetime(2026, 8, 16, 15, 15)).replace(
            f"<polygon>{_SQUARE}</polygon>", ""
        )
        assert _parse_cap_bytes(_cap_zip(no_poly)) == []

    def test_missing_description_is_none(self):
        no_desc = _cap_xml(datetime(2026, 8, 16, 15, 15)).replace(
            "    <description>Es treten Gewitter auf.</description>\n", ""
        )
        records = _parse_cap_bytes(_cap_zip(no_desc))
        assert records[0]["description"] is None

    def test_non_xml_entries_ignored(self):
        files = {
            "alert-0.xml": _cap_xml(datetime(2026, 8, 16, 15, 15)).encode("utf-8"),
            "readme.txt": b"not xml",
        }
        assert len(_parse_cap_bytes(_zip_bytes(files))) == 1


# ---------------------------------------------------------------------------
# _fetch_warnings — point-in-polygon + expiry filter + cache
# ---------------------------------------------------------------------------

# Reference "now" — dynamic so the synthetic expiry timestamps
# (now ± X) stay valid regardless of when the tests run.
_NOW = datetime.now(timezone.utc)


class TestFetchWarnings:
    def test_point_inside_square(self):
        with (
            patch.object(warnings, "_cache", None),
            patch.object(warnings, "_http_client") as client,
        ):
            client.get.return_value = _fake_response(_cap_zip(_cap_xml(_NOW + timedelta(hours=2))))
            alerts = warnings._fetch_warnings(49.5, 8.5)
        assert [a.event for a in alerts] == ["STARKES GEWITTER"]
        assert alerts[0].severity == "Moderate"
        assert alerts[0].area == "Kreis Darmstadt-Dieburg und Stadt Darmstadt"
        assert alerts[0].description is not None

    def test_point_outside_square(self):
        with (
            patch.object(warnings, "_cache", None),
            patch.object(warnings, "_http_client") as client,
        ):
            client.get.return_value = _fake_response(_cap_zip(_cap_xml(_NOW + timedelta(hours=2))))
            alerts = warnings._fetch_warnings(51.0, 10.0)
        assert alerts == []

    def test_expired_alert_filtered(self):
        with (
            patch.object(warnings, "_cache", None),
            patch.object(warnings, "_http_client") as client,
        ):
            client.get.return_value = _fake_response(_cap_zip(_cap_xml(_NOW - timedelta(hours=1))))
            alerts = warnings._fetch_warnings(49.5, 8.5)
        assert alerts == []

    def test_download_failure_returns_empty(self):
        with (
            patch.object(warnings, "_cache", None),
            patch.object(warnings, "_http_client") as client,
        ):
            client.get.side_effect = Exception("no network")
            assert warnings._fetch_warnings(49.5, 8.5) == []

    def test_cache_hit_avoids_second_download(self):
        with (
            patch.object(warnings, "_cache", None),
            patch.object(warnings, "_http_client") as client,
        ):
            client.get.return_value = _fake_response(_cap_zip(_cap_xml(_NOW + timedelta(hours=2))))
            a1 = warnings._fetch_warnings(49.5, 8.5)
            a2 = warnings._fetch_warnings(49.5, 8.5)
        assert [x.event for x in a1] == [x.event for x in a2] == ["STARKES GEWITTER"]
        assert client.get.call_count == 1

    def test_cache_expired_triggers_redownload(self):
        with (
            patch.object(warnings, "_cache", (datetime.now(timezone.utc) - timedelta(hours=1), [])),
            patch.object(warnings, "_http_client") as client,
        ):
            client.get.return_value = _fake_response(_cap_zip(_cap_xml(_NOW + timedelta(hours=2))))
            alerts = warnings._fetch_warnings(49.5, 8.5)
        assert len(alerts) == 1
        assert client.get.call_count == 1


# ---------------------------------------------------------------------------
# fetch_warnings — async entry point
# ---------------------------------------------------------------------------


class TestFetchWarningsAsync:
    async def test_returns_weather_data_with_alerts(self):
        with (
            patch.object(warnings, "_cache", None),
            patch.object(warnings, "_http_client") as client,
        ):
            client.get.return_value = _fake_response(_cap_zip(_cap_xml(_NOW + timedelta(hours=2))))
            wd = await warnings.fetch_warnings(49.5, 8.5)
        assert isinstance(wd, WeatherData)
        assert [a.event for a in wd.alerts] == ["STARKES GEWITTER"]

    async def test_no_alerts_for_outside_point(self):
        with (
            patch.object(warnings, "_cache", None),
            patch.object(warnings, "_http_client") as client,
        ):
            client.get.return_value = _fake_response(_cap_zip(_cap_xml(_NOW + timedelta(hours=2))))
            wd = await warnings.fetch_warnings(51.0, 10.0)
        assert wd.alerts == []
