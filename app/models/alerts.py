from pydantic import BaseModel, Field

from app.models.weather_data import Alert


class AlertsResponse(BaseModel):
    """Response for GET /weather/alerts — active DWD warnings for a location."""

    count: int = Field(description="Number of active warnings (0 if none)")
    alerts: list[Alert] = Field(description="Active warnings, most severe first")
