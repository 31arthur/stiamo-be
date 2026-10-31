import json

from app.domain.llm import ChatResult

from .conftest import headers
from .test_mail_filter import BASE, answer, config, make

USAGE = "/v1/agent/usage"


def priced(label, confidence=0.95, cost=0.001, tokens_in=100, tokens_out=20):
    return ChatResult(json.dumps({"label": label, "confidence": confidence, "reason": "r"}), usage={"inputTokens": tokens_in, "outputTokens": tokens_out, "cost": cost})


async def summary(rig, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return (await rig.client.get(f"{USAGE}/summary?{q}", headers=headers())).json()


async def test_a_real_run_records_its_tokens_cost_and_emails(rig):
    wid = await make(rig)
    rig.script += [priced("keep"), priced("spam", 0.6)]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    s = await summary(rig)
    assert s["total"] == {"runs": 1, "errors": 0, "inputTokens": 200, "outputTokens": 40, "items": 2, "cost": 0.002, "pricedRuns": 1}
    assert s["byAgent"][0]["name"] == "Spam filter" and s["byAgent"][0]["kind"] == "workflow" and s["byAgent"][0]["cost"] == 0.002
    t = (await rig.client.get(f"{USAGE}/transactions", headers=headers())).json()
    row = t["items"][0]
    assert t["nextBefore"] is None and len(t["items"]) == 1
    assert (row["agentName"], row["trigger"], row["inputTokens"], row["outputTokens"], row["cost"], row["items"]) == ("Spam filter", "manual", 200, 40, 0.002, 2)
    assert row["provider"] == "openai" and row["model"] == "gpt-4o"


async def test_test_runs_count_too_because_they_cost_the_same(rig):
    wid = await make(rig)
    rig.script += [priced("keep"), priced("keep")]
    await rig.client.post(f"{BASE}/{wid}/test", json={}, headers=headers())
    t = (await rig.client.get(f"{USAGE}/transactions", headers=headers())).json()["items"]
    assert [r["trigger"] for r in t] == ["test"]


async def test_the_decision_model_reports_its_cost_and_a_model_that_doesnt_has_none(rig):
    from .test_agent_api import connect

    await connect(rig, "openrouter", "sk-or-12345678")
    j = (await rig.client.post(BASE, json={"name": "Jev", "description": "", "config": config(decider="jev", batchSize=10)}, headers=headers())).json()["id"]
    await rig.client.post(f"{BASE}/{j}/test", json={}, headers=headers())
    row = (await rig.client.get(f"{USAGE}/transactions", headers=headers())).json()["items"][0]
    assert row["cost"] == 1e-05 and row["provider"] == "openrouter" and row["inputTokens"] == 0  # the decision model charges by the call, not by token

    wid = await make(rig)
    rig.script += [answer("keep"), answer("keep")]  # no usage at all
    await rig.client.post(f"{BASE}/{wid}/test", json={}, headers=headers())
    s = await summary(rig, agent=wid)
    assert s["total"]["cost"] is None and s["total"]["pricedRuns"] == 0 and s["total"]["runs"] == 1  # unknown is not the same as free


async def test_usage_survives_clearing_the_history_and_deleting_the_agent(rig):
    wid = await make(rig)
    rig.script += [priced("keep"), priced("keep")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    await rig.client.delete(f"/v1/agent/agents/{wid}/runs", headers=headers())
    assert (await summary(rig))["total"]["inputTokens"] == 200
    await rig.client.delete(f"{BASE}/{wid}", headers=headers())
    s = await summary(rig)
    assert s["total"]["inputTokens"] == 200 and s["byAgent"][0]["name"] == "Spam filter"  # the money was spent, and the line says on what


async def test_window_filter_daily_and_paging(rig):
    wid = await make(rig)
    for _ in range(3):
        rig.script += [priced("keep"), priced("keep")]
        await rig.client.post(f"{BASE}/{wid}/test", json={}, headers=headers())
    s = await summary(rig, days=7)
    assert s["windowDays"] == 7 and len(s["daily"]) == 7 and s["daily"][-1]["runs"] == 3 and s["allTime"]["runs"] == 3
    assert len((await summary(rig))["daily"]) == 30
    page = (await rig.client.get(f"{USAGE}/transactions?limit=2", headers=headers())).json()
    assert len(page["items"]) == 2 and page["nextBefore"]
    rest = (await rig.client.get(f"{USAGE}/transactions?limit=2&before={page['nextBefore']}", headers=headers())).json()
    assert len(rest["items"]) == 1 and rest["nextBefore"] is None
    assert (await rig.client.get(f"{USAGE}/transactions?agent=someone-else", headers=headers())).json()["items"] == []


async def test_usage_is_private_and_needs_a_signed_in_person(rig):
    wid = await make(rig)
    rig.script += [priced("keep"), priced("keep")]
    await rig.client.post(f"{BASE}/{wid}/test", json={}, headers=headers())
    other = headers("b@example.com", "u2")
    assert (await rig.client.get(f"{USAGE}/summary", headers=other)).json()["total"]["runs"] == 0
    assert (await rig.client.get(f"{USAGE}/transactions", headers=other)).json()["items"] == []
    assert (await rig.client.get(f"{USAGE}/summary")).status_code == 401


async def test_a_chat_agents_tokens_are_recorded_and_its_stats_show_cost(rig):
    from .test_agent_api import CASES, choose, connect

    await connect(rig)
    await choose(rig)
    rig.script += [ChatResult("Here is what I found.", usage={"inputTokens": 50, "outputTokens": 10, "cost": 0.0004})]
    r = await rig.client.post(f"{CASES}/chat", json={"message": "hello"}, headers=headers())
    assert r.status_code == 200, r.text
    s = await summary(rig)
    assert s["total"]["inputTokens"] == 50 and s["total"]["cost"] == 0.0004 and s["byAgent"][0]["kind"] == "chat"
    stats = (await rig.client.get(f"{CASES}/stats", headers=headers())).json()
    assert stats["inputTokens"] == 50 and stats["cost"] == 0.0004
    assert stats["allTime"] == {"runs": 1, "errors": 0, "inputTokens": 50, "outputTokens": 10, "items": 0, "cost": 0.0004, "pricedRuns": 1}
