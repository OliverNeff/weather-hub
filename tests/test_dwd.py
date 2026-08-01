import re
from datetime import datetime, timedelta, timezone

import polars as pl

from app.adapter.wetterdienst_dwd import (
    _find_nearest_stations,
    _haversine_km,
    _parse_csv_tail,
    _parse_directory_listing,
    _parse_station_tsv,
    _process_forecast_df,
)


class TestHaversineKm:
    def test_same_point(self):
        assert _haversine_km(50.0, 9.0, 50.0, 9.0) == 0.0

    def test_berlin_hamburg(self):
        dist = _haversine_km(52.5200, 13.4050, 53.5511, 10.0000)
        assert 250 < dist < 265

    def test_london_paris(self):
        dist = _haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
        assert 330 < dist < 350

    def test_equator_one_degree_lon(self):
        dist = _haversine_km(0.0, 0.0, 0.0, 1.0)
        assert 100 < dist < 112

    def test_pole_one_degree_lat(self):
        dist = _haversine_km(90.0, 0.0, 89.0, 0.0)
        assert 110 < dist < 112


class TestFindNearestStations:
    def _make_stations(self, offsets):
        stations = []
        for i, (dlat, dlon) in enumerate(offsets):
            stations.append(
                {
                    "id": f"{1000 + i:05d}",
                    "lat": 50.0 + dlat,
                    "lon": 9.0 + dlon,
                    "name": f"Station {i}",
                }
            )
        return stations

    def test_picks_closest(self):
        stations = self._make_stations([(0.01, 0.01), (0.5, 0.5), (0.001, 0.001)])
        result = _find_nearest_stations(stations, 50.0, 9.0, count=2)
        assert len(result) == 2
        assert result[0]["id"] == "01002"
        assert result[1]["id"] == "01000"

    def test_respects_count_limit(self):
        stations = self._make_stations([(0.01, 0.01), (0.02, 0.02), (0.03, 0.03)])
        result = _find_nearest_stations(stations, 50.0, 9.0, count=1)
        assert len(result) == 1

    def test_respects_max_distance(self):
        stations = self._make_stations([(0.5, 0.5), (1.0, 1.0)])
        result = _find_nearest_stations(stations, 50.0, 9.0, count=10)
        for s in result:
            assert s["distance"] < 50

    def test_returns_empty_when_none_in_range(self):
        stations = self._make_stations([(5.0, 5.0)])
        result = _find_nearest_stations(stations, 50.0, 9.0, count=3)
        assert result == []

    def test_includes_distance_field(self):
        stations = self._make_stations([(0.01, 0.01)])
        result = _find_nearest_stations(stations, 50.0, 9.0, count=3)
        assert len(result) == 1
        assert "distance" in result[0]
        assert result[0]["distance"] > 0

    def test_does_not_mutate_input(self):
        stations = self._make_stations([(0.01, 0.01)])
        original_keys = set(stations[0].keys())
        _find_nearest_stations(stations, 50.0, 9.0, count=3)
        assert set(stations[0].keys()) == original_keys


class TestParseCsvTail:
    def _make_csv(self, rows):
        header = "MESS_DATUM;TT_10;Qualitaet"
        lines = [header, *rows]
        return ("\n".join(lines)).encode("latin-1")

    def test_valid_data(self):
        csv_bytes = self._make_csv(
            [
                "202608011200;22.5;0",
                "202608011210;23.0;0",
            ]
        )
        val, dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val == 23.0
        assert dt == datetime(2026, 8, 1, 12, 10, tzinfo=timezone.utc)

    def test_nan_value_skipped(self):
        csv_bytes = self._make_csv(
            [
                "202608011200;-999;0",
                "202608011210;23.0;0",
            ]
        )
        val, _dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val == 23.0

    def test_all_nan_returns_none(self):
        csv_bytes = self._make_csv(
            [
                "202608011200;-999;0",
                "202608011210;-999;0",
            ]
        )
        val, dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val is None
        assert dt is None

    def test_empty_input(self):
        val, dt = _parse_csv_tail(b"", "TT_10")
        assert val is None
        assert dt is None

    def test_missing_column(self):
        csv_bytes = self._make_csv(
            [
                "202608011200;22.5;0",
            ]
        )
        val, dt = _parse_csv_tail(csv_bytes, "NONEXISTENT")
        assert val is None
        assert dt is None

    def test_missing_mess_datum_column(self):
        raw = b"TIME;VALUE\n202608011200;22.5"
        val, _dt = _parse_csv_tail(raw, "VALUE")
        assert val is None

    def test_returns_last_valid_row(self):
        csv_bytes = self._make_csv(
            [
                "202608011200;20.0;0",
                "202608011210;-999;0",
                "202608011220;21.0;0",
            ]
        )
        val, dt = _parse_csv_tail(csv_bytes, "TT_10")
        assert val == 21.0
        assert dt.minute == 20


class TestParseStationTsv:
    def test_parses_valid_data(self):
        text = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland\n"
            "--- --- --- --- --- --- --- --- ---\n"
            "00001 20200101 20260801 150 50.1234 9.5678 Berlin Mitte  Brandenburg \n"
        )
        result = _parse_station_tsv(text)
        assert len(result) == 1
        assert result[0]["id"] == "00001"
        assert result[0]["lat"] == 50.1234
        assert result[0]["lon"] == 9.5678

    def test_parses_multiple_stations(self):
        text = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland\n"
            "--- --- --- --- --- --- --- --- ---\n"
            "00001 20200101 20260801 150 50.1000 9.1000 Station A  Berlin \n"
            "00002 20200101 20260801 200 51.2000 10.2000 Station B  Hamburg \n"
        )
        result = _parse_station_tsv(text)
        assert len(result) == 2
        assert result[0]["id"] == "00001"
        assert result[1]["id"] == "00002"

    def test_skips_header_lines(self):
        text = (
            "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland\n"
            "--- --- --- --- --- --- --- --- ---\n"
            "00001 20200101 20260801 150 50.1000 9.1000 Only One  Berlin \n"
        )
        result = _parse_station_tsv(text)
        assert len(result) == 1
        assert result[0]["name"] == "Only One"

    def test_empty_text(self):
        result = _parse_station_tsv("")
        assert result == []

    def test_malformed_lines_skipped(self):
        text = (
            "header\n"
            "---\n"
            "garbage line\n"
            "00001 20200101 20260801 150 50.1000 9.1000 Valid One  Berlin \n"
        )
        result = _parse_station_tsv(text)
        assert len(result) == 1
        assert result[0]["id"] == "00001"


class TestProcessForecastDf:
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
            # Build an empty DataFrame with correct schema instead of Null types.
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

    def test_precip_30m_window(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            (now, 2.0),
            (now + timedelta(hours=1), 5.0),
        ]
        rad = [(now, 500.0)]
        df = self._make_df(rows, rad)
        result = _process_forecast_df(df, now)
        assert result["precip_30m"] == 2.0
        assert result["intensity_30m"] == 2.0

    def test_precip_1h_window(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            (now, 1.0),
            (now + timedelta(minutes=30), 3.0),
            (now + timedelta(hours=1), 5.0),
        ]
        rad = []
        df = self._make_df(rows, rad)
        result = _process_forecast_df(df, now)
        assert result["precip_1h"] == 2.0
        assert result["intensity_1h"] == 2.0

    def test_precip_2h_window(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            (now, 1.0),
            (now + timedelta(minutes=60), 2.0),
            (now + timedelta(minutes=90), 3.0),
            (now + timedelta(hours=2), 10.0),
        ]
        rad = []
        df = self._make_df(rows, rad)
        result = _process_forecast_df(df, now)
        assert result["precip_2h"] == 2.0
        assert result["intensity_2h"] == 2.0

    def test_uv_index_from_radiation(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = []
        rad = [(now, 500.0)]
        df = self._make_df(rows, rad)
        result = _process_forecast_df(df, now)
        assert result["uv_index"] == 9.5

    def test_uv_index_clamped_to_16(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = []
        rad = [(now, 5000.0)]
        df = self._make_df(rows, rad)
        result = _process_forecast_df(df, now)
        assert result["uv_index"] == 16

    def test_uv_index_clamped_to_zero(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = []
        rad = [(now, -100.0)]
        df = self._make_df(rows, rad)
        result = _process_forecast_df(df, now)
        assert result["uv_index"] == 0

    def test_no_forecast_data_returns_empty(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        df = self._make_df([], [])
        result = _process_forecast_df(df, now)
        assert result["precip_30m"] is None
        assert result["precip_1h"] is None
        assert result["uv_index"] is None

    def test_nan_in_value_returns_none(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [(now, float("nan"))]
        rad = []
        df = self._make_df(rows, rad)
        result = _process_forecast_df(df, now)
        assert result["precip_30m"] is None


class TestParseDirectoryListing:
    def _make_pattern(self, prefix: str):
        return re.compile(rf"10minutenwerte_{prefix}_(\d{{5}})_akt\.zip")

    def test_valid_html_with_zip_files(self):
        html = (
            "<html><body>\n"
            '<a href="10minutenwerte_TU_00250_akt.zip">10minutenwerte_TU_00250_akt.zip</a>\n'
            '<a href="10minutenwerte_TU_00310_akt.zip">10minutenwerte_TU_00310_akt.zip</a>\n'
            '<a href="10minutenwerte_TU_10420_akt.zip">10minutenwerte_TU_10420_akt.zip</a>\n'
            "</body></html>"
        )
        pattern = self._make_pattern("TU")
        result = _parse_directory_listing(html, pattern)
        assert "00250" in result
        assert "00310" in result
        assert "10420" in result
        assert len(result) == 3

    def test_empty_html(self):
        html = ""
        pattern = self._make_pattern("TU")
        result = _parse_directory_listing(html, pattern)
        assert result == []

    def test_no_matching_files(self):
        html = '<html><body>\n<a href="some_other_file.txt">some_other_file.txt</a>\n</body></html>'
        pattern = self._make_pattern("TU")
        result = _parse_directory_listing(html, pattern)
        assert result == []

    def test_deduplicates_station_ids(self):
        html = (
            '<a href="10minutenwerte_wind_00250_akt.zip">10minutenwerte_wind_00250_akt.zip</a>\n'
            '<a href="10minutenwerte_wind_00250_akt.zip">10minutenwerte_wind_00250_akt.zip</a>\n'
            '<a href="10minutenwerte_wind_00310_akt.zip">10minutenwerte_wind_00310_akt.zip</a>\n'
        )
        pattern = self._make_pattern("wind")
        result = _parse_directory_listing(html, pattern)
        assert len(result) == 2
        assert "00250" in result
        assert "00310" in result

    def test_various_zip_prefixes(self):
        html = (
            '<a href="10minutenwerte_nieder_00250_akt.zip">10minutenwerte_nieder_00250_akt.zip</a>\n'
            '<a href="10minutenwerte_extrema_wind_10420_akt.zip">10minutenwerte_extrema_wind_10420_akt.zip</a>\n'
        )
        nieder_pattern = self._make_pattern("nieder")
        extrema_pattern = self._make_pattern("extrema_wind")
        nieder_result = _parse_directory_listing(html, nieder_pattern)
        extrema_result = _parse_directory_listing(html, extrema_pattern)
        assert nieder_result == ["00250"]
        assert extrema_result == ["10420"]
