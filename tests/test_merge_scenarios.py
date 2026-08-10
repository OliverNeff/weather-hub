"""Scenario-driven integration tests for merge logic and transitions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.weather_data import WeatherData
from app.models.weather_station import WeatherStation


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


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


# ---------------------------------------------------------------------------
# Scenario: Rain is starting — radar detects it, stations haven't updated yet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_rain_starting_radar_only(client):
    """Buienradar radar reports precip, DWD stations report 0 (stale)."""
    dwd = _wd(
        temperature=20.0,
        precipitation_intensity=0.0,
        source="dwd",
    )
    bu = _wd(
        precipitation_intensity=1.5,
        source="buienradar",
    )
    om = _wd(
        weather_code=2,
        cloud_cover=40,
        source="openmeteo",
    )
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        # max() across adapters picks the radar value
        assert data["precipitation_intensity"] == 1.5
        assert data["precipitation_now"] is True
        assert data["status"] == "rainy"


# ---------------------------------------------------------------------------
# Scenario: Rain stopping — stations still report, radar is clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_rain_stopping_station_only(client):
    """DWD reports precip (delayed), radar and openmeteo show clear skies."""
    dwd = _wd(
        temperature=18.0,
        precipitation_intensity=2.0,
        source="dwd",
    )
    bu = _wd(
        precipitation_intensity=0.0,
        source="buienradar",
    )
    om = _wd(
        weather_code=0,
        sun_elevation=45.0,
        source="openmeteo",
    )
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        # max() picks DWD's stale data — conservative (over-reporting is safer)
        assert data["precipitation_intensity"] == 2.0
        assert data["precipitation_now"] is True
        # Status: max precip wins, status = rainy
        assert data["status"] == "rainy"


# ---------------------------------------------------------------------------
# Scenario: Rain stopping, all sources clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_rain_stopped_all_clear(client):
    """All adapters agree: no rain, clear sky."""
    dwd = _wd(temperature=20.0, precipitation_intensity=0.0, source="dwd")
    bu = _wd(precipitation_intensity=0.0, source="buienradar")
    om = _wd(weather_code=0, sun_elevation=45.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["precipitation_intensity"] == 0.0
        assert data["precipitation_now"] is False
        assert data["status"] == "sunny"


# ---------------------------------------------------------------------------
# Scenario: Cloudy to sunny transition (weather_code changes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_cloudy_to_sunny(client):
    """Cloud cover drops, weather_code goes from 3 to 0."""
    dwd = _wd(temperature=22.0, source="dwd")
    bu = _wd(source="buienradar")
    om = _wd(weather_code=0, sun_elevation=50.0, cloud_cover=5, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "sunny"


@pytest.mark.asyncio
async def test_scenario_sunny_to_cloudy(client):
    """Cloud cover increases, weather_code goes from 0 to 3."""
    dwd = _wd(temperature=20.0, source="dwd")
    bu = _wd(source="buienradar")
    om = _wd(weather_code=3, sun_elevation=50.0, cloud_cover=85, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "cloudy"


# ---------------------------------------------------------------------------
# Scenario: Rain to pouring transition (intensity increases)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_rain_to_pouring(client):
    """Light rain intensifies to heavy rain."""
    dwd = _wd(precipitation_intensity=3.0, source="dwd")
    bu = _wd(precipitation_intensity=7.0, source="buienradar")
    om = _wd(weather_code=63, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["precipitation_intensity"] == 7.0
        assert data["status"] == "pouring"


# ---------------------------------------------------------------------------
# Scenario: Night transition — sunny to clear-night
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_day_to_night_clear(client):
    """Same weather_code=0, sun_elevation drops below horizon."""
    dwd = _wd(temperature=15.0, source="dwd")
    bu = _wd(source="buienradar")
    om = _wd(weather_code=0, sun_elevation=-10.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "clear-night"


@pytest.mark.asyncio
async def test_scenario_night_to_day_sunny(client):
    """Same weather_code=0, sun_elevation rises above horizon."""
    dwd = _wd(temperature=16.0, source="dwd")
    bu = _wd(source="buienradar")
    om = _wd(weather_code=0, sun_elevation=5.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "sunny"


# ---------------------------------------------------------------------------
# Scenario: Wind picks up — calm to windy to windy-variant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_wind_picks_up(client):
    """Wind prefers DWD station measurements over remote Buienradar."""
    dwd = _wd(wind_speed=3.0, wind_gust=5.0, source="dwd")
    bu = _wd(wind_speed=12.0, wind_gust=18.0, source="buienradar")
    om = _wd(wind_speed=8.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        # DWD preferred over Buienradar (200km away) and Open-Meteo (model)
        assert data["wind_speed"] == 3.0
        assert data["wind_gust"] == 5.0
        assert data["status"] is None


# ---------------------------------------------------------------------------
# Scenario: Thunderstorm — model data takes precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_thunderstorm_starts(client):
    """No measured precip yet, but WMO code 96 (thunderstorm with rain)."""
    dwd = _wd(precipitation_intensity=0.0, source="dwd")
    bu = _wd(precipitation_intensity=0.0, source="buienradar")
    om = _wd(weather_code=96, cloud_cover=90, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "lightning-rainy"


# ---------------------------------------------------------------------------
# Scenario: Only one adapter works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_only_openmeteo_available(client):
    """DWD and Buienradar fail, only Open-Meteo returns data."""
    dwd_fail = RuntimeError("timeout")
    bu_fail = RuntimeError("timeout")
    om = _wd(
        temperature=22.0,
        feels_like=20.0,
        wind_speed=5.0,
        uv_index=4.0,
        sun_elevation=50.0,
        weather_code=2,
        cloud_cover=40,
        source="openmeteo",
    )
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", side_effect=dwd_fail),
        patch("app.routers.weather_data.fetch_buienradar_weather", side_effect=bu_fail),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["temperature"] == 22.0
        assert data["feels_like"] == 20.0
        assert data["wind_speed"] == 5.0
        assert data["status"] == "partlycloudy"
        sources = [s["source"] for s in data["stations"]]
        assert "openmeteo" in sources
        assert "dwd" not in sources


# ---------------------------------------------------------------------------
# Scenario: Forecast windows — rain in 1h but not 30m
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_rain_later(client):
    """No rain now — forecast fields are cleared to None.
    Without current rain, there's no active rain session to report."""
    dwd = _wd(
        temperature=20.0,
        precipitation_intensity=0.0,
        precipitation_next_30m=False,
        precipitation_amount_next_30m=0.0,
        precipitation_next_1h=True,
        precipitation_amount_next_1h=3.5,
        precipitation_intensity_next_1h=5.0,
        source="dwd",
    )
    bu = _wd(precipitation_intensity=0.0, source="buienradar")
    om = _wd(weather_code=2, sun_elevation=50.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["precipitation_now"] is False
        assert data["status"] == "partlycloudy"
        # No current rain -> all forecast fields cleared
        assert data["precipitation_next_30m"] is None
        assert data["precipitation_next_1h"] is None
        assert data["precipitation_amount_next_1h"] is None


# ---------------------------------------------------------------------------
# Scenario: Feels_like comes from same adapter as temperature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feels_like_bound_to_temperature_source(client):
    """DWD provides temperature+feels_like, Open-Meteo provides different values.
    Temperature is picked from freshest source; feels_like should follow.
    When both have same timestamp, order is preserved (DWD first in tuple)."""
    now = datetime.now(timezone.utc)
    dwd_wd = _wd(
        temperature=20.0,
        feels_like=18.0,
        source="dwd",
        station_time=now,
    )
    om_wd = _wd(
        temperature=22.0,
        feels_like=20.0,
        source="openmeteo",
        station_time=now,
    )
    bu_wd = _wd(source="buienradar", station_time=now)
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd_wd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu_wd),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om_wd),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        # Both have same timestamp; DWD comes first in the tuple
        assert data["temperature"] == 20.0
        # feels_like should be from same adapter as temperature
        assert data["feels_like"] == 18.0


# ---------------------------------------------------------------------------
# Scenario: UV index prefers Open-Meteo (accurate) over DWD (rough estimate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uv_from_openmeteo(client):
    """Open-Meteo provides accurate UV, DWD provides rough estimate from radiation.
    Open-Meteo's fresher timestamp makes its UV index win."""
    now = datetime.now(timezone.utc)
    dwd = _wd(uv_index=3.0, source="dwd", station_time=now - timedelta(minutes=10))
    bu = _wd(source="buienradar", station_time=now)
    om = _wd(uv_index=6.2, sun_elevation=50.0, source="openmeteo", station_time=now)
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        # Open-Meteo is freshest → its UV wins
        assert data["uv_index"] == 6.2


# ---------------------------------------------------------------------------
# Scenario: WMO rain fallback when DWD stations are stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_wmo_rain_overrides_stale_dwd(client):
    """DWD reports 0 precip (stale), but WMO code = 63 (moderate rain).
    Status should be rainy, not sunny."""
    dwd = _wd(temperature=18.0, precipitation_intensity=0.0, source="dwd")
    bu = _wd(precipitation_intensity=0.0, source="buienradar")
    om = _wd(weather_code=63, cloud_cover=80, sun_elevation=45.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["precipitation_now"] is False
        assert data["status"] == "rainy"  # WMO fallback


# ---------------------------------------------------------------------------
# Scenario: Precipitation_now edge case — zero across all adapters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_precipitation_now_all_zero(client):
    """All adapters report 0 — precipitation_now should be False."""
    dwd = _wd(precipitation_intensity=0.0, source="dwd")
    bu = _wd(precipitation_intensity=0.0, source="buienradar")
    om = _wd(precipitation_intensity=0.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["precipitation_now"] is False


@pytest.mark.asyncio
async def test_precipitation_now_none_from_all(client):
    """No adapter reports precipitation — precipitation_now stays None."""
    dwd = _wd(source="dwd")
    bu = _wd(source="buienradar")
    om = _wd(source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["precipitation_now"] is None


# ---------------------------------------------------------------------------
# Scenario: Fog during day vs night
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_fog_day(client):
    """Fog (WMO 45) during daytime."""
    dwd = _wd(source="dwd")
    bu = _wd(source="buienradar")
    om = _wd(weather_code=45, sun_elevation=5.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "fog"


@pytest.mark.asyncio
async def test_scenario_fog_night(client):
    """Fog (WMO 45) during night."""
    dwd = _wd(source="dwd")
    bu = _wd(source="buienradar")
    om = _wd(weather_code=45, sun_elevation=-5.0, source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["status"] == "fog"


# ---------------------------------------------------------------------------
# Scenario: Buienradar station too far — temperature excluded, radar used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_buienradar_too_far(client):
    """Buienradar reports no temperature (DE coords, NL station too far)
    but radar precipitation works cross-border."""
    dwd = _wd(temperature=20.0, source="dwd")
    bu = _wd(
        temperature=None,
        feels_like=None,
        precipitation_intensity=3.0,
        source="buienradar",
    )
    om = _wd(source="openmeteo")
    with (
        patch("app.routers.weather_data.fetch_wetterdienst_weather", return_value=dwd),
        patch("app.routers.weather_data.fetch_buienradar_weather", return_value=bu),
        patch("app.routers.weather_data.fetch_openmeteo_weather", return_value=om),
    ):
        resp = await client.get("/weather/data", params={"lat": 50.0, "lon": 9.0})
        data = resp.json()
        assert data["temperature"] == 20.0  # from DWD
        assert data["precipitation_intensity"] == 3.0  # from Buienradar radar
        assert data["status"] == "rainy"
