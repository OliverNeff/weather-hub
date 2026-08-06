import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from app.mqtt import MQTTClient, StubClient, set_client
from app.routers import weather_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = os.environ.get("MQTT_BROKER", "").strip()
    client: MQTTClient | StubClient
    if broker:
        client = MQTTClient()
        await client.connect()
    else:
        client = StubClient()
    set_client(client)
    yield
    await client.disconnect()


app = FastAPI(title="Weather Hub", lifespan=lifespan)
app.include_router(weather_data.router)
