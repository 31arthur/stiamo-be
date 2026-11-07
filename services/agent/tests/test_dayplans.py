import asyncio
from datetime import UTC, datetime

from app.application.dayplans import seconds_until, tomorrow_in
from app.domain.llm import ChatResult, ToolCall
from app.domain.phone import strip_markup

from .conftest import headers
from .test_agent_api import choose, connect
from .test_channels import send, telegram
from .test_listen import until
from .test_planning import PLANNER, TOMORROW, fill

PLANS = "/v1/agent/day-plans"
GOOD = {"blocks": [{"kind": "task", "id": "t1", "start": 660, "duration": 90}, {"kind": "followUp", "id": "f1", "start": 570}]}


async def planner_ready(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)


def plan_script(rig, blocks=GOOD, answer="Tomorrow: the hearing at 10:00, then the rejoinder."):
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {}), ToolCall("b", "propose_schedule", blocks)]), ChatResult(answer)]


async def generate(rig, **body):
    return await rig.client.post(f"{PLANS}/generate", json=body, headers=headers())


def test_the_next_nine_pm_in_india_is_found_whatever_the_server_clock_says():
    now = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)  # 19:30 in India
    assert seconds_until(now, "21:00", "Asia/Kolkata") == 90 * 60
    late = datetime(2026, 11, 1, 16, 0, tzinfo=UTC)  # 21:30 in India: tomorrow's nine
    assert seconds_until(late, "21:00", "Asia/Kolkata") == 23.5 * 3600
    assert tomorrow_in(datetime(2026, 11, 1, 19, 0, tzinfo=UTC), "Asia/Kolkata") == "2026-11-03"  # it is already the 2nd there


async def test_the_evening_plan_is_off_until_it_is_set_and_checks_what_it_is_given(rig):
    first = (await rig.client.get(PLANS, headers=headers())).json()
    assert first["settings"] == {"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "channels": True} and first["plans"] == []
    for bad in ({"time": "9pm"}, {"time": "25:00"}, {"timezone": "Mars/Base"}):
        r = await rig.client.put(f"{PLANS}/settings", json={"enabled": True, "time": "21:00", "timezone": "Asia/Kolkata", **bad}, headers=headers())
        assert r.status_code == 422
    ok = await rig.client.put(f"{PLANS}/settings", json={"enabled": True, "time": "20:45", "timezone": "Asia/Kolkata", "channels": False}, headers=headers())
    assert ok.json()["settings"]["time"] == "20:45" and ok.json()["schedule"]["scheduled"] is True and ok.json()["schedule"]["at"] == "20:45"
    off = await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "20:45", "timezone": "Asia/Kolkata"}, headers=headers())
    assert off.json()["schedule"]["scheduled"] is False


async def test_a_plan_is_made_kept_and_books_nothing_until_it_is_approved(rig):
    await planner_ready(rig)
    plan_script(rig)
    out = (await generate(rig, day=TOMORROW)).json()
    assert out["status"] == "proposed" and out["day"] == TOMORROW and "rejoinder" in out["briefing"]
    assert [(b["id"], b["from"], b["to"]) for b in out["blocks"]] == [("f1", "09:30", "09:45"), ("t1", "11:00", "12:30")]
    assert rig.data.saved == []
    listed = (await rig.client.get(PLANS, headers=headers())).json()["plans"]
    assert [p["id"] for p in listed] == [out["id"]]
    runs = (await rig.client.get("/v1/agent/agents/daily-planner/runs", headers=headers())).json()["items"]
    assert runs[0]["trigger"] == "manual"


async def test_approving_a_plan_puts_a_time_on_each_task_and_call(rig):
    await planner_ready(rig)
    plan_script(rig)
    plan = (await generate(rig, day=TOMORROW)).json()
    done = (await rig.client.post(f"{PLANS}/{plan['id']}/apply", headers=headers())).json()
    assert done["booked"] == 2 and done["plan"]["status"] == "applied"
    by_table = {t: r for t, r in rig.data.saved}
    assert by_table["tasks"]["slot"] == {"date": TOMORROW, "start": 660, "duration": 90} and by_table["tasks"]["id"] == "t1"
    assert by_table["follow-ups"]["due"] == {"date": TOMORROW, "time": 570} and by_table["follow-ups"]["duration"] == 15
    assert (await rig.client.post(f"{PLANS}/{plan['id']}/apply", headers=headers())).status_code == 409  # only once
    plan_script(rig)
    assert (await generate(rig, day=TOMORROW)).status_code == 409  # an approved day is not quietly replaced
    plan_script(rig)
    assert (await generate(rig, day=TOMORROW, force=True)).status_code == 200


async def test_a_plan_is_refused_if_the_day_has_changed_since(rig):
    await planner_ready(rig)
    plan_script(rig)
    plan = (await generate(rig, day=TOMORROW)).json()
    rig.data.tables["meetings"].append({"id": "m2", "title": "Urgent call", "slot": {"date": TOMORROW, "start": 650, "duration": 60}, "outcome": {"status": "open"}})
    r = await rig.client.post(f"{PLANS}/{plan['id']}/apply", headers=headers())
    assert r.status_code == 409 and "Urgent call" in r.json()["error"]["message"]
    assert rig.data.saved == []


async def test_a_plan_can_be_dismissed_and_is_only_ever_the_owners(rig):
    await planner_ready(rig)
    plan_script(rig)
    plan = (await generate(rig, day=TOMORROW)).json()
    assert (await rig.client.post(f"{PLANS}/{plan['id']}/dismiss", headers=headers("other@example.com", "u2"))).status_code == 404
    assert (await rig.client.post(f"{PLANS}/{plan['id']}/dismiss", headers=headers())).json()["status"] == "dismissed"
    assert (await rig.client.post(f"{PLANS}/{plan['id']}/apply", headers=headers())).status_code == 409
    assert (await rig.client.get(PLANS)).status_code == 401


async def test_the_planner_in_a_chat_shows_its_schedule_on_the_phone(rig):
    from app.application.channels import format_reply
    from app.application.runner import AgentRun

    card = {"kind": "schedule", "id": "x", "date": TOMORROW, "blocks": [{"from": "09:30", "title": "Call the advocate"}, {"from": "11:00", "title": "Draft the rejoinder"}]}
    text = format_reply(AgentRun("Here you go.", [], [card], []), 3000)
    assert "Here you go." in text and "• 09:30 Call the advocate" in text and "Approve it in Stiamo" in text


async def test_the_evening_run_plans_tomorrow_and_tells_the_phone(rig):
    await planner_ready(rig)
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    plan_script(rig)
    rig.app.state.daily.delay_for = lambda at, zone: 0.02
    await rig.client.put(f"{PLANS}/settings", json={"enabled": True, "time": "21:00", "timezone": "Asia/Kolkata", "channels": True}, headers=headers())
    assert await until(lambda: any("Proposed schedule" in t for _, t in rig.telegram.sent), tries=200)
    rig.app.state.daily.delay_for = lambda at, zone: 3600  # no further runs; let the one that is under way finish before the loop is stopped
    await asyncio.sleep(0.3)
    rig.app.state.daily.stop("u1")
    text = strip_markup(next(t for _, t in rig.telegram.sent if "Proposed schedule" in t))
    assert "Plan for" in text and "09:30 Call the advocate" in text and "Approve it in Stiamo" in text
    plans = (await rig.client.get(PLANS, headers=headers())).json()["plans"]
    assert plans and plans[0]["status"] == "proposed"
    runs = (await rig.client.get("/v1/agent/agents/daily-planner/runs", headers=headers())).json()["items"]
    assert runs[0]["trigger"] == "evening"
