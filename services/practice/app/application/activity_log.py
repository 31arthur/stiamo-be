"""Appends to the history every record carries."""

import json
from typing import Any

from pydantic.alias_generators import to_camel

from app.application.ports import Env
from app.domain.common import SYSTEM_FIELDS, Entity, EntityRef
from app.domain.history import KIND_LABEL, Activity, ActivityAction, FieldChange

_QUIET_FIELDS = SYSTEM_FIELDS | {"doc"}  # the plan page is autosaved; it has its own single "edited" line
_SCHEDULING_FIELDS = {"slot", "due", "dueDate", "date", "time"}
_OUTCOME_LABEL = {"open": "Reopened", "done": "Done", "partial": "Partly done", "not_done": "Not done", "cancelled": "Cancelled"}


def title_of(kind: str, e: Any) -> str:
    if kind == "case":
        return f"{e.reference} — {e.title}"
    if kind == "hearing":
        return f"Hearing: {e.purpose}"
    return getattr(e, "title", None) or getattr(e, "name", None) or KIND_LABEL.get(kind, kind)


def parent_of(kind: str, e: Any) -> EntityRef | None:
    if kind == "hearing":
        return EntityRef(kind="case", id=e.case_id)
    if kind == "project":
        return EntityRef(kind="client", id=e.client_id)
    return None


def _show(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= 120 else text[:117] + "…"


def diff(before: Entity, after: Entity) -> tuple[ActivityAction, list[FieldChange]]:
    b, a = before.model_dump(), after.model_dump()
    changes = [
        FieldChange(field=to_camel(k), from_=_show(b.get(k)), to=_show(a.get(k)))
        for k in a
        if k not in _QUIET_FIELDS and a.get(k) != b.get(k)
    ]
    fields = {c.field for c in changes}
    if fields == {"outcome"}:
        return "outcome", changes
    if fields and fields <= _SCHEDULING_FIELDS:
        return "scheduled", changes
    return "updated", changes


def summarize(action: ActivityAction, changes: list[FieldChange], after: Entity) -> str:
    if action == "outcome":
        return f"Marked {_OUTCOME_LABEL.get(after.outcome.status, after.outcome.status).lower()}"  # type: ignore[attr-defined]
    names = [c.field for c in changes]
    return "Changed " + ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")


class ActivityLog:
    def __init__(self, env: Env) -> None:
        self._env = env

    async def record(
        self,
        user_id: str,
        *,
        ref: EntityRef,
        title: str,
        action: ActivityAction,
        summary: str,
        changes: list[FieldChange] | None = None,
        related: EntityRef | None = None,
    ) -> None:
        now = self._env.now_iso()
        await self._env.stores.activity.save(
            Activity(
                id=self._env.ids.next(), user_id=user_id, at=now, created_at=now, updated_at=now,
                ref=ref, title=title, action=action, summary=summary, changes=changes, related=related,
            )
        )

    async def created(self, user_id: str, kind: str, entity: Entity) -> None:
        await self.record(
            user_id, ref=EntityRef(kind=kind, id=entity.id), title=title_of(kind, entity), action="created",
            summary=f"{KIND_LABEL[kind]} created", related=parent_of(kind, entity),
        )

    async def changed(self, user_id: str, kind: str, before: Entity, after: Entity) -> None:
        action, changes = diff(before, after)
        if not changes:
            return
        await self.record(
            user_id, ref=EntityRef(kind=kind, id=after.id), title=title_of(kind, after), action=action,
            summary=summarize(action, changes, after), changes=changes,
        )

    async def deleted(self, user_id: str, kind: str, entity: Entity) -> None:
        await self.record(
            user_id, ref=EntityRef(kind=kind, id=entity.id), title=title_of(kind, entity), action="deleted",
            summary=f"{KIND_LABEL[kind]} deleted", related=parent_of(kind, entity),
        )
