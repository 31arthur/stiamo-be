import json
from datetime import UTC, datetime, timedelta

from app.domain.llm import ChatResult, ToolCall
from app.domain.phone import strip_markup
from sqlalchemy import text

from .conftest import headers
from .test_agent_api import choose, connect
from .test_channels import send, telegram
from .test_planning import PLANNER, TOMORROW, fill

APPLY = "/v1/agent/proposals/{}/apply"
DROP = "/v1/agent/proposals/{}/drop"


async def ask(rig, calls, answer="I've proposed it. Approve to save."):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    rig.script += [ChatResult("", [ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(calls)]), ChatResult(answer)]
    return (await rig.client.post(f"{PLANNER}/chat", json={"message": "do it"}, headers=headers())).json()


async def apply(rig, found):
    return await rig.client.post(APPLY.format(found["id"]), headers=headers())


async def test_a_new_task_is_only_a_card_until_it_is_approved(rig):
    out = await ask(rig, [("add_task", {"title": "Draft the rejoinder", "due": TOMORROW, "priority": "high", "minutes": 90, "case": "SA-2026-001"})])
    card = out["found"][0]
    assert card["kind"] == "proposal" and card["action"] == "task.create" and card["title"] == "New task: Draft the rejoinder"
    assert "Due " in " ".join(card["lines"]) and "High priority" in card["lines"] and "90 min" in card["lines"] and any("SA-2026-001" in ln for ln in card["lines"])
    assert rig.data.saved == []  # a proposal writes nothing
    done = await apply(rig, card)
    assert done.json()["status"] == "applied"
    (table, record), = rig.data.saved
    assert table == "tasks" and record["title"] == "Draft the rejoinder" and record["dueDate"] == TOMORROW and record["priority"] == "high" and record["estimate"] == 90
    assert record["related"] == {"kind": "case", "id": "c1"} and record["workspace"] == "sb" and record["outcome"] == {"status": "open"}
    again = await apply(rig, card)
    assert again.status_code == 200 and again.json()["status"] == "applied"  # a second tap says it is saved, and saves nothing twice
    assert len(rig.data.saved) == 1


async def test_a_proposal_can_be_dropped_and_is_only_ever_the_owners(rig):
    card = (await ask(rig, [("add_task", {"title": "Buy stamps"})]))["found"][0]
    assert (await rig.client.post(APPLY.format(card["id"]), headers=headers("other@example.com", "u2"))).status_code == 404
    assert (await rig.client.post(DROP.format(card["id"]), headers=headers())).json()["status"] == "dropped"
    assert (await rig.client.post(DROP.format(card["id"]), headers=headers())).status_code == 200  # dropping twice is fine too
    refused = await apply(rig, card)
    assert refused.status_code == 409 and "dropped" in refused.json()["error"]["message"] and rig.data.saved == []
    assert (await rig.client.post(APPLY.format(card["id"]))).status_code == 401


async def test_a_day_old_proposal_is_stale(rig):
    card = (await ask(rig, [("add_task", {"title": "Old idea"})]))["found"][0]
    old = (datetime.now(UTC) - timedelta(hours=30)).replace(tzinfo=None)
    async with rig.app.state.session_factory() as s:
        await s.execute(text("update agent_proposals set created_at = :t"), {"t": old})
        await s.commit()
    r = await apply(rig, card)
    assert r.status_code == 409 and "Ask for it again" in r.json()["error"]["message"] and rig.data.saved == []


async def test_changing_a_task_merges_into_it_as_it_is_when_approved(rig):
    out = await ask(rig, [("change_task", {"id": "t1", "status": "done", "date": TOMORROW, "start": "10:30", "priority": "urgent"})])
    card = out["found"][0]
    assert card["action"] == "task.update" and "Mark it done" in card["lines"]
    assert (await apply(rig, card)).status_code == 200
    (_, record), = rig.data.saved
    assert record["id"] == "t1" and record["title"] == "Draft the rejoinder" and record["priority"] == "urgent"  # what was there is kept
    assert record["outcome"]["status"] == "done" and record["slot"] == {"date": TOMORROW, "start": 630, "duration": 90}


async def test_a_follow_up_is_added_and_moved(rig):
    out = await ask(rig, [("add_follow_up", {"title": "Call the labour office", "channel": "call", "due": TOMORROW, "time": "15:00", "contact": "Hemla Pawar", "case": "SA-2026-001"}), ("change_follow_up", {"id": "f1", "due": TOMORROW, "time": "09:45"})])
    new, moved = out["found"]
    assert new["action"] == "followUp.create" and moved["action"] == "followUp.update"
    await apply(rig, new)
    await apply(rig, moved)
    created, changed = (r for _, r in rig.data.saved)
    assert created["due"] == {"date": TOMORROW, "time": 900} and created["subject"] == {"kind": "case", "id": "c1"} and created["contact"] == "Hemla Pawar"
    assert changed["id"] == "f1" and changed["due"] == {"date": TOMORROW, "time": 585} and changed["title"] == "Call the advocate"


async def test_a_hearing_needs_a_real_case_and_a_free_date(rig):
    out = await ask(rig, [("add_hearing", {"case": "NOPE-1", "date": TOMORROW, "purpose": "Arguments"}), ("add_hearing", {"case": "SA-2026-001", "date": TOMORROW, "purpose": "Arguments"}), ("add_hearing", {"case": "SA-2026-001", "date": "2027-01-05", "time": "11:00", "purpose": "Evidence", "forum": "JCL Kurnool"})])
    assert [f["action"] for f in out["found"]] == ["hearing.create"]  # the unknown case and the clash were sent back, not proposed
    await apply(rig, out["found"][0])
    (table, record), = rig.data.saved
    assert table == "hearings" and record["caseId"] == "c1" and record["date"] == "2027-01-05" and record["time"] == 660 and record["forum"] == "JCL Kurnool"


async def test_a_bad_value_is_told_to_the_model_not_proposed(rig):
    out = await ask(rig, [("add_task", {"title": "x", "due": "next friday"}), ("add_case", {"title": "A case"}), ("add_task", {"title": "x", "date": TOMORROW})])
    assert out["found"] == []
    assert "give both date and start" in json.loads(rig.llm_log[-1]["last"])["error"]  # the reason reaches the model, which can fix it
    async with rig.app.state.session_factory() as s:
        assert (await s.execute(text("select count(*) from agent_proposals"))).scalar_one() == 0


async def test_a_case_needs_its_organization_and_gets_its_number_when_opened(rig):
    out = await ask(rig, [("add_case", {"title": "Alur 40 workers", "organization": "TUMWU", "type": "pow", "client": "40 Workers", "opposing_party": "NHAI", "court": "JCL Kurnool", "filed_on": "2026-11-02"})])
    card = out["found"][0]
    assert card["action"] == "case.create" and any("TUMWU" in ln for ln in card["lines"])
    await apply(rig, card)
    (table, record), = rig.data.saved
    assert table == "cases" and record["organization"] == "TUMWU" and record["reference"] == "" and record["filedOn"] == "2026-11-02" and record["status"] == "active"


async def test_an_action_plan_is_a_plan_and_its_steps_saved_together(rig):
    out = await ask(rig, [("add_action_plan", {"title": "Prepare the rejoinder", "objective": "File before the next hearing", "due": TOMORROW, "cases": ["SA-2026-001"], "steps": [{"title": "Collect the wage records"}, {"kind": "followUp", "title": "Ask the advocate for the counter", "contact": "Ravindra"}, {"title": "Draft", "due": TOMORROW, "minutes": 120, "priority": "high"}]})])
    card = out["found"][0]
    assert card["action"] == "plan.create" and len([ln for ln in card["lines"] if ln[:2] in ("1.", "2.", "3.")]) == 3
    assert (await apply(rig, card)).status_code == 200
    plan = next(r for t, r in rig.data.saved if t == "plans")
    steps = [(t, r) for t, r in rig.data.saved if t != "plans"]
    assert plan["status"] == "active" and plan["caseIds"] == ["c1"] and plan["origin"] == {"kind": "case", "id": "c1"} and plan["workspace"] == "sb"
    assert [(t, r["order"], r["planId"]) for t, r in steps] == [("tasks", 0, plan["id"]), ("follow-ups", 1, plan["id"]), ("tasks", 2, plan["id"])]
    assert steps[2][1]["estimate"] == 120 and steps[2][1]["dueDate"] == TOMORROW and steps[1][1]["contact"] == "Ravindra"


async def test_a_yes_on_the_phone_saves_what_was_just_proposed_without_asking_the_model(rig):
    fill(rig)
    await connect(rig)
    c = await telegram(rig)
    await rig.client.put(f"/v1/agent/channels/{c['id']}/agent", json={"agent": "daily-planner"}, headers=headers())
    await choose(rig, agent=PLANNER)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.script += [ChatResult("", [ToolCall("a", "add_task", {"title": "Call Hemla"})]), ChatResult("Proposed.")]
    await send(rig, c, "add a task to call Hemla", chat=555, update=2)
    reply = strip_markup(rig.telegram.sent[-1][1])
    assert "New task: Call Hemla" in reply and "Reply YES" in reply and rig.data.saved == []
    asked = len(rig.llm_log)
    await send(rig, c, "yes", chat=555, update=3)
    assert rig.telegram.sent[-1][1].startswith("Saved:") and "Call Hemla" in rig.telegram.sent[-1][1]
    assert len(rig.llm_log) == asked and [t for t, _ in rig.data.saved] == ["tasks"]
    rig.script += [ChatResult("", [ToolCall("b", "add_task", {"title": "Never mind this"})]), ChatResult("Proposed.")]
    await send(rig, c, "add another", chat=555, update=4)
    await send(rig, c, "no", chat=555, update=5)
    assert rig.telegram.sent[-1][1].startswith("Dropped:") and len(rig.data.saved) == 1
    rig.script += [ChatResult("Nothing waiting, so this reached me.")]
    await send(rig, c, "yes", chat=555, update=6)
    assert rig.telegram.sent[-1][1] == "Nothing waiting, so this reached me."  # with nothing proposed, a yes is just a message
