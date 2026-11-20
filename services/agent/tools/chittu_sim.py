"""A bench for trying Chittu with a real model in the loop, without a database server, a Telegram bot or a mailbox.

The model is whoever you put in the loop (a person, or another Claude reading the prompt this prints): it is given the exact system prompt and tool
descriptions production sends, calls the real tools against a seeded, persisted day, and writes its answer. `replay` then runs the same tool calls and answer
through the real Telegram webhook, so what is printed is exactly what the owner's phone would show.

    PYTHONPATH=. python tools/chittu_sim.py reset                 # seed a day
    PYTHONPATH=. python tools/chittu_sim.py prompt                # the system prompt and the tools, as the model sees them
    PYTHONPATH=. python tools/chittu_sim.py tool find_items '{"day": "today"}'
    PYTHONPATH=. python tools/chittu_sim.py show                  # the day as it stands (tasks, calls, meetings, history)
    PYTHONPATH=. python tools/chittu_sim.py replay transcript.json   # {"message": "...", "rounds": [[{"tool":..., "args":...}]], "reply": "..."}

State lives in $CHITTU_SIM (default /tmp/chittu-sim).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path(os.environ.get("CHITTU_SIM", "/tmp/chittu-sim"))
TODAY = datetime.now(ZoneInfo("Asia/Kolkata")).date()
T = TODAY.isoformat()
DAY = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731


def seed() -> dict:
    return {
        "hearings": [{"id": "h9", "caseId": "c1", "date": T, "time": 660, "duration": 60, "forum": "JCL Kurnool", "purpose": "Arguments, Alur wages", "outcome": {"status": "open"}}],
        "calendar": [{"id": "g1", "title": "Board call", "date": DAY(1), "startMinute": 600, "endMinute": 660, "calendar": "FSA"}],
        "tables": {
            "meetings": [{"id": "m1", "title": "Union leaders", "counterpart": "Mr Rao", "workspace": "sb", "slot": {"date": T, "start": 900, "duration": 60}, "outcome": {"status": "open"}}],
            "tasks": [
                {"id": "t1", "title": "Draft the rejoinder", "workspace": "sb", "priority": "high", "slot": {"date": T, "start": 540, "duration": 90}, "dueDate": T, "estimate": 90, "outcome": {"status": "open"}, "related": {"kind": "case", "id": "c1"}},
                {"id": "t2", "title": "File the court fee receipt", "workspace": "sb", "priority": "normal", "dueDate": T, "estimate": 20, "outcome": {"status": "open"}},
                {"id": "t3", "title": "Review the MFA consultation draft", "workspace": "sb", "priority": "low", "dueDate": T, "estimate": 45, "outcome": {"status": "open"}},
                {"id": "t4", "title": "Send the RTI first appeal", "workspace": "sb", "priority": "urgent", "slot": {"date": T, "start": 990, "duration": 30}, "dueDate": T, "estimate": 30, "outcome": {"status": "open"}},
                {"id": "t5", "title": "Pay the registry fee", "workspace": "sb", "priority": "normal", "dueDate": DAY(-2), "estimate": 15, "outcome": {"status": "open"}},
                {"id": "t6", "title": "Quarterly invoice to the client", "workspace": "fsa", "priority": "normal", "dueDate": DAY(3), "estimate": 30, "outcome": {"status": "open"}},
                {"id": "t7", "title": "Renew the domain", "workspace": "personal", "priority": "low", "dueDate": T, "estimate": 10, "outcome": {"status": "open"}},
            ],
            "follow-ups": [
                {"id": "f1", "title": "Call Ramesh Kumar about the wage list", "channel": "call", "workspace": "sb", "contact": "Ramesh Kumar", "due": {"date": T, "time": 750}, "duration": 15, "outcome": {"status": "open"}},
                {"id": "f2", "title": "Call Meena about the visit", "channel": "call", "workspace": "personal", "contact": "Meena", "due": {"date": T, "time": 960}, "duration": 15, "outcome": {"status": "open"}},
                {"id": "f3", "title": "Message Hemla the hearing date", "channel": "message", "workspace": "sb", "contact": "Hemla", "due": {"date": T}, "duration": 5, "outcome": {"status": "open"}},
            ],
            "plans": [],
        },
    }


def _save_state(data) -> None:
    (STATE / "data.json").write_text(json.dumps({"hearings": data.extra_hearings, "calendar": data.calendar, "tables": data.tables}, default=str))


class Bench:
    """The app, with its fakes, on a persisted day."""

    def __init__(self, script=None) -> None:
        from httpx import ASGITransport, AsyncClient  # noqa: F401

        from stiamo_common.db import Base  # noqa: F401
        from tests.conftest import FakeCaller, FakeData, FakeDecisions, FakeLlm, FakeMailEvents, FakeTelegram, FakeTokens, FakeTwilio, FakeVoice, test_registry  # noqa: F401

        self.script: list = script if script is not None else []
        self.log: list = []

    async def boot(self):
        from app.config import Settings
        from app.main import create_app
        from httpx import ASGITransport, AsyncClient
        from stiamo_common.db import Base
        from tests.conftest import FakeCaller, FakeData, FakeDecisions, FakeLlm, FakeMailEvents, FakeTelegram, FakeTokens, FakeTwilio, FakeVoice, test_registry
        from app.domain.llm import ModelInfo

        STATE.mkdir(parents=True, exist_ok=True)
        data = FakeData()
        saved = json.loads((STATE / "data.json").read_text())
        data.extra_hearings, data.calendar, data.tables = saved["hearings"], saved["calendar"], saved["tables"]

        async def save(table, record):
            rows = data.tables.setdefault(table, [])
            for i, r in enumerate(rows):
                if r["id"] == record["id"]:
                    rows[i] = record
                    break
            else:
                rows.append(record)
            _save_state(data)

        data.save_record = save

        async def hearings():
            return data.extra_hearings

        data.hearings = hearings
        self.data, self.telegram = data, FakeTelegram()
        models = [ModelInfo("o3", "o3", ("low", "medium", "high"))]
        settings = Settings(database_url=f"sqlite+aiosqlite:///{STATE}/db.sqlite", public_url="https://bench.example.com")
        self.app = create_app(settings, mail_events=FakeMailEvents(), registry=test_registry(), llm_factory=lambda provider, key: FakeLlm(key, self.script, models, self.log), decision_factory=lambda key: FakeDecisions([]),
                              data_factory=lambda token: data, telegram=self.telegram, twilio=FakeTwilio(), tokens=FakeTokens(), voice=FakeVoice(), caller=FakeCaller())
        self.cm = self.app.router.lifespan_context(self.app)
        await self.cm.__aenter__()
        async with self.app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        return self

    async def close(self):
        await self.client.aclose()
        await self.cm.__aexit__(None, None, None)


WHO_ID, WHO_EMAIL = "u1", "arjun@sb.example.com"


async def prepared(bench):
    """The tools and the prompt exactly as the agent service builds them for Chittu on Telegram."""
    from app.application.services import Who
    from app.presentation.deps import build_services

    async with bench.app.state.session_factory() as session:
        agent, _ = build_services(bench.app, session)
        who = Who(WHO_ID, WHO_EMAIL)
        definition = await agent._def(who, "daily-planner")
        prep = await agent._prepare(who, "t", definition, "Telegram")
        return session, agent, who, definition, prep


async def cmd_reset(quiet: bool = False) -> None:
    keep = {n: (STATE / n).read_text() for n in ("system.txt", "tools.json") if (STATE / n).exists()}  # the prompt and tools the model reads survive a reset
    if STATE.exists():
        shutil.rmtree(STATE)
    STATE.mkdir(parents=True)
    for n, text in keep.items():
        (STATE / n).write_text(text)
    (STATE / "data.json").write_text(json.dumps(seed()))
    b = await Bench().boot()
    # the owner's own mail that needs them, kept as an action item
    from app.application.actions import ActionService
    from app.application.services import Who
    from app.infrastructure.db.actions import SqlActionRepository

    async with b.app.state.session_factory() as session:
        svc = ActionService(SqlActionRepository(session), lambda t: b.data)
        await svc.record_mail(Who(WHO_ID, WHO_EMAIL), agent_name="Mail watch · S&B", workflow_id="watch-sb-u1", item_id="acc1:1:11", account="acc1", uid=11, folder="INBOX", subject="Reply to RTI application No. 4471", sender="pio@labour.gov.in", summary="The PIO replied to RTI 4471; the first-appeal window is running.", priority="high", label="now", due=T, workspace="sb")
        await session.commit()
    _save_state(b.data)
    await b.close()
    if not quiet:
        print("seeded a day for", T)


async def cmd_prompt() -> None:
    from app.domain.prompt import build_package

    b = await Bench().boot()
    session, agent, who, definition, prep = await prepared(b)
    pkg = build_package(prep.config, [t.spec for t in prep.tools], prep.context, "{message}")
    (STATE / "system.txt").write_text(pkg.system)
    (STATE / "tools.json").write_text(json.dumps([{"name": t.name, "description": t.description, "parameters": t.parameters} for t in pkg.tools], indent=1, ensure_ascii=False))
    print(f"system prompt: {len(pkg.system.split())} words -> {STATE / 'system.txt'}\ntools: {len(pkg.tools)} -> {STATE / 'tools.json'} ({sum(len(t.description.split()) for t in pkg.tools)} words of descriptions)")
    await session.close()
    await b.close()


async def cmd_tool(name: str, args: str) -> None:
    b = await Bench().boot()
    session, agent, who, definition, prep = await prepared(b)
    tool = next((t for t in prep.tools if t.spec.name == name), None)
    if tool is None:
        print(json.dumps({"error": f"no tool called {name}", "tools": [t.spec.name for t in prep.tools]}))
        await session.close()
        await b.close()
        return
    out = await tool.run(json.loads(args or "{}"))
    await session.commit()
    _save_state(b.data)
    text = json.dumps(out.result, ensure_ascii=False, default=str)
    print(text if len(text) <= 12000 else text[:12000] + "…(shortened)")
    if out.found:
        print(f"[cards shown to the owner: {len(out.found)}]")
    await session.close()
    await b.close()


async def cmd_show() -> None:
    b = await Bench().boot()
    session, agent, who, definition, prep = await prepared(b)
    svc = agent.actions
    loaded = await svc.load(who, "t")
    for it in sorted(loaded.items, key=lambda i: (i.day or i.due or "9999", i.start if i.start is not None else 24 * 60)):
        print(f"{it.status:9} {it.ref:18} {it.when:18} {it.priority:7} {'FIXED ' if it.fixed else '      '}{it.title}")
    print("--- history (newest first)")
    for e in (await svc.history(who, None, 25)):
        print(f"{e['at'][11:19]} {e['event']:10} {e['actor']:20} {e['ref']:18} {e['title'][:50]}")
    await session.close()
    await b.close()


class Script(list):
    """What the model said, handed out one answer at a time. A tool argument written "$changeset" becomes the id of the change last previewed."""

    def pop(self, i=0):
        item = super().pop(i)
        calls = getattr(item, "tool_calls", None) or []
        if calls:
            import sqlite3

            con = sqlite3.connect(STATE / "db.sqlite", timeout=5)
            try:
                row = con.execute("select id from action_changesets where status='pending' order by created_at desc limit 1").fetchone()
            finally:
                con.close()
            for c in calls:
                c.args = {k: (row[0] if v == "$changeset" and row else v) for k, v in (c.args or {}).items()}
        return item


async def cmd_replay(path: str) -> None:
    """The model's tool calls and answer, put through the real Telegram webhook onto a freshly seeded day. A transcript is
    {"turns": [{"message", "rounds": [[{"tool", "args"}]], "reply"}]} (or one turn on its own)."""
    from app.domain.llm import ChatResult, ToolCall
    from tests.test_agent_api import choose, connect
    from tests.test_channels import send, telegram

    t = json.loads(Path(path).read_text())
    turns = t.get("turns") or [t]
    await cmd_reset(quiet=True)
    script = Script()
    b = await Bench(script).boot()

    class R:  # what the test helpers need
        client = b.client
        app = b.app
        data = b.data
        telegram = b.telegram
        script = b.script

    r = R()
    await connect(r)
    await choose(r, agent="/v1/agent/agents/daily-planner")
    c = await telegram(r, agent="daily-planner")
    await send(r, c, f"/start {c['pairingCode']}", chat=555, update=1)
    n = 0
    for k, turn in enumerate(turns):
        b.telegram.sent.clear()
        b.telegram.keyboards.clear()
        for rnd in turn.get("rounds", []):
            calls = []
            for call in rnd:
                n += 1
                calls.append(ToolCall(f"c{n}", call["tool"], call.get("args") or {}))
            script.append(ChatResult("", calls))
        script.append(ChatResult(turn["reply"]))
        print(f"\n▶ You: {turn['message']}")
        await send(r, c, turn["message"], chat=555, update=10 + k)
        for _, text in b.telegram.sent:
            print("┌" + "─" * 58)
            print("\n".join("│ " + line for line in text.splitlines()))
            print("└" + "─" * 58)
        shown = [kb for kb in b.telegram.keyboards if kb]
        if shown:
            print("buttons:", [x["text"] for row in shown[-1] for x in row])
        for j, word in enumerate(turn.get("then") or []):  # plain words typed afterwards: the code handles these, so no model answer is scripted
            if not word.startswith("[tap] "):
                b.telegram.sent.clear()
                b.telegram.keyboards.clear()
            if word.startswith("[tap] "):  # a button pressed, by its label, under the latest message that has one
                from tests.test_alerts import press

                label = word[6:]
                boards = [kb for kb in b.telegram.edit_buttons_shown if kb] + [kb for kb in b.telegram.keyboards if kb]
                board = [x for kb in ([b.telegram.edit_buttons_shown[-1]] if b.telegram.edit_buttons_shown and b.telegram.edit_buttons_shown[-1] else []) + ([b.telegram.keyboards[-1]] if b.telegram.keyboards and b.telegram.keyboards[-1] else []) for row in kb for x in row]
                pick = next((x for x in board if x["text"] == label), None) or next((x for kb in reversed(boards) for row in kb for x in row if x["text"] == label), None)
                print(f"\n▶ You tap: {label}")
                edits_before = len(b.telegram.edits)
                await press(r, c, pick["callback_data"], update=100 + k * 10 + j)
                for _, _, text in b.telegram.edits[edits_before:]:
                    print("┌" + "─" * 58)
                    print("\n".join("│ " + line for line in text.splitlines()))
                    print("└" + "─" * 58)
                kb = [x for x in b.telegram.edit_buttons_shown if x]
                if kb:
                    print("buttons:", [x["text"] for row in kb[-1] for x in row])
                continue
            print(f"\n▶ You: {word}   (handled by the app, no model)")
            await send(r, c, word, chat=555, update=100 + k * 10 + j)
            for _, text in b.telegram.sent:
                print("┌" + "─" * 58)
                print("\n".join("│ " + line for line in text.splitlines()))
                print("└" + "─" * 58)
            kb = [x for x in b.telegram.keyboards if x]
            if kb:
                print("buttons:", [x["text"] for row in kb[-1] for x in row])
    _save_state(b.data)
    await b.close()


def main() -> None:
    import logging

    logging.disable(logging.CRITICAL)  # the app's start-up notes about tables that do not exist yet are not for the bench
    cmd, *rest = sys.argv[1:] or ["help"]
    run = {"reset": lambda: cmd_reset(), "prompt": lambda: cmd_prompt(), "tool": lambda: cmd_tool(rest[0], rest[1] if len(rest) > 1 else "{}"), "show": lambda: cmd_show(), "replay": lambda: cmd_replay(rest[0])}.get(cmd)
    if run is None:
        print(__doc__)
        return
    asyncio.run(run())


if __name__ == "__main__":
    main()
