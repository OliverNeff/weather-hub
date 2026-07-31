import json
import math

from datetime import datetime, timezone
from buienradar.buienradar import get_data
from buienradar.constants import CONTENT, SUCCESS, RAINCONTENT
from app.models.weather_data import WeatherData


from app.models.weather_station import WeatherStation

# Max distance (degrees) for using Buienradar station data.
# Buienradar only has NL stations; for DE coords the nearest can be 200km+.
# Beyond this threshold the temperature/feels_like are irrelevant.
_MAX_STATION_DEG = 0.9  # ~100km

async def fetch_buienradar_weather(
        latitude: float,
        longitude: float
) -> WeatherData:
    """
    Holt Wetterdaten von Buienradar und mappt sie auf WeatherData.
    """

    result = get_data(latitude=latitude, longitude=longitude)

    if not result.get(SUCCESS):
        raise RuntimeError("Buienradar lieferte keinen SUCCESS-Status")

    # "content" ist ein JSON-String → parsen
    data = json.loads(result[CONTENT])

    # Wir nehmen die Station, die den Koordinaten am nächsten kommt
    station = __nearest_station(data, latitude, longitude)
    weather_station = WeatherStation(
        source="buienradar",
        name=station.get("stationname"),
        lat=station.get("lat"),
        lon=station.get("lon"),
        time=datetime.now(timezone.utc),
    )
    # --- Regenvorhersage (5-Minuten-Raster) ---
    raw_raindata = result.get(RAINCONTENT, "")
    raindata = __parse_raindata(raw_raindata)

    if not raindata:
        precipitation_next_30m = None
        precipitation_amount_next_30m = None
        precipitation_intensity_next_30m = None

        precipitation_next_1h = None
        precipitation_amount_next_1h = None
        precipitation_intensity_next_1h = None

        precipitation_next_2h = None
        precipitation_amount_next_2h = None
        precipitation_intensity_next_2h = None

    else:
        # 5‑Minuten‑Raster
        data_30m = raindata[:6]      # 6 Werte = 30 Minuten
        data_1h  = raindata[:12]     # 12 Werte = 60 Minuten
        data_2h  = raindata          # alle Werte = 120 Minuten

        # 30 Minuten
        precipitation_next_30m = any(v > 0 for v in data_30m)
        precipitation_amount_next_30m = sum(data_30m)
        precipitation_intensity_next_30m = max(data_30m)

        # 1 Stunde
        precipitation_next_1h = any(v > 0 for v in data_1h)
        precipitation_amount_next_1h = sum(data_1h)
        precipitation_intensity_next_1h = max(data_1h)

        # 2 Stunden
        precipitation_next_2h = any(v > 0 for v in data_2h)
        precipitation_amount_next_2h = sum(data_2h)
        precipitation_intensity_next_2h = max(data_2h)

    precip_mm = station.get("precipitation")

    # Buienradar has NL-only stations. For DE coords the nearest station can be
    # 200km+ away — its temperature/feels_like are irrelevant. Only rain radar
    # is useful across the border.
    station_dist_deg = math.sqrt(
        (station.get("lat", 0) - latitude) ** 2
        + (station.get("lon", 0) - longitude) ** 2
    )
    station_too_far = station_dist_deg > _MAX_STATION_DEG

    weather_data = WeatherData(
        time=datetime.now(timezone.utc),
        # Wind (Buienradar gibt m/s an → unverändert übernehmen)
        wind_speed=station.get("windspeed", None),
        wind_gust=station.get("windgusts", None),

        # Regen
        precipitation_intensity=precip_mm,

        # Regen – 30 Minuten
        precipitation_next_30m=precipitation_next_30m,
        precipitation_amount_next_30m=precipitation_amount_next_30m,
        precipitation_intensity_next_30m=precipitation_intensity_next_30m,
        # Regen – 1 Stunde
        precipitation_next_1h=precipitation_next_1h,
        precipitation_amount_next_1h=precipitation_amount_next_1h,
        precipitation_intensity_next_1h=precipitation_intensity_next_1h,
        # Regen – 2 Stunden
        precipitation_next_2h=precipitation_next_2h,
        precipitation_amount_next_2h=precipitation_amount_next_2h,
        precipitation_intensity_next_2h=precipitation_intensity_next_2h,
        # Temperatur — only use if station is close enough
        temperature=None if station_too_far else station.get("temperature", None),
        feels_like=None if station_too_far else station.get("feeltemperature", None),
        # UV → Buienradar liefert das nicht
        uv_index = None,
        # Sonnenstand → Buienradar liefert das nicht
        sun_elevation = None
    )
    weather_data.stations.append(weather_station)
    return weather_data

def __parse_raindata(raw: str) -> list[float]:
    if not raw:
        return []

    def to_mmh(code: int) -> float:
        return 0.0 if code == 0 else 10 ** ((code - 109) / 32)

    return [
        to_mmh(int(line.split("|")[0]))
        for line in raw.strip().split("\n")
        if "|" in line
    ]

def __nearest_station(
        data: dict, latitude: float, longitude: float
) -> dict:
    """
    Gibt die Messstation zurück, die den gegebenen Koordinaten am
    nächsten liegt.
    """
    stations = data["actual"]["stationmeasurements"]

    return min(
        stations,
        key=lambda s: (s["lat"] - latitude) ** 2 + (s["lon"] - longitude) ** 2
    )