from pydantic import BaseModel

class WeatherStation(BaseModel):
    name: str | None = None
    lat: float | None = None
    lon: float | None = None