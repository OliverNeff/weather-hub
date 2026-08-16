"""Weather warnings endpoint — active DWD CAP alerts for a location."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app.adapter.warnings import fetch_warnings
from app.models.alerts import AlertsResponse
from app.models.weather_data import Alert

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/weather/alerts", tags=["weather-alerts"])

_SEVERITY_RANK = {"Extreme": 3, "Severe": 2, "Moderate": 1, "Minor": 0}
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def _sort_alerts(alerts: list[Alert]) -> list[Alert]:
    """Most severe first; within a severity, earliest expiry first."""
    return sorted(
        alerts,
        key=lambda a: (-_SEVERITY_RANK.get(a.severity, -1), a.expires or _FAR_FUTURE),
    )


@router.get("", response_model=AlertsResponse)
async def get_weather_alerts(
    lat: float = Query(
        ..., description="Latitude of the location (decimal degrees)", ge=-90, le=90
    ),
    lon: float = Query(
        ..., description="Longitude of the location (decimal degrees)", ge=-180, le=180
    ),
) -> AlertsResponse:
    """Active DWD weather warnings (CAP alerts) for the location.

    Lightweight endpoint that returns only warnings — no merged weather
    data — for consumers that display alerts independently of the
    weather response (e.g. pixel displays). On a DWD download failure
    an empty list is returned (200), never an error.
    """
    data = await fetch_warnings(lat, lon)
    alerts = _sort_alerts(data.alerts)
    return AlertsResponse(count=len(alerts), alerts=alerts)
