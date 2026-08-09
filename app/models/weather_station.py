from datetime import datetime

from pydantic import BaseModel, Field


class WeatherStation(BaseModel):
    """Weather station that contributed data."""

    source: str | None = Field(
        default=None, description="Data provider: dwd, openmeteo, or buienradar"
    )
    name: str | None = Field(default=None, description="Name of the weather station")
    lat: float | None = Field(default=None, description="Station latitude (decimal degrees)")
    lon: float | None = Field(default=None, description="Station longitude (decimal degrees)")
    time: datetime | None = Field(default=None, description="Timestamp of the measurement (UTC)")
