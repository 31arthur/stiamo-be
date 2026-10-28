"""Follow-ups are chases: this works out when to try again from how the last try went."""

from dataclasses import dataclass

from app.domain.common import shift_date
from app.domain.work import Attempt, AttemptResult, FollowUp

_MISSES: tuple[AttemptResult, ...] = ("no_answer", "left_message")
_DAY_END = 18 * 60


@dataclass(frozen=True)
class Moment:
    date: str
    minute: int


@dataclass(frozen=True)
class Hints:
    call_back_at: int | None = None
    promised_for: str | None = None
    best_minute: int | None = None


@dataclass(frozen=True)
class RetrySuggestion:
    date: str
    reason: str
    time: int | None = None
    #: Enough calls have failed that another channel is the better bet.
    switch_channel: bool = False


def is_miss(result: AttemptResult) -> bool:
    return result in _MISSES


def miss_streak(attempts: list[Attempt]) -> int:
    """Consecutive tries without anyone picking up, ending at the latest."""
    n = 0
    for attempt in reversed(attempts):
        if not is_miss(attempt.result):
            break
        n += 1
    return n


def best_minute(contact: str | None, follow_ups: list[FollowUp]) -> int | None:
    """The time of day this contact last actually picked up, if they ever have."""
    who = (contact or "").strip().lower()
    if not who:
        return None
    connected = sorted(
        (
            a
            for f in follow_ups
            if (f.contact or "").strip().lower() == who
            for a in (f.attempts or [])
            if a.result in ("reached", "not_ready")
        ),
        key=lambda a: (a.date, a.minute),
    )
    return round(connected[-1].minute / 15) * 15 if connected else None


def _later(now: Moment, delta: int, reason: str) -> RetrySuggestion:
    minute = -(-(now.minute + delta) // 15) * 15  # round up to the next quarter hour
    if minute > _DAY_END:
        return RetrySuggestion(shift_date(now.date, 1), "Too late today — first thing tomorrow", 10 * 60)
    return RetrySuggestion(now.date, reason, minute)


def suggest_retry(
    before: list[Attempt], result: AttemptResult, now: Moment, hints: Hints | None = None
) -> RetrySuggestion | None:
    """When to try next. Misses back off and move around the day: later today, then a different time
    tomorrow, then two days on, and after three the advice is to switch channel. What the person said
    (a time to call back, a day they promised) always wins over the pattern. `before` is the history
    without this attempt; returns None when nothing more is to be scheduled."""
    hints = hints or Hints()
    if result in ("reached", "unreachable"):
        return None
    if result == "call_back":
        asked = hints.call_back_at
        if asked is None:
            return _later(now, 120, "Two hours on, as they asked")
        if asked > now.minute:
            return RetrySuggestion(now.date, "The time they asked for", asked)
        return RetrySuggestion(shift_date(now.date, 1), "That time has passed — same time tomorrow", asked)
    if result == "they_will_call":
        return RetrySuggestion(shift_date(now.date, 2), "Chase them if they haven’t called by then")
    if result == "not_ready":
        if hints.promised_for:
            return RetrySuggestion(hints.promised_for, "The day they promised")
        return RetrySuggestion(shift_date(now.date, 2), "Two days to get it done")

    attempts = [*before, Attempt(date=now.date, minute=now.minute, result=result)]
    failed = [a for a in attempts if is_miss(a.result)]
    streak = miss_streak(attempts)
    if streak <= 1:
        return _later(now, 120, "Later today — a second try often connects")
    if streak == 2:
        mean = sum(a.minute for a in failed) / len(failed)
        time = hints.best_minute if hints.best_minute is not None else (17 * 60 if mean < 13 * 60 else 10 * 60 + 30)
        reason = "Tomorrow, when they last picked up" if hints.best_minute is not None else "Tomorrow, at a different time of day"
        return RetrySuggestion(shift_date(now.date, 1), reason, time)
    time = hints.best_minute if hints.best_minute is not None else 11 * 60
    if streak == 3:
        return RetrySuggestion(shift_date(now.date, 2), "Two days on — or send a message first", time, True)
    return RetrySuggestion(shift_date(now.date, 3), f"{streak} tries without luck — a message may land better", time, True)
