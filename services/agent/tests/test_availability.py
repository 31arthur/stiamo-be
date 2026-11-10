"""Time inside an event: the planner asks, remembers, and plans calls into it; and every button says at once what it is doing."""

from app.domain.dayplan import busy_blocks, call_only_blocks, check_blocks, normalize_title
from app.domain.llm import ChatResult, ToolCall

from .conftest import headers
from .test_dayplans import PLANS
from .test_leaks import connect_bot, evening
from .test_orchestrator import team_ready
from .test_planning import TOMORROW

AV = "/v1/agent/availability"
HOSPITAL = {"id": "g-h", "date": TOMORROW, "startMinute": 540, "endMinute": 720, "allDay": False, "title": "Visit hospital", "workspace": "sb"}
OPEN = {"status": "open"}


def day(rig, events=(HOSPITAL,)):
    rig.data.extra_hearings = []
    rig.data.tables = {
        "tasks": [{"id": "t-sb", "title": "Draft the rejoinder", "workspace": "sb", "dueDate": TOMORROW, "estimate": 60, "outcome": OPEN}],
        "follow-ups": [{"id": "f-sb", "title": "Call Hemla", "workspace": "sb", "channel": "call", "due": {"date": TOMORROW}, "duration": 15, "outcome": OPEN}],
        "meetings": [],
        "plans": [],
    }
    rig.data.calendar = list(events)


def script(rig, kind, ident, start):
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {}), ToolCall("b", "propose_schedule", {"blocks": [{"kind": kind, "id": ident, "start": start, "duration": 15 if kind == "followUp" else 60}]})]), ChatResult("Here is the day.")]


async def planner(rig):
    await team_ready(rig)
    bot = await connect_bot(rig, "111:sbp", 601, workspace="sb", role="planner")
    await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "workspaces": ["sb"]}, headers=headers())
    return bot


def press_data(rig, prefix):
    return next(b["callback_data"] for k in rig.telegram.keyboards if k for row in k for b in row if b["callback_data"].startswith(prefix))


async def press(rig, bot, data, update, chat=601, message=900):
    secret = next(sec for url, sec in reversed(rig.telegram.webhooks) if bot["id"] in url)
    body = {"update_id": update, "callback_query": {"id": f"cb{update}", "data": data, "message": {"message_id": message, "chat": {"id": chat}}}}
    r = await rig.client.post(f"/v1/agent/webhooks/telegram/{bot['id']}", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    assert r.status_code == 200
    return r


# --- the rule, without any chat -----------------------------------------------------------------------------------------------


def test_a_window_where_calls_are_fine_takes_follow_ups_but_not_tasks():
    event = {"id": "e", "date": "2026-11-11", "startMinute": 540, "endMinute": 720, "allDay": False, "title": "Visit hospital"}
    assert busy_blocks("2026-11-11", [], [], [], [], calendar=[event]) == [(540, 720, "calendar: Visit hospital")]  # nothing said: busy
    calls = {**event, "mode": "calls"}
    assert busy_blocks("2026-11-11", [], [], [], [], calendar=[calls]) == []
    windows = call_only_blocks("2026-11-11", [], [calls])
    assert windows == [(540, 720, "Visit hospital")]
    call_block = {"kind": "followUp", "title": "Call Hemla", "start": 600, "duration": 15}
    task_block = {"kind": "task", "title": "Draft", "start": 600, "duration": 60}
    assert check_blocks([call_block], [], windows) == []
    problems = check_blocks([task_block], [], windows)
    assert len(problems) == 1 and "only calls and follow-ups fit" in problems[0]
    free = {**event, "mode": "free"}
    assert check_blocks([task_block], busy_blocks("2026-11-11", [], [], [], [], calendar=[free]), call_only_blocks("2026-11-11", [], [free])) == []  # free for anything
    assert busy_blocks("2026-11-11", [], [], [], [], calendar=[{**event, "mode": "busy"}]) != []  # busy stays busy


def test_titles_are_remembered_however_they_are_written():
    assert normalize_title("Visit Hospital!") == normalize_title("  visit   hospital ") == "visit hospital"


# --- asking, answering and re-planning -----------------------------------------------------------------------------------------


async def test_the_planner_asks_a_short_question_about_a_long_event_and_plans_calls_into_it_when_told(rig):
    bot = await planner(rig)
    day(rig)
    script(rig, "followUp", "f-sb", 600)  # the plan made before the answer: 10:00 is inside the hospital visit, so it is busy
    rig.telegram.sent.clear()
    rig.telegram.keyboards.clear()
    made = await evening(rig)
    asked = [t for chat, t in rig.telegram.sent if "Free for calls then?" in t]
    assert len(asked) == 1 and asked[0].startswith("🏥 <b>Visit hospital</b>, tomorrow 09:00–12:00") and len(asked[0].splitlines()) == 2  # short, human, pictographic
    assert made[0]["blocks"] == []  # nothing was said yet, so the call could not go in the window

    # the answer: calls are fine → at once a toast, then the full confirmation, then the new plan
    script(rig, "followUp", "f-sb", 600)
    rig.telegram.sent.clear()
    await press(rig, bot, press_data(rig, "av:c:"), 2)
    assert rig.telegram.toasts[-1].startswith("⏳ Noting you can take calls")
    confirmation = next(t for chat, t in rig.telegram.sent if t.startswith("✅ Got it, noted."))
    assert "🏥 Visit hospital · " in confirmation and "09:00–12:00" in confirmation and "calls and follow-ups" in confirmation
    assert "I'll remember this for “Visit hospital”" in confirmation and "Remembered answers" in confirmation
    assert "re-planned" in confirmation and "1 call now sits inside the window" in confirmation
    new_plan = next(t for chat, t in rig.telegram.sent if "plan for" in t)
    assert "10:00" in new_plan and "Call Hemla" in new_plan
    plan = (await rig.client.get(PLANS, headers=headers())).json()["plans"][0]
    assert [(b["id"], b["from"]) for b in plan["blocks"]] == [("f-sb", "10:00")]


async def test_a_task_does_not_go_into_a_window_that_is_only_for_calls(rig):
    bot = await planner(rig)
    day(rig)
    script(rig, "followUp", "f-sb", 780)
    await evening(rig)
    script(rig, "followUp", "f-sb", 780)
    await press(rig, bot, press_data(rig, "av:c:"), 2)
    script(rig, "task", "t-sb", 600)
    out = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "sb"}, headers=headers())).json()
    assert out["blocks"] == []  # refused: only calls fit in there


async def test_free_for_anything_takes_a_task_too(rig):
    bot = await planner(rig)
    day(rig)
    script(rig, "followUp", "f-sb", 780)
    await evening(rig)
    script(rig, "task", "t-sb", 600)
    await press(rig, bot, press_data(rig, "av:f:"), 2)
    plan = (await rig.client.get(PLANS, headers=headers())).json()["plans"][0]
    assert [(b["id"], b["from"]) for b in plan["blocks"]] == [("t-sb", "10:00")]


async def test_an_answer_is_remembered_so_the_same_event_is_not_asked_twice_and_can_be_forgotten(rig):
    bot = await planner(rig)
    day(rig)
    script(rig, "followUp", "f-sb", 780)
    await evening(rig)
    script(rig, "followUp", "f-sb", 780)
    await press(rig, bot, press_data(rig, "av:b:"), 2)
    assert (await rig.client.get(AV, headers=headers())).json()["rules"] == [{"title": "visit hospital", "mode": "busy"}]
    rig.telegram.sent.clear()
    script(rig, "followUp", "f-sb", 780)
    await evening(rig)  # the next evening: the same event, already answered
    assert not [t for _, t in rig.telegram.sent if "Free for calls then?" in t]
    assert (await rig.client.delete(f"{AV}/rules/visit%20hospital", headers=headers())).status_code == 204
    assert (await rig.client.get(AV, headers=headers())).json()["rules"] == []
    day(rig)
    script(rig, "followUp", "f-sb", 780)
    await evening(rig)
    assert [t for _, t in rig.telegram.sent if "Free for calls then?" in t]  # forgotten: asked again


async def test_short_events_hearings_all_day_and_other_workspaces_events_are_not_asked_about(rig):
    await planner(rig)
    short = {**HOSPITAL, "id": "g1", "title": "Quick chat", "endMinute": 570}
    hearing = {**HOSPITAL, "id": "g2", "title": "Hearing at the High Court"}
    other = {**HOSPITAL, "id": "g3", "title": "Sprint review", "workspace": "fsa"}
    allday = {**HOSPITAL, "id": "g4", "title": "Holiday", "allDay": True}
    day(rig, (short, hearing, other, allday))
    script(rig, "followUp", "f-sb", 780)
    rig.telegram.sent.clear()
    await evening(rig)
    assert not [t for _, t in rig.telegram.sent if "Free for calls then?" in t]


async def test_at_most_three_questions_and_the_plan_waits_for_the_last_answer(rig):
    bot = await planner(rig)
    events = [{**HOSPITAL, "id": f"g{i}", "title": f"Visit {name}", "startMinute": 540 + i * 130, "endMinute": 540 + i * 130 + 120} for i, name in enumerate(("hospital", "bank", "school", "market"))]
    day(rig, events)
    script(rig, "followUp", "f-sb", 1190)
    rig.telegram.keyboards.clear()
    await evening(rig)
    questions = [b["callback_data"] for k in rig.telegram.keyboards if k for row in k for b in row if b["callback_data"].startswith("av:c:")]
    assert len(questions) == 3
    rig.telegram.sent.clear()
    await press(rig, bot, questions[0], 2)
    assert "2 more questions first" in next(t for _, t in rig.telegram.sent if t.startswith("✅ Got it"))  # no re-plan yet
    assert not [t for _, t in rig.telegram.sent if t.startswith("🗓️")]  # the new plan is not sent yet


async def test_a_question_that_is_out_of_date_is_said_so_not_ignored(rig):
    bot = await planner(rig)
    await press(rig, bot, "av:c:deadbeef", 2)
    assert "out of date" in rig.telegram.sent[-1][1] and rig.telegram.sent[-1][1].startswith("⚠️")


# --- every button says at once what it is doing ----------------------------------------------------------------------------------


async def plan_to_approve(rig):
    bot = await planner(rig)
    day(rig, ())
    script(rig, "task", "t-sb", 660)
    plan = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "sb"}, headers=headers())).json()
    return bot, plan


async def test_a_button_is_answered_at_once_and_a_message_says_it_is_being_done_then_becomes_the_result(rig):
    bot, plan = await plan_to_approve(rig)
    rig.telegram.sent.clear()
    await press(rig, bot, f"pl:a:{plan['id']}", 2)
    assert rig.telegram.toasts[-1] == "⏳ Putting it on your planner…"  # the instant answer
    holding = rig.telegram.edits[-1]
    assert holding[2].startswith("✅ Plan approved · S&B · ") and "1 item is now on your planner:" in holding[2]
    assert "11:00–12:00  📝 Draft the rejoinder" in holding[2] and "Google Calendar updated" in holding[2] and "Nothing more to do" in holding[2]  # the long, detailed confirmation
    assert len(rig.telegram.sent) == 1 and rig.telegram.sent[0][1] == holding[2]  # one message: the "⏳" one, now holding the result


async def test_a_press_still_works_when_telegram_refuses_the_instant_answer_or_the_edit(rig):
    bot, plan = await plan_to_approve(rig)
    rig.telegram.answer_fails = True  # "query is too old": this used to stop the press and say nothing
    await press(rig, bot, f"pl:a:{plan['id']}", 2)
    assert rig.data.saved and "Plan approved" in rig.telegram.sent[-1][1]
    rig.telegram.answer_fails = False
    rig.telegram.edit_fails = True  # the "⏳" message can't be changed: the result is sent on its own
    script(rig, "task", "t-sb", 780)
    again = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "sb", "force": True}, headers=headers())).json()
    before = len(rig.telegram.sent)
    await press(rig, bot, f"pl:a:{again['id']}", 3)
    assert len(rig.telegram.sent) == before + 2 and "Plan approved" in rig.telegram.sent[-1][1]


async def test_a_button_that_fails_or_is_out_of_date_says_so(rig):
    bot, plan = await plan_to_approve(rig)
    await press(rig, bot, "pl:a:no-such-plan", 2)
    assert rig.telegram.sent[-1][1].startswith("⚠️ No such plan.") and "Nothing was changed" in rig.telegram.sent[-1][1]
    await press(rig, bot, "zz:q:1", 3)
    assert "older message" in rig.telegram.sent[-1][1]
    await press(rig, bot, f"pl:x:{plan['id']}", 4)
    assert rig.telegram.sent[-1][1].startswith("👍 Dropped. Nothing was changed")
    await press(rig, bot, f"pl:a:{plan['id']}", 5)
    assert "already been dealt with" in rig.telegram.sent[-1][1]  # a second tap on a settled plan says so


async def test_what_is_said_about_one_workspaces_event_carries_over_as_bare_busy_time(rig):
    from app.application.availdata import AvailabilityData
    from app.application.scope import PlannerData

    day(rig, ({**HOSPITAL, "workspace": "fsa"},))
    raw = {"rules": {"visit hospital": "calls"}}
    sb = PlannerData(AvailabilityData(rig.data, raw), "sb")  # S&B's planner, while the FSA event is "calls are fine"
    events = await sb.calendar_events(TOMORROW, TOMORROW)
    assert [(e["title"], e.get("mode")) for e in events] == [("Busy (another workspace, calls are fine)", "calls")]
    assert "hospital" not in str(events).lower()  # the time and that calls fit: never what it is
    free = PlannerData(AvailabilityData(rig.data, {"rules": {"visit hospital": "free"}}), "sb")
    assert await free.calendar_events(TOMORROW, TOMORROW) == []  # free for anything: it takes nothing from anyone's day
