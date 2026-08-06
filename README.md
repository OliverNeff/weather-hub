# Weather Hub

Weather Hub ist ein FastAPI-Mikrodienst, der Wetterdaten mehrerer Anbieter aggregiert und ueber eine einzige REST-Schnittstelle bereitstellt.

## Starten

```bash
uv sync
uv run uvicorn app.main:app --reload
curl "http://127.0.0.1:8000/weather/data?lat=49.87&lon=8.93"
```

Die [OpenAPI-Dokumentation](http://127.0.0.1:8000/docs) ist unter `/docs` erhaeltbar.

## Wetterdaten-Anfrage

```
GET /weather/data?lat=<Breitengrad>&lon=<Laengengrad>
```

Liefert aktuelle Wetterbedingungen sowie Niederschlagsvorhersage fuer die naechsten 30 Minuten, 1 und 2 Stunden als JSON.

### Antwort-Schema

| Feld | Typ | Beschreibung |
|---|---|---|
| **Wind** | | |
| `wind_speed` | `float \| null` | Aktuelle Windgeschwindigkeit in m/s |
| `wind_gust` | `float \| null` | Maximale Windböe in m/s |
| **Niederschlag (aktuell)** | | |
| `precipitation_now` | `bool \| null` | `true`, wenn aktuell Niederschlag gemessen wird |
| `precipitation_intensity` | `float \| null` | Aktuelle Regenintensität in mm/h |
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
| **Temperatur** | | |
| `temperature` | `float \| null` | Aktuelle Temperatur in °C |
| `feels_like` | `float \| null` | Gefuehlte Temperatur in °C |
| **UV / Sonne** | | |
| `uv_index` | `float \| null` | UV-Index (0–16+) |
| `sun_elevation` | `float \| null` | Sonnenhoehe in Grad (negativ, wenn unter dem Horizont) |
| `sunrise` | `datetime \| null` | Sonnenaufgang heute (UTC) |
| `sunset` | `datetime \| null` | Sonnenuntergang heute (UTC) |
| **Wetter-Status** | | |
| `status` | `str \| null` | Home Assistant-kompatibler Wetterstatus (siehe unten) |
| `weather_code` | `int \| null` | WMO-Wettercode (0–96) aus Open-Meteo |
| `cloud_cover` | `int \| null` | Bewölkungsgrad in % (0–100) aus Open-Meteo |
| **Stationen** | | |
| `stations` | `list[WeatherStation]` | Alle beteiligten Wetterstationen (siehe unten) |

Alle Felder sind optional — `null` bedeutet, dass der Anbieter keine Daten fuer dieses Feld lieferte.

### Home Assistant Status

Das `status`-Feld mappt auf die 14 vordefinierten Werte der Home Assistant Weather Integration:

| Status | Bedingung |
|---|---|
| `sunny` | Wettercode 0–1 tagsueber (Sonne über Horizont) |
| `clear-night` | Wettercode 0–1 nachts (Sonne unter Horizont) |
| `partlycloudy` | Wettercode 2 oder Bewoelkung 11–50 % |
| `cloudy` | Wettercode 3–44 oder Bewoelkung >50 % |
| `fog` | Wettercode 45, 48 |
| `rainy` | Messbarer Niederschlag >0 mm/h oder Wettercode 51–65, 80–82 |
| `pouring` | Niederschlagsintensitaet >5 mm/h |
| `snowy` | Wettercode 71, 73, 75, 77 (temp >2 °C), 85 |
| `snowy-rainy` | Wettercode 66, 67, 86 (Gefrierender Regen) |
| `hail` | Wettercode 77 bei Temperatur ≤2 °C |
| `lightning` | Wettercode 95 (Gewitter ohne regen) |
| `lightning-rainy` | Wettercode 96 (Gewitter mit regen) |
| `windy` | Windgeschwindigkeit ≥10 m/s |
| `windy-variant` | Windgeschwindigkeit ≥15 m/s |

Die Berechnung nutzt Daten aller drei Anbieter: WMO-Codes aus Open-Meteo für nicht-messbare Bedingungen (Nebel, Schnee, Gewitter), gemergte Maxwerte fuer Wind und Niederschlag (alle Adapter), und Sonnenhoehe fuer Tag/Nacht-Erkennung.

Bei Wettercode 3–44 oder Bewoelkung >50 % wird `cloudy` gemeldet. Wenn der gemessene Niederschlag 0 ist, aber der Wettercode Regen anzeigt (z. B. 61, 63, 64), uebernimmt der Wettercode — er spiegelt die aktuellsten Modell-Daten wider, die noch nicht von Stationsmessungen erfasst sind.

### Stations-Objekt

| Feld | Typ | Beschreibung |
|---|---|---|
| `source` | `str` | Datenanbieter: `dwd`, `openmeteo` oder `buienradar` |
| `name` | `str` | Stationsname |
| `lat` | `float` | Breitengrad der Station |
| `lon` | `float` | Laengengrad der Station |
| `time` | `datetime \| null` | Zeitstempel der Messung (UTC) |

## Architektur

```
┌─────────────────────────────────────────────────────────────────────┐
│  GET /weather/data?lat=49.87&lon=8.93                              │
│                              │                                      │
│          ┌───────────────────┼───────────────────┐                  │
│          ▼                   ▼                   ▼                  │
│    ┌───────────┐     ┌───────────┐     ┌──────────────┐            │
│    │   DWD     │     │  Open-    │     │  Buienradar  │            │
│    │ (parallel)│     │  Meteo    │     │ (parallel)   │            │
│    │           │     │           │     │              │            │
│    │ Obs: temp │     │ current:  │     │ radar grid:  │            │
│    │ Obs: wind │     │ temp/uv   │     │ precip 30m   │            │
│    │ Obs: rain │     │ wind      │     │ precip 1h    │            │
│    │ forecast: │     │ sunrise   │     │ precip 2h    │            │
│    │  precip   │     │ feels_like│     │ temp (NL)    │            │
│    │  uv (est) │     │ sonnenhoehe│    │ wind (NL)    │            │
│    └───────────┘     └───────────┘     └──────────────┘            │
│          │                   │                   │                  │
│          └───────────────────┼───────────────────┘                  │
│                              ▼                                      │
│                    Merge-Strategie (siehe unten)                    │
│                              │                                      │
│                              ▼                                      │
│                      Einzelne JSON-Antwort                          │
└─────────────────────────────────────────────────────────────────────┘
```

Alle drei Datenanbieter laufen parallel. Ein einzelner Anbieterausfall beeinflusst nicht die gesamte Antwort.

## Datenanbieter

| Anbieter | Starkpunkte | Abdeckung | Typische Antwortzeit |
|---|---|---|---|
| **Open-Meteo** | Genauer UV-Index, Sonnenauf-/untergang, gefuehlte Temperatur, weltweite Abdeckung | Weltweit | < 1s |
| **DWD** | Deutsche Stationsdaten (Temperatur, Wind, Niederschlag) | Deutschland | ~7s (kalt), ~0,3s (gecacht) |
| **Buienradar** | Radar-basierte Niederschlagsvorhersage (30 Min/1 Std/2 Std) | NL-Stationen + DE-Radar | < 1s |

### DWD (`wetterdienst_dwd.py`)

Kombiniert zwei DWD-Datenquellen:

**Beobachtung** — Liefert Temperatur, Windgeschwindigkeit, Windböen und Niederschlag aus dem `recent`-Zeitraum (10-Minuten-Aeolung). Jeder Parameter wird aus einem eigenen Pool der naechsten Stationen abgerufen, die diesen Parameter melden — Niederschlag aus dem naechsten Regenmesser, Wind aus dem naechsten Anemometer. Das ist wichtig, weil viele kleine DWD-Stationen nur Niederschlag messen. Die vier Anfragen laufen parallel via `ThreadPoolExecutor`. Veraltete Messungen (>2h) werden verworfen, das Ergebnis wird auf die 3 naechsten Stationen mit gueltigen Daten beschränkt.

**Vorhersage** — Stündliche MosMix Small-Prognosen fuer Niederschlag und Strahlung. Bildet Fenster fuer 30 Min/1 Std/2 Std und mittelt die Niederschlagswerte. Der UV-Index wird aus Globalstrahlung mit `* 0.019` approximiert und auf 0–16 begrenzt (Grobe Schaeztung).

**Caching** — Zwei Ebenen:
1. **In-Memory MosMix-Cache** — 10 Minuten TTL, nach `{lat:.2f},{lon:.2f}` getastet. MosMix aktualisiert sich alle 1–3 Stunden.
2. **fsspec Disk-Cache** — Steuert die Umgebungsvariable `DWD_CACHE`. Wenn aktiviert, beschleunigt wiederholte Anfragen von ~7s auf ~0,3s. Leere Ergebnisse werden nach Cache-Loeschung einmal erneut abgerufen.

### Open-Meteo (`openmeteo.py`)

Plain HTTP/JSON Client (`httpx`), ohne FFI-Bindings. Liefert aktuelle Temperatur, gefuehlte Temperatur, Windgeschwindigkeit, Windböen, Niederschlag, UV-Index, Sonnenaufgang und Sonnenuntergang.

Die Sonnenhoehe wird mit der NOAA-Formel berechnet. Liefert negative Werte, wenn die Sonne unter dem Horizont steht (Nachts).

### Buienradar (`buinradar.py`)

- **Radar-Raster** fuer Niederschlagsvorhersage (30 Min/1 Std/2 Std) — funktioniert auch fuer Deutschland (Raster-basiert, nicht stationsbasiert)
- **Stationsmessungen** (Temperatur, Wind, aktueller Niederschlag) sind auf die Niederlande beschränkt
- Stationsdaten werden ignoriert, wenn die naechste NL-Station >100km entfernt liegt — Temperatur und gefuehlte Temperatur aus einer fernen Station sind fuer den angeforderten Standort nicht relevant
- Regen-Niederschlag verwendet 5-Minuten-Intervalle, ueber `10 ** ((code - 109) / 32)` in mm/h umgerechnet

## Merge-Strategie

Nachdem alle Datenanbieter ihre Ergebnisse geliefert haben, fusioniert der Router (`weather_data.py`):

| Felder | Strategie | Begründung |
|---|---|---|
| Windgeschwindigkeit / Böen | `max()` ueber alle Anbieter |_ueber_berichterstattung ist sicherer als Unterberichterstattung |
| Niederschlag + 30 Min/1 Std/2 Std Fenster | `max()` ueber alle Anbieter | Verpasster Regen ist schlimmer als_ueber_berichterstattung |
| Gefuehlte Temperatur | Gleicher Anbieter wie Temperatur, sonst frischeste Quelle | Haeelt Temperatur und gefuehlte Temperatur konsistent |
| Temperatur | Frischeste Quelle (neuster Zeitstempel zuerst) | Neueste Daten sind am genauesten. Buienradar wird bei Station >100km ausgeschlossen |
| UV-Index | Frischeste Quelle | Open-Meteo liefert genaue Echtzeit-UV; DWD ist eine grobe Schaeztung |
| Sonnenauf- / untergang / Sonnenhoehe | Frischeste Quelle | Nur Open-Meteo liefert diese Daten |
| Stationen | Alle Stationen aller Anbieter mit Daten | Zeigt, welche Quellen mitgewirkt haben |

## Konfiguration

Erstelle eine `.env`-Datei im Projektstammverzeichnis:

```env
# DWD-Disk-Cache aktivieren (fsspec).
# true  = Cache aktiviert (~0,3s warm, ~7s kalt) — Standard
# false = Cache deaktiviert (immer frisch vom DWD)
DWD_CACHE=true

# MQTT-Push: Wetterdaten werden bei MosMix-Cache-Miss (10min) per MQTT
# an weather-hub/state gesendet. Wenn MQTT_BROKER nicht gesetzt, wird ein
# Stub verwendet (logged, kein Broker erforderlich).
# MQTT_BROKER=mqtt://broker.example.com
# MQTT_PORT=1883
# MQTT_USERNAME=
# MQTT_PASSWORD=
# MQTT_CLIENT_ID=weather-hub
# MQTT_TOPIC=weather-hub/state
# MQTT_LAT=49.87
# MQTT_LON=8.93
```

## MQTT-Push

Wenn `MQTT_LAT` und `MQTT_LON` gesetzt sind, startet ein Background-Timer, der alle 10 Minuten (MosMix Cache TTL) die Wetterdaten für den konfigurierten Standort abruft. Bei einem Cache-Miss werden die gemergten Wetterdaten per MQTT an das Topic `weather-hub/state` gesendet. Das Payload enthält alle WeatherData-Felder (Home-Assistant-kompatibel).

Wenn `MQTT_BROKER` nicht gesetzt ist, läuft ein Stub, der die MQTT-Nachricht im Log anzeigt — ideal für die Entwicklung ohne MQTT-Broker.

Ohne `MQTT_LAT`/`MQTT_LON` startet kein Timer — die MQTT-Push-Funktion kann auch über HTTP-Requests ausgelöst werden.

Beispiel-Payload:

```json
{
  "temperature": 21.4,
  "feels_like": 19.8,
  "wind_speed": 5.8,
  "precipitation_now": false,
  "precipitation_next_30m": false,
  "precipitation_next_1h": false,
  "precipitation_next_2h": false,
  "uv_index": 0,
  "status": "partlycloudy",
  "weather_code": 2,
  "stations": [...]
}
```

Das Topic `weather-hub/state` passt zum Zigbee2MQTT / Home Assistant-Muster. Setze `MQTT_TOPIC` auf einen anderen Wert (z. B. `weather-hub/garten/state`) fuer mehrere Standorte.