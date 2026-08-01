from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _make_minutely_15(start: datetime, precip_values: list[float | None]) -> dict:
    times = [(start + timedelta(minutes=15 * i)).isoformat() for i in range(len(precip_values))]
    return {"time": times, "precipitation": precip_values}


def _wd(
    temperature=None,
    feels_like=None,
    wind_speed=None,
    wind_gust=None,
    precipitation_intensity=None,
    precipitation_next_30m=None,
    precipitation_amount_next_30m=None,
    precipitation_intensity_next_30m=None,
    precipitation_next_1h=None,
    precipitation_amount_next_1h=None,
    precipitation_intensity_next_1h=None,
    uv_index=None,
    sun_elevation=None,
    weather_code=None,
    cloud_cover=None,
    source="dwd",
    station_time=None,
):
    wd = WeatherData(
        temperature=temperature,
        feels_like=feels_like,
        wind_speed=wind_speed,
        wind_gust=wind_gust,
        precipitation_intensity=precipitation_intensity,
        precipitation_next_30m=precipitation_next_30m,
        precipitation_amount_next_30m=precipitation_amount_next_30m,
        precipitation_intensity_next_30m=precipitation_intensity_next_30m,
        precipitation_next_1h=precipitation_next_1h,
        precipitation_amount_next_1h=precipitation_amount_next_1h,
        precipitation_intensity_next_1h=precipitation_intensity_next_1h,
        uv_index=uv_index,
        sun_elevation=sun_elevation,
        weather_code=weather_code,
        cloud_cover=cloud_cover,
    )
    if station_time is None:
        station_time = datetime.now(timezone.utc)
    wd.stations.append(
        WeatherStation(
            source=source,
            name=f"Test {source}",
            lat=50.0,
            lon=9.0,
            time=station_time,
        )
    )
    return wd
