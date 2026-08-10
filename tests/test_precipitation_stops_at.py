"""Tests for precipitation_stops_at: no false positives when not raining."""

from datetime import datetime, timedelta, timezone

import polars as pl

from app.adapter.buinradar import _calculate_precipitation_stops_at
from app.adapter.openmeteo import _precipitation_stops_at as om_stops_at, _precip_stops_hourly
from app.adapter.wetterdienst_dwd import _precipitation_stops_at as dwd_stops_at


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hourly(start: datetime, values: list[float]) -> dict:
    """Build hourly dict with 1-hour intervals starting from *start*."""
    times = [(start + timedelta(hours=i)).isoformat() for i in range(len(values))]
    return {"time": times, "precipitation": values}


def _minutely_15(start: datetime, values: list[float]) -> dict:
    """Build minutely_15 dict with 15-min intervals starting from *start*."""
    times = [(start + timedelta(minutes=15 * i)).isoformat() for i in range(len(values))]
    return {"time": times, "precipitation": values}


def _dwd_df(start: datetime, values: list[float]) -> pl.DataFrame:
    """Build a polars DataFrame for DWD _precipitation_stops_at."""
    dates = [start + timedelta(hours=i) for i in range(len(values))]
    return pl.DataFrame({"date": dates, "value": values})


# ---------------------------------------------------------------------------
# Open-Meteo: _precipitation_stops_at
# ---------------------------------------------------------------------------


class TestOmStopsAt:
    """The main entry point delegates to minutely_15, then hourly fallback."""

    def test_no_rain_now_returns_none(self):
        """No rain in minutely, no rain in hourly -> None."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        minutely = _minutely_15(now, [0.0, 0.0, 0.0, 0.0])
        hourly = _hourly(now, [0.0, 0.0, 0.0, 0.0])
        assert om_stops_at(minutely, hourly, now) is None

    def test_no_rain_in_minutely_but_rain_in_3_days_returns_none(self):
        """Hourly has rain 72h out — must NOT leak through."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        minutely = _minutely_15(now, [0.0, 0.0, 0.0, 0.0])
        # Rain 3 days away
        far = now + timedelta(days=3)
        hourly = _hourly(far, [2.0, 3.0, 0.0, 0.0])
        assert om_stops_at(minutely, hourly, now) is None

    def test_rain_now_stops_in_45min(self):
        """Rain in first 3 minutely intervals, stops at 45min."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        minutely = _minutely_15(now, [1.0, 0.5, 0.8, 0.0, 0.0])
        hourly = _hourly(now, [1.0, 0.5, 0.0, 0.0])
        result = om_stops_at(minutely, hourly, now)
        # Last rainy interval is now + 30 min
        assert result == now + timedelta(minutes=30)

    def test_rain_through_minutely_extends_hourly(self):
        """Rain fills minutely window; hourly extends to find the end."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        minutely = _minutely_15(now, [1.0, 1.0, 1.0, 1.0])
        # Hourly: rain for 2h, then stops
        hourly = _hourly(now, [1.0, 2.0, 0.0, 0.0])
        result = om_stops_at(minutely, hourly, now)
        # Last rainy hourly entry is now + 1h
        assert result == now + timedelta(hours=1)


class TestOmPrecipStopsHourly:
    """The 24h hourly fallback layer."""

    def test_no_near_rain_returns_none(self):
        """Rain starts 5 hours out -> None."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        hourly = _hourly(now, [0.0, 0.0, 0.0, 0.0, 5.0, 0.0])
        assert _precip_stops_hourly(hourly, now) is None

    def test_rain_in_2h_guard_then_stops(self):
        """Rain at h0 and h1, stops at h2."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        hourly = _hourly(now, [2.0, 1.5, 0.0, 0.0, 0.0])
        result = _precip_stops_hourly(hourly, now)
        assert result == now + timedelta(hours=1)

    def test_long_rain_6_hours(self):
        """Rain for 6 hours straight -> returns last rainy hour."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        hourly = _hourly(now, [2.0, 3.0, 1.0, 0.5, 0.5, 1.0, 0.0, 0.0])
        result = _precip_stops_hourly(hourly, now)
        assert result == now + timedelta(hours=5)

    def test_rain_exactly_at_2h_boundary(self):
        """Rain only at now+2h counts as 'near' -> scans forward."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        hourly = _hourly(now, [0.0, 0.0, 3.0, 0.0])
        result = _precip_stops_hourly(hourly, now)
        # 2h boundary is exclusive in guard (< 2h), so this should be None
        assert result is None

    def test_rain_at_1h59min_counts(self):
        """Rain just before 2h mark -> near guard passes."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        times = [
            (now + timedelta(minutes=119)).isoformat(),
            (now + timedelta(hours=2, minutes=10)).isoformat(),
        ]
        hourly = {"time": times, "precipitation": [2.0, 0.0]}
        result = _precip_stops_hourly(hourly, now)
        assert result == now + timedelta(minutes=119)


# ---------------------------------------------------------------------------
# DWD: _precipitation_stops_at
# ---------------------------------------------------------------------------


class TestDwdStopsAt:
    """MosMix helper — 2h guard prevents far-future rain from leaking."""

    def test_no_rain_2h_returns_none(self):
        """No rain in 2h window -> None, even if rain is 7 days out."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        df = _dwd_df(
            now,
            [0.0] * 168 + [2.0, 3.0, 0.0],  # 7 days of zeros, then rain
        )
        assert dwd_stops_at(df, now) is None

    def test_rain_in_2h_stops_soon(self):
        """Rain at h0, h1, stops at h2."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        df = _dwd_df(now, [2.0, 1.5, 0.0, 0.0, 0.0])
        result = dwd_stops_at(df, now)
        assert result == now + timedelta(hours=1)

    def test_long_rain_12_hours(self):
        """12 hours of rain -> returns the 12th hour."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        df = _dwd_df(now, [1.0] * 12 + [0.0, 0.0])
        result = dwd_stops_at(df, now)
        assert result == now + timedelta(hours=11)

    def test_rain_starts_next_week(self):
        """The original bug: rain on Aug 17 when it's Aug 10."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        # 240 hours of data (full MosMix), rain at idx 166-169 (Aug 17)
        values = [0.0] * 166 + [0.8, 0.9, 1.8, 1.6] + [0.0] * 64
        df = _dwd_df(now, values)
        assert dwd_stops_at(df, now) is None

    def test_empty_df(self):
        assert dwd_stops_at(pl.DataFrame({"date": [], "value": []}), datetime.now(timezone.utc)) is None

    def test_rain_only_past(self):
        """Rain was yesterday, not today."""
        now = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(hours=3)
        df = _dwd_df(past, [2.0, 1.0, 0.0])
        # Past rain is skipped; no future rain -> None
        result = dwd_stops_at(df, now)
        assert result is None


# ---------------------------------------------------------------------------
# Buienradar: _calculate_precipitation_stops_at
# ---------------------------------------------------------------------------


class TestBuienradarStopsAt:
    """5-min radar grid (2h = 24 intervals)."""

    def test_no_rain_returns_none(self):
        assert _calculate_precipitation_stops_at([0.0] * 24) is None

    def test_rain_stops_after_3_intervals(self):
        """Rain at idx 0,1,2 then stops."""
        now = datetime.now(timezone.utc)
        result = _calculate_precipitation_stops_at([1.0, 0.8, 0.5, 0.0, 0.0])
        expected = now + timedelta(minutes=10)
        assert result is not None
        assert abs((result - expected).total_seconds()) < 1

    def test_rain_through_full_2h_window_returns_none(self):
        """Continuous rain through all 24 intervals -> None (defer to wider horizon)."""
        result = _calculate_precipitation_stops_at([1.0] * 24)
        assert result is None

    def test_empty(self):
        assert _calculate_precipitation_stops_at([]) is None

    def test_rain_then_stops_at_1h55(self):
        """Rain for 23 intervals, stops at last."""
        now = datetime.now(timezone.utc)
        values = [1.0] * 23 + [0.0]
        result = _calculate_precipitation_stops_at(values)
        # Result should be ~now + 110 min (23 × 5 min); allow 1s tolerance
        expected = now + timedelta(minutes=110)
        assert result is not None
        assert abs((result - expected).total_seconds()) < 1
