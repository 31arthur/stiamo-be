"""Place search for "where is this contact": Open-Meteo's free geocoding (no key, worldwide, and it says the time zone of every place)."""

from typing import Any

import httpx

URL = "https://geocoding-api.open-meteo.com/v1/search"


def label(r: dict[str, Any]) -> str:
    parts: list[str] = []
    for k in ("name", "admin2", "admin1", "country"):
        v = str(r.get(k) or "").strip()
        if v and v not in parts:
            parts.append(v)
    return ", ".join(parts)


def rank(results: list[dict[str, Any]], rest: list[str]) -> list[dict[str, Any]]:
    """What was typed after the first comma ("Telangana", "Algeria") puts the places that match it first."""
    words = [w.lower() for w in rest if w]
    def score(r: dict[str, Any]) -> tuple[int, float]:
        text = " ".join(str(r.get(k) or "") for k in ("admin1", "admin2", "country", "country_code")).lower()
        return (-sum(1 for w in words if w in text), -float(r.get("population") or 0))
    return sorted(results, key=score)


class HttpPlaces:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def search(self, q: str, count: int = 8) -> list[dict[str, Any]]:
        first, *rest = [p.strip() for p in q.split(",")]
        if len(first) < 2:
            return []
        try:
            r = await self._http.get(URL, params={"name": first, "count": 20, "language": "en", "format": "json"}, timeout=8)
        except httpx.HTTPError:
            return []
        if r.status_code != 200:
            return []
        found = rank(r.json().get("results") or [], rest)[:count]
        return [{"label": label(x), "name": x.get("name"), "region": x.get("admin1") or "", "country": x.get("country") or "", "timezone": x.get("timezone") or "", "lat": x.get("latitude"), "lon": x.get("longitude")} for x in found]
