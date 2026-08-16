"""Tests for the GET /weather/alerts endpoint."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.alerts import AlertsResponse
from app.models.weather_data import Alert, WeatherData


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _alert(event: str, severity: str, expires: datetime | None = None) -> Alert:
    return Alert(
        event=event,
        severity=severity,
        description=f"{event} text",
        area="Testgebiet",
        expires=expires,
    )


def _warnings_data(alerts: list[Alert]) -> WeatherData:
    wd = WeatherData()
    wd.alerts = alerts
    return wd


class TestAlertsEndpoint:
    async def test_returns_200_with_count_and_alerts(self, client):
        alerts = [
            _alert("STARKE HITZE", "Minor"),
            _alert("STARKES GEWITTER", "Moderate"),
        ]
        with patch(
            "app.routers.alerts.fetch_warnings", return_value=_warnings_data(alerts)
        ) as mock_fetch:
            resp = await client.get("/weather/alerts", params={"lat": 50.0, "lon": 9.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        # Sorted most-severe-first: STARKES GEWITTER (Moderate) before STARKE HITZE (Minor).
        assert [a["event"] for a in data["alerts"]] == ["STARKES GEWITTER", "STARKE HITZE"]
        mock_fetch.assert_awaited_once()

    async def test_sorted_most_severe_first(self, client):
        expires = datetime.now(timezone.utc) + timedelta(hours=2)
        alerts = [
            _alert("STARKE HITZE", "Minor", expires),
            _alert("STURM", "Extreme", expires),
            _alert("STARKWIND", "Severe", expires),
            _alert("GEWITTER", "Moderate", expires),
        ]
        with patch("app.routers.alerts.fetch_warnings", return_value=_warnings_data(alerts)):
            resp = await client.get("/weather/alerts", params={"lat": 50.0, "lon": 9.0})
        assert [a["severity"] for a in resp.json()["alerts"]] == [
            "Extreme",
            "Severe",
            "Moderate",
            "Minor",
        ]

    async def test_same_severity_soonest_expiry_first(self, client):
        now = datetime.now(timezone.utc)
        alerts = [
            _alert("HITZE", "Moderate", now + timedelta(hours=4)),
            _alert("GEWITTER", "Moderate", now + timedelta(hours=1)),
        ]
        with patch("app.routers.alerts.fetch_warnings", return_value=_warnings_data(alerts)):
            resp = await client.get("/weather/alerts", params={"lat": 50.0, "lon": 9.0})
        assert [a["event"] for a in resp.json()["alerts"]] == ["GEWITTER", "HITZE"]

    async def test_no_warnings_returns_empty(self, client):
        with patch("app.routers.alerts.fetch_warnings", return_value=_warnings_data([])):
            resp = await client.get("/weather/alerts", params={"lat": 50.0, "lon": 9.0})
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "alerts": []}

    async def test_response_model_is_alerts_response(self, client):
        with patch("app.routers.alerts.fetch_warnings", return_value=_warnings_data([])):
            resp = await client.get("/weather/alerts", params={"lat": 50.0, "lon": 9.0})
            parsed = AlertsResponse.model_validate(resp.json())
        assert parsed.count == 0
        assert parsed.alerts == []
