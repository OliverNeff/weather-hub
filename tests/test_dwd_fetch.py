"""Mock-based tests for DWD adapter entry point and uncovered internals."""

import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch

import httpx
import polars as pl

from app.adapter.wetterdienst_dwd import (
    _empty_forecast,
    _empty_observation,
    _fetch_forecast,
    _fetch_observation,
    _fetch_param_from_stations,
    _get_all_stations,
    _get_csv_from_zip,
    _get_stations_for_param,
    _parse_csv_tail,
    _parse_station_tsv,
    _process_forecast_df,
)

FROZEN = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _parse_station_tsv — fallback path (lines without Bundesland)
# ---------------------------------------------------------------------------


class TestParseStationTsvFallback:
    def test_fallback_parses_line_without_bundesland(self):
        """Lines without the Bundesland separator use the fallback regex."""
        text = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname\n"
            "--- --- --- --- --- --- --- ---\n"
            "00001 20200101 20260801 150 50.1000 9.1000 Station Without State\n"
        )
        result = _parse_station_tsv(text)
        assert len(result) == 1
        assert result[0]["id"] == "00001"
        assert result[0]["name"] == "Station Without State"

    def test_mixed_lines_both_main_and_fallback(self):
        """Some lines match main pattern, some fall through to fallback."""
        text = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland\n"
            "--- --- --- --- --- --- --- --- ---\n"
            "00001 20200101 20260801 150 50.1000 9.1000 Station A  Berlin \n"
            "00002 20200101 20260801 200 51.2000 10.2000 Station B\n"
        )
        result = _parse_station_tsv(text)
        assert len(result) == 2
        assert result[0]["id"] == "00001"
        assert result[1]["id"] == "00002"


# ---------------------------------------------------------------------------
# _parse_csv_tail — edge cases not covered by existing tests
# ---------------------------------------------------------------------------


class TestParseCsvTailEdgeCases:
    def _make_csv(self, rows):
        header = "MESS_DATUM;TT_10;Qualitaet"
        lines = [header, *rows]
        return ("\n".join(lines)).encode("latin-1")

    def test_empty_line_skipped(self):
        """Empty lines between data rows are skipped."""
        csv_bytes = self._make_csv(
            [
                "202608011200;20.0;0",
                "",
                "202608011210;23.0;0",
            ]
        )
        val, _dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val == 23.0

    def test_datetime_parse_error_continues(self):
        """Rows with unparseable datetime values are skipped."""
        csv_bytes = self._make_csv(
            [
                "notadate;20.0;0",
                "202608011210;23.0;0",
            ]
        )
        val, _dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val == 23.0

    def test_non_numeric_value_continues(self):
        """Rows with non-numeric value strings are skipped."""
        csv_bytes = self._make_csv(
            [
                "202608011200;abc;0",
                "202608011210;23.0;0",
            ]
        )
        val, _dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val == 23.0

    def test_insufficient_fields_skipped(self):
        csv_bytes = self._make_csv(
            [
                "202608011200;0",
                "202608011210;23.0;0",
            ]
        )
        val, _dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val == 23.0

    def test_short_timestamp_skipped(self):
        csv_bytes = self._make_csv(
            [
                "20260801120;22.5;0",
                "202608011200;23.0;0",
            ]
        )
        val, _dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val == 23.0


# ---------------------------------------------------------------------------
# _get_csv_from_zip
# ---------------------------------------------------------------------------


class TestGetCsvFromZip:
    def _make_zip_bytes(self, csv_content: str = "MESS_DATUM;TT_10\n202608011200;22.5\n"):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("data.csv", csv_content)
        return buf.getvalue()

    def test_cache_miss_downloads_and_caches(self):
        """On cache miss, downloads ZIP, caches it, returns CSV."""
        from app.adapter.wetterdienst_dwd import _zip_cache

        _zip_cache.clear()
        zip_bytes = self._make_zip_bytes("MESS_DATUM;TT_10\n202608011200;22.5\n")

        with patch("app.adapter.wetterdienst_dwd._http_get_bytes", return_value=zip_bytes):
            result = _get_csv_from_zip("00001", "TU", "air_temperature")

        assert b"22.5" in result
        cache_key = "00001:TU"
        assert cache_key in _zip_cache

    def test_cache_hit_returns_cached(self):
        """On cache hit, returns cached data without downloading."""
        from app.adapter.wetterdienst_dwd import _zip_cache

        _zip_cache.clear()
        zip_bytes = self._make_zip_bytes()

        # Prime the cache
        _zip_cache["00001:TU"] = (datetime.now(timezone.utc), zip_bytes)

        with patch("app.adapter.wetterdienst_dwd._http_get_bytes") as mock_get:
            result = _get_csv_from_zip("00001", "TU", "air_temperature")

        mock_get.assert_not_called()
        assert b"22.5" in result

    def test_stale_cache_entry_triggers_redownload(self):
        """Entries older than TTL trigger a fresh download."""
        from app.adapter.wetterdienst_dwd import _zip_cache

        _zip_cache.clear()
        zip_bytes = self._make_zip_bytes("MESS_DATUM;TT_10\n202608011200;99.9\n")

        _zip_cache["00001:TU"] = (datetime.now(timezone.utc) - timedelta(minutes=20), b"stale")

        with patch("app.adapter.wetterdienst_dwd._http_get_bytes", return_value=zip_bytes):
            result = _get_csv_from_zip("00001", "TU", "air_temperature")

        assert b"99.9" in result

    def test_zip_url_construction(self):
        """Verifies the correct URL is constructed on cache miss."""
        from app.adapter.wetterdienst_dwd import _zip_cache

        _zip_cache.clear()
        zip_bytes = self._make_zip_bytes()

        with patch("app.adapter.wetterdienst_dwd._http_get_bytes", return_value=zip_bytes) as mock_get:
            _get_csv_from_zip("12345", "nieder", "precipitation")

        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        assert "precipitation/recent/10minutenwerte_nieder_12345_akt.zip" in url


# ---------------------------------------------------------------------------
# _get_stations_for_param
# ---------------------------------------------------------------------------


class TestGetStationsForParam:
    def _clear_station_caches(self):
        from app.adapter.wetterdienst_dwd import _station_cache, _station_cache_time
        _station_cache.clear()
        _station_cache_time.clear()

    def test_with_tsv_parses_station_file(self):
        """Parameters with has_station_tsv=True parse the TSV file."""
        self._clear_station_caches()
        tsv_text = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland\n"
            "--- --- --- --- --- --- --- --- ---\n"
            "00001 20200101 20260801 150 50.1000 9.1000 Station A  Berlin \n"
        )

        with patch("app.adapter.wetterdienst_dwd._http_get_text", return_value=tsv_text):
            stations = _get_stations_for_param("wind_speed")

        assert len(stations) == 1
        assert stations[0]["id"] == "00001"

    def test_without_tsv_uses_directory_listing(self):
        """Parameters without TSV use directory listing + precipitation lookup."""
        self._clear_station_caches()
        directory_html = '<a href="10minutenwerte_TU_00250_akt.zip">link</a>'
        precip_tsv = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland\n"
            "--- --- --- --- --- --- --- --- ---\n"
            "00250 20200101 20260801 150 50.1000 9.1000 Temp Station  Berlin \n"
        )

        def mock_get_text(url: str) -> str:
            if "air_temperature" in url:
                return directory_html
            if "rr_Beschreibung" in url:
                return precip_tsv
            return ""

        with patch("app.adapter.wetterdienst_dwd._http_get_text", side_effect=mock_get_text):
            stations = _get_stations_for_param("temperature")

        assert len(stations) == 1
        assert stations[0]["id"] == "00250"

    def test_caches_result(self):
        """Second call returns cached result without HTTP call."""
        self._clear_station_caches()
        tsv_text = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland\n"
            "--- --- --- --- --- --- --- --- ---\n"
            "00001 20200101 20260801 150 50.1000 9.1000 Station A  Berlin \n"
        )

        with patch("app.adapter.wetterdienst_dwd._http_get_text", return_value=tsv_text) as mock_get:
            _get_stations_for_param("wind_speed")
            stations = _get_stations_for_param("wind_speed")

        assert mock_get.call_count == 1
        assert len(stations) == 1

    def test_stale_cache_triggers_refresh(self):
        """Expired cache entries trigger a fresh fetch — and return the new data."""
        self._clear_station_caches()

        from app.adapter.wetterdienst_dwd import _station_cache, _station_cache_time

        _station_cache["wind_speed"] = []
        _station_cache_time["wind_speed"] = datetime.now(timezone.utc) - timedelta(minutes=10)

        tsv_text = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland\n"
            "--- --- --- --- --- --- --- --- ---\n"
            "00001 20200101 20260801 150 50.1000 9.1000 Station A  Berlin \n"
        )

        with patch("app.adapter.wetterdienst_dwd._http_get_text", return_value=tsv_text):
            stations = _get_stations_for_param("wind_speed")

        assert len(stations) == 1

    def test_precip_lookup_failure_doesnt_crash(self):
        """If precipitation TSV fails for non-TSV param, stations are empty but no crash."""
        self._clear_station_caches()
        directory_html = '<a href="10minutenwerte_TU_00250_akt.zip">link</a>'

        def mock_get_text(url: str) -> str:
            if "air_temperature" in url:
                return directory_html
            raise httpx.HTTPError("connection refused")

        with patch("app.adapter.wetterdienst_dwd._http_get_text", side_effect=mock_get_text):
            stations = _get_stations_for_param("temperature")

        assert stations == []


# ---------------------------------------------------------------------------
# _get_all_stations
# ---------------------------------------------------------------------------


class TestGetAllStations:
    def test_deduplicates_stations(self):
        def mock_get(pk):
            return [
                {"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A"},
            ]

        with patch("app.adapter.wetterdienst_dwd._get_stations_for_param", mock_get):
            stations = _get_all_stations()

        assert len(stations) == 1
        assert stations[0]["coverage"] == 4


# ---------------------------------------------------------------------------
# _fetch_param_from_stations
# ---------------------------------------------------------------------------


class TestFetchParamFromStations:
    def test_fetches_values_from_stations(self):
        """Normal case: fetches values from stations that have data."""
        def mock_get_stations(pk):
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A"}]

        def mock_fetch(sid, zp, cc, dd):
            return 22.5, FROZEN

        all_stations = [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 10.0}]

        with patch("app.adapter.wetterdienst_dwd._get_stations_for_param", mock_get_stations):
            with patch("app.adapter.wetterdienst_dwd._fetch_station_value", mock_fetch):
                results = _fetch_param_from_stations("temperature", all_stations)

        assert len(results) == 1
        pk, val, _dt, si = results[0]
        assert pk == "temperature"
        assert val == 22.5
        assert si["id"] == "00001"

    def test_station_failure_returns_none(self):
        """When a single station fails, it returns None for that station."""
        def mock_get_stations(pk):
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A"}]

        def mock_fetch(sid, zp, cc, dd):
            raise httpx.HTTPError("connection failed")

        all_stations = [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 10.0}]

        with patch("app.adapter.wetterdienst_dwd._get_stations_for_param", mock_get_stations):
            with patch("app.adapter.wetterdienst_dwd._fetch_station_value", mock_fetch):
                results = _fetch_param_from_stations("temperature", all_stations)

        assert len(results) == 1
        assert results[0][1] is None

    def test_no_available_stations_returns_empty(self):
        """When no stations have data for this param, returns empty list."""
        def mock_get_stations(pk):
            return []

        all_stations = [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 10.0}]

        with patch("app.adapter.wetterdienst_dwd._get_stations_for_param", mock_get_stations):
            results = _fetch_param_from_stations("temperature", all_stations)

        assert results == []


# ---------------------------------------------------------------------------
# _fetch_observation
# ---------------------------------------------------------------------------


class TestFetchObservation:
    def test_no_candidates_returns_empty(self):
        with patch(
            "app.adapter.wetterdienst_dwd._get_all_stations",
            return_value=[],
        ):
            result = _fetch_observation(50.0, 9.0)

        assert result == _empty_observation()

    def test_http_error_in_station_discovery_returns_empty(self):
        with patch(
            "app.adapter.wetterdienst_dwd._get_all_stations",
            side_effect=httpx.HTTPError("connection failed"),
        ):
            result = _fetch_observation(50.0, 9.0)

        assert result == _empty_observation()

    def test_successful_fetch_returns_primary_and_stations(self):
        """Full successful flow returns observation data and station list."""
        def mock_all_stations():
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 5.0}]

        def mock_find_nearest(stations, lat, lon, count):
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 5.0}]

        def mock_get_stations_for_param(pk):
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A"}]

        def mock_fetch_station(sid, zp, cc, dd):
            return 22.0, FROZEN

        with patch("app.adapter.wetterdienst_dwd._get_all_stations", mock_all_stations):
            with patch("app.adapter.wetterdienst_dwd._find_nearest_stations", mock_find_nearest):
                with patch("app.adapter.wetterdienst_dwd._get_stations_for_param", mock_get_stations_for_param):
                    with patch("app.adapter.wetterdienst_dwd._fetch_station_value", mock_fetch_station):
                        result = _fetch_observation(50.0, 9.0)

        primary = result.get("_primary")
        assert primary is not None
        assert primary["temperature"] == 22.0
        assert len(result.get("_all", [])) >= 1

    def test_stale_precipitation_discarded(self):
        """Stale precipitation data (>2h old) is discarded."""
        def mock_all_stations():
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 5.0}]

        def mock_find_nearest(stations, lat, lon, count):
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 5.0}]

        def mock_get_stations_for_param(pk):
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A"}]

        def mock_fetch_station(sid, zp, cc, dd):
            # Return a stale timestamp for precipitation, fresh for other params
            if dd == "precipitation":
                return 5.0, FROZEN - timedelta(hours=3)
            return 22.0, FROZEN

        with patch("app.adapter.wetterdienst_dwd._get_all_stations", mock_all_stations):
            with patch("app.adapter.wetterdienst_dwd._find_nearest_stations", mock_find_nearest):
                with patch("app.adapter.wetterdienst_dwd._get_stations_for_param", mock_get_stations_for_param):
                    with patch("app.adapter.wetterdienst_dwd._fetch_station_value", mock_fetch_station):
                        result = _fetch_observation(50.0, 9.0)

        primary = result.get("_primary")
        # Precipitation is stale, should be discarded
        assert primary["precipitation"] is None
        # Temperature should still be there
        assert primary["temperature"] == 22.0

    def test_no_temp_and_no_wind_returns_empty(self):
        """When neither temperature nor wind is available, returns empty observation."""
        def mock_all_stations():
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 5.0}]

        def mock_find_nearest(stations, lat, lon, count):
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A", "distance": 5.0}]

        def mock_get_stations_for_param(pk):
            return [{"id": "00001", "lat": 50.0, "lon": 9.0, "name": "Station A"}]

        def mock_fetch_station(sid, zp, cc, dd):
            # Only precipitation data available, no temp or wind
            if dd == "precipitation":
                return 5.0, FROZEN
            return None, None

        with patch("app.adapter.wetterdienst_dwd._get_all_stations", mock_all_stations):
            with patch("app.adapter.wetterdienst_dwd._find_nearest_stations", mock_find_nearest):
                with patch("app.adapter.wetterdienst_dwd._get_stations_for_param", mock_get_stations_for_param):
                    with patch("app.adapter.wetterdienst_dwd._fetch_station_value", mock_fetch_station):
                        result = _fetch_observation(50.0, 9.0)

        assert result == _empty_observation()


# ---------------------------------------------------------------------------
# _fetch_forecast — error paths
# ---------------------------------------------------------------------------


class TestFetchForecast:
    def test_cache_hit_returns_cached_result(self):
        """Cached forecast data is returned without calling wetterdienst."""
        from app.adapter.wetterdienst_dwd import _mosmix_cache

        now = datetime.now(timezone.utc)
        vals_df = pl.DataFrame({
            "station_id": ["1"],
            "parameter": ["precipitation_height_significant_weather_last_1h"],
            "quantity": ["precipitation_height"],
            "date": [now],
            "value": [3.0],
        })
        _mosmix_cache["50.00,9.00"] = (now, vals_df)

        with patch("app.adapter.wetterdienst_dwd.DwdMosmixRequest") as mock_req:
            result = _fetch_forecast(50.0, 9.0)

        mock_req.assert_not_called()
        assert result["precip_30m"] == 3.0

        # Clean up
        _mosmix_cache.clear()


# ---------------------------------------------------------------------------
# _process_forecast_df — edge cases not covered by test_dwd.py
# ---------------------------------------------------------------------------


class TestProcessForecastDfEdgeCases:
    def _make_df(self, precip_rows, rad_rows):
        """Build a polars DataFrame matching the MosMix forecast schema."""
        all_rows = []
        for d, v in precip_rows:
            all_rows.append(
                {
                    "station_id": "1",
                    "parameter": "precipitation_height_significant_weather_last_1h",
                    "quantity": "precipitation_height",
                    "date": d,
                    "value": v,
                }
            )
        for d, v in rad_rows:
            all_rows.append(
                {
                    "station_id": "1",
                    "parameter": "radiation_global",
                    "quantity": "radiation_global",
                    "date": d,
                    "value": v,
                }
            )
        if not all_rows:
            return pl.DataFrame(
                {
                    "station_id": pl.Series([], dtype=pl.String),
                    "parameter": pl.Series([], dtype=pl.String),
                    "quantity": pl.Series([], dtype=pl.String),
                    "date": pl.Series([], dtype=pl.Datetime("us", "UTC")),
                    "value": pl.Series([], dtype=pl.Float64),
                }
            )
        return pl.DataFrame(all_rows)

    def test_nan_in_radiation_returns_no_uv(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        rad = [(now, float("nan"))]
        df = self._make_df([], rad)
        result = _process_forecast_df(df, now)
        assert result["uv_index"] is None

    def test_seconds_truncated_for_window(self):
        """Seconds and microseconds are truncated when building windows."""
        now = datetime(2026, 8, 1, 12, 0, 30, tzinfo=timezone.utc)
        rows = [(datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc), 5.0)]
        df = self._make_df(rows, [])
        result = _process_forecast_df(df, now)
        assert result["precip_30m"] == 5.0


# ---------------------------------------------------------------------------
# fetch_wetterdienst_weather — async entry point
# ---------------------------------------------------------------------------


class TestFetchWetterdienstWeather:
    async def test_empty_observation_and_forecast(self):
        """Both observation and forecast empty returns empty WeatherData."""
        with patch(
            "app.adapter.wetterdienst_dwd._fetch_observation",
            return_value={"_primary": None, "_all": []},
        ), patch(
            "app.adapter.wetterdienst_dwd._fetch_forecast",
            return_value=_empty_forecast(),
        ):
            from app.adapter.wetterdienst_dwd import fetch_wetterdienst_weather

            wd = await fetch_wetterdienst_weather(50.0, 9.0)

        assert wd.temperature is None
        assert wd.wind_speed is None

    async def test_observation_with_data(self):
        """Observation data populates WeatherData fields."""
        primary = {
            "temperature": 22.0,
            "wind_speed": 5.0,
            "wind_gust": 10.0,
            "precipitation": 0.5,
            "time": FROZEN,
        }
        all_stations = [
            {
                "station_name": "Test Station",
                "lat": 50.0,
                "lon": 9.0,
                "time": FROZEN,
            }
        ]

        forecast = {
            "precip_30m": 1.0,
            "intensity_30m": 1.0,
            "precip_1h": 2.0,
            "intensity_1h": 2.0,
            "precip_2h": None,
            "intensity_2h": None,
            "uv_index": 5.0,
        }

        with patch(
            "app.adapter.wetterdienst_dwd._fetch_observation",
            return_value={"_primary": primary, "_all": all_stations},
        ), patch(
            "app.adapter.wetterdienst_dwd._fetch_forecast",
            return_value=forecast,
        ):
            from app.adapter.wetterdienst_dwd import fetch_wetterdienst_weather

            wd = await fetch_wetterdienst_weather(50.0, 9.0)

        assert wd.temperature == 22.0
        assert wd.wind_speed == 5.0
        # precipitation: 0.5 mm/10min * 6 = 3.0 mm/h
        assert wd.precipitation_intensity == 3.0
        assert wd.precipitation_next_30m is True
        assert wd.uv_index == 5.0
        assert len(wd.stations) == 1
        assert wd.stations[0].source == "dwd"
