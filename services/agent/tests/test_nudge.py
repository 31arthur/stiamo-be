from app.domain.nudge import LADDER, due_run, free_now, next_due, runs, speech


def call(i, start, duration=15, status="open", day="2026-11-06"):
    return {"id": i, "title": f"Call {i}", "due": {"date": day, "time": start}, "duration": duration, "outcome": {"status": status}}


def test_calls_lined_up_one_after_the_other_are_one_run_that_ends_with_the_last():
    found = runs("2026-11-06", [call("a", 540), call("b", 555), call("c", 560, 30), call("d", 900), {"id": "x", "title": "No time", "due": {"date": "2026-11-06"}}, call("y", 540, day="2026-11-07")])
    assert [(r["key"], r["start"], r["end"], [c["id"] for c in r["calls"]]) for r in found] == [("a", 540, 590, ["a", "b", "c"]), ("d", 900, 915, ["d"])]


def test_the_reminders_come_ten_minutes_after_the_run_then_an_hour_half_an_hour_and_ten_minutes_after_each_one():
    run = runs("2026-11-06", [call("a", 540)])[0]  # ends 09:15
    assert LADDER == (10, 60, 30, 10)
    assert next_due(run, {}) == 565
    assert next_due(run, {"step": 1, "last": 566}) == 626
    assert next_due(run, {"step": 2, "last": 630}) == 660
    assert next_due(run, {"step": 3, "last": 661}) == 671
    assert next_due(run, {"step": 4, "last": 672}) is None  # they have all been sent


def test_a_reminder_is_due_only_when_the_time_has_come_something_is_still_open_and_it_is_the_working_day():
    f = [call("a", 540), call("b", 555, status="done")]
    assert due_run(579, "2026-11-06", f, {}) is None and due_run(580, "2026-11-06", f, {})["pending"] == [f[0]]
    assert due_run(700, "2026-11-06", [call("a", 540, status="done")], {}) is None  # all done: nothing to say
    assert due_run(580, "2026-11-06", f, {"a": {"step": 1, "last": 580}}) is None  # one was just sent
    assert due_run(8 * 60, "2026-11-06", [call("a", 6 * 60)], {}) is None and due_run(21 * 60 + 5, "2026-11-06", [call("a", 20 * 60)], {}) is None  # not before nine or after nine at night


def test_it_waits_while_something_else_is_on_but_the_calls_themselves_do_not_count():
    day = "2026-11-06"
    meeting = {"id": "m", "title": "Union", "slot": {"date": day, "start": 600, "duration": 60}, "outcome": {"status": "open"}}
    a = call("a", 540)
    assert free_now(570, day, [a], [], [meeting], [], [a], []) is True
    assert free_now(630, day, [a], [], [meeting], [], [a], []) is False
    assert free_now(545, day, [a], [], [], [], [a], []) is True  # its own call
    event = {"date": day, "startMinute": 700, "endMinute": 760, "title": "Court"}
    assert free_now(710, day, [a], [], [], [], [a], [event]) is False


def test_the_spoken_reminder_names_the_calls_and_gets_firmer_each_time():
    pending = [{"title": "Call him again"}, {"title": "Chase the labour office"}]
    first, last = speech(0, pending), speech(3, pending)
    assert "A reminder." in first and "2 calls are still pending" in first and "1, Call him again; 2, Chase the labour office" in first
    assert "last reminder" in last and "A call is still pending" in speech(0, pending[:1])
    assert "and 2 more" in speech(0, [{"title": f"C{i}"} for i in range(7)])
