from fastapi import APIRouter
from app.adapter.buinradar import fetch_buienradar_weather
from app.adapter.wetterdienst_dwd import fetch_wetterdienst_weather
from app.models.weather_data import WeatherData

router = APIRouter(
    prefix="/weather/data",
    tags=["weather-data"]
)

@router.get("", response_model=WeatherData)
async def get_weather_data(
    lat: float,
    lon: float
) -> WeatherData:
    return await fetch_wetterdienst_weather(
        latitude=lat,
        longitude=lon
    )