"""The day-to-day work items: tasks, follow-ups and meetings (appointments are meetings that need confirming)."""

from typing import Literal

from pydantic import Field

from app.domain.common import (
    DomainModel,
    Duration,
    Entity,
    EntityRef,
    ISODate,
    Minutes,
    Outcome,
    TimeSlot,
    Workspace,
)

# --- tasks --------------------------------------------------------------------------------------------

TaskPriority = Literal["low", "normal", "high", "urgent"]


class Task(Entity):
    title: str
    notes: str = ""
    workspace: Workspace = "personal"
    priority: TaskPriority = "normal"
    due_date: ISODate | None = None
    #: Planned time block on the day planner.
    slot: TimeSlot | None = None
    #: Expected effort, used when the task is placed on the timeline.
    estimate: Duration = 30
    #: The action plan this task is a step of, and its position there.
    plan_id: str | None = None
    order: int | None = None
    #: Where the task came from (a meeting, hearing, follow-up…).
    related: EntityRef | None = None
    outcome: Outcome = Field(default_factory=Outcome)


# --- follow-ups ---------------------------------------------------------------------------------------

FollowUpChannel = Literal["call", "message", "email", "visit"]
ConfirmationTiming = Literal["day_before", "hour_before"]
AttemptResult = Literal[
    "no_answer", "left_message", "call_back", "they_will_call", "not_ready", "reached", "unreachable"
]


class DueDate(DomainModel):
    date: ISODate
    time: Minutes | None = None


class Attempt(DomainModel):
    """One try at reaching someone, kept on the follow-up so the whole chase can be read back."""

    date: ISODate
    minute: Minutes
    result: AttemptResult
    channel: FollowUpChannel | None = None
    note: str | None = None
    promised_for: ISODate | None = None


class FollowUp(Entity):
    title: str
    channel: FollowUpChannel = "call"
    workspace: Workspace = "personal"
    contact: str | None = None
    #: Optional: a follow-up can simply be pending, or due on a day, or at a specific time.
    due: DueDate | None = None
    duration: Duration = 15
    subject: EntityRef | None = None
    #: Set when generated automatically as an appointment confirmation.
    trigger: ConfirmationTiming | None = None
    notes: str | None = None
    attempts: list[Attempt] | None = None
    plan_id: str | None = None
    order: int | None = None
    outcome: Outcome = Field(default_factory=Outcome)


# --- meetings -----------------------------------------------------------------------------------------

MeetingKind = Literal["appointment", "meeting"]
MeetingMode = Literal["in_person", "virtual", "phone"]


class AgendaItem(DomainModel):
    id: str
    topic: str
    notes: str | None = None
    owner: str | None = None
    minutes: int | None = None
    outcome: Outcome = Field(default_factory=Outcome)


class Meeting(Entity):
    """Appointments and meetings share one model: both are timed engagements with people.

    An appointment is booked with an outside party and must be confirmed beforehand; a meeting is
    agenda-driven and needs preparation.
    """

    kind: MeetingKind = "meeting"
    title: str
    workspace: Workspace = "personal"
    slot: TimeSlot
    mode: MeetingMode = "in_person"
    location: str | None = None
    link: str | None = None
    counterpart: str | None = None
    contact: str | None = None
    attendees: list[str] = Field(default_factory=list)
    purpose: str | None = None
    agenda: list[AgendaItem] = Field(default_factory=list)
    confirmations: list[ConfirmationTiming] = Field(default_factory=list)
    prep_plan_id: str | None = None
    plan_id: str | None = None
    order: int | None = None
    related: EntityRef | None = None
    minutes: str | None = None
    outcome: Outcome = Field(default_factory=Outcome)


def default_confirmations(kind: MeetingKind) -> list[ConfirmationTiming]:
    return ["day_before", "hour_before"] if kind == "appointment" else []
