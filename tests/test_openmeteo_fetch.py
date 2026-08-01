"""Mock-based tests for Open-Meteo adapter entry point."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from freezegun import freeze_time

from app.adapter.openmeteo import fetch_openmeteo_weather


def _make_openmeteo_response(
    frozen_now: datetime,
    temperature: float = 22.5,
    apparent_temperature: float = 21.0,
    wind_speed: float = 14.4,  # km/h -> 4.0 m/s
    wind_gusts: float = 28.8,  # km/h -> 8.0 m/s
    precip: float = 0.0,
    uv_index: float = 3.5,
    weather_code: int = 1,
    cloud_cover: int = 20,
    include_minutely_15: bool = True,
    include_hourly: bool = True,
    include_sunrise_sunset: bool = True,
    minutely_precip_values: list[float] | None = None,
) -> dict:
    """Build a mock Open-Meteo API response."""
    response: dict = {
        "current": {
            "time": frozen_now.isoformat(),
            "temperature_2m": temperature,
            "apparent_temperature": apparent_temperature,
            "wind_speed_10m": wind_speed,
            "wind_gusts_10m": wind_gusts,
            "precipitation": precip,
            "uv_index": uv_index,
            "weather_code": weather_code,
            "cloud_cover": cloud_cover,
        },
    }

    if include_minutely_15:
        times = [(frozen_now + timedelta(minutes=15 * i)).isoformat() for i in range(48)]
        if minutely_precip_values is None:
            minutely_precip_values = [0.0] * 48
        response["minutely_15"] = {
            "time": times,
            "precipitation": minutely_precip_values,
        }

    if include_hourly:
        times = [(frozen_now + timedelta(hours=i)).isoformat() for i in range(48)]
        response["hourly"] = {
            "time": times,
            "precipitation": [0.0] * 48,
            "uv_index": [0.0] * 48,
        }

    if include_sunrise_sunset:
        response["daily"] = {
            "sunrise": [(frozen_now - timedelta(hours=8)).isoformat()],
            "sunset": [(frozen_now + timedelta(hours=8)).isoformat()],
        }

    return response


def _mock_response(resp: dict) -> MagicMock:
    """Build a mock httpx Response that returns the given JSON."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = resp
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


FROZEN = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestFetchOpenMeteoWeather:
    async def test_happy_path(self):
        """Normal fetch returns mapped weather data."""
        resp = _make_openmeteo_response(FROZEN)

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.temperature == 22.5
        assert wd.feels_like == 21.0
        assert wd.wind_speed == 4.0  # 14.4 km/h -> 4.0 m/s
        assert wd.wind_gust == 8.0
        assert wd.uv_index == 3.5
        assert wd.weather_code == 1
        assert wd.cloud_cover == 20
        assert len(wd.stations) == 1
        assert wd.stations[0].source == "openmeteo"

    async def test_http_error_returns_empty(self):
        """HTTP error returns empty WeatherData."""
        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(side_effect=httpx.HTTPError("connection failed")),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.temperature is None
        assert len(wd.stations) == 1
        assert wd.stations[0].source == "openmeteo"

    async def test_http_status_error_returns_empty(self):
        """HTTP status error (4xx/5xx) returns empty WeatherData."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=MagicMock(status_code=404)
        )

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=mock_resp),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.temperature is None

    async def test_nan_temperature_becomes_none(self):
        """NaN temperature values become None."""
        resp = _make_openmeteo_response(FROZEN, temperature=float("nan"))

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.temperature is None

    async def test_missing_minutely_15_uses_hourly(self):
        """When minutely_15 is absent, hourly data is used for forecasts."""
        resp = _make_openmeteo_response(
            FROZEN,
            include_minutely_15=False,
            include_hourly=True,
        )

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.temperature == 22.5

    async def test_minutely_precip_converted_to_mmh(self):
        """Minutely precipitation values are converted to mm/h."""
        # First interval at now: 0.25 mm per 15min -> 1.0 mm/h
        precip_vals = [0.25] + [0.0] * 47
        resp = _make_openmeteo_response(
            FROZEN,
            precip=0.0,
            minutely_precip_values=precip_vals,
        )

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.precipitation_intensity == 1.0  # 0.25 * 4

    async def test_sunrise_sunset_parsed(self):
        """Sunrise and sunset are parsed from daily data."""
        resp = _make_openmeteo_response(FROZEN, include_sunrise_sunset=True)

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.sunrise is not None
        assert wd.sunset is not None
        assert wd.sun_elevation is not None

    async def test_empty_daily_no_sunrise(self):
        """Empty daily data returns None for sunrise/sunset."""
        resp = _make_openmeteo_response(FROZEN)
        resp["daily"] = {"sunrise": [], "sunset": []}

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.sunrise is None
        assert wd.sunset is None

    async def test_station_has_correct_coords(self):
        """Station coordinates match the requested coordinates."""
        resp = _make_openmeteo_response(FROZEN)

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(49.5, 8.7)

        assert wd.stations[0].lat == 49.5
        assert wd.stations[0].lon == 8.7
        assert wd.stations[0].name == "computed"

    async def test_no_uv_index_field(self):
        """Missing UV index returns None."""
        resp = _make_openmeteo_response(FROZEN)
        resp["current"].pop("uv_index")

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.uv_index is None

    async def test_precipitation_from_current_fallback(self):
        """When minutely_15 has no future data, falls back to current.precipitation."""
        # All minutely times are in the past
        past_times = [(FROZEN - timedelta(minutes=15 * (48 - i))).isoformat() for i in range(48)]
        resp = _make_openmeteo_response(FROZEN, precip=2.5)
        resp["minutely_15"] = {"time": past_times, "precipitation": [0.0] * 48}

        with (
            freeze_time(FROZEN),
            patch(
                "app.adapter.openmeteo._session.get",
                new=AsyncMock(return_value=_mock_response(resp)),
            ),
        ):
            wd = await fetch_openmeteo_weather(50.0, 9.0)

        assert wd.precipitation_intensity == 2.5
