# Weather Hub

Weather Hub aggregiert Wetterdaten aus drei Quellen (DWD, Open-Meteo, Buienradar) und stellt sie als **REST-API**, **MQTT-Push** und **Home Assistant MQTT Discovery** bereit.

Alle drei Datenanbieter werden parallel abgefragt — ein einzelner Anbieterausfall beeintraechtigt nicht die gesamte Antwort. Die gemergten Daten lassen sich als REST-Endpunkt abfragen oder automatisch per MQTT an einen Broker pushen, wo Home Assistant sie via MQTT Discovery automatisch erkennt.

## Docker

### Schnellstart

```bash
docker run -d --name weather-hub -p 8000:8000 \
  -e MQTT_LAT=49.87 -e MQTT_LON=8.93 \
  -e MQTT_BROKER=mqtt://your-broker:1883 \
  -e LOG_LEVEL=WARNING \
  oliverneff/weather-hub:latest
```

### Docker Compose

Erstelle eine `docker-compose.yml`:

```yaml
services:
  weather-hub:
    image: oliverneff/weather-hub:latest
    ports:
      - "8000:8000"
    environment:
      MQTT_LAT: "49.87"
      MQTT_LON: "8.93"
      MQTT_BROKER: "mqtt://your-broker:1883"
      LOG_LEVEL: "WARNING"
```

Oder mit `.env`-Datei:

```yaml
services:
  weather-hub:
    image: oliverneff/weather-hub:latest
    ports:
      - "8000:8000"
    env_file:
      - .env
```

```bash
docker compose up -d
```

### Eigenes Image bauen

```bash
git clone https://github.com/OliverNeff/weather-hub.git
cd weather-hub
docker compose up --build
```

## Lokal entwickeln

```bash
git clone https://github.com/OliverNeff/weather-hub.git
cd weather-hub
uv sync
uv run uvicorn app.main:app --reload
```

Die OpenAPI-Dokumentation ist unter `http://127.0.0.1:8000/docs` erhaeltbar.

## REST-API

### Wetterdaten abfragen

```
GET /weather/data?lat=<Breitengrad>&lon=<Laengengrad>
```

Liefert aktuelle Wetterbedingungen sowie Niederschlagsvorhersage fuer die naechsten 30 Minuten, 1 und 2 Stunden als JSON.

```bash
curl "http://127.0.0.1:8000/weather/data?lat=49.87&lon=8.93"
```

### Antwort-Schema

| Feld | Typ | Beschreibung |
|---|---|---|
| **Wind** | | |
| `wind_speed` | `float \| null` | Aktuelle Windgeschwindigkeit in m/s |
| `wind_gust` | `float \| null` | Maximale Windböe in m/s |
| **Niederschlag (aktuell)** | | |
| `precipitation_now` | `bool \| null` | `true`, wenn aktuell Niederschlag gemessen wird |
| `precipitation_intensity` | `float \| null` | Aktuelle Regenintensitaet in mm/h |
| **Niederschlag (Vorhersage)** | | |
| `precipitation_next_30m` | `bool \| null` | Regen in den naechsten 30 Minuten erwartet |
| `precipitation_amount_next_30m` | `float \| null` | Erwartete Niederschlagsmenge in mm (naechste 30 Min) |
| `precipitation_intensity_next_30m` | `float \| null` | Staerkste erwartete Intensitaet in mm/h (naechste 30 Min) |
| `precipitation_next_1h` | `bool \| null` | Regen in der naechsten Stunde erwartet |
| `precipitation_amount_next_1h` | `float \| null` | Erwartete Niederschlagsmenge in mm (naechste 1 Std) |
| `precipitation_intensity_next_1h` | `float \| null` | Staerkste erwartete Intensitaet in mm/h (naechste 1 Std) |
| `precipitation_next_2h` | `bool \| null` | Regen in den naechsten 2 Stunden erwartet |
| `precipitation_amount_next_2h` | `float \| null` | Erwartete Niederschlagsmenge in mm (naechste 2 Std) |
| `precipitation_intensity_next_2h` | `float \| null` | Staerkste erwartete Intensitaet in mm/h (naechste 2 Std) |
| **Niederschlag (Endzeit)** | | |
| `precipitation_stops_at` | `datetime \| null` | Geschaeetzter Zeitpunkt, wann aktueller Regen aufhoert (UTC) |
| **Temperatur** | | |
| `temperature` | `float \| null` | Aktuelle Temperatur in °C |
| `feels_like` | `float \| null` | Gefuehlte Temperatur in °C |
| **UV / Sonne** | | |
| `uv_index` | `float \| null` | UV-Index (0-16+) |
| `sun_elevation` | `float \| null` | Sonnenhoehe in Grad (negativ, wenn unter dem Horizont) |
| `sunrise` | `datetime \| null` | Sonnenaufgang heute (UTC) |
| `sunset` | `datetime \| null` | Sonnenuntergang heute (UTC) |
| **Wetter-Status** | | |
| `status` | `str \| null` | Home Assistant-kompatibler Wetterstatus |
| `weather_code` | `int \| null` | WMO-Wettercode (0-96) aus Open-Meteo |
| `cloud_cover` | `int \| null` | Bewoelkungsgrad in % (0-100) aus Open-Meteo |
| **Stationen** | | |
| `stations` | `list[WeatherStation]` | Alle beteiligten Wetterstationen |

Alle Felder sind optional — `null` bedeutet, dass der Anbieter keine Daten fuer dieses Feld lieferte.

## Konfiguration

Alle Einstellungen erfolgen ueber Umgebungsvariablen. Erstelle optional eine `.env`-Datei:

| Umgebungsvariable | Standard | Beschreibung |
|---|---|---|
| `DWD_CACHE` | `true` | DWD Disk-Cache aktivieren. Deaktivieren fuer immer frische Daten (`false`) |
| `LOG_LEVEL` | `WARNING` | Log-Level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. `INFO` fuer Entwicklungsmodus, `WARNING` fuer Produktion |
| `MQTT_BROKER` | *(leer)* | MQTT-Broker-URL (z.B. `mqtt://broker:1883`). Leer = Stub-Modus (nur Logausgabe) |
| `MQTT_PORT` | `1883` | MQTT-Broker-Port |
| `MQTT_USERNAME` | *(leer)* | MQTT-Username fuer Authentifizierung |
| `MQTT_PASSWORD` | *(leer)* | MQTT-Passwort fuer Authentifizierung |
| `MQTT_CLIENT_ID` | `weather-hub` | MQTT-Client-ID am Broker |
| `MQTT_TOPIC` | `weather-hub/state` | MQTT-Topic fuer Wetterdaten-State |
| `MQTT_LAT` | *(leer)* | Breitengrad fuer MQTT-Push-Timer. Timer startet nur, wenn dies und `MQTT_LON` gesetzt sind |
| `MQTT_LON` | *(leer)* | Laengengrad fuer MQTT-Push-Timer |
| `MQTT_INTERVAL` | `600` | Push-Interval in Sekunden (Standard: 10 Minuten, entspricht DWD MosMix Cache TTL) |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | Prefix fuer Home Assistant MQTT Discovery Topics |

### Beispiel `.env`

```env
DWD_CACHE=true
LOG_LEVEL=INFO
MQTT_BROKER=mqtt://your-broker:1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_LAT=49.87
MQTT_LON=8.93
MQTT_INTERVAL=600
HA_DISCOVERY_PREFIX=homeassistant
```

## MQTT-Push

Wenn `MQTT_LAT` und `MQTT_LON` gesetzt sind, startet ein Background-Timer, der im konfigurierten Intervall (Standard: 10 Minuten) die Wetterdaten fuer den Standort abruft und die gemergten Ergebnisse aller drei Anbieter per MQTT publiziert.

Das Payload enthaelt alle Felder des WeatherData-Schemas und ist fuer Home Assistant kompatibel. Das Standard-Topic `weather-hub/state` laesst sich ueber `MQTT_TOPIC` anpassen — beispielsweise fuer mehrere Standorte (`weather-hub/garten/state`).

Ohne `MQTT_BROKER` laeuft ein Stub-Client, der die MQTT-Nachricht im Log anzeigt — ideal fuer die Entwicklung ohne MQTT-Broker. Ohne `MQTT_LAT`/`MQTT_LON` startet kein Timer.

### Beispiel-Payload

```json
{
  "temperature": 21.4,
  "feels_like": 19.8,
  "wind_speed": 5.8,
  "wind_gust": 8.2,
  "precipitation_now": false,
  "precipitation_intensity": 0.0,
  "precipitation_next_30m": false,
  "precipitation_next_1h": false,
  "precipitation_next_2h": false,
  "uv_index": 0,
  "sun_elevation": -5.2,
  "status": "clear-night",
  "weather_code": 0,
  "cloud_cover": 5,
  "sunrise": "2025-01-15T07:12:00Z",
  "sunset": "2025-01-15T16:45:00Z"
}
```

## Home Assistant MQTT Discovery

Wenn ein MQTT-Broker konfiguriert ist (`MQTT_BROKER`), publiziert Weather Hub beim Start automatisch [MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) Konfigurationen. Home Assistant erkennt das Gerat „Weather Hub" mit 24 Entitaeten automatisch — ohne manuelle Konfiguration.

### Erkannte Entitaeten

| Typ | Entity ID | Daten |
|-----|-----------|-------|
| **Weather** | `weather.weather_hub` | Status, Temperatur, Wind, Niederschlag, UV, Bewoelkung |
| **Sensor** | `sensor.temperature` | Aktuelle Temperatur (°C) |
| **Sensor** | `sensor.feels_like` | Gefuehlte Temperatur (°C) |
| **Sensor** | `sensor.precipitation_intensity` | Aktuelle Regenintensitaet (mm/h) |
| **Sensor** | `sensor.wind_speed` | Windgeschwindigkeit (m/s) |
| **Sensor** | `sensor.wind_gust` | Windboene (m/s) |
| **Sensor** | `sensor.uv_index` | UV-Index (0-16+) |
| **Sensor** | `sensor.sun_elevation` | Sonnenhoehe (Grad) |
| **Sensor** | `sensor.cloud_cover` | Bewoelkungsgrad (%) |
| **Sensor** | `sensor.weather_code` | WMO-Wettercode |
| **Sensor** | `sensor.status` | HA-Wetterstatus-String |
| **Sensor** | `sensor.sunrise` | Sonnenaufgang (Timestamp) |
| **Sensor** | `sensor.sunset` | Sonnenuntergang (Timestamp) |
| **Sensor** | `sensor.precipitation_stops_at` | Regen endet um (Timestamp) |
| **Sensor** | `sensor.precipitation_amount_30m` | Niederschlagsmenge 30 Min (mm) |
| **Sensor** | `sensor.precipitation_amount_1h` | Niederschlagsmenge 1 Std (mm) |
| **Sensor** | `sensor.precipitation_amount_2h` | Niederschlagsmenge 2 Std (mm) |
| **Sensor** | `sensor.precipitation_intensity_30m` | Niederschlagsintensitaet 30 Min (mm/h) |
| **Sensor** | `sensor.precipitation_intensity_1h` | Niederschlagsintensitaet 1 Std (mm/h) |
| **Sensor** | `sensor.precipitation_intensity_2h` | Niederschlagsintensitaet 2 Std (mm/h) |
| **Binary** | `binary_sensor.precipitation_now` | Aktueller Niederschlag (moisture) |
| **Binary** | `binary_sensor.precipitation_30m` | Regen in 30 Min (problem) |
| **Binary** | `binary_sensor.precipitation_1h` | Regen in 1 Std (problem) |
| **Binary** | `binary_sensor.precipitation_2h` | Regen in 2 Std (problem) |

Alle Entitaeten sind unter einem Gerat gruppiert: „Weather Hub". Discovery Messages verwenden `retain=true` und ueberleben Broker Restarts.

In Stub-Modus (kein Broker) wird Discovery uebersprungen.

## Datenanbieter

| Anbieter | Starkpunkte | Abdeckung | Antwortzeit |
|---|---|---|---|
| **Open-Meteo** | Genauer UV-Index, Sonnenauf-/untergang, gefuehlte Temperatur, weltweite Abdeckung | Weltweit | < 1s |
| **DWD** | Deutsche Stationsdaten (Temperatur, Wind, Niederschlag), MosMix-Vorhersage | Deutschland | ~7s (kalt), ~0,3s (gecacht) |
| **Buienradar** | Radar-basierte Niederschlagsvorhersage (30 Min/1 Std/2 Std) in 5-Minuten-Schritten | NL-Stationen + DE-Radar | < 1s |

### DWD

Kombiniert zwei Datenquellen:

- **Beobachtung** — Temperatur, Wind, Niederschlag aus dem `recent`-Zeitraum (10-Minuten-Aeolung). Jeder Parameter wird aus einem eigenen Pool der naechsten Stationen abgerufen — Niederschlag aus dem naechsten Regenmesser, Wind aus dem naechsten Anemometer. Viele kleine DWD-Stationen melden nur Niederschlag, daher die Trennung. Die vier Parameter-Anfragen laufen parallel via `ThreadPoolExecutor`. Veraltete Messungen (>2h) werden verworfen, das Ergebnis auf die 3 naechsten Stationen mit gueltigen Daten beschraenkt.

- **Vorhersage** — Stündliche MosMix Small-Prognosen fuer Niederschlag und Strahlung. Bildet Fenster fuer 30 Min/1 Std/2 Std und mittelt die Niederschlagswerte. Der UV-Index wird aus Globalstrahlung mit `* 0.019` approximiert und auf 0-16 begrenzt (Grobe Schaetzung).

- **Caching** — Zwei Ebenen: In-Memory MosMix-Cache (10 Minuten TTL) und optionaler fsspec Disk-Cache (steuert `DWD_CACHE`). Wenn aktiviert, beschleunigt wiederholte Anfragen von ~7s auf ~0,3s.

### Open-Meteo

Plain HTTP/JSON Client (`httpx`), ohne FFI-Bindings. Liefert aktuelle Temperatur, gefuehlte Temperatur, Windgeschwindigkeit, Windböen, Niederschlag, UV-Index (genau), Sonnenaufgang und Sonnenuntergang. Die Sonnenhoehe wird mit der NOAA-Formel berechnet — negative Werte bedeuten Nacht.

### Buienradar

- **Radar-Raster** fuer Niederschlagsvorhersage (30 Min/1 Std/2 Std) in 5-Minuten-Schritten — funktioniert auch fuer Deutschland (Raster-basiert, nicht stationsbasiert)
- **Stationsmessungen** (Temperatur, Wind, aktueller Niederschlag) sind auf die Niederlande beschraenkt
- Stationsdaten werden ignoriert, wenn die naechste NL-Station >100km entfernt liegt — Radar-Daten funktionieren jedoch grenzueberschreitend

## Merge-Strategie

Nachdem alle Datenanbieter ihre Ergebnisse geliefert haben, fusioniert der Router die Werte:

| Felder | Strategie | Begründung |
|---|---|---|
| Windgeschwindigkeit / Böen | DWD > Open-Meteo > Buienradar | DWD = echte Stationsdaten (frisch <30min); sonst Open-Meteo (Modell); Buienradar = NL-only |
| Niederschlag + Vorhersage | `max()` ueber alle Anbieter | Verpasster Regen ist schlimmer als ueberberichterstattung |
| Gefuehlte Temperatur | Gleicher Anbieter wie Temperatur | Haeelt Temperatur und gefuehlte Temperatur konsistent |
| Temperatur | Frischeste Quelle (neuster Zeitstempel zuerst) | Neueste Daten sind am genauesten |
| UV-Index | Frischeste Quelle | Open-Meteo liefert genaue Echtzeit-UV; DWD ist eine grobe Schaeztung |
| Sonnenauf-/untergang / Sonnenhoehe | Frischeste Quelle | Nur Open-Meteo liefert diese Daten |
| Stationen | Alle Stationen aller Anbieter mit Daten | Zeigt, welche Quellen mitgewirkt haben |

Wenn aktuell kein Niederschlag gemessen wird, wird `precipitation_stops_at` auf `null` gesetzt — ohne aktive Regen-Phase ist ein "Endezeitpunkt" nicht sinnvoll. Die Vorhersage-Felder (`precipitation_next_*`) bleiben unverändert vom Adapter: `false` bedeutet "kein Regen erwartet" (Daten verfügbar), `null` bedeutet "keine Daten".

## Home Assistant Status-Werte

Das `status`-Feld mappt auf die vordefinierten Werte der HA Weather Integration:

| Status | Bedingung |
|---|---|
| `sunny` | Wettercode 0-1 tagsueber (Sonne über Horizont) |
| `clear-night` | Wettercode 0-1 nachts (Sonne unter Horizont) |
| `partlycloudy` | Wettercode 2 oder Bewoelkung 11-50 % |
| `cloudy` | Wettercode 3-44 oder Bewoelkung >50 % |
| `fog` | Wettercode 45, 48 |
| `rainy` | Niederschlag >0 mm/h oder Wettercode 51-65, 80-82 |
| `pouring` | Niederschlagsintensitaet >5 mm/h |
| `snowy` | Wettercode 71, 73, 75, 77 (temp >2 °C), 85 |
| `snowy-rainy` | Wettercode 66, 67, 86 (Gefrierender Regen) |
| `hail` | Wettercode 77 bei Temperatur <=2 °C |
| `lightning` | Wettercode 95 (Gewitter ohne regen) |
| `lightning-rainy` | Wettercode 96 (Gewitter mit regen) |
| `windy` | Windgeschwindigkeit >=10 m/s |
| `windy-variant` | Windgeschwindigkeit >=15 m/s |

Die Berechnung nutzt Daten aller drei Anbieter: WMO-Codes aus Open-Meteo fuer nicht-messbare Bedingungen (Nebel, Schnee, Gewitter), gemergte Maxwerte fuer Niederschlag (alle Adapter), Wind aus DWD (Stationenprioritaet), und Sonnenhoehe fuer Tag/Nacht-Erkennung.
