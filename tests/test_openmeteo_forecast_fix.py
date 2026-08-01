"""Tests for the forecast window bug: when data is available but shows no rain,
fields should return False/0.0 instead of None (which means "no data available").

Also tests the time-window boundary issue: minutely_15 data spans the full day,
so at 18:25 there are only 1-2 intervals left in the 30m window.
"""

from datetime import datetime, timedelta, timezone

from app.adapter.openmeteo import (
    _parse_hourly_precipitation,
    _parse_minutely_precipitation,
)


def _make_minutely_15(start: datetime, precip_values: list[float | None]) -> dict:
    times = [(start + timedelta(minutes=15 * i)).isoformat() for i in range(len(precip_values))]
    return {"time": times, "precipitation": precip_values}


class TestNoRainShouldNotBeNone:
    """When forecast data is available but shows zero precipitation,
    the result should be False/0.0 — not None (which means unknown)."""

    def test_minutely_zero_precip_returns_false_not_none(self):
        """If minutely data exists and all values are 0, return False/0.0."""
        now = datetime(2026, 8, 1, 18, 25, 0, tzinfo=timezone.utc)
        # 4 intervals starting at now, all zeros
        data = _make_minutely_15(now, [0.0, 0.0, 0.0, 0.0])
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is False
        assert result["precipitation_amount_next_30m"] == 0.0
        assert result["precipitation_intensity_next_30m"] == 0.0

    def test_minutely_zero_precip_all_windows_false(self):
        """With 8 zero intervals covering 2 hours, all windows should be False."""
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        data = _make_minutely_15(now, [0.0] * 8)
        result = _parse_minutely_precipitation(data, {}, now)
        for window in ("30m", "1h", "2h"):
            assert result[f"precipitation_next_{window}"] is False
            assert result[f"precipitation_amount_next_{window}"] == 0.0
            assert result[f"precipitation_intensity_next_{window}"] == 0.0

    def test_minutely_partial_rain_returns_true(self):
        """If some intervals have rain, return True with the values."""
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        data = _make_minutely_15(now, [0.0, 0.5, 0.0, 0.0])
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is True
        assert result["precipitation_amount_next_30m"] > 0

    def test_hourly_zero_precip_returns_false_not_none(self):
        """Hourly fallback: if data exists but shows 0, return False/0.0."""
        now = datetime(2026, 8, 1, 18, 25, 0, tzinfo=timezone.utc)
        # Data at now+15min (falls inside 30m window [now, now+30))
        data = {
            "time": [(now + timedelta(minutes=15)).isoformat()],
            "precipitation": [0.0],
        }
        result = _parse_hourly_precipitation(data, now)
        assert result["precipitation_next_30m"] is False
        assert result["precipitation_amount_next_30m"] == 0.0

    def test_hourly_zero_precip_all_windows(self):
        """Hourly: multiple zero values across windows should all be False."""
        now = datetime(2026, 8, 1, 18, 0, 0, tzinfo=timezone.utc)
        data = {
            "time": [
                (now + timedelta(minutes=15)).isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                (now + timedelta(hours=2)).isoformat(),
            ],
            "precipitation": [0.0, 0.0, 0.0],
        }
        result = _parse_hourly_precipitation(data, now)
        assert result["precipitation_next_30m"] is False
        assert result["precipitation_next_1h"] is False
        assert result["precipitation_next_2h"] is False


class TestFullDayForecastCoverage:
    """Ensure we have forecast data at any time of day.
    Open-Meteo minutely_15 returns full-day data (00:00–23:45).
    At 18:25, the 30m window only has 1-2 intervals."""

    def test_evening_forecast_works(self):
        """At 18:25, the 30m window [18:25, 18:55) should catch 18:30 and 18:45."""
        now = datetime(2026, 8, 1, 18, 25, 0, tzinfo=timezone.utc)
        # Only intervals in the evening
        times = [
            (now + timedelta(minutes=5)).isoformat(),  # 18:30
            (now + timedelta(minutes=20)).isoformat(),  # 18:45
            (now + timedelta(minutes=35)).isoformat(),  # 19:00 (1h window)
        ]
        data = {"time": times, "precipitation": [0.0, 0.0, 0.0]}
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is False
        assert result["precipitation_next_1h"] is False

    def test_evening_forecast_with_rain(self):
        """At 18:25, if rain starts at 18:45, the 30m window catches it."""
        now = datetime(2026, 8, 1, 18, 25, 0, tzinfo=timezone.utc)
        times = [
            (now + timedelta(minutes=5)).isoformat(),  # 18:30
            (now + timedelta(minutes=20)).isoformat(),  # 18:45
        ]
        data = {"time": times, "precipitation": [0.0, 0.5]}
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is True


class TestEmptyDataStillReturnsNone:
    """When no data is available at all (empty API response),
    fields should remain None — not False."""

    def test_minutely_empty_returns_none(self):
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _parse_minutely_precipitation({}, {}, now)
        assert result["precipitation_next_30m"] is None

    def test_minutely_no_matching_intervals_returns_none(self):
        """If data exists but no intervals fall in any window (e.g., all past),
        fields should remain None."""
        now = datetime(2026, 8, 1, 23, 50, 0, tzinfo=timezone.utc)
        # All intervals are in the past
        start = now - timedelta(hours=2)
        data = _make_minutely_15(start, [0.5, 0.3, 0.0])
        result = _parse_minutely_precipitation(data, {}, now)
        assert result["precipitation_next_30m"] is None
