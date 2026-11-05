import json

from app.domain.dayplan import briefing_text
from app.domain.llm import ChatResult, ToolCall
from sqlalchemy import text

from .conftest import headers
from .test_agent_api import choose, connect
from .test_dayplans import PLANS, generate
from .test_planning import PLANNER, TOMORROW, fill

PLAN = {
    "headline": "A court morning, then a quiet afternoon.",
    "summary": "One hearing and two calls.",
    "sections": [
        {"kind": "fixed", "items": [{"type": "hearing", "id": "h9", "text": "Take the vakalat"}, {"type": "meeting", "id": "m1"}]},
        {"kind": "calls", "items": [{"type": "followUp", "id": "f1", "text": "She asked for a call back"}, {"type": "followUp", "id": "f2"}]},
        {"kind": "work", "title": "Today's work", "items": [{"type": "task", "id": "t1", "text": "Due tomorrow"}, {"type": "note", "text": "Leave the afternoon open."}]},
    ],
}


async def ask(rig, calls, answer="Here you go."):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    rig.script += [ChatResult("", [ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(calls)]), ChatResult(answer)]
    return (await rig.client.post(f"{PLANNER}/chat", json={"message": "plan tomorrow"}, headers=headers())).json()


async def test_the_day_is_laid_out_as_a_card_of_real_items_with_their_own_titles_and_times(rig):
    out = await ask(rig, [("present_plan", PLAN)])
    (card,) = out["found"]
    assert card["kind"] == "briefing" and card["date"] == TOMORROW and card["headline"].startswith("A court morning")
    assert [s["kind"] for s in card["sections"]] == ["fixed", "calls", "work"] and card["sections"][2]["title"] == "Today's work" and card["sections"][0]["title"] == "Fixed"
    hearing, meeting = card["sections"][0]["items"]
    assert hearing["type"] == "hearing" and hearing["id"] == "h9" and hearing["time"] == "10:00–11:00" and hearing["caseRef"] == "SA-2026-001" and hearing["note"] == "Take the vakalat"
    assert meeting["title"] == "Union leaders" and meeting["time"] == "14:00–15:00"
    call = card["sections"][1]["items"][0]
    assert call["title"] == "Call the advocate" and call["tries"] == 1 and call["channel"] == "call" and call["note"] == "She asked for a call back"
    task, note = card["sections"][2]["items"]
    assert task["title"] == "Draft the rejoinder" and task["priority"] == "high" and task["minutes"] == 90 and note == {"type": "note", "note": "Leave the afternoon open."}
    assert card["free"] and card["free"][0]["from"] == "09:00"


async def test_an_item_that_is_not_there_is_sent_back_so_the_card_never_shows_a_guess(rig):
    out = await ask(rig, [("present_plan", {"headline": "x", "sections": [{"kind": "calls", "items": [{"type": "followUp", "id": "nope"}, {"type": "task", "id": "t1"}]}]})])
    assert out["found"] == []
    result = json.loads(rig.llm_log[-1]["last"])
    assert result["ok"] is False and "nope" in result["problems"][0]


async def test_a_schedule_proposed_in_a_chat_can_be_approved_there_and_books_the_times(rig):
    out = await ask(rig, [("propose_schedule", {"blocks": [{"kind": "task", "id": "t1", "start": 660, "duration": 90}, {"kind": "followUp", "id": "f1", "start": 570}]})])
    schedule = next(f for f in out["found"] if f["kind"] == "schedule")
    proposal = next(f for f in out["found"] if f["kind"] == "proposal") if any(f["kind"] == "proposal" for f in out["found"]) else None
    assert schedule["proposalId"] and proposal is None  # the schedule card carries its own approval
    assert rig.data.saved == []
    assert (await rig.client.post(f"/v1/agent/proposals/{schedule['proposalId']}/apply", headers=headers())).status_code == 200
    by_table = {t: r for t, r in rig.data.saved}
    assert by_table["tasks"]["slot"] == {"date": TOMORROW, "start": 660, "duration": 90} and by_table["tasks"]["title"] == "Draft the rejoinder"
    assert by_table["follow-ups"]["due"] == {"date": TOMORROW, "time": 570} and by_table["follow-ups"]["title"] == "Call the advocate"


async def test_the_evening_plan_keeps_the_card_and_does_not_pile_up_proposals(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    rig.script += [ChatResult("", [ToolCall("a", "propose_schedule", {"blocks": [{"kind": "task", "id": "t1", "start": 660}]}), ToolCall("b", "present_plan", PLAN)]), ChatResult("Tomorrow is light.")]
    plan = (await generate(rig, day=TOMORROW)).json()
    assert plan["card"]["kind"] == "briefing" and plan["briefing"] == "Tomorrow is light." and [b["id"] for b in plan["blocks"]] == ["t1"]
    again = (await rig.client.get(PLANS, headers=headers())).json()["plans"][0]
    assert again["card"]["headline"].startswith("A court morning")
    async with rig.app.state.session_factory() as s:  # the evening plan keeps its own plan: no proposal beside it
        assert (await s.execute(text("select count(*) from agent_proposals"))).scalar_one() == 0


def test_a_briefing_reads_as_plain_text_for_a_phone():
    card = {"headline": "A light day.", "summary": "Two calls.", "sections": [{"kind": "calls", "items": [{"title": "Call the advocate", "time": "09:30", "note": "asked to call back"}, {"note": "Leave the afternoon open."}]}, {"kind": "fixed", "items": []}]}
    out = briefing_text(card)
    assert out.startswith("A light day.\nTwo calls.") and "CALLS TO MAKE" in out and "• 09:30 Call the advocate (asked to call back)" in out and "Leave the afternoon open." in out and "FIXED" not in out


async def test_a_reopened_chat_shows_whether_its_schedule_was_put_on_the_planner(rig):
    await connect(rig)
    fill(rig)
    await choose(rig, agent=PLANNER)
    c = (await rig.client.post("/v1/agent/conversations", json={"agentId": "daily-planner", "workspace": "sb"}, headers=headers())).json()
    rig.script += [ChatResult("", [ToolCall("a", "propose_schedule", {"blocks": [{"kind": "followUp", "id": "f1", "start": 570}]})]), ChatResult("Proposed.")]
    turn = (await rig.client.post(f"/v1/agent/conversations/{c['id']}/messages", json={"message": "plan tomorrow"}, headers=headers())).json()["turn"]
    schedule = next(f for f in turn["found"] if f["kind"] == "schedule")
    assert schedule["proposalStatus"] == "pending"
    await rig.client.post(f"/v1/agent/proposals/{schedule['proposalId']}/apply", headers=headers())
    again = (await rig.client.get(f"/v1/agent/conversations/{c['id']}", headers=headers())).json()
    assert next(f for f in again["turns"][0]["found"] if f["kind"] == "schedule")["proposalStatus"] == "applied"
