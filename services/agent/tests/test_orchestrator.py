from datetime import UTC, datetime, timedelta

from app.domain.llm import ChatResult
from app.domain.orchestrator import (
    build_preface,
    gist_of,
    is_follow_up,
    jev_question,
    route_by_rules,
    update_digest,
    update_focus,
)
from app.domain.phone import strip_markup
from sqlalchemy import text

from .conftest import headers
from .test_agent_api import choose, connect
from .test_channels import send, telegram
from .test_planning import fill

C = "/v1/agent/conversations"
TEAM = [
    {"id": "daily-planner", "name": "Daily planner", "description": "Plans tomorrow each evening: what is fixed, what to prepare, who to call and what to do when.", "suggestions": ["Plan tomorrow", "How full is my day tomorrow?"]},
    {"id": "cases", "name": "Case agent", "description": "Finds cases and the documents that go with them, and gets you ready for a hearing.", "suggestions": ["Prepare me for tomorrow's hearings"]},
]


def test_a_message_goes_to_the_agent_whose_work_it_is():
    pick = lambda m, last=None: route_by_rules(m, TEAM, last)[0]  # noqa: E731
    assert pick("Who should I call tomorrow?") == "daily-planner"
    assert pick("Prepare me for the Kurnool hearing") == "cases"
    assert pick("Plan my day") == "daily-planner"
    assert pick("What is due this week and what's in my inbox?") == "daily-planner"
    assert pick("zzz qqq") == "daily-planner"  # nothing to go on: the usual one


def test_a_short_follow_up_stays_with_the_agent_that_just_answered():
    assert is_follow_up("yes") and is_follow_up("and the labour office?") and is_follow_up("move it to Monday please") and not is_follow_up("Prepare me for tomorrow's hearing at Kurnool")
    assert route_by_rules("and the labour office?", TEAM, "daily-planner")[0] == "daily-planner"
    assert route_by_rules("what about Hemla", TEAM, "cases")[0] == "cases"


def test_the_decision_model_is_asked_to_choose_between_the_agents_with_the_last_one_in_mind():
    q = jev_question(TEAM, "cases")["agent"]
    assert q["type"] == "choice" and list(q["criteria"]) == [a["id"] for a in TEAM] and "prefer “cases”" in q["instructions"]


def test_every_agent_is_told_what_the_conversation_has_been_about():
    turns = [{"question": f"Question {i}", "answer": f"Answer {i}", "agentName": "Daily planner" if i % 2 else "Case agent", "gist": "Laid out 2026-11-07: A light day. Items: Call Hemla" if i == 7 else ""} for i in range(8)]
    context = {"focus": {"case": {"ref": "TUMWU-2026-001", "title": "Nikhil Construction"}, "date": "2026-11-07"}, "digest": [f"Question {i} → agent" for i in range(8)]}
    preface = build_preface(turns, context)
    assert "the case TUMWU-2026-001 (Nikhil Construction) and the day 2026-11-07" in preface
    assert "Person: Question 7" in preface and "Case agent: Answer 6" in preface and "Question 1\n" not in preface.split("The latest exchanges")[1]  # only the last six in full
    assert "Earlier in the conversation" in preface and "[Laid out 2026-11-07" in preface
    assert build_preface([], {}) == ""


def test_the_focus_follows_the_case_named_and_the_day_laid_out():
    focus = update_focus({}, "How is TUMWU-2026-001 doing?", [])
    assert focus["case"]["ref"] == "TUMWU-2026-001"
    focus = update_focus(focus, "show me the other one", [{"kind": "case", "reference": "TUMWU-2026-002", "title": "Alur"}])
    assert focus["case"] == {"ref": "TUMWU-2026-002", "title": "Alur"}
    assert update_focus(focus, "plan it", [{"kind": "briefing", "date": "2026-11-07"}])["date"] == "2026-11-07"
    assert update_focus(focus, "hello", [])["case"]["ref"] == "TUMWU-2026-002"  # nothing new: it stays
    assert len(update_digest(["x"] * 25, {"question": "q", "agentName": "A"})) == 20
    assert "Items: Call Hemla" in gist_of([{"kind": "briefing", "date": "d", "headline": "H", "sections": [{"items": [{"title": "Call Hemla"}]}]}])


async def team_ready(rig):
    fill(rig)
    await connect(rig)
    for a in ("daily-planner", "cases"):
        await choose(rig, agent=f"/v1/agent/agents/{a}")


async def start(rig, workspace="sb"):
    r = await rig.client.post(C, json={"agentId": "assistant", "workspace": workspace}, headers=headers())
    assert r.status_code == 201, r.text
    return r.json()


async def say(rig, cid, message):
    r = await rig.client.post(f"{C}/{cid}/messages", json={"message": message}, headers=headers())
    assert r.status_code == 200, r.text
    return r.json()["turn"]


async def test_the_assistant_picks_the_agent_and_every_agent_knows_the_whole_conversation(rig):
    await team_ready(rig)
    c = await start(rig, "all")
    rig.script += [ChatResult("Call Hemla at 09:30.")]
    first = await say(rig, c["id"], "Who should I call tomorrow?")
    assert first["agentId"] == "daily-planner" and first["agentName"] == "Daily planner" and first["routedBy"] == "rules"
    rig.script += [ChatResult("The labour office is first.")]
    second = await say(rig, c["id"], "and the labour office?")
    assert second["agentId"] == "daily-planner" and second["routedBy"] == "continuity"
    rig.script += [ChatResult("Bring the vakalat and the last order.")]
    third = await say(rig, c["id"], "Prepare me for the hearing in TUMWU-2026-001")
    assert third["agentId"] == "cases"
    told = rig.llm_log[-1]["last"]  # what the third agent was given: the earlier exchanges, which another agent answered
    assert "Who should I call tomorrow?" in told and "Call Hemla at 09:30." in told and "Daily planner:" in told and "TUMWU-2026-001" in told and told.rstrip().endswith("Prepare me for the hearing in TUMWU-2026-001")
    full = (await rig.client.get(f"{C}/{c['id']}", headers=headers())).json()
    assert full["agentId"] == "assistant" and [t["agentId"] for t in full["turns"]] == ["daily-planner", "daily-planner", "cases"]


async def test_the_decision_model_chooses_when_it_is_on_and_the_rules_cover_when_it_is_not(rig):
    await team_ready(rig)
    await connect(rig, provider="openrouter", key="sk-or-1234567890")
    assert (await rig.client.put("/v1/agent/decision", json={"enabled": True, "modelId": "~typesafe/jev-latest"}, headers=headers())).status_code == 204
    c = await start(rig, "all")
    rig.script += [ChatResult("ok")]
    turn = await say(rig, c["id"], "Prepare me for the hearing")
    assert turn["routedBy"] == "jev" and turn["agentId"] in {a["id"] for a in TEAM} | {"cases"}
    asked = rig.decision_log[-1]
    assert asked["questions"]["agent"]["type"] == "choice" and "Prepare me for the hearing" in asked["state"]
    await rig.client.put("/v1/agent/decision", json={"enabled": False, "modelId": "~typesafe/jev-latest"}, headers=headers())
    rig.script += [ChatResult("ok")]
    assert (await say(rig, c["id"], "Plan my whole day for Monday please"))["routedBy"] == "rules"


async def test_only_the_agents_of_the_workspace_are_chosen_from(rig):
    await team_ready(rig)
    c = await start(rig, "personal")  # the hearing agent works in S&B only, and the case agent too
    rig.script += [ChatResult("ok")]
    turn = await say(rig, c["id"], "Prepare me for the hearing")
    assert turn["agentId"] != "cases"


async def test_the_assistant_on_a_phone_keeps_one_thread_that_the_web_app_shows_too(rig):
    await team_ready(rig)
    c = await telegram(rig, agent="assistant")
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.script += [ChatResult("Call Hemla first.")]
    await send(rig, c, "who should I call tomorrow at S&B?", chat=555, update=2)
    reply = rig.telegram.sent[-1][1]
    assert reply.startswith("Call Hemla first.") and strip_markup(reply).rstrip().endswith("— Daily planner · S&B") and "<i>— Daily planner · S&amp;B</i>" in reply  # signed with the workspace it worked in
    rig.script += [ChatResult("Then the labour office.")]
    await send(rig, c, "and after that?", chat=555, update=3)
    assert "who should I call tomorrow at S&B?" in rig.llm_log[-1]["last"]  # the second message knows the first (same workspace, no need to say it again)
    threads = (await rig.client.get(C, headers=headers())).json()["items"]
    assert len(threads) == 1 and threads[0]["title"].startswith("Telegram: who should I call") and threads[0]["turns"] == 2 and threads[0]["workspace"] == "sb"
    await send(rig, c, "/new", chat=555, update=4)
    assert "Started a new conversation" in rig.telegram.sent[-1][1]
    rig.script += [ChatResult("Plan: nothing much.")]
    await send(rig, c, "plan my day at S&B", chat=555, update=5)
    assert len((await rig.client.get(C, headers=headers())).json()["items"]) == 2  # a new thread; the old one is kept


async def test_a_phone_chat_that_has_been_quiet_overnight_starts_fresh(rig):
    await team_ready(rig)
    c = await telegram(rig, agent="assistant")
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.script += [ChatResult("First.")]
    await send(rig, c, "who should I call tomorrow at S&B?", chat=555, update=2)
    old = (datetime.now(UTC) - timedelta(hours=20)).replace(tzinfo=None)
    async with rig.app.state.session_factory() as s:
        await s.execute(text("update agent_conversations set updated_at = :t"), {"t": old})
        await s.commit()
    rig.script += [ChatResult("Second.")]
    await send(rig, c, "plan my day at S&B", chat=555, update=3)
    assert "who should I call tomorrow at S&B?" not in rig.llm_log[-1]["last"]  # yesterday's talk is not in it
    assert len((await rig.client.get(C, headers=headers())).json()["items"]) == 2


async def test_a_yes_on_the_phone_still_saves_what_the_assistants_agent_proposed(rig):
    from app.domain.llm import ToolCall

    await team_ready(rig)
    c = await telegram(rig, agent="assistant")
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.script += [ChatResult("", [ToolCall("a", "add_task", {"title": "Call Hemla"})]), ChatResult("Proposed.")]
    await send(rig, c, "add a task to call Hemla tomorrow at S&B", chat=555, update=2)
    assert "<b>YES</b> to save" in rig.telegram.sent[-1][1] and rig.data.saved == []
    await send(rig, c, "yes", chat=555, update=3)
    assert rig.telegram.sent[-1][1].startswith("✅ Saved") and [t for t, _ in rig.data.saved] == ["tasks"]
