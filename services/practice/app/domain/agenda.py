"""The agenda: every dated item between two days, whatever its source, as uniform entries."""

from dataclasses import dataclass, field
from typing import Literal

from app.domain.cases import Hearing, LegalCase
from app.domain.common import EntityRef, Outcome
from app.domain.work import FollowUp, Meeting, Task

AgendaKind = Literal["task", "meeting", "followUp", "hearing"]
_KIND_ORDER: dict[str, int] = {"hearing": 0, "meeting": 1, "followUp": 2, "task": 3}

#: The planner's visible day, in minutes from midnight.
DAY_START = 6 * 60
DAY_END = 24 * 60
MIN_DURATION = 5


@dataclass
class AgendaEntry:
    #: Stable key, `kind:id`.
    key: str
    ref: EntityRef
    kind: AgendaKind
    title: str
    date: str
    #: Planned or default duration, used when it's placed on the timeline.
    duration: int
    workspace: str
    outcome: Outcome
    #: Fixed in time: a reflow flows around it.
    pinned: bool
    #: Can be dragged to another time.
    movable: bool
    subtitle: str | None = None
    #: Present when the entry has a time on that day.
    start: int | None = None
    slot_duration: int | None = None
    meeting_kind: str | None = None
    priority: str | None = None


@dataclass
class AgendaSources:
    tasks: list[Task] = field(default_factory=list)
    meetings: list[Meeting] = field(default_factory=list)
    follow_ups: list[FollowUp] = field(default_factory=list)
    hearings: list[Hearing] = field(default_factory=list)
    cases: list[LegalCase] = field(default_factory=list)


def clamp_block(start: int, duration: int) -> tuple[int, int]:
    """Keeps a block inside the visible day and at least MIN_DURATION long."""
    d = max(MIN_DURATION, min(duration, DAY_END - DAY_START))
    return min(max(start, DAY_START), DAY_END - d), d


def build_agenda(src: AgendaSources, start: str, end: str) -> list[AgendaEntry]:
    """Collects every dated item between `start` and `end` (inclusive), timed items first in time order."""

    def in_range(d: str | None) -> bool:
        return d is not None and start <= d <= end

    entries: list[AgendaEntry] = []
    cases = {c.id: c for c in src.cases}

    for t in src.tasks:
        day = t.slot.date if t.slot else t.due_date
        if not in_range(day):
            continue
        entries.append(AgendaEntry(
            f"task:{t.id}", EntityRef(kind="task", id=t.id), "task", t.title, day, t.slot.duration if t.slot else t.estimate,
            t.workspace, t.outcome, pinned=False, movable=True, start=t.slot.start if t.slot else None,
            slot_duration=t.slot.duration if t.slot else None, priority=t.priority))

    for m in src.meetings:
        if not in_range(m.slot.date):
            continue
        entries.append(AgendaEntry(
            f"meeting:{m.id}", EntityRef(kind="meeting", id=m.id), "meeting", m.title, m.slot.date, m.slot.duration,
            m.workspace, m.outcome, pinned=True, movable=True, subtitle=m.counterpart, start=m.slot.start,
            slot_duration=m.slot.duration, meeting_kind=m.kind))

    for f in src.follow_ups:
        if f.due is None or not in_range(f.due.date):
            continue
        timed = f.due.time is not None
        entries.append(AgendaEntry(
            f"followUp:{f.id}", EntityRef(kind="followUp", id=f.id), "followUp", f.title, f.due.date, f.duration,
            f.workspace, f.outcome,
            # Confirmations are tied to the appointment's time; other follow-ups can flow.
            pinned=bool(f.trigger), movable=True, subtitle=f.contact, start=f.due.time if timed else None,
            slot_duration=f.duration if timed else None))

    for h in src.hearings:
        if not in_range(h.date):
            continue
        main = cases.get(h.case_id)
        refs = [c.reference for c in (main, *(cases.get(i) for i in h.also_case_ids or [])) if c]
        subtitle = " · ".join(p for p in (" + ".join(refs), h.purpose) if p)
        entries.append(AgendaEntry(
            f"hearing:{h.id}", EntityRef(kind="hearing", id=h.id), "hearing", f"Hearing — {main.title if main else 'Case'}",
            h.date, h.duration, "sb", h.outcome, pinned=True, movable=False, subtitle=subtitle or None,
            start=h.time, slot_duration=h.duration if h.time is not None else None))

    return sorted(entries, key=lambda e: (e.date, e.start if e.start is not None else float("inf"),
                                          _KIND_ORDER[e.kind], e.title))
