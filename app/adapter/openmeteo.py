"""Fetch current UV index from Open-Meteo.

DWD does not provide a UV index parameter, so we use the
Open-Meteo free API which returns a directly computed value.
"""

import httpx

_TIMEOUT = 5.0


async def fetch_uv_index(latitude: float, longitude: float) -> float | None:
    """Return the current UV index or None on error."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "uv_index",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["current"]["uv_index"]
    except Exception:
        return None