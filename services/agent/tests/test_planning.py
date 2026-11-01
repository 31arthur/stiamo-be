import json
from datetime import date, timedelta

from app.domain.dayplan import check_blocks, free_windows
from app.domain.llm import ChatResult, ToolCall

from .conftest import headers
from .test_agent_api import choose, connect

PLANNER = "/v1/agent/agents/daily-planner"
TODAY = date.today()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


def fill(rig):
    """A day with a hearing at 10, a meeting at 14, a call due, two tasks and an overdue call."""
    rig.data.extra_hearings = [{"id": "h9", "caseId": "c1", "date": TOMORROW, "time": 600, "duration": 60, "forum": "JCL Kurnool", "purpose": "Arguments", "outcome": {"status": "open"}}]
    rig.data.tables = {
        "meetings": [{"id": "m1", "title": "Union leaders", "slot": {"date": TOMORROW, "start": 840, "duration": 60}, "outcome": {"status": "open"}}],
        "tasks": [
            {"id": "t1", "title": "Draft the rejoinder", "priority": "high", "dueDate": TOMORROW, "estimate": 90, "outcome": {"status": "open"}, "related": {"kind": "case", "id": "c1"}},
            {"id": "t2", "title": "Pay the court fee", "priority": "normal", "dueDate": (TODAY - timedelta(days=2)).isoformat(), "estimate": 20, "outcome": {"status": "open"}},
            {"id": "t3", "title": "Done already", "dueDate": TOMORROW, "outcome": {"status": "done"}},
        ],
        "follow-ups": [
            {"id": "f1", "title": "Call the advocate", "channel": "call", "due": {"date": TOMORROW}, "duration": 15, "outcome": {"status": "open"}, "attempts": [{"date": TODAY.isoformat(), "minute": 600, "result": "no_answer"}]},
            {"id": "f2", "title": "Chase the labour office", "channel": "call", "due": {"date": (TODAY - timedelta(days=1)).isoformat()}, "duration": 15, "outcome": {"status": "open"}},
        ],
        "plans": [],
    }


async def ask(rig, calls, answer="Here is the plan."):
    await connect(rig)
    await choose(rig, agent=PLANNER)
    rig.script += [ChatResult("", [ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(calls)]), ChatResult(answer)]
    return (await rig.client.post(f"{PLANNER}/chat", json={"message": "Plan tomorrow"}, headers=headers())).json()


def test_free_windows_are_what_the_fixed_things_leave():
    busy = [(600, 660, "hearing"), (840, 900, "meeting")]
    assert free_windows(busy) == [(540, 600), (660, 840), (900, 1260)]
    assert free_windows([(500, 1300, "all day")]) == []


def test_a_schedule_must_fit_around_what_is_fixed_and_itself():
    busy = [(600, 660, "hearing: Arguments")]
    assert check_blocks([{"title": "A", "start": 540, "duration": 60}, {"title": "B", "start": 660, "duration": 30}], busy) == []
    overlaps = check_blocks([{"title": "A", "start": 630, "duration": 60}, {"title": "B", "start": 700, "duration": 30}, {"title": "C", "start": 710, "duration": 30}], busy)
    assert len(overlaps) == 2 and "hearing: Arguments" in overlaps[0] and "“B”" in overlaps[1] or "“C”" in overlaps[1]
    assert check_blocks([{"title": "Late", "start": 22 * 60, "duration": 30}], []) and check_blocks([{"title": "Zero", "start": 600, "duration": 0}], [])


async def test_the_team_is_registered_with_the_tools_each_one_needs(rig):
    ids = [a["id"] for a in (await rig.client.get("/v1/agent/settings", headers=headers())).json()["agents"]]
    assert {"daily-planner", "hearing-prep", "follow-up-chaser", "deadline-scout"} <= set(ids)
    out = (await rig.client.post(f"{PLANNER}/prompt/preview", json={"question": "x"}, headers=headers())).json()
    assert [t["name"] for t in out["tools"]] == ["day_agenda", "free_time", "upcoming", "propose_schedule", "search_cases", "get_case", "case_documents", "waiting_in_attention"]
    assert TOMORROW in out["system"] and "21:00" in out["system"]
    prep = (await rig.client.post("/v1/agent/agents/hearing-prep/prompt/preview", json={"question": "x"}, headers=headers())).json()
    assert "find_documents" in [t["name"] for t in prep["tools"]]
    chaser = (await rig.client.post("/v1/agent/agents/follow-up-chaser/prompt/preview", json={"question": "x"}, headers=headers())).json()
    assert [t["name"] for t in chaser["tools"]] == ["day_agenda", "free_time", "upcoming", "propose_schedule"]
    assert all(set(a["workspaces"]) == {"personal", "sb", "fsa", "media"} for a in (await rig.client.get("/v1/agent/settings", headers=headers())).json()["agents"] if a["id"] in ("daily-planner", "follow-up-chaser", "deadline-scout"))


async def test_the_day_agenda_lists_what_is_fixed_what_is_due_and_what_is_free(rig):
    fill(rig)
    await ask(rig, [("day_agenda", {})])
    result = json.loads(rig.llm_log[-1]["last"])
    assert [h["time"] for h in result["hearings"]] == ["10:00"] and result["hearings"][0]["case"] == "SA-2026-001"
    assert [m["title"] for m in result["meetings"]] == ["Union leaders"]
    assert [f["title"] for f in result["followUps"]] == ["Call the advocate"] and result["followUps"][0]["tries"] == 1
    assert [t["title"] for t in result["tasks"]] == ["Draft the rejoinder"]  # the finished one is not there
    assert [t["title"] for t in result["overdueTasks"]] == ["Pay the court fee"] and [f["title"] for f in result["overdueFollowUps"]] == ["Chase the labour office"]
    assert result["freeWindows"] == ["09:00–10:00", "11:00–14:00", "15:00–21:00"]


async def test_a_good_schedule_comes_back_as_a_card_and_books_nothing(rig):
    fill(rig)
    out = await ask(rig, [("propose_schedule", {"blocks": [{"kind": "task", "id": "t1", "start": 660, "duration": 90}, {"kind": "followUp", "id": "f1", "start": 570}]})])
    card = out["found"][0]
    assert card["kind"] == "schedule" and card["date"] == TOMORROW
    assert [(b["id"], b["from"], b["to"]) for b in card["blocks"]] == [("f1", "09:30", "09:45"), ("t1", "11:00", "12:30")]
    assert rig.data.meetings == []  # a proposal writes nothing


async def test_a_schedule_that_clashes_is_sent_back_with_the_reason(rig):
    fill(rig)
    out = await ask(rig, [("propose_schedule", {"blocks": [{"kind": "task", "id": "t1", "start": 630, "duration": 60}, {"kind": "task", "id": "nope", "start": 700}]})])
    result = json.loads(rig.llm_log[-1]["last"])
    assert result["ok"] is False and any("hearing" in p for p in result["problems"]) and any("nope" in p for p in result["problems"])
    assert out["found"] == []


async def test_the_scout_reads_the_attention_queue_and_what_is_coming(rig):
    fill(rig)
    await connect(rig)
    scout = "/v1/agent/agents/deadline-scout"
    await choose(rig, agent=scout)
    rig.script += [ChatResult("", [ToolCall("a", "upcoming", {"days": 3}), ToolCall("b", "waiting_in_attention", {})]), ChatResult("Two things.")]
    out = (await rig.client.post(f"{scout}/chat", json={"message": "What is due?"}, headers=headers())).json()
    assert out["answer"] == "Two things."
    assert json.loads(rig.llm_log[-1]["last"]) == {"count": 0, "items": []}  # the queue is empty here; the tool ran and said so
