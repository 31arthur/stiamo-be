"""History and connections: the activity trail every record carries, and hand-made links between records."""

from typing import Literal

from pydantic import Field

from app.domain.common import DomainModel, Entity, EntityRef

ActivityAction = Literal[
    "created", "updated", "outcome", "scheduled", "deleted", "linked", "unlinked", "attached", "detached", "note"
]


class FieldChange(DomainModel):
    field: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class Activity(Entity):
    """One line of a record's history, written automatically on every change and by hand as notes."""

    at: str
    ref: EntityRef
    title: str
    action: ActivityAction
    summary: str
    changes: list[FieldChange] | None = None
    related: EntityRef | None = None


class Link(Entity):
    """A connection made by hand between any two records. Links are undirected."""

    a: EntityRef
    b: EntityRef
    note: str | None = None

    def touches(self, ref: EntityRef) -> bool:
        return self.a.same_as(ref) or self.b.same_as(ref)

    def other_end(self, ref: EntityRef) -> EntityRef:
        return self.b if self.a.same_as(ref) else self.a


KIND_LABEL: dict[str, str] = {
    "task": "Task",
    "meeting": "Meeting",
    "followUp": "Follow-up",
    "case": "Case",
    "hearing": "Hearing",
    "plan": "Action plan",
    "goal": "Goal",
    "counsel": "Counsel",
    "client": "Client",
    "project": "Project",
}
