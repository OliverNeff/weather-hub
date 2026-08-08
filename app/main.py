"""FastAPI app entry point with background weather timer."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from app.mqtt import MQTTClient, StubClient, publish_weather, set_client
from app.routers import weather_data
from app.routers.weather_data import get_weather_data

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "WARNING")),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# Timer interval matches MosMix cache TTL (configurable via MQTT_INTERVAL env var, default 10min)
_TIMER_INTERVAL = int(os.environ.get("MQTT_INTERVAL", "600"))


async def _weather_timer(lat: float, lon: float) -> None:
    """Background task: fetch weather every _TIMER_INTERVAL and publish via MQTT."""
    while True:
        await asyncio.sleep(_TIMER_INTERVAL)
        logger.info("timer: fetching weather for lat=%.2f lon=%.2f", lat, lon)
        try:
            result = await get_weather_data(lat=lat, lon=lon)
            await publish_weather(result)
            logger.info("timer: done")
        except Exception:
            logger.error("timer: fetch failed for lat=%.2f lon=%.2f", lat, lon, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    # MQTT client setup
    broker = os.environ.get("MQTT_BROKER", "").strip()
    client: MQTTClient | StubClient
    if broker:
        client = MQTTClient()
        await client.connect()
    else:
        client = StubClient()
    set_client(client)

    # Home Assistant MQTT Discovery (real broker only)
    if broker:
        from app.mqtt_discovery import discover_all
        await discover_all(client)
    else:
        logger.info("discovery: skipped (no MQTT broker configured)")

    # Start background timer if lat/lon configured
    task = None
    mqtt_lat = os.environ.get("MQTT_LAT", "").strip()
    mqtt_lon = os.environ.get("MQTT_LON", "").strip()
    if mqtt_lat and mqtt_lon:
        lat = float(mqtt_lat)
        lon = float(mqtt_lon)
        task = asyncio.create_task(_weather_timer(lat, lon))
        logger.info("timer: started (lat=%.2f, lon=%.2f, interval=%ds)", lat, lon, _TIMER_INTERVAL)
    else:
        logger.info("timer: disabled (MQTT_LAT/MQTT_LON not set)")

    yield

    # Cancel timer on shutdown
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        logger.info("timer: stopped")

    await client.disconnect()


app = FastAPI(title="Weather Hub", lifespan=lifespan)
app.include_router(weather_data.router)
