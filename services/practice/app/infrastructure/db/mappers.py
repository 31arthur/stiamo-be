"""Translation between domain entities and table rows.

Entities whose fields line up one-to-one with their columns use `flat_mapper`; the rest (nested value objects
flattened into columns) have hand-written mappers.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain.common import EntityRef, Outcome, TimeSlot, to_dt, to_iso
from app.domain.history import Activity, FieldChange, Link
from app.infrastructure.db.models import ActivityModel, LinkModel


@dataclass(frozen=True)
class Mapper:
    model: type
    to_row: Callable[[Any], dict[str, Any]]
    from_row: Callable[[Any], Any]


def base_row(e) -> dict[str, Any]:
    return {"user_id": e.user_id, "id": e.id, "created_at": to_dt(e.created_at), "updated_at": to_dt(e.updated_at)}


def base_fields(m) -> dict[str, Any]:
    return {"user_id": m.user_id, "id": m.id, "created_at": to_iso(m.created_at), "updated_at": to_iso(m.updated_at)}


def flat_mapper(model: type, entity: type) -> Mapper:
    columns = [c.key for c in model.__table__.columns if c.key not in ("created_at", "updated_at")]

    def to_row(e) -> dict[str, Any]:
        return {**{c: getattr(e, c) for c in columns}, **base_row(e)}

    def from_row(m) -> Any:
        return entity.model_validate({**{c: getattr(m, c) for c in columns}, **base_fields(m)})

    return Mapper(model, to_row, from_row)


# --- reusable column groups -----------------------------------------------------------------------------


def ref_to_cols(ref: EntityRef | None, prefix: str) -> dict[str, Any]:
    return {f"{prefix}_kind": ref.kind if ref else None, f"{prefix}_id": ref.id if ref else None}


def ref_from_cols(m, prefix: str) -> EntityRef | None:
    kind = getattr(m, f"{prefix}_kind")
    return EntityRef(kind=kind, id=getattr(m, f"{prefix}_id")) if kind else None


def outcome_to_cols(o: Outcome) -> dict[str, Any]:
    return {
        "outcome_status": o.status, "outcome_comment": o.comment, "outcome_next_action": o.next_action,
        "outcome_recorded_at": to_dt(o.recorded_at) if o.recorded_at else None,
    }


def outcome_from_cols(m) -> Outcome:
    return Outcome(
        status=m.outcome_status, comment=m.outcome_comment, next_action=m.outcome_next_action,
        recorded_at=to_iso(m.outcome_recorded_at) if m.outcome_recorded_at else None,
    )


def slot_to_cols(s: TimeSlot | None, prefix: str = "slot") -> dict[str, Any]:
    return {
        f"{prefix}_date": s.date if s else None,
        f"{prefix}_start": s.start if s else None,
        f"{prefix}_duration": s.duration if s else None,
    }


def slot_from_cols(m, prefix: str = "slot") -> TimeSlot | None:
    date = getattr(m, f"{prefix}_date")
    if date is None:
        return None
    return TimeSlot(date=date, start=getattr(m, f"{prefix}_start"), duration=getattr(m, f"{prefix}_duration"))


# --- history ------------------------------------------------------------------------------------------


def _activity_to_row(a: Activity) -> dict[str, Any]:
    return {
        **base_row(a), "at": to_dt(a.at), **ref_to_cols(a.ref, "ref"), "title": a.title, "action": a.action,
        "summary": a.summary, "related_kind": a.related.kind if a.related else None,
        "related_id": a.related.id if a.related else None,
        "changes": [c.model_dump(by_alias=True) for c in a.changes] if a.changes else None,
    }


def _activity_from_row(m: ActivityModel) -> Activity:
    return Activity(
        **base_fields(m), at=to_iso(m.at), ref=ref_from_cols(m, "ref"), title=m.title, action=m.action,
        summary=m.summary, related=ref_from_cols(m, "related"),
        changes=[FieldChange.model_validate(c) for c in m.changes] if m.changes else None,
    )


def _link_to_row(link: Link) -> dict[str, Any]:
    return {**base_row(link), **ref_to_cols(link.a, "a"), **ref_to_cols(link.b, "b"), "note": link.note}


def _link_from_row(m: LinkModel) -> Link:
    return Link(**base_fields(m), a=ref_from_cols(m, "a"), b=ref_from_cols(m, "b"), note=m.note)


ACTIVITY = Mapper(ActivityModel, _activity_to_row, _activity_from_row)
LINK = Mapper(LinkModel, _link_to_row, _link_from_row)
