from fastapi import FastAPI
from app.routers import weather_data

app = FastAPI(title="Weather Hub")
app.include_router(weather_data.router)