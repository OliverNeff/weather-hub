from datetime import datetime, timezone

import pytest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _make_weather_data(
    temperature=None,
    feels_like=None,
    wind_speed=None,
    wind_gust=None,
    precipitation_intensity=None,
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
        uv_index=uv_index,
        sun_elevation=sun_elevation,
        weather_code=weather_code,
        cloud_cover=cloud_cover,
    )
    if station_time is None:
        station_time = datetime.now(timezone.utc)
    wd.stations.append(WeatherStation(
        source=source,
        name=f"Test {source}",
        lat=50.0,
        lon=9.0,
        time=station_time,
    ))
    return wd


@pytest.mark.asyncio
async def test_endpoint_returns_200(client):
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=_make_weather_data(temperature=20.0, source="dwd")),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=_make_weather_data(source="buienradar")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=_make_weather_data(source="openmeteo")),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_endpoint_has_all_fields(client):
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=_make_weather_data(source="dwd")),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=_make_weather_data(source="buienradar")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=_make_weather_data(source="openmeteo")),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert "temperature" in data
        assert "wind_speed" in data
        assert "precipitation_intensity" in data
        assert "status" in data
        assert "stations" in data
        assert "uv_index" in data


@pytest.mark.asyncio
async def test_status_computed(client):
    wd = _make_weather_data(temperature=20.0, precipitation_intensity=3.0, source="dwd")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=wd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=_make_weather_data(source="buienradar")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=_make_weather_data(source="openmeteo")),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "rainy"


@pytest.mark.asyncio
async def test_status_sunny_when_clear(client):
    wd = _make_weather_data(temperature=25.0, weather_code=0, sun_elevation=45.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=_make_weather_data(source="dwd")),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=_make_weather_data(source="buienradar")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=wd),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "sunny"


@pytest.mark.asyncio
async def test_stations_from_all_adapters(client):
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=_make_weather_data(source="dwd")),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=_make_weather_data(source="buienradar")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=_make_weather_data(source="openmeteo")),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        sources = [s["source"] for s in data["stations"]]
        assert "dwd" in sources
        assert "buienradar" in sources
        assert "openmeteo" in sources


@pytest.mark.asyncio
async def test_one_adapter_failure_does_not_break(client):
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", side_effect=RuntimeError("boom")),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=_make_weather_data(temperature=20.0, source="buienradar")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=_make_weather_data(source="openmeteo")),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["temperature"] == 20.0
        sources = [s["source"] for s in data["stations"]]
        assert "buienradar" in sources
        assert "openmeteo" in sources


@pytest.mark.asyncio
async def test_all_adapters_failure_returns_empty(client):
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", side_effect=RuntimeError("a")),
        patch("app.routers.weather_data.fetch_buienradar_weather", side_effect=RuntimeError("b")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", side_effect=RuntimeError("c")),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["temperature"] is None
        assert data["status"] is None
        assert data["stations"] == []


@pytest.mark.asyncio
async def test_max_wind_speed_across_adapters(client):
    dwd = _make_weather_data(wind_speed=5.0, source="dwd")
    bu = _make_weather_data(wind_speed=12.0, source="buienradar")
    om = _make_weather_data(wind_speed=3.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["wind_speed"] == 12.0


@pytest.mark.asyncio
async def test_temperature_from_freshest_source(client):
    now = datetime.now(timezone.utc)
    dwd_wd = _make_weather_data(temperature=18.0, source="dwd", station_time=now)
    om_wd = _make_weather_data(temperature=22.0, source="openmeteo", station_time=now)
    bu_wd = _make_weather_data(temperature=20.0, source="buienradar", station_time=now)
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd_wd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu_wd),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om_wd),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["temperature"] is not None


@pytest.mark.asyncio
async def test_precipitation_now_true(client):
    wd = _make_weather_data(precipitation_intensity=2.5, source="dwd")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=wd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=_make_weather_data(source="buienradar")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=_make_weather_data(source="openmeteo")),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["precipitation_now"] is True


@pytest.mark.asyncio
async def test_precipitation_now_false(client):
    wd = _make_weather_data(precipitation_intensity=0.0, source="dwd")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=wd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=_make_weather_data(source="buienradar")),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=_make_weather_data(source="openmeteo")),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["precipitation_now"] is False