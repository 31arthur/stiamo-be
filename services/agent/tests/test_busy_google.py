import json

from app.domain.dayplan import all_day_on, busy_blocks, calendar_on
from app.domain.llm import ChatResult, ToolCall

from .conftest import headers
from .test_agent_api import choose, connect
from .test_dayplans import PLANS, generate, plan_script
from .test_planning import PLANNER, TOMORROW, fill

BOARD = {"id": "g1", "title": "Board call", "date": TOMORROW, "endDate": TOMORROW, "allDay": False, "startMinute": 660, "endMinute": 750, "calendar": "Work", "workspace": "fsa", "location": "Meet"}
HOLIDAY = {"id": "g2", "title": "State holiday", "date": TOMORROW, "endDate": TOMORROW, "allDay": True, "calendar": "Holidays", "workspace": "personal"}


async def ask(rig, calls):
    await connect(rig)
    await choose(rig, agent=PLANNER)
    rig.script += [ChatResult("", [ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(calls)]), ChatResult("ok")]
    await rig.client.post(f"{PLANNER}/chat", json={"message": "x"}, headers=headers())
    return json.loads(rig.llm_log[-1]["last"])


def test_a_google_event_with_a_time_is_busy_and_an_all_day_one_is_only_noted():
    assert busy_blocks(TOMORROW, [], [], [], [], calendar=[BOARD, HOLIDAY]) == [(660, 750, "calendar: Board call")]
    assert [e["title"] for e in calendar_on(TOMORROW, [BOARD, HOLIDAY])] == ["Board call"]
    assert [e["title"] for e in all_day_on(TOMORROW, [BOARD, HOLIDAY])] == ["State holiday"]
    trip = {**HOLIDAY, "date": "2026-11-04", "endDate": "2026-11-06"}
    assert all_day_on("2026-11-05", [trip]) and not all_day_on("2026-11-07", [trip])


async def test_the_day_shows_the_google_events_and_leaves_no_free_time_under_them(rig):
    fill(rig)
    rig.data.calendar = [BOARD, HOLIDAY]
    day = await ask(rig, [("day_agenda", {})])
    assert day["calendarEvents"] == [{"id": "g1", "time": "11:00–12:30", "title": "Board call", "calendar": "Work", "where": "Meet"}] and day["allDayEvents"] == ["State holiday"]
    assert day["freeWindows"] == ["09:00–10:00", "12:30–14:00", "15:00–21:00"]  # the hearing at 10, the meeting at 14, and the board call all stay clear


async def test_a_schedule_cannot_be_placed_over_a_google_event(rig):
    fill(rig)
    rig.data.calendar = [BOARD]
    out = await ask(rig, [("propose_schedule", {"blocks": [{"kind": "task", "id": "t1", "start": 690, "duration": 60}]})])
    assert out["ok"] is False and any("calendar: Board call" in p for p in out["problems"])


async def test_what_is_coming_includes_the_google_events(rig):
    fill(rig)
    rig.data.calendar = [BOARD, HOLIDAY]
    out = await ask(rig, [("upcoming", {"days": 3})])
    day = next(d for d in out["busyDays"] if d["date"] == TOMORROW)
    assert day["calendar"] == ["State holiday", "Board call"]


async def test_approving_is_refused_if_a_google_event_has_appeared_since(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    plan_script(rig)
    plan = (await generate(rig, day=TOMORROW)).json()
    rig.data.calendar = [{**BOARD, "startMinute": 650, "endMinute": 700}]  # lands on the 11:00 task the plan placed
    r = await rig.client.post(f"{PLANS}/{plan['id']}/apply", headers=headers())
    assert r.status_code == 409 and "Board call" in r.json()["error"]["message"] and rig.data.saved == []
