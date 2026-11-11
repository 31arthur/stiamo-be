"""Workflows the way n8n runs them: items pass between nodes, branches and loops, this app's data as nodes, a message to the phone, a trace of every node,
and new agents created from template workflows."""

import pytest
from app.application.flowrun import NODES
from app.domain.flow import CATALOG, Flow, validate
from app.domain.flow_templates import TEMPLATES
from app.domain.llm import AiError, ChatResult

from .conftest import headers
from .test_agent_api import choose, connect
from .test_channels import telegram
from .test_dayplans import planner_ready
from .test_leaks import CANARY, TOMORROW, connect_bot, seed
from .test_planning import PLANNER


@pytest.fixture(autouse=True)
def flow_engine(rig):
    rig.app.state.settings.engine = "flow"


F = f"{PLANNER}/flow"
RUN = f"{PLANNER}/flow/run"


def n(i, t, name="", x=0, **parameters):
    return {"id": i, "type": t, "name": name or i, "position": [x, 0], "parameters": parameters}


def wire(*triples):
    out = {}
    for src, output, dst in triples:
        lanes = out.setdefault(src, {"main": []})["main"]
        while len(lanes) <= output:
            lanes.append([])
        lanes[output].append({"node": dst, "index": 0})
    return out


async def put(rig, nodes, connections, agent=PLANNER):
    return await rig.client.put(f"{agent}/flow", json={"nodes": nodes, "connections": connections}, headers=headers())


async def run(rig, message="hello", scheduled=False, agent=PLANNER):
    r = await rig.client.post(f"{agent}/flow/run", json={"message": message, "scheduled": scheduled}, headers=headers())
    assert r.status_code == 200, r.text
    return r.json()


async def last(rig, agent_id="daily-planner"):
    runs = (await rig.client.get(f"/v1/agent/agents/{agent_id}/runs", headers=headers())).json()["items"]
    return (await rig.client.get(f"/v1/agent/runs/{runs[0]['id']}", headers=headers())).json()


def by_node(run_detail):
    return {t["node"]: t for t in run_detail["trace"]}


def test_every_node_type_in_the_catalog_has_an_implementation_or_is_structural():
    structural = {"core.merge", "core.loop", "core.note"}
    assert [t for t in CATALOG if t not in NODES and t not in structural] == []


def test_the_templates_are_workflows_that_can_run():
    assert [(t["id"], validate(Flow.from_json(t["flow"]))) for t in TEMPLATES] == [(t["id"], []) for t in TEMPLATES]


async def test_the_default_workflow_is_a_chat_trigger_the_ai_agent_and_an_answer_and_every_node_keeps_its_items(rig):
    await planner_ready(rig)
    f = (await rig.client.get(F, headers=headers())).json()
    assert [x["type"] for x in f["nodes"]] == ["trigger.chat", "ai.agent", "respond"] and f["custom"] is False and f["problems"] == []
    assert {t["name"] for t in f["tools"]} >= {"day_agenda", "propose_schedule"} and any(c["type"] == "core.if" for c in f["catalog"])
    rig.script += [ChatResult("Here is the day.")]
    assert (await run(rig, "Plan tomorrow"))["answer"] == "Here is the day."
    t = by_node(await last(rig))
    assert t["trigger"]["itemsIn"] == 0 and t["trigger"]["outputs"] == [1]
    assert t["agent"]["itemsIn"] == 1 and "Plan tomorrow" in str(t["agent"]["in"]) and "Here is the day." in str(t["agent"]["out"])
    assert t["respond"]["state"] == "ok" and all(x["ms"] >= 0 for x in t.values())


async def test_if_sends_each_item_down_true_or_false_and_a_branch_with_nothing_does_not_run(rig):
    await planner_ready(rig)
    seed(rig)
    nodes = [
        n("t", "trigger.chat"),
        n("tasks", "data.tasks", status="open"),
        n("if", "core.if", conditions=[{"left": "{{ $json.title }}", "op": "contains", "right": "SBX"}]),
        n("yes", "core.set", fields=[{"name": "flag", "value": "mine"}]),
        n("no", "core.set", fields=[{"name": "flag", "value": "other"}]),
        n("agg", "core.aggregate", field="title"),
        n("a", "respond", text="{{ join($json.title, ',') }}"),
    ]
    c = wire(("t", 0, "tasks"), ("tasks", 0, "if"), ("if", 0, "yes"), ("if", 1, "no"), ("yes", 0, "agg"), ("agg", 0, "a"))
    assert (await put(rig, nodes, c)).status_code == 200
    out = await run(rig)
    assert out["answer"] == "SBX-task"  # (workspace-limited runs are tested below; the app's own chat sees all three, only SBX matches)
    t = by_node(await last(rig))
    assert t["if"]["outputs"] == [1, 2] and "no" in t and t["no"]["itemsIn"] == 2 and t["yes"]["itemsIn"] == 1


async def test_switch_filter_sort_limit_split_out_and_set_work_on_items(rig):
    await planner_ready(rig)
    seed(rig)
    nodes = [
        n("t", "trigger.chat"),
        n("tasks", "data.tasks", status="open"),
        n("sort", "core.sort", field="title", order="desc"),
        n("two", "core.limit", max=2),
        n("set", "core.set", fields=[{"name": "label", "value": "{{ upper($json.title) }} / {{ $workspace }}"}], keepOthers=False),
        n("agg", "core.aggregate", field="label"),
        n("a", "respond", text="{{ join($json.label, ' | ') }}"),
    ]
    assert (await put(rig, nodes, wire(("t", 0, "tasks"), ("tasks", 0, "sort"), ("sort", 0, "two"), ("two", 0, "set"), ("set", 0, "agg"), ("agg", 0, "a")))).status_code == 200
    assert (await run(rig))["answer"] == "SBX-TASK /  | PEX-TASK / "  # sorted Z to A, two kept, set from expressions ($workspace is empty in the app's own chat)
    sw = [n("t", "trigger.chat"), n("tasks", "data.tasks"), n("sw", "core.switch", rules=[{"left": "{{ $json.title }}", "op": "starts_with", "right": "SBX"}, {"left": "{{ $json.title }}", "op": "starts_with", "right": "FSX"}]),
          n("sb", "respond", text="sb"), n("fsa", "respond", text="fsa"), n("rest", "respond", text="rest")]
    assert (await put(rig, sw, wire(("t", 0, "tasks"), ("tasks", 0, "sw"), ("sw", 0, "sb"), ("sw", 1, "fsa"), ("sw", 2, "rest")))).status_code == 200
    await run(rig)
    t = by_node(await last(rig))
    assert t["sw"]["outputs"] == [1, 1, 1] and {"sb", "fsa", "rest"} <= set(t)


async def test_loop_over_items_works_through_batches_and_hands_back_everything_when_done(rig):
    await planner_ready(rig)
    seed(rig)
    nodes = [
        n("t", "trigger.chat"),
        n("tasks", "data.tasks"),
        n("loop", "core.loop", batchSize=1),
        n("mark", "core.set", fields=[{"name": "seen", "value": "{{ $json.title }}!"}]),
        n("agg", "core.aggregate", field="seen"),
        n("a", "respond", text="{{ $json.count }}: {{ join($json.seen, ' ') }}"),
    ]
    c = wire(("t", 0, "tasks"), ("tasks", 0, "loop"), ("loop", 0, "mark"), ("mark", 0, "loop"), ("loop", 1, "agg"), ("agg", 0, "a"))
    assert (await put(rig, nodes, c)).status_code == 200
    out = await run(rig)
    assert out["answer"].startswith("3: ") and out["answer"].count("!") == 3
    t = await last(rig)
    assert [x["node"] for x in t["trace"]].count("mark") == 3  # once per batch


async def test_merge_waits_for_both_inputs_and_puts_the_items_together(rig):
    await planner_ready(rig)
    seed(rig)
    nodes = [n("t", "trigger.chat"), n("tasks", "data.tasks"), n("calls", "data.follow_ups"), n("m", "core.merge"), n("agg", "core.aggregate", field="title"), n("a", "respond", text="{{ $json.count }}")]
    c = wire(("t", 0, "tasks"), ("t", 0, "calls"), ("tasks", 0, "m"), ("agg", 0, "a"), ("m", 0, "agg"))
    c["calls"] = {"main": [[{"node": "m", "index": 1}]]}
    assert (await put(rig, nodes, c)).status_code == 200
    assert (await run(rig))["answer"] == "6"  # three tasks and three calls, once both had arrived


async def test_a_workflow_under_a_workspace_can_only_read_that_workspaces_data_in_its_data_nodes(rig):
    await planner_ready(rig)
    seed(rig)
    nodes = [n("t", "trigger.chat"), n("tasks", "data.tasks"), n("agg", "core.aggregate", field="title"), n("a", "respond", text="{{ join($json.title, ',') }}")]
    c = wire(("t", 0, "tasks"), ("tasks", 0, "agg"), ("agg", 0, "a"))
    assert (await put(rig, nodes, c)).status_code == 200
    sb = await connect_bot(rig, "456:def", 556, workspace="sb", agent="daily-planner")
    from .test_leaks import say

    await say(rig, sb, "anything", 556, 2)
    said = "\n".join(t for _, t in rig.telegram.sent)
    assert CANARY["sb"] in said and CANARY["fsa"] not in said and CANARY["personal"] not in said  # the S&B bot's workflow read S&B's tasks only
    assert (await last(rig))["workspace"] == "sb"


async def test_data_nodes_read_mail_contacts_follow_ups_meetings_and_the_calendar(rig):
    await planner_ready(rig)
    seed(rig)
    rig.data.people = [{"name": "Hemla Naik", "phones": [{"value": "+91 98100 22114"}], "emails": [{"value": "h@x.org"}], "workspaces": ["sb"]}, {"name": "Kaveri", "phones": [], "emails": [], "workspaces": ["fsa"]}]
    for ntype, params, want in (
        ("data.mail", {"days": 400, "limit": 3}, 3),
        ("data.contacts", {"query": "hem"}, 1),
        ("data.follow_ups", {"dueOn": TOMORROW}, 3),
        ("data.meetings", {"on": TOMORROW}, 3),
        ("data.hearings", {"on": TOMORROW}, 1),
        ("data.cases", {}, 1),
        ("data.calendar", {"from": TOMORROW, "to": TOMORROW}, 3),
    ):
        nodes = [n("t", "trigger.chat"), n("d", ntype, **params), n("agg", "core.aggregate"), n("a", "respond", text="{{ $json.count }}")]
        assert (await put(rig, nodes, wire(("t", 0, "d"), ("d", 0, "agg"), ("agg", 0, "a")))).status_code == 200, ntype
        assert (await run(rig))["answer"] == str(want), ntype


async def test_a_workflow_can_send_a_message_to_the_phone_and_an_ai_node_writes_it(rig):
    await planner_ready(rig)
    seed(rig)
    c = await telegram(rig, agent="assistant")
    from .test_channels import send

    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    nodes = [n("t", "trigger.schedule"), n("calls", "data.follow_ups", overdue=False), n("agg", "core.aggregate", field="title"),
             n("ai", "ai.agent", prompt="Write a one line list: {{ join($json.title, '; ') }}", tools=[], history=False), n("send", "msg.send", text="📞 {{ $json.text }}")]
    assert (await put(rig, nodes, wire(("t", 0, "calls"), ("calls", 0, "agg"), ("agg", 0, "ai"), ("ai", 0, "send")))).status_code == 200
    rig.script += [ChatResult("Call SBX, FSX and PEX.")]
    rig.telegram.sent.clear()
    await run(rig, scheduled=True)
    assert [t for chat, t in rig.telegram.sent if chat == 555] == ["📞 Call SBX, FSX and PEX."]
    assert rig.llm_log[-1]["tools"] == [] and "SBX-call; FSX-call; PEX-call" in rig.llm_log[-1]["last"]


async def test_a_failing_node_is_recorded_with_its_error_and_the_run_is_marked_failed(rig):
    await planner_ready(rig)
    nodes = [n("t", "trigger.chat"), n("s", "core.stop", message="No good: {{ $json.message }}"), n("ai", "ai.agent"), n("a", "respond")]
    assert (await put(rig, nodes, wire(("t", 0, "s"), ("t", 0, "ai"), ("ai", 0, "a")))).status_code == 200
    rig.script += [AiError("down")]
    r = await rig.client.post(RUN, json={"message": "hi"}, headers=headers())
    assert r.status_code == 422
    run_ = await last(rig)
    assert run_["status"] == "error" and any(x["state"] == "error" for x in run_["trace"])
    bad = await put(rig, [n("t", "trigger.chat"), n("x", "core.if", conditions=[{"left": "{{ nope( }}", "op": "equals", "right": "1"}]), n("a", "respond")], wire(("t", 0, "x"), ("x", 0, "a")))
    assert bad.status_code == 200
    r = await rig.client.post(RUN, json={"message": "hi"}, headers=headers())
    assert r.status_code == 422 and "x" in r.json()["error"]["message"]  # the expression's words, with the node it was in


async def test_a_workflow_that_cannot_run_is_refused_in_words_and_a_stored_broken_one_falls_back(rig):
    await planner_ready(rig)
    r = await put(rig, [n("t", "trigger.chat"), n("a", "respond")], {})
    assert r.status_code == 422 and "nothing leading into it" in r.json()["error"]["message"] or "can't be reached" in r.json()["error"]["message"]
    checked = (await rig.client.post(f"{F}/check", json={"nodes": [n("a", "respond")], "connections": {}}, headers=headers())).json()
    assert "A workflow needs a trigger to start from." in checked["problems"]
    circle = [n("t", "trigger.chat"), n("a", "core.noop"), n("b", "core.noop"), n("r", "respond")]
    assert any("circle" in p for p in (await rig.client.post(f"{F}/check", json={"nodes": circle, "connections": wire(("t", 0, "a"), ("a", 0, "b"), ("b", 0, "a"), ("b", 0, "r"))}, headers=headers())).json()["problems"])
    assert (await rig.client.get(F, headers=headers())).json()["custom"] is False
    assert (await rig.client.delete(F, headers=headers())).status_code == 200


# --- creating agents from workflows -------------------------------------------------------------------------------------------


async def test_a_new_agent_is_created_from_a_template_listed_run_and_deleted(rig):
    await connect(rig)
    await choose(rig, agent=PLANNER)
    cat = (await rig.client.get("/v1/agent/flow/catalog", headers=headers())).json()
    assert {t["id"] for t in cat["templates"]} >= {"assistant", "call-digest", "mail-digest", "contact-lookup", "overdue-tasks"} and any(x["type"] == "ai.agent" for x in cat["nodes"])
    made = await rig.client.post("/v1/agent/agents", json={"name": "Contact finder", "templateId": "contact-lookup"}, headers=headers())
    assert made.status_code == 201 and made.json()["id"] == "contact-finder" and made.json()["name"] == "Contact finder"
    again = (await rig.client.post("/v1/agent/agents", json={"name": "Contact finder", "templateId": "contact-lookup"}, headers=headers())).json()
    assert again["id"] == "contact-finder-2"  # never reuses an id
    ids = [a["id"] for a in (await rig.client.get("/v1/agent/settings", headers=headers())).json()["agents"]]
    assert "contact-finder" in ids
    rig.data.people = [{"name": "Hemla Naik", "phones": [], "emails": [], "workspaces": ["sb"]}]
    a = "/v1/agent/agents/contact-finder"
    assert (await rig.client.post(f"{a}/flow/run", json={"message": "hemla"}, headers=headers())).json()["answer"] == "Found: Hemla Naik"
    assert (await rig.client.post(f"{a}/flow/run", json={"message": "nobody"}, headers=headers())).json()["answer"].startswith("I couldn't find anyone")
    assert (await rig.client.get(f"{a}/flow", headers=headers())).json()["created"] is True
    assert (await rig.client.delete(a, headers=headers())).status_code == 204
    assert "contact-finder" not in [x["id"] for x in (await rig.client.get("/v1/agent/settings", headers=headers())).json()["agents"]]
    assert (await rig.client.delete(PLANNER, headers=headers())).status_code == 422  # the ones built into Stiamo stay
    assert (await rig.client.post("/v1/agent/agents", json={"name": "x", "templateId": "nope"}, headers=headers())).status_code == 422
    assert (await rig.client.post("/v1/agent/agents", json={"name": "  "}, headers=headers())).status_code in (422,)


async def test_a_created_agent_can_be_chatted_with_and_its_runs_have_their_own_history(rig):
    await connect(rig)
    await choose(rig, agent=PLANNER)
    made = (await rig.client.post("/v1/agent/agents", json={"name": "Helper"}, headers=headers())).json()
    base = f"/v1/agent/agents/{made['id']}"
    rig.script += [ChatResult("Hi there.")]
    r = await rig.client.post(f"{base}/chat", json={"message": "hello"}, headers=headers())
    assert r.status_code == 200 and r.json()["answer"] == "Hi there."
    runs = (await rig.client.get(f"{base}/runs", headers=headers())).json()["items"]
    assert len(runs) == 1
    detail = (await rig.client.get(f"/v1/agent/runs/{runs[0]['id']}", headers=headers())).json()
    assert [t["node"] for t in detail["trace"]] == ["trigger", "agent", "answer"]


@pytest.mark.parametrize("bad", [{"name": "x", "flow": {"nodes": [], "connections": {}}}])
async def test_creating_an_agent_from_a_workflow_that_cannot_run_is_refused(rig, bad):
    await connect(rig)
    assert (await rig.client.post("/v1/agent/agents", json=bad, headers=headers())).status_code == 422


# --- schedule triggers run by themselves -----------------------------------------------------------------------------------------


def scheduled(time="07:30"):
    return [n("t", "trigger.schedule", time=time, timezone="Asia/Kolkata"), n("calls", "data.follow_ups"), n("agg", "core.aggregate", field="title"), n("send", "msg.send", text="📞 {{ join($json.title, ' · ') }}")], wire(("t", 0, "calls"), ("calls", 0, "agg"), ("agg", 0, "send"))


async def test_a_schedule_trigger_runs_its_workflow_by_itself_and_stops_when_the_workflow_changes(rig):
    import asyncio

    from .test_channels import send
    from .test_listen import until

    await planner_ready(rig)
    seed(rig)
    c = await telegram(rig, agent="assistant")
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    mgr = rig.app.state.flowsched
    mgr.delay_for = lambda at, zone: 0.02  # a test can't wait until half past seven
    nodes, conns = scheduled()
    rig.telegram.sent.clear()
    assert (await put(rig, nodes, conns)).status_code == 200
    assert mgr.keys("u1|") == ["u1|daily-planner|t"] and mgr.status("u1|daily-planner|t")["at"] == "07:30"
    assert await until(lambda: any("📞" in t for _, t in rig.telegram.sent), tries=200)
    mgr.delay_for = lambda at, zone: 3600  # no further runs; let the one under way finish
    await asyncio.sleep(0.3)
    assert [t for chat, t in rig.telegram.sent if chat == 555 and "📞" in t][0] == "📞 SBX-call · FSX-call · PEX-call"
    run_ = await last(rig)
    assert run_["trigger"] == "schedule" and [x["node"] for x in run_["trace"]] == ["t", "calls", "agg", "send"] and run_["status"] == "ok"
    # moving the time moves the schedule; removing the trigger stops it
    nodes[0]["parameters"]["time"] = "08:15"
    await put(rig, nodes, conns)
    assert mgr.status("u1|daily-planner|t")["at"] == "08:15"
    off = [n("t", "trigger.chat"), n("a", "respond", text="x")]
    await put(rig, off, wire(("t", 0, "a")))
    assert mgr.keys("u1|") == []
    await put(rig, nodes, conns)
    assert mgr.keys("u1|") == ["u1|daily-planner|t"]
    assert (await rig.client.delete(F, headers=headers())).status_code == 200
    assert mgr.keys("u1|") == []  # back to the default workflow: no schedule


async def test_an_unreadable_time_or_zone_does_not_schedule_and_a_created_agents_schedule_stops_with_it(rig):
    await connect(rig)
    await choose(rig, agent=PLANNER)
    mgr = rig.app.state.flowsched
    made = (await rig.client.post("/v1/agent/agents", json={"name": "Digest", "templateId": "call-digest"}, headers=headers())).json()
    assert mgr.keys("u1|") == [f"u1|{made['id']}|trigger"]  # the template's schedule starts as soon as the agent exists
    base = f"/v1/agent/agents/{made['id']}"
    flow = (await rig.client.get(f"{base}/flow", headers=headers())).json()
    flow["nodes"][0]["parameters"]["time"] = "7pm"
    r = await rig.client.put(f"{base}/flow", json={"nodes": flow["nodes"], "connections": {k: v for k, v in flow["connections"].items()}}, headers=headers())
    assert r.status_code == 200 and mgr.keys("u1|") == []  # saved, but a time that can't be read doesn't run
    assert (await rig.client.delete(base, headers=headers())).status_code == 204 and mgr.keys("u1|") == []


async def test_after_a_restart_every_saved_schedule_runs_again(rig):
    from app.main import start_flow_schedules

    await planner_ready(rig)
    nodes, conns = scheduled("06:45")
    await put(rig, nodes, conns)
    mgr = rig.app.state.flowsched
    mgr.stop_prefix("u1|")
    assert mgr.keys() == []  # (the service was restarted: the loops are gone, the saved workflow is not)
    await start_flow_schedules(rig.app)
    assert mgr.keys() == ["u1|daily-planner|t"] and mgr.status("u1|daily-planner|t")["at"] == "06:45"


# --- Ask me: a question on the phone that the workflow waits on ---------------------------------------------------------------------


def asking():
    return [
        n("t", "trigger.schedule"),
        n("calls", "data.follow_ups"),
        n("agg", "core.aggregate", field="title"),
        n("ask", "msg.ask", text="You have {{ $json.count }} calls: {{ join($json.title, ', ') }}. Do them now?", options=["Call now", "Later"]),
        n("now", "msg.send", text="📞 Great, first: {{ $node['Put them together'].first.title[0] }} ({{ $json.answer }})"),
        n("later", "msg.send", text="⏰ OK, I'll leave {{ $json.count }} for later."),
    ], wire(("t", 0, "calls"), ("calls", 0, "agg"), ("agg", 0, "ask"), ("ask", 0, "now"), ("ask", 1, "later"))


async def master(rig):
    from .test_channels import send

    c = await telegram(rig, agent="assistant")
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    return c


async def test_ask_me_sends_a_question_with_buttons_and_the_workflow_waits_then_carries_on_from_the_answer(rig):
    from .test_availability import press

    await planner_ready(rig)
    seed(rig)
    c = await master(rig)
    nodes, conns = asking()
    nodes[4]["name"], nodes[2]["name"] = "Send go", "Put them together"
    nodes[4]["parameters"]["text"] = "📞 Great ({{ $json.answer }}): {{ $json.count }} calls"
    assert (await put(rig, nodes, conns)).status_code == 200
    rig.telegram.sent.clear()
    rig.telegram.keyboards.clear()
    await run(rig, scheduled=True)
    q = [t for chat, t in rig.telegram.sent if chat == 555]
    assert q == ["You have 3 calls: SBX-call, FSX-call, PEX-call. Do them now?"]  # asked, and nothing else yet
    keys = [b["callback_data"] for k in rig.telegram.keyboards if k for row in k for b in row]
    assert [k.split(":")[1] for k in keys] == ["0", "1"] and all(k.startswith("fw:") for k in keys)
    first = await last(rig)
    assert [t["node"] for t in first["trace"]] == ["t", "calls", "agg", "ask"]  # it stopped at the question

    rig.telegram.sent.clear()
    await press(rig, c, keys[0], 2, chat=555)
    assert any("📞 Great (Call now): 3 calls" in t for _, t in rig.telegram.sent)  # the "Call now" output ran, with the answer in its item
    assert not any("later" in t for _, t in rig.telegram.sent)
    resumed = await last(rig)
    assert resumed["trigger"] == "reply" and [t["node"] for t in resumed["trace"]] == ["now"]
    rig.telegram.sent.clear()
    await press(rig, c, keys[0], 3, chat=555)  # a second tap does nothing but say so
    assert "out of date" in rig.telegram.sent[-1][1]


async def test_the_other_answer_takes_the_other_output(rig):
    from .test_availability import press

    await planner_ready(rig)
    seed(rig)
    c = await master(rig)
    nodes, conns = asking()
    assert (await put(rig, nodes, conns)).status_code == 200
    rig.telegram.keyboards.clear()
    await run(rig, scheduled=True)
    later = next(b["callback_data"] for k in rig.telegram.keyboards if k for row in k for b in row if b["callback_data"].startswith("fw:1:"))
    rig.telegram.sent.clear()
    await press(rig, c, later, 2, chat=555)
    assert any("⏰ OK, I'll leave 3 for later." in t for _, t in rig.telegram.sent)


async def test_an_ask_with_no_phone_to_ask_fails_clearly_and_leaves_nothing_waiting(rig):
    from app.application.ports import UserSettings
    from app.infrastructure.db.repositories import SqlSettingsRepository

    await planner_ready(rig)
    seed(rig)
    nodes, conns = asking()
    assert (await put(rig, nodes, conns)).status_code == 200
    r = await rig.client.post(RUN, json={"scheduled": True}, headers=headers())
    assert r.status_code == 422 and "no Telegram chat is linked" in r.json()["error"]["message"]
    async with rig.app.state.session_factory() as session:
        s = await SqlSettingsRepository(session, rig.app.state.box).get("u1") or UserSettings("u1")
        assert s.flow_waits == {}


async def test_ask_me_needs_two_to_four_answers_and_a_question(rig):
    await planner_ready(rig)
    for bad in ({"text": "Ok?", "options": ["Only one"]}, {"text": "", "options": ["a", "b"]}, {"text": "Ok?", "options": ["a", "b", "c", "d", "e"]}, {"text": "Ok?", "options": ["a", " "]}):
        nodes = [n("t", "trigger.chat"), n("q", "msg.ask", **bad), n("a", "respond")]
        r = await rig.client.post(f"{F}/check", json={"nodes": nodes, "connections": wire(("t", 0, "q"), ("q", 0, "a"))}, headers=headers())
        assert any("answers to offer" in p or "question to ask" in p for p in r.json()["problems"]), bad
    ok = [n("t", "trigger.chat"), n("q", "msg.ask", text="Ok?", options=["a", "b", "c"]), n("a", "respond"), n("b", "respond"), n("c", "respond")]
    good = await rig.client.post(f"{F}/check", json={"nodes": ok, "connections": wire(("t", 0, "q"), ("q", 0, "a"), ("q", 1, "b"), ("q", 2, "c"))}, headers=headers())
    assert good.json()["problems"] == []
