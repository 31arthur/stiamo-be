"""The Attention queue: the emails the agents read for what matters, waiting on the owner. Mail that is an issue or important, and
events found in mail that are waiting to be approved for the calendar."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC
from typing import Any

from stiamo_common.errors import Conflict, NotFound, ValidationFailed

from app.application.ports import DataGateway, LedgerEntry, SettingsRepository, WorkflowRepository
from app.application.services import Who
from app.domain.llm import AiError
from app.domain.workflows import clean_event, event_to_meeting

WAITING = ["queued", "event_proposed"]
ACTIONS = ("done", "dismiss", "approve_event", "skip_event")


def _iso(d) -> str | None:
    return d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None


class AttentionService:
    def __init__(self, repo: WorkflowRepository, settings: SettingsRepository, data_factory: Callable[[str], DataGateway]) -> None:
        self._repo, self._settings, self._data = repo, settings, data_factory

    async def items(self, who: Who, workspace: str | None) -> dict[str, Any]:
        s = await self._settings.get(who.user_id)
        agents = {w.id: w for w in await self._repo.all(who.user_id)}
        out = []
        for r in await self._repo.queue(who.user_id, WAITING):
            w = agents.get(r.agent_id)
            made = (s.custom_agents.get(r.agent_id) if s else None) or {}  # an agent made on the canvas has no workflow row, only its setup
            if w is None and not made:
                continue
            name = w.name if w else str(made.get("name") or r.agent_id)
            where = (s.workspaces.get(r.agent_id) if s else None) or (list(made.get("workspaces") or []) if made else [])
            if workspace and where and workspace not in where:
                continue
            d = r.data or {}
            out.append({
                "agentId": r.agent_id, "agentName": name, "itemId": r.item_id, "title": r.title, "excerpt": r.excerpt, "label": r.label, "confidence": round(r.confidence, 3),
                "status": r.status, "summary": d.get("summary") or r.title, "priority": d.get("priority") or "normal", "action": d.get("action"), "due": d.get("due"),
                "event": d.get("event"), "note": d.get("note"), "at": _iso(r.created_at), "folder": (w.config.get("source") or {}).get("folder") if w else d.get("folder") or "INBOX",
                "workspaces": where,
            })
        order = {"high": 0, "normal": 1, "low": 2}
        out.sort(key=lambda i: (i["status"] != "event_proposed", order.get(i["priority"], 1), i["at"] or ""), reverse=False)
        return {"items": out, "count": len(out)}

    async def act(self, who: Who, token: str, agent_id: str, item_id: str, action: str, event: dict[str, Any] | None) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValidationFailed("Say what to do: done, dismiss, approve_event or skip_event.")
        w = await self._repo.get(who.user_id, agent_id)
        s0 = await self._settings.get(who.user_id)
        known = w is not None or bool(s0 and agent_id in s0.custom_agents)  # an agent made on the canvas counts too
        entry = await self._repo.entry(who.user_id, agent_id, item_id) if known else None
        if not known or entry is None:
            raise NotFound("That item isn't in the queue.")
        if entry.status not in WAITING:
            raise Conflict("That one has already been dealt with.")
        data = dict(entry.data or {})
        if action == "done":
            await self._repo.settle(who.user_id, agent_id, item_id, "done")
            return {"status": "done"}
        if action == "dismiss":
            await self._repo.settle(who.user_id, agent_id, item_id, "dismissed")
            return {"status": "dismissed"}
        if action == "skip_event":
            data["event"] = None
            await self._repo.settle(who.user_id, agent_id, item_id, "queued", data)
            return {"status": "queued"}
        # approve_event: the person may have changed it, so what they sent is checked like what the agent found
        chosen = clean_event(event if event is not None else data.get("event"))
        if chosen is None:
            raise ValidationFailed("The event needs a title and a date.")
        s = await self._settings.get(who.user_id)
        where = (s.workspaces.get(agent_id) if s else None) or []
        record = event_to_meeting(chosen, record_id=str(uuid.uuid4()), workspace=where[0] if where else "personal", summary=data.get("summary") or entry.title, subject=entry.title, sender=_sender(entry))
        try:
            meeting_id = await self._data(token).create_meeting(record)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        await self._repo.settle(who.user_id, agent_id, item_id, "approved", {**data, "event": chosen, "meetingId": meeting_id})
        return {"status": "approved", "meetingId": meeting_id}


def _sender(entry: LedgerEntry) -> str:
    import re

    m = re.search(r"^From:[ \t]*(.*)$", entry.excerpt, re.MULTILINE)
    return m.group(1).strip() if m else ""
