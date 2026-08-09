"""Home Assistant MQTT Discovery configuration for Weather Hub."""

import logging
import os
from typing import Any

from app.mqtt import MQTTClient, StubClient

logger = logging.getLogger(__name__)

DISCOVERY_PREFIX = os.environ.get("HA_DISCOVERY_PREFIX", "homeassistant").strip() or "homeassistant"

DEVICE_INFO = {
    "identifiers": ["weather-hub"],
    "name": "Weather Hub",
    "manufacturer": "Weather Hub",
    "model": "MQTT",
}


def _state_topic() -> str:
    """Return the configured state topic (defaults to weather-hub/state)."""
    return os.environ.get("MQTT_TOPIC", "weather-hub/state").strip() or "weather-hub/state"


def _base_config(state_topic: str, unique_id: str, name: str) -> dict[str, Any]:
    """Common fields for every discovery payload."""
    return {
        "state_topic": state_topic,
        "unique_id": unique_id,
        "name": name,
        "device": DEVICE_INFO,
    }


def build_weather_config(state_topic: str) -> tuple[str, dict[str, Any]]:
    """Weather entity — uses HA weather component attributes."""
    return (
        f"{DISCOVERY_PREFIX}/weather/weather-hub/config",
        {
            **_base_config(state_topic, "weather-hub", "Weather Hub"),
            "device_class": "weather",
            "value_template": "{{ value_json.status }}",
            "temperature_attribute": "temperature",
            "wind_speed_attribute": "wind_speed",
            "precipitation_intensity_attribute": "precipitation_intensity",
            "uv_index_attribute": "uv_index",
            "cloud_cover_attribute": "cloud_cover",
            "attributes_template": "{{ value_json | tojson }}",
        },
    )


def build_sensor_config(
    entity_id: str,
    field: str,
    *,
    name: str,
    unit_of_measurement: str | None = None,
    device_class: str | None = None,
    state_class: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Generic sensor builder."""
    st = _state_topic()
    return (
        f"{DISCOVERY_PREFIX}/sensor/weather-hub/{entity_id}/config",
        {
            **_base_config(st, f"weather-hub-{entity_id}", name),
            "value_template": f"{{{{ value_json.{field} if value_json.{field} is not none else 'unknown' }}}}",
            "unit_of_measurement": unit_of_measurement,
            "device_class": device_class,
            "state_class": state_class,
        },
    )


def build_binary_sensor_config(
    entity_id: str,
    field: str,
    *,
    name: str,
    device_class: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Generic binary sensor builder."""
    st = _state_topic()
    return (
        f"{DISCOVERY_PREFIX}/binary_sensor/weather-hub/{entity_id}/config",
        {
            **_base_config(st, f"weather-hub-{entity_id}", name),
            "value_template": f"{{{{ 'ON' if value_json.{field} else 'OFF' }}}}",
            "device_class": device_class,
        },
    )


async def discover_all(client: "MQTTClient | StubClient") -> None:
    """Publish all discovery configs with retain=true."""
    st = _state_topic()
    configs: list[tuple[str, dict[str, Any]]] = [
        # Weather entity
        build_weather_config(st),

        # Sensors
        build_sensor_config("temperature", "temperature", name="Temperature",
                            unit_of_measurement="°C", device_class="temperature",
                            state_class="measurement"),
        build_sensor_config("feels-like", "feels_like", name="Feels Like",
                            unit_of_measurement="°C", device_class="temperature",
                            state_class="measurement"),
        build_sensor_config("precipitation", "precipitation_intensity",
                            name="Precipitation Intensity",
                            unit_of_measurement="mm/h",
                            device_class="precipitation_intensity",
                            state_class="measurement"),
        build_sensor_config("wind-speed", "wind_speed", name="Wind Speed",
                            unit_of_measurement="m/s", device_class="wind_speed",
                            state_class="measurement"),
        build_sensor_config("wind-gust", "wind_gust", name="Wind Gust",
                            unit_of_measurement="m/s", device_class="wind_speed",
                            state_class="measurement"),
        build_sensor_config("uv-index", "uv_index", name="UV Index",
                            device_class="uv_index", state_class="measurement"),
        build_sensor_config("sun-elevation", "sun_elevation", name="Sun Elevation",
                            unit_of_measurement="°"),
        build_sensor_config("cloud-cover", "cloud_cover", name="Cloud Cover",
                            unit_of_measurement="%"),
        build_sensor_config("weather-code", "weather_code", name="Weather Code"),
        build_sensor_config("sunrise", "sunrise", name="Sunrise",
                            device_class="timestamp"),
        build_sensor_config("sunset", "sunset", name="Sunset",
                            device_class="timestamp"),
        build_sensor_config("status", "status", name="Status"),
        build_sensor_config("precip-stops-at", "precipitation_stops_at", name="Precipitation Stops At",
                            device_class="timestamp"),

        # Precipitation forecast amounts (mm)
        build_sensor_config("precip-amount-30m", "precipitation_amount_next_30m",
                            name="Precipitation Amount 30m",
                            unit_of_measurement="mm",
                            device_class="precipitation",
                            state_class="measurement"),
        build_sensor_config("precip-amount-1h", "precipitation_amount_next_1h",
                            name="Precipitation Amount 1h",
                            unit_of_measurement="mm",
                            device_class="precipitation",
                            state_class="measurement"),
        build_sensor_config("precip-amount-2h", "precipitation_amount_next_2h",
                            name="Precipitation Amount 2h",
                            unit_of_measurement="mm",
                            device_class="precipitation",
                            state_class="measurement"),

        # Precipitation forecast intensities (mm/h)
        build_sensor_config("precip-intensity-30m", "precipitation_intensity_next_30m",
                            name="Precipitation Intensity 30m",
                            unit_of_measurement="mm/h",
                            device_class="precipitation_intensity",
                            state_class="measurement"),
        build_sensor_config("precip-intensity-1h", "precipitation_intensity_next_1h",
                            name="Precipitation Intensity 1h",
                            unit_of_measurement="mm/h",
                            device_class="precipitation_intensity",
                            state_class="measurement"),
        build_sensor_config("precip-intensity-2h", "precipitation_intensity_next_2h",
                            name="Precipitation Intensity 2h",
                            unit_of_measurement="mm/h",
                            device_class="precipitation_intensity",
                            state_class="measurement"),

        # Binary sensors
        build_binary_sensor_config("precipitation-now", "precipitation_now",
                                   name="Precipitation Now", device_class="moisture"),
        build_binary_sensor_config("precip-30m", "precipitation_next_30m",
                                   name="Precipitation 30m", device_class="problem"),
        build_binary_sensor_config("precip-1h", "precipitation_next_1h",
                                   name="Precipitation 1h", device_class="problem"),
        build_binary_sensor_config("precip-2h", "precipitation_next_2h",
                                   name="Precipitation 2h", device_class="problem"),
    ]

    for topic, payload in configs:
        await client.publish(topic, payload, retain=True)
        logger.info("discovery: published %s", topic)

    logger.info("discovery: all %d entities published", len(configs))
