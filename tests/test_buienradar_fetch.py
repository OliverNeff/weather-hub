"""Mock-based tests for Buienradar adapter entry point."""

import json
from unittest.mock import patch

from app.adapter.buinradar import fetch_buienradar_weather


def _rain_line(code: int) -> str:
    """Build a single Buienradar rain data line (code|x format, split on |)."""
    return f"{code}|x"


def _make_buienradar_result(
    temperature: float = 22.5,
    feels_like: float = 21.0,
    windspeed: float = 3.5,
    windgusts: float = 7.0,
    precipitation: float = 0.0,
    rain_codes: list[int] | None = None,
    station_lat: float = 52.0,
    station_lon: float = 5.0,
) -> dict:
    """Build a mock Buienradar API response."""
    if rain_codes is None:
        rain_codes = [0] * 240  # 240 intervals = 20 hours of no rain

    rain_content = "\n".join(_rain_line(c) for c in rain_codes) + "\n"

    # Build proper JSON string for content field
    content = json.dumps(
        {
            "actual": {
                "stationmeasurements": [
                    {
                        "stationname": "Test Station",
                        "lat": station_lat,
                        "lon": station_lon,
                        "temperature": temperature,
                        "feeltemperature": feels_like,
                        "windspeed": windspeed,
                        "windgusts": windgusts,
                        "precipitation": precipitation,
                    }
                ]
            }
        }
    )

    return {
        "success": True,
        "content": content,
        "raincontent": rain_content,
    }


class TestFetchBuienradarWeather:
    async def test_happy_path_close_station(self):
        """Normal fetch with nearby station."""
        # First 30min of rain (6 intervals × 5min), rest dry
        rain_codes = [109] * 6 + [0] * 234
        result = _make_buienradar_result(
            temperature=20.0,
            windspeed=5.0,
            feels_like=18.0,
            rain_codes=rain_codes,
            station_lat=52.3,
            station_lon=5.3,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(52.3, 5.3)

        assert wd.temperature == 20.0
        assert wd.feels_like == 18.0
        assert wd.wind_speed == 5.0
        assert len(wd.stations) == 1
        assert wd.stations[0].source == "buienradar"

    async def test_no_rain(self):
        """Fetch with no rain at all — station too far for DE coords."""
        result = _make_buienradar_result(
            temperature=25.0,
            precipitation=0.0,
            rain_codes=[0] * 240,
            station_lat=52.0,
            station_lon=5.0,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(50.0, 9.0)

        # Station too far: no temp/feels_like
        assert wd.temperature is None
        assert wd.feels_like is None
        # Rain windows should be False/0.0 (data present but no rain)
        assert wd.precipitation_next_30m is False
        assert wd.precipitation_amount_next_30m == 0.0

    async def test_station_too_far_returns_no_temperature(self):
        """For German coords, NL station is too far — temperature should be None."""
        result = _make_buienradar_result(
            temperature=20.0,
            rain_codes=[0] * 240,
            station_lat=52.0,  # NL station
            station_lon=5.0,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(50.0, 9.0)  # DE coords, ~370km away

        assert wd.temperature is None
        assert wd.feels_like is None

    async def test_radar_precip_for_de_coords(self):
        """For DE coords, radar rain should still show as precipitation."""
        # First 5-min interval shows rain, station too far
        result = _make_buienradar_result(
            temperature=20.0,
            precipitation=0.0,
            rain_codes=[109] + [0] * 239,
            station_lat=52.0,
            station_lon=5.0,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(50.0, 9.0)

        # Station too far for temp, but radar rain visible
        assert wd.precipitation_intensity == 1.0

    async def test_success_false_raises_runtimeerror(self):
        """When Buienradar returns success=False, RuntimeError is raised."""
        result = {"success": False, "content": "", "raincontent": ""}

        import pytest

        with patch("app.adapter.buinradar.get_data", return_value=result):
            with pytest.raises(RuntimeError):
                await fetch_buienradar_weather(52.3, 5.3)

    async def test_empty_raindata(self):
        """Empty raincontent produces None for rain fields (no data at all)."""
        result = _make_buienradar_result(
            rain_codes=[],
            station_lat=52.3,
            station_lon=5.3,
        )
        result["raincontent"] = ""

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(52.3, 5.3)

        assert wd.precipitation_next_30m is None
        assert wd.precipitation_amount_next_30m is None

    async def test_forecast_windows(self):
        """Check 30m/1h/2h windows are computed correctly."""
        # 12 intervals of rain (1 hour worth), then zeros
        rain_codes = [109] * 12 + [0] * 228
        result = _make_buienradar_result(
            rain_codes=rain_codes,
            station_lat=52.3,
            station_lon=5.3,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(52.3, 5.3)

        assert wd.precipitation_next_30m is True
        assert wd.precipitation_next_1h is True
        assert wd.precipitation_amount_next_30m > 0
        assert wd.precipitation_amount_next_1h > 0

    async def test_station_info_populated(self):
        """WeatherStation is populated with correct info."""
        result = _make_buienradar_result(
            temperature=18.0,
            rain_codes=[0] * 240,
            station_lat=52.3,
            station_lon=5.3,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(52.3, 5.3)

        assert len(wd.stations) == 1
        station = wd.stations[0]
        assert station.source == "buienradar"
        assert station.name == "Test Station"
        assert station.lat == 52.3
        assert station.lon == 5.3

    async def test_wind_gust_from_station(self):
        """Wind gust is extracted from station data."""
        result = _make_buienradar_result(
            windgusts=12.5,
            rain_codes=[0] * 240,
            station_lat=52.3,
            station_lon=5.3,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(52.3, 5.3)

        assert wd.wind_gust == 12.5

    async def test_heavy_rain_high_code(self):
        """Code 141 = 10 mm/h for all intervals."""
        rain_codes = [141] * 240
        result = _make_buienradar_result(
            rain_codes=rain_codes,
            station_lat=52.3,
            station_lon=5.3,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(52.3, 5.3)

        assert wd.precipitation_next_30m is True
        assert wd.precipitation_intensity_next_30m == 10.0
        assert wd.precipitation_intensity_next_1h == 10.0

    async def test_2h_window_includes_all_2_hours(self):
        """Rain in second hour should show up in 2h window only."""
        # 12-24 intervals (1h-2h from now) have rain, first hour is dry
        rain_codes = [0] * 12 + [109] * 12 + [0] * 216
        result = _make_buienradar_result(
            rain_codes=rain_codes,
            station_lat=52.3,
            station_lon=5.3,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(52.3, 5.3)

        assert wd.precipitation_next_30m is False
        assert wd.precipitation_amount_next_30m == 0.0
        assert wd.precipitation_next_1h is False
        assert wd.precipitation_amount_next_1h == 0.0
        assert wd.precipitation_next_2h is True

    async def test_nearby_station_in_nl(self):
        """For NL coords close to station, temp should be returned."""
        result = _make_buienradar_result(
            temperature=15.0,
            feels_like=13.0,
            rain_codes=[0] * 240,
            station_lat=52.3,
            station_lon=5.3,
        )

        with patch("app.adapter.buinradar.get_data", return_value=result):
            wd = await fetch_buienradar_weather(52.3, 5.3)

        assert wd.temperature == 15.0
        assert wd.feels_like == 13.0
