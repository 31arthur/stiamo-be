from app.domain.llm import AiError, ChatResult, ToolCall

from .conftest import headers
from .test_agent_api import CASES, LIBRARIAN, choose, connect
from .test_channels import ready, send, telegram


async def ask(rig, message="NHAI cases", agent=CASES):
    return await rig.client.post(f"{agent}/chat", json={"message": message}, headers=headers())


async def runs(rig, agent=CASES, **params):
    return (await rig.client.get(f"{agent}/runs", params=params, headers=headers())).json()


async def prepared(rig):
    await connect(rig)
    await choose(rig)


async def test_a_question_is_kept_with_what_it_was_given_what_it_did_and_what_it_answered(rig):
    await prepared(rig)
    await rig.client.put(f"{CASES}/prompt", json={"system": "Terse. {{tools}}", "inputTemplate": "Q: {{input}}"}, headers=headers())
    rig.script += [ChatResult("", [ToolCall("t1", "search_cases", {"query": "NHAI"})], usage={"inputTokens": 100, "outputTokens": 10}), ChatResult("One case.", usage={"inputTokens": 150, "outputTokens": 20})]
    await ask(rig)
    listed = await runs(rig)
    assert len(listed["items"]) == 1
    summary = listed["items"][0]
    assert (summary["trigger"], summary["status"], summary["input"], summary["output"], summary["model"]) == ("chat", "ok", "NHAI cases", "One case.", "gpt-4o")
    assert summary["found"] == 1 and summary["durationMs"] >= 0 and "systemPrompt" not in summary

    full = (await rig.client.get(f"/v1/agent/runs/{summary['id']}", headers=headers())).json()
    assert full["userMessage"] == "Q: NHAI cases" and full["systemPrompt"].startswith("Terse.")
    assert "search_cases" in full["tools"] and full["usage"] == {"inputTokens": 250, "outputTokens": 30}
    step = full["steps"][0]
    assert step["tool"] == "search_cases" and step["args"] == {"query": "NHAI"} and step["found"] == 1 and "SA-2026-001" in step["result"]


async def test_a_run_that_failed_is_kept_too_with_the_reason(rig):
    await prepared(rig)
    rig.script += [AiError("OpenAI says the account is out of credit.", 402)]
    r = await ask(rig)
    assert r.status_code == 422
    failed = (await runs(rig))["items"][0]
    assert failed["status"] == "error" and "credit" in failed["error"] and failed["output"] == ""


async def test_a_question_that_never_reached_a_model_is_not_logged(rig):
    r = await ask(rig)  # no model chosen
    assert r.status_code == 422
    assert (await runs(rig))["items"] == []


async def test_history_is_newest_first_pages_and_belongs_to_one_agent_and_one_person(rig):
    await prepared(rig)
    await choose(rig, agent=LIBRARIAN)
    rig.script += [ChatResult("a"), ChatResult("b"), ChatResult("c"), ChatResult("lib")]
    for q in ("first", "second", "third"):
        await ask(rig, q)
    await ask(rig, "library", LIBRARIAN)
    first_page = await runs(rig, limit=2)
    assert [i["input"] for i in first_page["items"]] == ["third", "second"] and first_page["next"]
    second_page = await runs(rig, limit=2, before=first_page["next"])
    assert [i["input"] for i in second_page["items"]] == ["first"] and second_page["next"] is None
    assert [i["input"] for i in (await runs(rig, LIBRARIAN))["items"]] == ["library"]
    other = headers("b@example.com", "u2")
    assert (await rig.client.get(f"{CASES}/runs", headers=other)).json()["items"] == []
    run_id = first_page["items"][0]["id"]
    assert (await rig.client.get(f"/v1/agent/runs/{run_id}", headers=other)).status_code == 404


async def test_statistics_cover_totals_the_last_days_and_the_last_run(rig):
    await prepared(rig)
    empty = (await rig.client.get(f"{CASES}/stats", headers=headers())).json()
    assert empty["totalRuns"] == 0 and empty["lastRun"] is None and len(empty["daily"]) == 14
    rig.script += [ChatResult("fine", usage={"inputTokens": 10, "outputTokens": 5}), AiError("down"), ChatResult("again", usage={"inputTokens": 20, "outputTokens": 5})]
    await ask(rig, "one")
    await ask(rig, "two")
    await ask(rig, "three")
    s = (await rig.client.get(f"{CASES}/stats", headers=headers())).json()
    assert (s["totalRuns"], s["runs"], s["ok"], s["errors"]) == (3, 3, 2, 1)
    assert s["inputTokens"] == 30 and s["outputTokens"] == 10 and s["byTrigger"] == {"chat": 3}
    assert s["daily"][-1]["runs"] == 3 and s["daily"][-1]["errors"] == 1
    assert s["lastRun"]["input"] == "three" and s["lastRun"]["status"] == "ok"


async def test_history_can_be_cleared_for_one_agent(rig):
    await prepared(rig)
    await choose(rig, agent=LIBRARIAN)
    rig.script += [ChatResult("a"), ChatResult("b")]
    await ask(rig)
    await ask(rig, "x", LIBRARIAN)
    assert (await rig.client.delete(f"{CASES}/runs", headers=headers())).status_code == 204
    assert (await runs(rig))["items"] == [] and len((await runs(rig, LIBRARIAN))["items"]) == 1


async def test_runs_from_telegram_say_where_they_came_from(rig):
    await ready(rig)
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", update=1)
    rig.script += [ChatResult("From Telegram.")]
    await send(rig, c, "hello bot", update=2)
    item = (await runs(rig))["items"][0]
    assert item["trigger"] == "telegram" and item["input"] == "hello bot"
    assert (await rig.client.get(f"/v1/agent/runs/{item['id']}", headers=headers())).json()["channel"] == c["id"]


async def test_the_history_keeps_only_the_newest_runs(rig):
    from app.infrastructure.db import runs as module

    old = module.KEEP
    module.KEEP = 3
    try:
        await prepared(rig)
        rig.script += [ChatResult(str(i)) for i in range(5)]
        for i in range(5):
            await ask(rig, f"q{i}")
        assert [i["input"] for i in (await runs(rig))["items"]] == ["q4", "q3", "q2"]
    finally:
        module.KEEP = old
