import pytest
from app.application.scope import ScopedData
from app.domain.llm import AiError, ChatResult, ToolCall
from app.domain.orchestrator import scope_line

from .conftest import FakeData, headers
from .test_channels import send, telegram
from .test_agent_api import choose
from .test_orchestrator import C, team_ready

TODAY = "2026-11-06"


def tasks():
    own = lambda i, ws, title: {"id": i, "title": title, "workspace": ws, "dueDate": TODAY, "estimate": 30, "outcome": {"status": "open"}}  # noqa: E731
    return [own("t1", "fsa", "FSA invoice Kaveri"), own("t2", "sb", "SB affidavit"), own("t3", "personal", "Renew passport")]


async def bot(rig, workspace, token, chat):
    r = await rig.client.post("/v1/agent/channels/telegram", json={"botToken": token, "workspace": workspace}, headers=headers())
    assert r.status_code == 201, r.text
    c = r.json()
    await send(rig, c, f"/start {c['pairingCode']}", chat=chat, update=1)
    return c


async def test_a_workspace_view_leaves_out_what_belongs_to_the_others():
    data = FakeData()
    data.tables = {"tasks": tasks(), "clients": [{"id": "cl1", "name": "Kaveri"}], "meetings": [], "follow-ups": [], "plans": []}
    data.calendar = [{"date": TODAY, "title": "Union call", "workspace": "sb"}, {"date": TODAY, "title": "Sprint review", "workspace": "fsa"}]
    data.accounts = [{"id": "a1", "workspace": "sb"}, {"id": "f1", "workspace": "fsa"}]
    fsa, sb = ScopedData(data, "fsa"), ScopedData(data, "sb")
    assert [t["title"] for t in await fsa.records("tasks")] == ["FSA invoice Kaveri"] and [t["title"] for t in await sb.records("tasks")] == ["SB affidavit"]
    assert [c["name"] for c in await fsa.records("clients")] == ["Kaveri"] and await sb.records("clients") == []  # clients are FSA's
    assert await fsa.cases() == [] and await fsa.hearings() == [] and await fsa.attachments() == [] and len(await sb.cases()) > 0  # cases are the S&B practice's
    assert [e["title"] for e in await fsa.calendar_events(TODAY, TODAY)] == ["Sprint review"]
    assert [a["id"] for a in await fsa.mail_accounts()] == ["f1"]
    assert (await fsa.mail_uids("f1", "INBOX", None, None))["uids"]
    with pytest.raises(AiError, match="isn't one of this workspace's"):
        await fsa.mail_message("a1", "INBOX", 4)  # S&B's mailbox is not FSA's to read
    with pytest.raises(AiError):
        await fsa.mail_send("a1", {"to": ["x@y.org"]})


def test_every_agent_in_a_workspace_is_told_to_stay_inside_it():
    assert "FSA (a software and technology consultancy) workspace" in scope_line("fsa") and "this workspace only" in scope_line("fsa")
    assert scope_line("all") == "" and scope_line("nowhere") == ""


async def test_each_workspace_bot_is_that_workspaces_assistant_and_sees_only_its_work(rig):
    await team_ready(rig)
    rig.data.tables = {"tasks": tasks(), "meetings": [], "follow-ups": [], "plans": []}
    fsa = await bot(rig, "fsa", "456:def", 888)
    assert rig.telegram.sent[-1][1].startswith("Connected. I'm the FSA assistant") and "I only see FSA" in rig.telegram.sent[-1][1]
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {"date": TODAY})]), ChatResult("Two things on.")]
    await send(rig, fsa, "Plan my day", chat=888, update=2)
    first = next(m for m in rig.llm_log if "Plan my day" in m["last"])
    assert "assistant of the FSA" in first["last"]  # told which workspace it works for
    seen = rig.llm_log[-1]["last"]  # what the agent read back from its tool
    assert "FSA invoice Kaveri" in seen and "SB affidavit" not in seen and "Renew passport" not in seen
    # the S&B bot over the same data sees S&B's work, not FSA's
    sb = await bot(rig, "sb", "789:ghi", 999)
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {"date": TODAY})]), ChatResult("One thing on.")]
    await send(rig, sb, "Plan my day", chat=999, update=2)
    seen = rig.llm_log[-1]["last"]
    assert "SB affidavit" in seen and "FSA invoice Kaveri" not in seen


async def test_what_a_workspace_assistant_adds_belongs_to_that_workspace_whatever_the_model_says(rig):
    await team_ready(rig)
    rig.data.tables = {"tasks": [], "meetings": [], "follow-ups": [], "plans": []}
    fsa = await bot(rig, "fsa", "456:def", 888)
    rig.script += [ChatResult("", [ToolCall("a", "add_task", {"title": "Send the invoice", "workspace": "sb"})]), ChatResult("Proposed.")]
    await send(rig, fsa, "add a task to send the invoice", chat=888, update=2)
    await send(rig, fsa, "yes", chat=888, update=3)
    saved = dict(rig.data.saved)
    assert saved["tasks"]["title"] == "Send the invoice" and saved["tasks"]["workspace"] == "fsa"


async def test_each_assistant_keeps_its_own_thread_and_only_routes_to_the_agents_of_its_workspace(rig):
    await team_ready(rig)
    # the planner is placed in S&B only: the FSA assistant can't hand a question to it
    await rig.client.put("/v1/agent/agents/daily-planner/workspaces", json={"workspaces": ["sb"]}, headers=headers())
    await rig.client.put("/v1/agent/agents/librarian/workspaces", json={"workspaces": ["fsa"]}, headers=headers())  # FSA has its own agent to answer there
    await choose(rig, agent="/v1/agent/agents/librarian")
    general = await telegram(rig, agent="assistant")
    await send(rig, general, f"/start {general['pairingCode']}", chat=555, update=1)
    fsa = await bot(rig, "fsa", "456:def", 888)
    sb = await bot(rig, "sb", "789:ghi", 999)
    for i, (c, chat) in enumerate(((general, 555), (fsa, 888), (sb, 999))):
        rig.script += [ChatResult("ok")]
        await send(rig, c, "Plan my day" + (" for FSA" if c is general else ""), chat=chat, update=2, secret=rig.telegram.webhooks[i][1])  # each bot has its own webhook secret
    threads = (await rig.client.get(C, headers=headers())).json()["items"]
    assert sorted(t["workspace"] for t in threads) == ["fsa", "fsa", "sb"]  # a thread each (the master bot worked in FSA, as asked)
    turns = {t["workspace"]: (await rig.client.get(f"{C}/{t['id']}", headers=headers())).json()["turns"][0] for t in threads}
    assert turns["sb"]["agentId"] == "daily-planner" and turns["fsa"]["agentId"] != "daily-planner"
