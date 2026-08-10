"""Tests for router merge logic: no rain -> all forecast fields None."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation
from app.routers.weather_data import _pick_by_source_order, get_weather_data


def _wd(
    precipitation_intensity=None,
    precipitation_next_30m=None,
    precipitation_amount_next_30m=None,
    precipitation_next_1h=None,
    precipitation_amount_next_1h=None,
    precipitation_next_2h=None,
    precipitation_stops_at=None,
    temperature=None,
    source="openmeteo",
):
    wd = WeatherData(
        precipitation_intensity=precipitation_intensity,
        precipitation_next_30m=precipitation_next_30m,
        precipitation_amount_next_30m=precipitation_amount_next_30m,
        precipitation_next_1h=precipitation_next_1h,
        precipitation_amount_next_1h=precipitation_amount_next_1h,
        precipitation_next_2h=precipitation_next_2h,
        precipitation_stops_at=precipitation_stops_at,
        temperature=temperature,
    )
    wd.stations.append(
        WeatherStation(
            source=source,
            name="test",
            lat=50.0,
            lon=9.0,
            time=datetime.now(timezone.utc),
        )
    )
    return wd


@pytest.mark.asyncio
async def test_no_rain_clears_stops_at():
    """When no adapter reports current rain, precipitation_stops_at must be None.
    Forecast bool/amount/intensity fields are kept as-is from adapter data."""
    stops_far = datetime.now(timezone.utc) + timedelta(days=7)
    dwd = _wd(
        precipitation_next_30m=True,
        precipitation_amount_next_30m=5.0,
        precipitation_next_1h=True,
        precipitation_amount_next_1h=10.0,
        precipitation_next_2h=True,
        precipitation_stops_at=stops_far,
        source="dwd",
    )
    openmeteo = _wd(source="openmeteo")
    buienradar = _wd(source="buienradar")

    with (
        patch(
            "app.routers.weather_data.fetch_wetterdienst_weather",
            new_callable=AsyncMock,
            return_value=dwd,
        ),
        patch(
            "app.routers.weather_data.fetch_buienradar_weather",
            new_callable=AsyncMock,
            return_value=buienradar,
        ),
        patch(
            "app.routers.weather_data.fetch_openmeteo_weather",
            new_callable=AsyncMock,
            return_value=openmeteo,
        ),
    ):
        result = await get_weather_data(lat=50.0, lon=9.0)

    # No adapter reported precipitation_intensity, so precipitation_now is None
    assert result.precipitation_now is not True
    # stops_at cleared (meaningless without active rain)
    assert result.precipitation_stops_at is None
    # Forecast fields preserved from adapter data
    assert result.precipitation_next_30m is True
    assert result.precipitation_amount_next_30m == 5.0
    assert result.precipitation_next_1h is True
    assert result.precipitation_amount_next_1h == 10.0
    assert result.precipitation_next_2h is True


@pytest.mark.asyncio
async def test_raining_preserves_forecast_fields():
    """When an adapter reports current rain, forecast fields are kept."""
    stops_at = datetime.now(timezone.utc) + timedelta(hours=3)
    dwd = _wd(
        precipitation_intensity=5.0,
        precipitation_next_30m=True,
        precipitation_amount_next_30m=3.0,
        precipitation_next_1h=True,
        precipitation_amount_next_1h=6.0,
        precipitation_stops_at=stops_at,
        source="dwd",
    )
    openmeteo = _wd(precipitation_intensity=5.5, source="openmeteo")
    buienradar = _wd(source="buienradar")

    with (
        patch(
            "app.routers.weather_data.fetch_wetterdienst_weather",
            new_callable=AsyncMock,
            return_value=dwd,
        ),
        patch(
            "app.routers.weather_data.fetch_buienradar_weather",
            new_callable=AsyncMock,
            return_value=buienradar,
        ),
        patch(
            "app.routers.weather_data.fetch_openmeteo_weather",
            new_callable=AsyncMock,
            return_value=openmeteo,
        ),
    ):
        result = await get_weather_data(lat=50.0, lon=9.0)

    assert result.precipitation_now is True
    assert result.precipitation_next_30m is True
    assert result.precipitation_amount_next_30m == 3.0
    assert result.precipitation_next_1h is True
    assert result.precipitation_amount_next_1h == 6.0
    assert result.precipitation_stops_at == stops_at


# ---------------------------------------------------------------------------
# _pick_by_source_order — wind prefers DWD over model data
# ---------------------------------------------------------------------------


def _wd_single(field: str, value: float | None, source: str) -> WeatherData:
    wd = WeatherData(**{field: value})
    wd.stations.append(
        WeatherStation(
            source=source, name="test", lat=50.0, lon=9.0, time=datetime.now(timezone.utc)
        )
    )
    return wd


class TestPickBySourceOrder:
    """Wind fields use source priority: DWD > Open-Meteo > Buienradar."""

    def test_dwd_wins_over_others(self):
        dwd = _wd_single("wind_speed", 5.0, "dwd")
        bu = _wd_single("wind_speed", 12.0, "buienradar")
        om = _wd_single("wind_speed", 3.0, "openmeteo")
        order = ("dwd", "openmeteo", "buienradar")
        assert _pick_by_source_order(dwd, bu, om, "wind_speed", order) == 5.0

    def test_falls_back_to_openmeteo_when_dwd_missing(self):
        dwd = _wd_single("wind_speed", None, "dwd")
        bu = _wd_single("wind_speed", 12.0, "buienradar")
        om = _wd_single("wind_speed", 8.0, "openmeteo")
        order = ("dwd", "openmeteo", "buienradar")
        assert _pick_by_source_order(dwd, bu, om, "wind_speed", order) == 8.0

    def test_falls_back_to_buienradar_when_all_else_missing(self):
        dwd = _wd_single("wind_gust", None, "dwd")
        bu = _wd_single("wind_gust", 15.0, "buienradar")
        om = _wd_single("wind_gust", None, "openmeteo")
        order = ("dwd", "openmeteo", "buienradar")
        assert _pick_by_source_order(dwd, bu, om, "wind_gust", order) == 15.0

    def test_all_none_returns_none(self):
        dwd = _wd_single("wind_speed", None, "dwd")
        bu = _wd_single("wind_speed", None, "buienradar")
        om = _wd_single("wind_speed", None, "openmeteo")
        order = ("dwd", "openmeteo", "buienradar")
        assert _pick_by_source_order(dwd, bu, om, "wind_speed", order) is None

    def test_stale_dwd_falls_back_to_freshest(self):
        """When DWD data is older than 30 min, skip to next source."""
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        dwd = _wd_single("wind_speed", 5.0, "dwd")
        dwd.stations[0].time = old_time
        bu = _wd_single("wind_speed", 12.0, "buienradar")
        om = _wd_single("wind_speed", 8.0, "openmeteo")
        order = ("dwd", "openmeteo", "buienradar")
        assert _pick_by_source_order(dwd, bu, om, "wind_speed", order) == 8.0
