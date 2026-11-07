"""When to remind someone about calls they haven't marked done. Pure: no network, no framework. Times are minutes after midnight.

Calls lined up one after the other are one run: the reminder waits for the whole run to end. Then it comes 10 minutes later, and again
1 hour, 30 minutes and 10 minutes after the one before, for as long as any call in the run is still open. It never comes while the
person is busy (a hearing, a meeting, a calendar event, a task with a time), and not outside the working day.
"""

from __future__ import annotations

from typing import Any

from app.domain.dayplan import DAY_END, DAY_START, busy_blocks, is_open

#: Minutes to wait after the run ends, then after each reminder. Once they are used up the run is left alone.
LADDER: tuple[int, ...] = (10, 60, 30, 10)
#: Calls this close together (minutes) are one run.
RUN_GAP = 10
DEFAULT_CALL = 15
#: The voice that reads a reminder aloud (a Deepgram Aura voice).
DEFAULT_VOICE = "aura-2-thalia-en"

TONES = ("A reminder.", "A reminder again.", "Another reminder.", "This is the last reminder for now.")


def runs(day: str, follow_ups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The day's timed follow-ups, grouped into runs: {key, start, end, calls}. `key` is the id of the first call, so moving a later one doesn't start over."""
    timed = sorted(
        (
            (int(f["due"]["time"]), int(f["due"]["time"]) + int(f.get("duration") or DEFAULT_CALL), f)
            for f in follow_ups
            if (f.get("due") or {}).get("date") == day and (f.get("due") or {}).get("time") is not None
        ),
        key=lambda x: (x[0], str(x[2].get("id"))),
    )
    out: list[dict[str, Any]] = []
    for start, end, f in timed:
        if out and start <= out[-1]["end"] + RUN_GAP:
            out[-1]["end"] = max(out[-1]["end"], end)
            out[-1]["calls"].append(f)
        else:
            out.append({"key": str(f.get("id")), "start": start, "end": end, "calls": [f]})
    return out


def next_due(run: dict[str, Any], state: dict[str, Any]) -> int | None:
    """The minute the next reminder for a run is due, or None when it has had them all. `state` is what was kept for it: {step, last}."""
    step = int(state.get("step", 0))
    if step >= len(LADDER):
        return None
    base = int(state["last"]) if step > 0 and state.get("last") is not None else run["end"]
    if state.get("until"):  # snoozed: the next one comes at that minute, whatever the ladder said (and the ladder carries on from there)
        return int(state["until"])
    return base + LADDER[step]


def free_now(minute: int, day: str, calls: list[dict[str, Any]], hearings: list[dict[str, Any]], meetings: list[dict[str, Any]], tasks: list[dict[str, Any]], follow_ups: list[dict[str, Any]], calendar: list[dict[str, Any]]) -> bool:
    """Nothing else is on at this minute. The run's own calls don't count: they are what is being reminded about."""
    skip = {f"followUp:{c.get('id')}" for c in calls}
    return not any(a <= minute < b for a, b, _ in busy_blocks(day, hearings, meetings, tasks, follow_ups, skip=skip, calendar=calendar))


def in_working_day(minute: int) -> bool:
    return DAY_START <= minute < DAY_END


def due_run(minute: int, day: str, follow_ups: list[dict[str, Any]], states: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The run whose reminder is due now (its calls still open), with `pending` (those calls) and `step`. The first, if more than one."""
    if not in_working_day(minute):
        return None
    for run in runs(day, follow_ups):
        pending = [c for c in run["calls"] if is_open(c)]
        due = next_due(run, states.get(run["key"], {}))
        if pending and due is not None and minute >= due:
            return {**run, "pending": pending, "step": int(states.get(run["key"], {}).get("step", 0))}
    return None


#: CallMeBot reads at most this many characters of a call and cuts the rest.
CALL_LIMIT = 256
DEFAULT_CALL_VOICE = "en-GB-Standard-B"


def call_speech(step: int, pending: list[dict[str, Any]], limit: int = CALL_LIMIT) -> str:
    """What a phone call says: the same reminder, short enough to be read whole (it names as many calls as fit)."""
    names = [str(c.get("title") or "a call")[:60] for c in pending]
    n = len(names)
    head = f"Stiamo. {'Last reminder. ' if step >= len(TONES) - 1 else ''}{n} call{'s' if n != 1 else ''} pending: "
    tail = ". Please mark them done."
    for k in range(n, -1, -1):
        text = head + "; ".join(names[:k]) + (f" and {n - k} more" if k < n else "") + tail
        if len(text) <= limit:
            return text
    return (head + tail)[:limit]


def speech(step: int, pending: list[dict[str, Any]]) -> str:
    """What is said aloud: short, with the names of the calls and no references."""
    names = [str(c.get("title") or "a call") for c in pending]
    shown = names[:5]
    listed = "; ".join(f"{i}, {n}" for i, n in enumerate(shown, 1)) + (f"; and {len(names) - 5} more" if len(names) > 5 else "")
    count = f"{len(names)} calls are" if len(names) != 1 else "A call is"
    return f"Hello, this is Stiamo. {TONES[min(step, len(TONES) - 1)]} {count} still pending. {listed}. Please make them, and mark them done when you have."
