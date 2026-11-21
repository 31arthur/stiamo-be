from datetime import UTC, datetime

from app.domain.rapport import DAILY_CAP, TIERS, after_reply, after_sent, due_now, may_send, schedule, slot_for, summary


def test_a_message_goes_inside_the_owners_window_and_the_contacts_working_hours():
    assert slot_for("2026-11-23", None) == (600, False)  # an Indian contact on a Monday: 10:00 IST, inside the window
    assert slot_for("2026-11-22", None) is None  # a Sunday: nobody is messaged
    m, outside = slot_for("2026-11-23", "Asia/Qatar")  # Qatar is 2h30 behind: 10:00 IST is 07:30 there, too early; the first fit is 11:30 IST (09:00 there)
    assert (m, outside) == (690, False)
    m, outside = slot_for("2026-11-23", "America/Sao_Paulo")  # Brazil is 8h30 behind: its 09:00 is 17:30 IST, outside the owner's window: the contact's hours win
    assert outside is True and m == 17 * 60 + 30
    assert slot_for("2026-11-26", "Asia/Qatar") is not None and slot_for("2026-11-27", "Asia/Qatar") is None and slot_for("2026-11-28", "Asia/Qatar") is None  # the Gulf weekend is Friday and Saturday


def test_the_gaps_are_dense_at_first_and_widen_and_the_daily_cap_holds_across_everyone():
    plan = schedule("A", "2026-11-23", None)
    days = [t["day"] for t in plan]
    gaps = [(datetime.fromisoformat(b) - datetime.fromisoformat(a)).days for a, b in zip(days, days[1:])]
    assert plan[0]["day"] == "2026-11-23" and [t["purpose"] for t in plan[:5]] == ["intro", "value", "ask", "connect", "invite"]
    assert gaps[:4] == sorted(gaps[:4]) and max(gaps) > min(gaps) and len(plan) == len(TIERS["A"][0]) + 3  # it widens, and it never ends: the rhythm goes on
    assert all(g >= 2 for g in gaps) and all(datetime.fromisoformat(t["day"]).weekday() != 6 for t in plan)  # never twice in a day or two, never on a Sunday
    taken = {"2026-11-23": DAILY_CAP}  # three contacts already have a message that Monday
    other = schedule("A", "2026-11-23", None, taken)
    assert other[0]["day"] == "2026-11-24"  # so this one starts the next day
    assert len(schedule("C", "2026-11-23", None)) < len(plan) and schedule("C", "2026-11-23", None)[1]["day"] == "2026-11-30"


def test_a_touch_is_due_when_its_time_has_come_and_only_for_a_live_plan():
    plan = {"status": "active", "tier": "B", "touches": schedule("B", "2026-11-23", None)}
    assert due_now(plan, datetime(2026, 11, 23, 4, 0, tzinfo=UTC)) is None  # 09:30 IST: before 10:00
    assert due_now(plan, datetime(2026, 11, 23, 4, 30, tzinfo=UTC))["purpose"] == "intro"  # 10:00 IST
    assert due_now({**plan, "status": "paused"}, datetime(2026, 11, 24, tzinfo=UTC)) is None
    assert due_now({**plan, "pausedUntil": "2026-12-30"}, datetime(2026, 11, 24, tzinfo=UTC)) is None


def test_someone_who_does_not_answer_is_written_to_less_and_then_left_alone_and_a_reply_warms_it_again():
    plan = {"status": "active", "tier": "B", "touches": schedule("B", "2026-11-23", None), "unanswered": 0}
    one = after_sent(plan, 0, "2026-11-23")
    two = after_sent(one, 1, "2026-11-26")
    before = next(t for t in one["touches"] if t["state"] == "planned")
    wider = next(t for t in two["touches"] if t["state"] == "planned")
    assert (datetime.fromisoformat(wider["day"]) - datetime.fromisoformat("2026-11-26")).days > (datetime.fromisoformat(before["day"]) - datetime.fromisoformat("2026-11-26")).days  # two with no answer: the gaps double
    three = after_sent(two, 2, "2026-12-03")
    assert three["pausedUntil"] == "2027-02-01" and "three messages" in three["pauseReason"] and three["unanswered"] == 0
    back = after_reply(three, "2026-12-20")
    assert "pausedUntil" not in back and back["unanswered"] == 0 and next(t for t in back["touches"] if t["state"] == "planned")["day"] <= "2026-12-27"


def test_the_caps_and_a_summary():
    assert may_send({}, 0, 0) is None and "most messages for one day" in may_send({}, 0, 3) and "for a month" in may_send({}, 4, 0)
    plan = {"status": "active", "tier": "A", "touches": schedule("A", "2026-11-23", None)}
    s = summary(after_sent(plan, 0, "2026-11-23"))
    assert s["sent"] == 1 and s["planned"] >= 8 and s["next"]["purpose"] == "value"
