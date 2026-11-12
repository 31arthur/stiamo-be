"""Marking the day's events and meetings with what the person said about their time inside them (see availability.py).

Kept apart from the service so that the agent runner can use it without depending on everything the service does.
"""

from __future__ import annotations

from abc import update_abstractmethods
from typing import Any

from app.application.ports import DataGateway
from app.domain.dayplan import normalize_title


def view(raw: dict[str, Any] | None) -> dict[str, Any]:
    r = raw or {}
    return {"events": dict(r.get("events") or {}), "rules": dict(r.get("rules") or {}), "asked": dict(r.get("asked") or {})}


class Delegating(DataGateway):
    """A data gateway that hands every call to another and changes only what a subclass overrides."""

    def __init__(self, inner: DataGateway) -> None:
        self._inner = inner


def _forward(name: str):
    async def call(self, *args: Any, **kwargs: Any):
        return await getattr(self._inner, name)(*args, **kwargs)

    call.__name__ = name
    return call


for _name in (*DataGateway.__abstractmethods__, "calendar_events", "calendar_sync", "google_links", "contacts", "chapters", "write_chapter", "join_cast"):
    setattr(Delegating, _name, _forward(_name))
update_abstractmethods(Delegating)


class AvailabilityData(Delegating):
    """The day's events and meetings, each marked with `mode` when the person said what they can do during it."""

    def __init__(self, inner: DataGateway, raw: dict[str, Any] | None) -> None:
        super().__init__(inner)
        v = view(raw)
        self._events, self._rules = v["events"], v["rules"]

    def mode_of(self, key: str, title: str) -> str | None:
        said = self._events.get(key)
        if said:
            return said[0] if isinstance(said, list) else said
        return self._rules.get(normalize_title(title))

    def _mark(self, key: str, record: dict[str, Any]) -> dict[str, Any]:
        mode = self.mode_of(key, str(record.get("title") or ""))
        return {**record, "mode": mode} if mode else record

    async def calendar_events(self, start: str, end: str):
        return [self._mark(f"cal:{e.get('id')}", e) for e in await self._inner.calendar_events(start, end)]

    async def records(self, table: str):
        rows = await self._inner.records(table)
        return [self._mark(f"meeting:{r.get('id')}", r) for r in rows] if table == "meetings" else rows
