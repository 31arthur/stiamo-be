from app.domain.chase import Hints, Moment, best_minute, miss_streak, suggest_retry
from app.domain.common import Outcome, TimeSlot, offset_minutes, progress_of, shift_date
from app.domain.confirmations import confirmation_due, sync_confirmations
from app.domain.work import Attempt, FollowUp, Meeting

NOW = Moment("2026-03-10", 10 * 60)


def attempt(result, minute=600, date="2026-03-09"):
    return Attempt(date=date, minute=minute, result=result)


def test_calendar_arithmetic_crosses_midnight_and_month_ends():
    assert shift_date("2026-02-28", 1) == "2026-03-01"
    assert offset_minutes("2026-03-10", 30, -60) == ("2026-03-09", 23 * 60 + 30)
    assert offset_minutes("2026-03-10", 23 * 60, 120) == ("2026-03-11", 60)


def test_progress_ignores_cancelled_and_scores_partial_as_half():
    outs = [Outcome(status="done"), Outcome(status="partial"), Outcome(status="not_done"), Outcome(status="cancelled")]
    assert progress_of(outs) == 0.5
    assert progress_of([Outcome(status="cancelled")]) is None


def test_retry_backs_off_with_each_miss():
    first = suggest_retry([], "no_answer", NOW)
    assert (first.date, first.time) == ("2026-03-10", 12 * 60)
    second = suggest_retry([attempt("no_answer", 600)], "no_answer", NOW)
    assert second.date == "2026-03-11" and second.time == 17 * 60  # morning misses -> try the evening
    third = suggest_retry([attempt("no_answer"), attempt("left_message")], "no_answer", NOW)
    assert third.date == "2026-03-12" and third.switch_channel
    assert suggest_retry([attempt("no_answer")] * 3, "no_answer", NOW).switch_channel


def test_retry_late_in_the_day_waits_for_tomorrow():
    late = suggest_retry([], "no_answer", Moment("2026-03-10", 17 * 60))
    assert (late.date, late.time) == ("2026-03-11", 10 * 60)


def test_what_the_person_said_wins():
    asked = suggest_retry([], "call_back", NOW, Hints(call_back_at=15 * 60))
    assert (asked.date, asked.time) == ("2026-03-10", 15 * 60)
    passed = suggest_retry([], "call_back", NOW, Hints(call_back_at=9 * 60))
    assert passed.date == "2026-03-11"
    assert suggest_retry([], "not_ready", NOW, Hints(promised_for="2026-03-20")).date == "2026-03-20"
    assert suggest_retry([], "reached", NOW) is None and suggest_retry([], "unreachable", NOW) is None


def test_miss_streak_and_best_time():
    assert miss_streak([attempt("reached"), attempt("no_answer"), attempt("left_message")]) == 2
    f = FollowUp(id="f", user_id="u", created_at="", updated_at="", title="x", contact="Ravi",
                 attempts=[attempt("reached", 14 * 60 + 10)])
    assert best_minute(" ravi ", [f]) == 14 * 60 + 15
    assert best_minute("nobody", [f]) is None


def meeting(**kw):
    base = dict(id="m1", user_id="u", created_at="", updated_at="", kind="appointment", title="Visa",
                slot=TimeSlot(date="2026-03-10", start=10 * 60, duration=30), counterpart="Ravi",
                confirmations=["day_before", "hour_before"])
    return Meeting(**{**base, **kw})


def follow_up(**kw):
    base = dict(id="f1", user_id="u", created_at="", updated_at="", title="t")
    return FollowUp(**{**base, **kw})


def test_confirmation_times():
    slot = TimeSlot(date="2026-03-10", start=7 * 60, duration=30)
    assert confirmation_due(slot, "day_before").model_dump() == {"date": "2026-03-09", "time": 9 * 60}  # not before 9
    late = TimeSlot(date="2026-03-10", start=21 * 60, duration=30)
    assert confirmation_due(late, "day_before").time == 19 * 60  # not after 19
    assert confirmation_due(slot, "hour_before").model_dump() == {"date": "2026-03-10", "time": 6 * 60}


def test_sync_creates_moves_and_removes():
    m = meeting()
    plan = sync_confirmations(m, [])
    assert {c["trigger"] for c in plan.create} == {"day_before", "hour_before"}

    generated = [follow_up(id=f"g-{t}", trigger=t, subject={"kind": "meeting", "id": "m1"},
                           title="Confirm appointment — Ravi", due=confirmation_due(m.slot, t)) for t in m.confirmations]
    assert sync_confirmations(m, generated).create == []

    moved = meeting(slot=TimeSlot(date="2026-03-12", start=10 * 60, duration=30))
    assert len(sync_confirmations(moved, generated).update) == 2

    fewer = meeting(confirmations=["day_before"])
    assert [f.trigger for f in sync_confirmations(fewer, generated).remove] == ["hour_before"]

    cancelled = meeting(outcome=Outcome(status="cancelled"))
    assert len(sync_confirmations(cancelled, generated).remove) == 2


def test_sync_never_touches_follow_ups_with_a_result():
    m = meeting(confirmations=[])
    done = follow_up(trigger="day_before", subject={"kind": "meeting", "id": "m1"}, outcome=Outcome(status="done"))
    assert sync_confirmations(m, [done]).remove == []
