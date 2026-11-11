"""Agents as workflows: branches, loops, a fixed scope, several agent nodes, tool limits, the trace of every node, and editing the graph."""

import pytest
from app.domain.llm import AiError, ChatResult, ToolCall

from .conftest import headers
from .test_dayplans import planner_ready
from .test_leaks import CANARY, TOMORROW, seed
from .test_planning import PLANNER

G = f"{PLANNER}/graph"
CHAT = f"{PLANNER}/chat"


@pytest.fixture(autouse=True)
def graph_engine(rig):
    rig.app.state.settings.engine = "graph"


def node(i, t, label="", **config):
    return {"id": i, "type": t, "label": label or i, "config": config}


def edge(a, b, label="next"):
    return {"from": a, "to": b, "label": label}


async def put(rig, nodes, edges):
    return await rig.client.put(G, json={"nodes": nodes, "edges": edges}, headers=headers())


async def ask(rig, text="Plan tomorrow"):
    return await rig.client.post(CHAT, json={"message": text}, headers=headers())


async def last_run(rig):
    runs = (await rig.client.get(f"{PLANNER}/runs", headers=headers())).json()["items"]
    return (await rig.client.get(f"/v1/agent/runs/{runs[0]['id']}", headers=headers())).json()


async def test_the_default_workflow_runs_straight_through_and_every_node_leaves_a_trace(rig):
    await planner_ready(rig)
    g = (await rig.client.get(G, headers=headers())).json()
    assert [n["type"] for n in g["nodes"]] == ["trigger", "scope", "prompt", "agent", "output"] and g["custom"] is False and g["problems"] == []
    assert g["engine"] == "graph" and {t["name"] for t in g["tools"]} >= {"day_agenda", "propose_schedule"}
    rig.script += [ChatResult("Here is the day.")]
    out = (await ask(rig)).json()
    assert out["answer"] == "Here is the day."
    trace = (await last_run(rig))["trace"]
    assert [(t["node"], t["state"]) for t in trace] == [("trigger", "ok"), ("scope", "ok"), ("prompt", "ok"), ("agent", "ok"), ("output", "ok")]
    agent = trace[3]
    assert agent["in"] == "Plan tomorrow" and agent["out"] == "Here is the day." and agent["ms"] >= 0 and "usage" in agent


async def test_a_branch_sends_the_run_down_the_edge_whose_condition_holds(rig):
    await planner_ready(rig)
    nodes = [
        node("t", "trigger"),
        node("b", "branch", "About money?", cases=[{"var": "input", "op": "contains", "value": "money", "label": "money"}]),
        node("s1", "set", "Money", name="kind", value="💰"),
        node("s2", "set", "Other", name="kind", value="🗓️"),
        node("p", "prompt"),
        node("a", "agent"),
        node("o", "output", template="{{vars.kind}} {{answer}}"),
    ]
    edges = [edge("t", "b"), edge("b", "s1", "money"), edge("b", "s2", "else"), edge("s1", "p"), edge("s2", "p"), edge("p", "a"), edge("a", "o")]
    assert (await put(rig, nodes, edges)).status_code == 200
    rig.script += [ChatResult("Pay the invoice.")]
    assert (await ask(rig, "How much money do we owe?")).json()["answer"] == "💰 Pay the invoice."
    rig.script += [ChatResult("Nothing on.")]
    assert (await ask(rig, "What is on tomorrow?")).json()["answer"] == "🗓️ Nothing on."
    trace = (await last_run(rig))["trace"]
    assert [t["node"] for t in trace] == ["t", "b", "s2", "p", "a", "o"] and trace[1]["out"] == "→ else"  # the path it really took


async def test_a_loop_goes_round_until_its_condition_holds_or_it_has_gone_round_enough(rig):
    await planner_ready(rig)
    nodes = [node("t", "trigger"), node("a", "agent", input="{{answer}}"), node("l", "loop", "Refine", max=3, until={"var": "answer", "op": "contains", "value": "FINAL"}), node("o", "output")]
    edges = [edge("t", "a"), edge("a", "l"), edge("l", "a", "again"), edge("l", "o", "done")]
    # the first round has no answer yet, so it asks with the person's own words
    nodes[1]["config"]["input"] = "{{input}}"
    assert (await put(rig, nodes, edges)).status_code == 200
    rig.script += [ChatResult("draft one"), ChatResult("draft two"), ChatResult("FINAL copy")]
    start = len(rig.llm_log)
    assert (await ask(rig, "Write it")).json()["answer"] == "FINAL copy"
    assert len(rig.llm_log) - start == 3  # went round until the condition held
    rig.script += [ChatResult("a"), ChatResult("b"), ChatResult("c"), ChatResult("d"), ChatResult("e")]
    start = len(rig.llm_log)
    await ask(rig, "Write it again")
    assert len(rig.llm_log) - start <= 4  # never held: stopped at the loop's limit (the first pass and up to three more rounds)


async def test_a_fixed_scope_holds_every_later_node_to_that_workspace(rig):
    await planner_ready(rig)
    seed(rig)
    nodes = [node("t", "trigger"), node("s", "scope", mode="fixed", workspace="fsa"), node("p", "prompt"), node("a", "agent"), node("o", "output")]
    assert (await put(rig, nodes, [edge("t", "s"), edge("s", "p"), edge("p", "a"), edge("a", "o")])).status_code == 200
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {"date": TOMORROW})]), ChatResult("FSA only.")]
    start = len(rig.llm_log)
    await ask(rig)  # asked with no workspace at all (the app's own chat): the workflow's scope node holds it to FSA
    seen = "\n".join(e["everything"] for e in rig.llm_log[start:])
    assert CANARY["fsa"] in seen and CANARY["sb"] not in seen and CANARY["personal"] not in seen
    assert (await last_run(rig))["workspace"] == "fsa"


async def test_two_agent_nodes_in_a_row_pass_the_answer_on_and_add_up_their_use(rig):
    await planner_ready(rig)
    nodes = [node("t", "trigger"), node("a1", "agent", "Draft"), node("a2", "agent", "Polish", input="Make this shorter: {{answer}}"), node("o", "output")]
    assert (await put(rig, nodes, [edge("t", "a1"), edge("a1", "a2"), edge("a2", "o")])).status_code == 200
    rig.script += [ChatResult("A long first draft."), ChatResult("Short.")]
    start = len(rig.llm_log)
    assert (await ask(rig, "Draft it")).json()["answer"] == "Short."
    assert "Make this shorter: A long first draft." in rig.llm_log[-1]["last"] and len(rig.llm_log) - start == 2
    run = await last_run(rig)
    assert [t["node"] for t in run["trace"]] == ["t", "a1", "a2", "o"] and run["output"] == "Short."


async def test_an_agent_node_can_be_given_fewer_tools_than_the_agent_has(rig):
    await planner_ready(rig)
    nodes = [node("t", "trigger"), node("a", "agent", tools=["free_time"]), node("o", "output")]
    assert (await put(rig, nodes, [edge("t", "a"), edge("a", "o")])).status_code == 200
    rig.script += [ChatResult("ok")]
    await ask(rig)
    assert rig.llm_log[-1]["tools"] == ["free_time"]


async def test_a_failing_model_is_recorded_on_the_node_that_failed(rig):
    await planner_ready(rig)
    rig.script += [AiError("The provider is down.")]
    assert (await ask(rig)).status_code == 422
    run = await last_run(rig)
    assert run["status"] == "error" and [(t["node"], t["state"]) for t in run["trace"]][-1] == ("agent", "error")


async def test_a_workflow_that_cannot_run_is_refused_with_the_reason_and_never_saved(rig):
    await planner_ready(rig)
    bad = [node("t", "trigger"), node("a", "agent"), node("o", "output")]
    r = await put(rig, bad, [edge("t", "a")])
    assert r.status_code == 422 and "leads nowhere" in r.json()["error"]["message"]
    checked = (await rig.client.post(f"{G}/check", json={"nodes": bad, "edges": [edge("t", "a")]}, headers=headers())).json()
    assert any("can't be reached" in p for p in checked["problems"])
    assert (await rig.client.get(G, headers=headers())).json()["custom"] is False


async def test_a_workflow_is_saved_per_person_and_can_be_put_back_to_the_default(rig):
    await planner_ready(rig)
    nodes = [node("t", "trigger"), node("a", "agent"), node("o", "output", template="{{answer}}!")]
    saved = (await put(rig, nodes, [edge("t", "a"), edge("a", "o")])).json()
    assert saved["custom"] is True and [n["id"] for n in saved["nodes"]] == ["t", "a", "o"]
    assert (await rig.client.get(G, headers=headers("other@example.com", "u2"))).json()["custom"] is False
    rig.script += [ChatResult("Hi")]
    assert (await ask(rig)).json()["answer"] == "Hi!"
    back = (await rig.client.delete(G, headers=headers())).json()
    assert back["custom"] is False and [n["type"] for n in back["nodes"]] == ["trigger", "scope", "prompt", "agent", "output"]
    rig.script += [ChatResult("Hi")]
    assert (await ask(rig)).json()["answer"] == "Hi"


async def test_a_stored_workflow_that_no_longer_runs_falls_back_to_the_default_rather_than_breaking_the_agent(rig):
    from app.application.ports import UserSettings
    from app.infrastructure.db.repositories import SqlSettingsRepository

    await planner_ready(rig)
    async with rig.app.state.session_factory() as session:
        repo = SqlSettingsRepository(session, rig.app.state.box)
        s = await repo.get("u1") or UserSettings("u1")
        s.graphs = {"daily-planner": {"nodes": [{"id": "t", "type": "trigger", "config": {}}], "edges": []}}
        await repo.save(s)
        await session.commit()
    rig.script += [ChatResult("Still answers.")]
    assert (await ask(rig)).json()["answer"] == "Still answers."


async def test_the_master_bot_and_the_evening_plan_run_on_the_same_engine(rig):
    from .test_dayplans import GOOD, generate, plan_script

    await planner_ready(rig)
    plan_script(rig, GOOD)
    out = (await generate(rig, day=TOMORROW)).json()
    assert out["status"] == "proposed" and out["blocks"]
    assert [t["node"] for t in (await last_run(rig))["trace"]] == ["trigger", "scope", "prompt", "agent", "output"]
