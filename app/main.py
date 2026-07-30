import logging

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from app.routers import weather_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

app = FastAPI(title="Weather Hub")
app.include_router(weather_data.router)