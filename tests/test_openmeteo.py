import math
from datetime import datetime, timezone, timedelta

from app.adapter.openmeteo import (
    _sf,
    _si,
    _kmh,
    _parse_iso,
    _noaa_elevation,
    _current_precip_from_minutely,
    _parse_minutely_precipitation,
    _parse_hourly_precipitation,
)


# ---------------------------------------------------------------------------
# _sf — float extraction with NaN handling
# ---------------------------------------------------------------------------


class TestSf:
    def test_normal_value(self):
        assert _sf({"temp": 22.5}, "temp") == 22.5

    def test_rounding(self):
        assert _sf({"temp": 22.555}, "temp") == 22.6

    def test_missing_key(self):
        assert _sf({"temp": 22.5}, "other") is None

    def test_none_value(self):
        assert _sf({"temp": None}, "temp") is None

    def test_nan_value(self):
        assert _sf({"temp": float("nan")}, "temp") is None

    def test_empty_dict(self):
        assert _sf({}, "temp") is None

    def test_integer_value(self):
        assert _sf({"temp": 22}, "temp") == 22.0

    def test_string_numeric(self):
        assert _sf({"temp": "22.5"}, "temp") == 22.5


# ---------------------------------------------------------------------------
# _si — int extraction with None handling
# ---------------------------------------------------------------------------


class TestSi:
    def test_normal_value(self):
        assert _si({"code": 61}, "code") == 61

    def test_missing_key(self):
        assert _si({"code": 61}, "other") is None

    def test_none_value(self):
        assert _si({"code": None}, "code") is None

    def test_empty_dict(self):
        assert _si({}, "code") is None

    def test_float_value(self):
        assert _si({"code": 61.0}, "code") == 61


# ---------------------------------------------------------------------------
# _kmh — km/h to m/s conversion
# ---------------------------------------------------------------------------


class TestKmh:
    def test_36_kmh(self):
        assert _kmh(36) == 10.0

    def test_zero(self):
        assert _kmh(0) == 0.0

    def test_none(self):
        assert _kmh(None) is None

    def test_rounding(self):
        assert _kmh(10) == 2.8

    def test_float_input(self):
        assert _kmh(72.0) == 20.0


# ---------------------------------------------------------------------------
# _parse_iso — ISO datetime parsing
# ---------------------------------------------------------------------------


class TestParseIso:
    def test_valid_iso(self):
        result = _parse_iso("2026-08-01T12:00:00")
        assert result == datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_valid_iso_with_tz(self):
        result = _parse_iso("2026-08-01T12:00:00+00:00")
        assert result == datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_none_input(self):
        assert _parse_iso(None) is None

    def test_empty_string(self):
        assert _parse_iso("") is None

    def test_invalid_string(self):
        assert _parse_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# _noaa_elevation — sun elevation calculation
# ---------------------------------------------------------------------------


class TestNoaaElevation:
    def test_noon_summer_positive(self):
        now = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
        el = _noaa_elevation(50, 9, now)
        assert el is not None
        assert el > 0

    def test_midnight_negative(self):
        now = datetime(2026, 6, 21, 0, 0, 0, tzinfo=timezone.utc)
        el = _noaa_elevation(50, 9, now)
        assert el is not None
        assert el < 0

    def test_solar_noon_offset(self):
        now = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
        el_offset = _noaa_elevation(50, 10, now)
        el_base = _noaa_elevation(50, 9, now)
        assert el_offset is not None
        assert el_base is not None
        assert abs(el_offset - el_base) > 0

    def test_winter_lower_elevation(self):
        summer = _noaa_elevation(50, 9, datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc))
        winter = _noaa_elevation(50, 9, datetime(2026, 12, 21, 12, 0, 0, tzinfo=timezone.utc))
        assert summer is not None and winter is not None
        assert summer > winter

    def test_equator_approx_zero_declination_equinox(self):
        now = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)
        el = _noaa_elevation(0, 0, now)
        assert el is not None
        assert el > 50

    def test_return_type_rounded(self):
        now = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
        el = _noaa_elevation(50, 9, now)
        assert isinstance(el, float)


# ---------------------------------------------------------------------------
# _current_precip_from_minutely — current precip from 15-min intervals
# ---------------------------------------------------------------------------


def _make_minutely_15(start: datetime, precip_values: list[float | None]) -> dict:
    times = [(start + timedelta(minutes=15 * i)).isoformat() for i in range(len(precip_values))]
    return {"time": times, "precipitation": precip_values}


class TestCurrentPrecipFromMinutely:
    def test_basic_future_interval(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        data = _make_minutely_15(now, [0.5, 0.3, 0.0])
        result = _current_precip_from_minutely(data, now)
        assert result == 2.0  # 0.5 * 4

    def test_zero_precip_returns_none(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        data = _make_minutely_15(now, [0.0, 0.0, 0.0])
        assert _current_precip_from_minutely(data, now) is None

    def test_all_none_values(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        data = _make_minutely_15(now, [None, None, None])
        assert _current_precip_from_minutely(data, now) is None

    def test_empty_data(self):
        assert _current_precip_from_minutely({"time": [], "precipitation": []}, datetime.now(timezone.utc)) is None

    def test_no_times_key(self):
        assert _current_precip_from_minutely({}, datetime.now(timezone.utc)) is None

    def test_past_interval_ignored(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        start = now - timedelta(minutes=45)
        data = _make_minutely_15(start, [0.5, 0.3, 0.0])
        assert _current_precip_from_minutely(data, now) is None

    def test_beyond_30min_window_ignored(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        start = now + timedelta(minutes=45)
        data = _make_minutely_15(start, [0.5, 0.3, 0.0])
        assert _current_precip_from_minutely(data, now) is None

    def test_nearest_future_picked(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        data = _make_minutely_15(now, [0.0, 0.5, 0.1])
        result = _current_precip_from_minutely(data, now)
        assert result is None  # first is 0, not > 0, so returns None

    def test_nearest_future_non_zero_picked(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        data = _make_minutely_15(now, [0.1, 0.5, 0.0])
        result = _current_precip_from_minutely(data, now)
        assert result == 0.4  # 0.1 * 4

    def test_rounding(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        data = _make_minutely_15(now, [0.1234, 0.0])
        result = _current_precip_from_minutely(data, now)
        assert result == 0.5  # 0.1234 * 4 = 0.4936, rounded to 0.5


# ---------------------------------------------------------------------------
# _parse_minutely_precipitation — 30m/1h/2h forecast windows
# ---------------------------------------------------------------------------


class TestParseMinutelyPrecipitation:
    def _make_now(self):
        return datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_empty_returns_all_none(self):
        now = self._make_now()
        result = _parse_minutely_precipitation({}, {}, now)
        for key in result:
            assert result[key] is None

    def test_rain_in_30m_window(self):
        now = self._make_now()
        data = _make_minutely_15(now, [0.5, 0.3, 0.2, 0.0])
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is True
        assert result["precipitation_amount_next_30m"] is not None

    def test_no_rain_returns_false_not_none(self):
        now = self._make_now()
        data = _make_minutely_15(now, [0.0, 0.0, 0.0, 0.0])
        result = _parse_minutely_precipitation(data, {}, now)
        # Data exists but shows no rain — return False/0.0, not None
        for window in ("30m", "1h", "2h"):
            assert result[f"precipitation_next_{window}"] is False
            assert result[f"precipitation_amount_next_{window}"] == 0.0

    def test_all_windows_populated(self):
        now = self._make_now()
        times = [
            (now + timedelta(minutes=15 * i)).isoformat() for i in range(8)
        ]
        data = {"time": times, "precipitation": [0.5] * 8}
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is True
        assert result["precipitation_next_1h"] is True
        assert result["precipitation_next_2h"] is True

    def test_30m_window_boundary(self):
        now = self._make_now()
        # now+15min falls in 30m window [now, now+30), now+30 is excluded
        times = [
            (now + timedelta(minutes=15)).isoformat(),
            (now + timedelta(minutes=30)).isoformat(),
        ]
        data = {"time": times, "precipitation": [0.5, 0.5]}
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is True
        assert result["precipitation_amount_next_30m"] == 2.0  # only the now+15min value

    def test_intensity_converted_to_mmh(self):
        now = self._make_now()
        data = _make_minutely_15(now, [0.25, 0.0])
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_intensity_next_30m"] == 1.0

    def test_none_values_in_series_ignored(self):
        now = self._make_now()
        # Place 0.5 at now+15min so it falls within the 30m window
        times = [
            (now + timedelta(minutes=15)).isoformat(),
            (now + timedelta(minutes=30)).isoformat(),
        ]
        data = {"time": times, "precipitation": [0.5, None]}
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is True

    def test_fallback_to_hourly_when_no_minutely(self):
        now = self._make_now()
        # Empty minutely triggers fallback to hourly
        hourly_data = {
            "time": [
                (now + timedelta(minutes=30)).isoformat(),
                (now + timedelta(minutes=90)).isoformat(),
            ],
            "precipitation": [2.0, 1.0],
        }
        result = _parse_minutely_precipitation({}, hourly_data, now)
        assert result["precipitation_next_1h"] is True

    def test_minutely_takes_precedence_over_hourly(self):
        now = self._make_now()
        minutely_data = _make_minutely_15(now, [0.5, 0.0, 0.0, 0.0])
        hourly_data = {
            "time": [(now + timedelta(hours=1)).isoformat()],
            "precipitation": [100.0],
        }
        result = _parse_minutely_precipitation(minutely_data, hourly_data, now)
        assert result["precipitation_next_30m"] is True
        assert result["precipitation_amount_next_30m"] == 2.0

    def test_rain_only_in_1h_not_30m(self):
        now = self._make_now()
        times = [
            (now + timedelta(minutes=45)).isoformat(),
            (now + timedelta(minutes=60)).isoformat(),
        ]
        data = {"time": times, "precipitation": [0.5, 0.3]}
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is None
        assert result["precipitation_next_1h"] is True

    def test_rain_only_in_2h_not_1h(self):
        now = self._make_now()
        times = [
            (now + timedelta(minutes=90)).isoformat(),
            (now + timedelta(minutes=105)).isoformat(),
        ]
        data = {"time": times, "precipitation": [0.5, 0.3]}
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is None
        assert result["precipitation_next_1h"] is None
        assert result["precipitation_next_2h"] is True


# ---------------------------------------------------------------------------
# _parse_hourly_precipitation — fallback hourly parsing
# ---------------------------------------------------------------------------


class TestParseHourlyPrecipitation:
    def _make_now(self):
        return datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_empty_returns_all_none(self):
        now = self._make_now()
        result = _parse_hourly_precipitation({}, now)
        for key in result:
            assert result[key] is None

    def test_single_hour_rain(self):
        now = self._make_now()
        # Place data at now+30min so it falls within [now, now+1h)
        data = {
            "time": [(now + timedelta(minutes=30)).isoformat()],
            "precipitation": [2.0],
        }
        result = _parse_hourly_precipitation(data, now)
        assert result["precipitation_next_1h"] is True
        assert result["precipitation_next_30m"] is None

    def test_no_rain_all_zero(self):
        now = self._make_now()
        data = {
            "time": [
                (now + timedelta(hours=1)).isoformat(),
                (now + timedelta(hours=2)).isoformat(),
            ],
            "precipitation": [0.0, 0.0],
        }
        result = _parse_hourly_precipitation(data, now)
        # Data exists but shows no rain — return False/0.0, not None
        for window in ("30m", "1h", "2h"):
            if result[f"precipitation_next_{window}"] is not None:
                assert result[f"precipitation_next_{window}"] is False
                assert result[f"precipitation_amount_next_{window}"] == 0.0
        # 30m window has no hourly data (first data point at 1h)
        assert result["precipitation_next_30m"] is None

    def test_two_hours_rain(self):
        now = self._make_now()
        # now+1h falls in [now, now+2h) only
        data = {
            "time": [
                (now + timedelta(hours=1)).isoformat(),
                (now + timedelta(hours=2)).isoformat(),
            ],
            "precipitation": [2.0, 4.0],
        }
        result = _parse_hourly_precipitation(data, now)
        assert result["precipitation_next_2h"] is True
        # only now+1h (2.0) is in the 2h window, now+2h is excluded by < boundary
        assert result["precipitation_amount_next_2h"] == 2.0

    def test_30m_window_empty_hourly(self):
        now = self._make_now()
        # Hourly data at now+30min falls in 1h window only
        data = {
            "time": [(now + timedelta(minutes=30)).isoformat()],
            "precipitation": [5.0],
        }
        result = _parse_hourly_precipitation(data, now)
        assert result["precipitation_next_30m"] is None
        assert result["precipitation_next_1h"] is True

    def test_past_hours_ignored(self):
        now = self._make_now()
        data = {
            "time": [(now - timedelta(hours=1)).isoformat()],
            "precipitation": [5.0],
        }
        result = _parse_hourly_precipitation(data, now)
        for key in result:
            assert result[key] is None

    def test_none_values_ignored(self):
        now = self._make_now()
        # now+30min in 1h window (value None, skipped), now+45min in 1h and 2h windows
        data = {
            "time": [
                (now + timedelta(minutes=30)).isoformat(),
                (now + timedelta(minutes=45)).isoformat(),
            ],
            "precipitation": [None, 2.0],
        }
        result = _parse_hourly_precipitation(data, now)
        # now+30min has None (skipped), now+45min has 2.0 in both 1h and 2h windows
        assert result["precipitation_next_1h"] is True
        assert result["precipitation_next_2h"] is True
        assert result["precipitation_amount_next_1h"] == 2.0
        assert result["precipitation_amount_next_2h"] == 2.0

    def test_boundary_exclusive(self):
        now = self._make_now()
        data = {
            "time": [(now + timedelta(hours=2)).isoformat()],
            "precipitation": [5.0],
        }
        result = _parse_hourly_precipitation(data, now)
        assert result["precipitation_next_2h"] is None

    def test_mean_of_two_values_in_1h(self):
        now = self._make_now()
        data = {
            "time": [
                (now + timedelta(minutes=15)).isoformat(),
                (now + timedelta(minutes=45)).isoformat(),
            ],
            "precipitation": [2.0, 4.0],
        }
        result = _parse_hourly_precipitation(data, now)
        assert result["precipitation_amount_next_30m"] == 2.0
        assert result["precipitation_amount_next_1h"] == 3.0