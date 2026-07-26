from pydantic import BaseModel

class WeatherStation(BaseModel):
    source: str | None = None
    name: str | None = None
    lat: float | None = None
    lon: float | None = None