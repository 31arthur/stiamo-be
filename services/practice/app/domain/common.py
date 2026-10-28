"""Shared domain vocabulary: value types every aggregate is built from.

Models use snake_case in Python and camelCase on the wire, so the JSON matches the frontend's domain types.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.alias_generators import to_camel

ISODate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
Minutes = Annotated[int, Field(ge=0, le=1439)]
Duration = Annotated[int, Field(gt=0, le=60 * 24 * 7)]

Workspace = Literal["personal", "sb", "fsa", "media"]
EntityKind = Literal["task", "meeting", "followUp", "case", "hearing", "plan", "goal", "counsel", "client", "project"]
CaseType = Literal["pow", "public_interest", "industrial_dispute", "minimum_wages", "workmen_compensation", "other"]
OutcomeStatus = Literal["open", "done", "partial", "not_done", "cancelled"]


class DomainModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class EntityRef(DomainModel):
    """A typed pointer to another record."""

    kind: EntityKind
    id: str

    def same_as(self, other: "EntityRef | None") -> bool:
        return other is not None and self.kind == other.kind and self.id == other.id


class TimeSlot(DomainModel):
    date: ISODate
    start: Minutes
    duration: Duration


class Outcome(DomainModel):
    """How something actually turned out, with an optional comment and further action."""

    status: OutcomeStatus = "open"
    comment: str | None = None
    next_action: str | None = None
    recorded_at: str | None = None


class Entity(DomainModel):
    """Every persisted record belongs to one user and is time-stamped."""

    id: str = Field(min_length=1, max_length=64)
    user_id: str
    created_at: str
    updated_at: str


SYSTEM_FIELDS = frozenset({"id", "user_id", "created_at", "updated_at"})

T = TypeVar("T", bound=Entity)


def draft_of(model: type[T]) -> type[DomainModel]:
    """The request body for creating or replacing a record: the entity without its system fields."""
    fields = {name: (f.annotation, f) for name, f in model.model_fields.items() if name not in SYSTEM_FIELDS}
    return create_model(f"{model.__name__}Draft", __base__=DomainModel, id=(str | None, None), **fields)


# --- time -------------------------------------------------------------------------------------------


def to_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def to_iso(dt: datetime) -> str:
    """JavaScript-style instant: 2026-01-31T10:20:30.123Z."""
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- outcome ----------------------------------------------------------------------------------------


def outcome_score(o: Outcome) -> float:
    return 1.0 if o.status == "done" else 0.5 if o.status == "partial" else 0.0


def progress_of(outcomes: list[Outcome]) -> float | None:
    """Progress over outcomes, ignoring cancelled ones: 0..1, or None when nothing counts."""
    counted = [o for o in outcomes if o.status != "cancelled"]
    return sum(outcome_score(o) for o in counted) / len(counted) if counted else None


# --- calendar arithmetic ------------------------------------------------------------------------------


def shift_date(date: str, days: int) -> str:
    from datetime import date as _date
    from datetime import timedelta

    return (_date.fromisoformat(date) + timedelta(days=days)).isoformat()


def offset_minutes(date: str, minute: int, delta: int) -> tuple[str, int]:
    """Moves a point in time by a number of minutes, crossing midnight as needed."""
    total = minute + delta
    day_shift = total // 1440
    return shift_date(date, day_shift), total - day_shift * 1440
