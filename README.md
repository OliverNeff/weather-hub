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
| **Stationen** | | |
| `stations` | `list[WeatherStation]` | Alle beteiligten Wetterstationen (siehe unten) |

Alle Felder sind optional — `null` bedeutet, dass der Anbieter keine Daten fuer dieses Feld lieferte.

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

**Beobachtung** — Liefert Temperatur, Windgeschwindigkeit, Windböen und Niederschlag aus dem `recent`-Zeitraum (10-Minuten-Aeolung). Jeder Parameter wird in einer eigenen Anfrage abgerufen, dabei die jeweils naechste Station, die den Parameter meldet. Die vier Anfragen laufen parallel via `ThreadPoolExecutor`.

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
# true  = Cache aktiviert (~0,3s warm, ~7s kalt)
# false = Cache deaktiviert (immer frisch vom DWD)
DWD_CACHE=false
```