from datetime import datetime, timezone

from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation


class TestWeatherStation:
    def test_create_station(self):
        ws = WeatherStation(
            source="dwd",
            name="Test Station",
            lat=50.0,
            lon=9.0,
            time=datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert ws.source == "dwd"
        assert ws.lat == 50.0
        assert ws.time.year == 2026

    def test_all_fields_optional(self):
        ws = WeatherStation()
        assert ws.source is None
        assert ws.name is None
        assert ws.lat is None
        assert ws.lon is None
        assert ws.time is None

    def test_serialization(self):
        ws = WeatherStation(source="openmeteo", name="computed", lat=49.0, lon=8.0)
        data = ws.model_dump()
        assert data["source"] == "openmeteo"


class TestWeatherData:
    def test_empty_construction(self):
        wd = WeatherData()
        assert wd.wind_speed is None
        assert wd.temperature is None
        assert wd.status is None
        assert wd.stations == []
        assert wd.weather_code is None
        assert wd.cloud_cover is None

    def test_full_construction(self):
        wd = WeatherData(
            wind_speed=5.0,
            wind_gust=10.0,
            precipitation_now=True,
            precipitation_intensity=2.5,
            temperature=20.0,
            feels_like=18.0,
            uv_index=6.0,
            sun_elevation=45.0,
            status="rainy",
            weather_code=61,
            cloud_cover=80,
        )
        assert wd.wind_speed == 5.0
        assert wd.precipitation_now is True
        assert wd.status == "rainy"
        assert wd.feels_like == 18.0

    def test_serialization_with_datetime(self):
        sunrise = datetime(2026, 8, 1, 5, 0, 0, tzinfo=timezone.utc)
        sunset = datetime(2026, 8, 1, 19, 0, 0, tzinfo=timezone.utc)
        wd = WeatherData(
            sunrise=sunrise,
            sunset=sunset,
            stations=[WeatherStation(source="dwd", name="Test", lat=50.0, lon=9.0)],
        )
        data = wd.model_dump()
        assert data["sunrise"] is not None
        assert len(data["stations"]) == 1
