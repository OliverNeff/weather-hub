"""MQTT push client — publishes WeatherData to weather-hub/state."""

import json
import logging
import os
from typing import Any

from app.models.weather_data import WeatherData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MQTT config from env
# ---------------------------------------------------------------------------
MQTT_BROKER = os.environ.get("MQTT_BROKER", "").strip()
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "").strip() or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "").strip() or None
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "weather-hub")
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "weather-hub/state")

USE_MQTT = bool(MQTT_BROKER)

# ---------------------------------------------------------------------------
# Singleton — either real FastMQTT client or stub
# ---------------------------------------------------------------------------
_mqtt_client: "MQTTClient | StubClient | None" = None


class MQTTClient:
    """Wrapper around FastMQTT client."""

    def __init__(self) -> None:
        from fastmqtt import FastMQTT
        from fastmqtt.encoders import JsonEncoder

        self._client = FastMQTT(
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            username=MQTT_USERNAME,
            password=MQTT_PASSWORD,
            client_id=MQTT_CLIENT_ID,
            payload_encoder=JsonEncoder(),
        )
        self._connected = False

    async def connect(self) -> None:
        await self._client.connect()
        self._connected = True
        logger.info("mqtt: connected to %s:%s", MQTT_BROKER, MQTT_PORT)

    async def disconnect(self) -> None:
        await self._client.disconnect()
        self._connected = False
        logger.info("mqtt: disconnected")

    async def publish(self, topic: str, payload: Any) -> None:
        if not self._connected:
            logger.warning("mqtt: not connected, skipping publish to %s", topic)
            return
        await self._client.publish(topic, payload)
        logger.info("mqtt: published to %s (%d fields)", topic, len(payload) if isinstance(payload, dict) else "?")

    @property
    def is_connected(self) -> bool:
        return self._connected


class StubClient:
    """No-op MQTT client for dev — logs what would be published."""

    def __init__(self) -> None:
        self._connected = True
        logger.info("mqtt:stub mode — no broker configured")

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def publish(self, topic: str, payload: Any) -> None:
        summary = json.dumps(payload, default=str)[:200]
        logger.info("mqtt:stub — would publish to %s: %s", topic, summary)

    @property
    def is_connected(self) -> bool:
        return True


def set_client(client: "MQTTClient | StubClient") -> None:
    """Set the global MQTT client (called from lifespan)."""
    global _mqtt_client
    _mqtt_client = client


def _payload_from_weather(data: WeatherData) -> dict[str, Any]:
    """Convert WeatherData to dict for MQTT payload (all fields)."""
    payload = data.model_dump(mode="json", exclude_none=True)
    # Convert nested station models to dicts
    payload["stations"] = [
        {"source": s.source, "name": s.name, "lat": s.lat, "lon": s.lon, "time": str(s.time) if s.time else None}
        for s in data.stations or []
    ]
    return payload


async def publish_weather(data: WeatherData) -> None:
    """Publish WeatherData via MQTT to weather-hub/state."""
    client = _mqtt_client
    if client is None:
        logger.warning("mqtt: client not initialized, skipping publish")
        return

    payload = _payload_from_weather(data)
    await client.publish(MQTT_TOPIC, payload)
