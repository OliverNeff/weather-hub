from fastapi import APIRouter
from app.adapter.buinradar import fetch_buienradar_weather
from app.adapter.openmeteo import fetch_openmeteo_weather
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
    # DWD: current observation + forecast
    dwd = await fetch_wetterdienst_weather(
        latitude=lat,
        longitude=lon
    )

    # OpenMeteo: sun elevation
    om = await fetch_openmeteo_weather(
        latitude=lat,
        longitude=lon
    )

    # Merge: DWD is primary, OpenMeteo fills sun_elevation.
    dwd.sun_elevation = om.sun_elevation
    if om.stations:
        dwd.stations.extend(om.stations)

    return dwd