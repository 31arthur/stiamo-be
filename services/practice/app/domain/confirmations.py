"""Appointments must be confirmed beforehand; the confirmation calls are follow-ups generated from the meeting."""

from dataclasses import dataclass, field
from typing import Any

from app.domain.common import TimeSlot, offset_minutes, shift_date
from app.domain.work import ConfirmationTiming, DueDate, FollowUp, Meeting

# Day-before confirmation calls happen at the meeting's own clock time, kept within working hours.
_DAY_BEFORE_EARLIEST = 9 * 60
_DAY_BEFORE_LATEST = 19 * 60
_CONFIRMATION_DURATION = 10


def confirmation_due(slot: TimeSlot, timing: ConfirmationTiming) -> DueDate:
    if timing == "hour_before":
        date, minute = offset_minutes(slot.date, slot.start, -60)
        return DueDate(date=date, time=minute)
    return DueDate(
        date=shift_date(slot.date, -1),
        time=min(_DAY_BEFORE_LATEST, max(_DAY_BEFORE_EARLIEST, slot.start)),
    )


def confirmation_title(meeting: Meeting) -> str:
    return f"Confirm {'appointment' if meeting.kind == 'appointment' else 'meeting'} — {meeting.counterpart or meeting.title}"


@dataclass
class ConfirmationSync:
    create: list[dict[str, Any]] = field(default_factory=list)
    update: list[FollowUp] = field(default_factory=list)
    remove: list[FollowUp] = field(default_factory=list)


def sync_confirmations(meeting: Meeting, follow_ups: list[FollowUp]) -> ConfirmationSync:
    """Keeps generated confirmation follow-ups in step with the meeting: creates missing ones, moves open
    ones when the meeting is rescheduled, and drops open ones that are no longer wanted (or when the
    meeting is cancelled). Follow-ups that already have an outcome are history and never touched."""
    generated = [
        f for f in follow_ups if f.trigger and f.subject and f.subject.kind == "meeting" and f.subject.id == meeting.id
    ]
    wanted = [] if meeting.outcome.status == "cancelled" else meeting.confirmations
    result = ConfirmationSync()
    title = confirmation_title(meeting)
    contact = meeting.contact or meeting.counterpart

    for timing in wanted:
        due = confirmation_due(meeting.slot, timing)
        existing = next((f for f in generated if f.trigger == timing), None)
        if existing is None:
            result.create.append({
                "title": title,
                "channel": "message" if meeting.mode == "virtual" else "call",
                "workspace": meeting.workspace,
                "contact": contact,
                "due": due.model_dump(),
                "duration": _CONFIRMATION_DURATION,
                "subject": {"kind": "meeting", "id": meeting.id},
                "trigger": timing,
            })
        elif existing.outcome.status == "open" and (
            existing.due != due or existing.title != title or existing.workspace != meeting.workspace
        ):
            result.update.append(existing.model_copy(update={
                "due": due, "title": title, "workspace": meeting.workspace, "contact": contact,
            }))

    result.remove = [f for f in generated if f.outcome.status == "open" and f.trigger not in wanted]
    return result
