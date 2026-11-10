"""A day plan for each workspace: its own work, around everything else on the day, to its own bot, and into its own Google calendar."""

from app.application.scope import PlannerData
from app.application.services import Who
from app.domain.llm import ChatResult, ToolCall
from app.presentation import deps

from .conftest import headers
from .test_alerts import press
from .test_channels import send, telegram
from .test_dayplans import PLANS, planner_ready
from .test_planning import TOMORROW, fill

ME = Who("u1", "arjun@example.com")


def own(rig):
    """S&B has the rejoinder and the advocate's call; FSA has the court fee and the labour office; the union meeting at 14:00 is FSA's."""
    fill(rig)
    for t, ws in (("t1", "sb"), ("t2", "fsa"), ("t3", "sb")):
        next(r for r in rig.data.tables["tasks"] if r["id"] == t)["workspace"] = ws
    for f, ws in (("f1", "sb"), ("f2", "fsa")):
        next(r for r in rig.data.tables["follow-ups"] if r["id"] == f)["workspace"] = ws
    rig.data.tables["meetings"][0]["workspace"] = "fsa"


def plan(rig, blocks, answer="Here is the day."):
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {}), ToolCall("b", "propose_schedule", {"blocks": blocks})]), ChatResult(answer)]


SB = [{"kind": "task", "id": "t1", "start": 660, "duration": 90}]
FSA = [{"kind": "task", "id": "t2", "start": 660, "duration": 20}]


async def evening(rig):
    async with rig.app.state.session_factory() as session:
        made = await deps.build_day_plans(rig.app, session).evening(ME, "minted-for-u1")
        await session.commit()
    return made


async def pair(rig, c, chat, update=1):
    await send(rig, c, f"/start {c['pairingCode']}", chat=chat, update=update)


async def test_a_workspace_plans_only_its_own_work_but_sees_the_rest_of_the_day_as_taken(rig):
    own(rig)
    sb = PlannerData(rig.data, "sb")
    assert sorted(t["id"] for t in await sb.records("tasks")) == ["t1", "t3"] and [f["id"] for f in await sb.records("follow-ups")] == ["f1"]
    taken = [(e["startMinute"], e["endMinute"], e["title"]) for e in await sb.calendar_events(TOMORROW, TOMORROW)]
    assert (840, 900, "Busy (another workspace)") in taken  # FSA's meeting blocks S&B's time, and says nothing more about itself
    assert not any("Union leaders" in t for _, _, t in taken)


async def test_each_workspace_keeps_its_own_plan_for_the_same_day(rig):
    await planner_ready(rig)
    own(rig)
    plan(rig, SB)
    a = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "sb"}, headers=headers())).json()
    plan(rig, FSA)
    b = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "fsa"}, headers=headers())).json()
    assert a["id"] != b["id"] and (a["workspace"], b["workspace"]) == ("sb", "fsa")
    assert [x["id"] for x in a["blocks"]] == ["t1"] and [x["id"] for x in b["blocks"]] == ["t2"]  # each chose only from its own work
    assert (await rig.client.post(f"{PLANS}/{a['id']}/apply", headers=headers())).status_code == 200
    plan(rig, FSA)  # S&B's day being approved doesn't stop FSA's being planned again
    assert (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "fsa"}, headers=headers())).status_code == 200
    listed = (await rig.client.get(PLANS, params={"workspace": "sb"}, headers=headers())).json()["plans"]
    assert [p["workspace"] for p in listed] == ["sb"]
    assert (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "mars"}, headers=headers())).status_code == 422


async def test_an_approved_plan_is_sent_to_google_straight_away(rig):
    await planner_ready(rig)
    own(rig)
    plan(rig, SB)
    p = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "sb"}, headers=headers())).json()
    rig.data.syncs = 0
    done = (await rig.client.post(f"{PLANS}/{p['id']}/apply", headers=headers())).json()
    assert done["booked"] == 1 and rig.data.syncs == 1 and done["calendar"]["sent"]["added"] == 2
    rig.data.sync_result = None  # Google or the calendar service is away: the plan is still booked, and the background sync catches up
    plan(rig, FSA)
    q = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "fsa"}, headers=headers())).json()
    again = (await rig.client.post(f"{PLANS}/{q['id']}/apply", headers=headers())).json()
    assert again["booked"] == 1 and again["calendar"] is None


async def test_the_evening_sends_each_workspaces_plan_to_its_own_bot_with_approve_and_drop(rig):
    await planner_ready(rig)
    own(rig)
    general = await telegram(rig)
    await pair(rig, general, 555)
    sb = (await rig.client.post("/v1/agent/channels/telegram", json={"botToken": "456:def", "workspace": "sb"}, headers=headers())).json()
    await pair(rig, sb, 888)
    await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "workspaces": ["sb", "fsa"]}, headers=headers())
    plan(rig, SB)
    plan(rig, FSA)
    rig.telegram.sent.clear()
    rig.telegram.keyboards.clear()
    made = await evening(rig)
    assert [p["workspace"] for p in made] == ["sb", "fsa"]
    where = {chat: text for chat, text in rig.telegram.sent if "plan for" in text}
    assert "S&amp;B plan for" in where[888] or "S&B plan for" in where[888]  # S&B's own bot
    assert "FSA plan for" in where[555] and 555 in where and 888 in where  # FSA has no bot: the general one carries it
    assert "Tap" in where[888] and "Approve" in where[888]
    keys = [b["callback_data"] for k in rig.telegram.keyboards if k for row in k for b in row]
    assert sorted(k.split(":")[1] for k in keys) == ["a", "a", "x", "x"] and all(k.startswith("pl:") for k in keys)


async def test_approving_on_the_phone_books_the_times_and_updates_google(rig):
    await planner_ready(rig)
    own(rig)
    sb = (await rig.client.post("/v1/agent/channels/telegram", json={"botToken": "456:def", "workspace": "sb"}, headers=headers())).json()
    await pair(rig, sb, 888)
    plan(rig, SB)
    p = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "sb"}, headers=headers())).json()
    rig.data.syncs = 0
    await press(rig, sb, f"pl:a:{p['id']}", update=2, chat=888)
    assert rig.data.saved and rig.data.saved[0][1]["slot"] == {"date": TOMORROW, "start": 660, "duration": 90}
    said = rig.telegram.sent[-1][1]
    assert "On your planner: 1 item" in said and "Google calendar" in said and rig.data.syncs == 1
    assert rig.telegram.stripped[-1][2] is None  # the buttons are gone
    await press(rig, sb, f"pl:a:{p['id']}", update=3, chat=888)  # pressed again: it only says so
    assert "already been dealt with" in rig.telegram.sent[-1][1] and len(rig.data.saved) == 1
    await press(rig, sb, "pl:x:nope", update=4, chat=888)
    assert rig.telegram.sent[-1][1].startswith("⚠️")


async def test_dropping_a_plan_on_the_phone_changes_nothing(rig):
    await planner_ready(rig)
    own(rig)
    sb = (await rig.client.post("/v1/agent/channels/telegram", json={"botToken": "456:def", "workspace": "sb"}, headers=headers())).json()
    await pair(rig, sb, 888)
    plan(rig, SB)
    p = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "sb"}, headers=headers())).json()
    await press(rig, sb, f"pl:x:{p['id']}", update=2, chat=888)
    assert rig.data.saved == [] and "Dropped" in rig.telegram.sent[-1][1]
    assert (await rig.client.get(PLANS, headers=headers())).json()["plans"][0]["status"] == "dismissed"


async def test_a_workspace_with_nothing_to_plan_is_left_out_of_the_evening(rig):
    await planner_ready(rig)
    own(rig)
    for rows in rig.data.tables.values():
        for r in rows:
            if r.get("workspace") == "fsa":
                r["outcome"] = {"status": "done"}
    rig.data.tables["meetings"] = []  # FSA's only meeting is gone too
    await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "workspaces": ["fsa", "sb"]}, headers=headers())
    plan(rig, SB)
    made = await evening(rig)
    assert [p["workspace"] for p in made] == ["sb"]  # FSA had nothing open: no model run was spent on it


async def test_the_workspaces_to_plan_are_checked_and_kept(rig):
    r = await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "workspaces": ["sb", "mars"]}, headers=headers())
    assert r.status_code == 422
    ok = await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "workspaces": ["sb"]}, headers=headers())
    assert ok.json()["settings"]["workspaces"] == ["sb"]
    keep = await rig.client.put(f"{PLANS}/settings", json={"enabled": True, "time": "20:00", "timezone": "Asia/Kolkata"}, headers=headers())
    assert keep.json()["settings"]["workspaces"] == ["sb"]  # left out: what was chosen stays
    await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "20:00", "timezone": "Asia/Kolkata"}, headers=headers())


async def test_what_is_approved_in_a_chat_reaches_google_at_once(rig):
    from .test_proposals import APPLY, ask

    card = (await ask(rig, [("add_task", {"title": "Draft the rejoinder", "date": TOMORROW, "start": "11:00", "minutes": 90})]))["found"][0]
    rig.data.syncs = 0
    assert (await rig.client.post(APPLY.format(card["id"]), headers=headers())).json()["status"] == "applied"
    assert rig.data.syncs == 1
    await rig.client.post(APPLY.format(card["id"]), headers=headers())  # a second tap saves nothing and doesn't sync again
    assert rig.data.syncs == 1


async def test_the_planner_in_a_workspace_chat_also_sees_the_rest_of_the_day_as_taken(rig):
    await planner_ready(rig)
    own(rig)
    # S&B's chat asks the planner to put its task on top of FSA's 14:00 meeting: refused, because that meeting is on the person's day too
    plan(rig, [{"kind": "task", "id": "t1", "start": 840, "duration": 60}], answer="It clashes.")
    chat = (await rig.client.post("/v1/agent/conversations", json={"agentId": "assistant", "workspace": "sb"}, headers=headers())).json()
    rig.script.clear()
    plan(rig, [{"kind": "task", "id": "t1", "start": 840, "duration": 60}], answer="It clashes.")
    out = (await rig.client.post(f"/v1/agent/conversations/{chat['id']}/messages", json={"message": "Plan tomorrow for S&B"}, headers=headers())).json()
    turn = out["turn"]
    assert turn["agentId"] == "daily-planner" and turn["found"] == []  # no schedule card: the clash was caught, so nothing was proposed
    assert [s["found"] for s in turn["steps"] if s["call"]["name"] == "propose_schedule"] == [0]
