"""Action items: everything is one kind of thing, every change is in the history, a bulk move is previewed then applied, and it can all be undone."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from stiamo_common.errors import Conflict, NotFound, ValidationFailed

from app.application.actions import ActionService
from app.application.services import Who
from app.domain.actionitems import from_follow_up, from_hearing, from_meeting, from_task, pick, plan_move, resolve_day
from app.infrastructure.db.actions import SqlActionRepository

TODAY = datetime.now(ZoneInfo("Asia/Kolkata")).date()
T = TODAY.isoformat()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()
WHO = Who("u1", "arjun@sb.example.com")


def fill(rig):
    rig.data.extra_hearings = [{"id": "h9", "caseId": "c1", "date": T, "time": 660, "duration": 60, "forum": "JCL", "purpose": "Arguments", "outcome": {"status": "open"}}]
    rig.data.tables = {
        "meetings": [{"id": "m1", "title": "Union leaders", "counterpart": "Mr Rao", "slot": {"date": T, "start": 900, "duration": 60}, "outcome": {"status": "open"}}],
        "tasks": [
            {"id": "t1", "title": "Draft the rejoinder", "priority": "high", "slot": {"date": T, "start": 600, "duration": 60}, "dueDate": T, "estimate": 60, "outcome": {"status": "open"}},
            {"id": "t2", "title": "File the receipts", "priority": "low", "dueDate": T, "estimate": 20, "outcome": {"status": "open"}},
            {"id": "t3", "title": "Urgent affidavit", "priority": "urgent", "slot": {"date": T, "start": 720, "duration": 60}, "estimate": 60, "outcome": {"status": "open"}},
            {"id": "t4", "title": "Already done", "dueDate": T, "outcome": {"status": "done"}},
        ],
        "follow-ups": [{"id": "f1", "title": "Call Ramesh", "channel": "call", "due": {"date": T, "time": 660}, "duration": 15, "outcome": {"status": "open"}}],
        "plans": [],
    }
    saved: list[tuple[str, dict]] = []
    original = rig.data.save_record

    async def save(table, record):
        saved.append((table, record))
        rows = rig.data.tables.setdefault(table, [])
        for i, r in enumerate(rows):
            if r["id"] == record["id"]:
                rows[i] = record
                break
        else:
            rows.append(record)

    rig.data.save_record = save  # the fake's own does nothing: keep what was written so it can be read back
    return saved, original


async def service(rig):
    session = rig.app.state.session_factory()
    s = await session.__aenter__()
    return ActionService(SqlActionRepository(s), rig.app.state.data_factory), s, session


async def finish(s, session):
    await s.commit()
    await session.__aexit__(None, None, None)


# --- the pure parts ------------------------------------------------------------------------------------------------------------


def test_days_in_words_are_worked_out_in_code_and_never_guessed():
    today = "2026-11-20"  # a Friday
    cases = {"today": "2026-11-20", "tomorrow": "2026-11-21", "day after tomorrow": "2026-11-22", "monday": "2026-11-23", "next friday": "2026-11-27", "in 3 days": "2026-11-23", "next week": "2026-11-23",
             "25th November, 2026": "2026-11-25", "25 nov": "2026-11-25", "November 25": "2026-11-25", "25/11/2026": "2026-11-25", "2026-11-25": "2026-11-25", "1 jan": "2027-01-01"}
    for words, day in cases.items():
        assert resolve_day(words, today) == day, words
    assert resolve_day("31 feb 2026", today) is None and resolve_day("whenever", today) is None and resolve_day("", today) is None


def test_a_move_keeps_fixed_things_where_they_are_and_reports_what_does_not_fit():
    task = from_task({"id": "t", "title": "Draft", "slot": {"date": "2026-11-20", "start": 600, "duration": 60}, "dueDate": "2026-11-20", "outcome": {"status": "open"}})
    due_only = from_task({"id": "d", "title": "File", "dueDate": "2026-11-20", "outcome": {"status": "open"}})
    meeting = from_meeting({"id": "m", "title": "Union", "counterpart": "Mr Rao", "slot": {"date": "2026-11-20", "start": 900, "duration": 60}})
    hearing = from_hearing({"id": "h", "date": "2026-11-20", "time": 660, "duration": 60, "purpose": "Arguments"})
    plan = plan_move([task, due_only, meeting, hearing], "2026-11-21", [(540, 700, "clash")])
    moved = {m.ref: m for m in plan.moves}
    assert moved["task:t"].to_start != 600 and "taken" in moved["task:t"].note  # its usual time was busy: it takes the first free window
    assert moved["task:d"].to_start is None  # only its day changes
    stays = {r: why for r, _, why in plan.stays}
    assert "fixed" in stays["meeting:m"] and "court" in stays["hearing:h"]
    full = plan_move([task], "2026-11-21", [(360, 1320, "all day")])
    assert full.moves == [] and full.overflow == [("task:t", "Draft")]


def test_only_what_is_open_and_asked_for_is_picked():
    items = [from_task({"id": "a", "title": "A", "priority": "urgent", "dueDate": "2026-11-20", "outcome": {"status": "open"}}), from_task({"id": "b", "title": "B", "priority": "low", "dueDate": "2026-11-20", "outcome": {"status": "open"}}),
             from_task({"id": "c", "title": "C", "dueDate": "2026-11-20", "outcome": {"status": "done"}}), from_follow_up({"id": "f", "title": "Call", "due": {"date": "2026-11-21"}, "outcome": {"status": "open"}})]
    assert [i.ref for i in pick(items, day="2026-11-20")] == ["task:a", "task:b"]
    assert [i.ref for i in pick(items, day="2026-11-20", priority_below="urgent")] == ["task:b"]
    assert [i.ref for i in pick(items, day="2026-11-20", until="2026-11-21", kinds=["call"])] == ["followUp:f"]


# --- the service ----------------------------------------------------------------------------------------------------------------


async def test_clearing_a_day_is_previewed_then_applied_and_everything_is_in_the_history_and_can_be_undone(rig):
    saved, _ = fill(rig)
    svc, s, session = await service(rig)
    try:
        assert {i.ref for i in await svc.find(WHO, "", day=T)} >= {"task:t1", "task:t2", "task:t3", "followUp:f1", "meeting:m1", "hearing:h9"}
        preview = await svc.preview_move(WHO, "", to="tomorrow", day="today")
        assert preview["willMove"] == 4 and saved == []  # nothing is written by a preview
        assert {x["ref"] for x in preview["staying"]} == {"meeting:m1", "hearing:h9"}  # the meeting has other people in it; a hearing is set by a court
        done = await svc.apply_changeset(WHO, "", preview["changesetId"])
        assert done["applied"] == 4
        tasks = {r["id"]: r for r in rig.data.tables["tasks"]}
        assert tasks["t1"]["slot"]["date"] == TOMORROW and tasks["t1"]["dueDate"] == TOMORROW and tasks["t1"]["slot"]["start"] == 600  # its time was free, so it kept it
        assert tasks["t2"]["dueDate"] == TOMORROW and rig.data.tables["follow-ups"][0]["due"] == {"date": TOMORROW, "time": 660}
        assert rig.data.tables["meetings"][0]["slot"]["date"] == T  # untouched
        history = await svc.history(WHO, "task:t1")
        assert history[0]["event"] == "moved" and history[0]["actor"] == "owner" and history[0]["before"]["dueDate"] == T and history[0]["changesetId"] == preview["changesetId"]
        assert (await svc.apply_changeset(WHO, "", preview["changesetId"]))["again"] is True  # a second tap changes nothing
        undone = await svc.undo_changeset(WHO, "")
        assert undone["undone"] == 4
        assert {r["id"]: r for r in rig.data.tables["tasks"]}["t1"]["slot"]["date"] == T and rig.data.tables["follow-ups"][0]["due"]["date"] == T
        assert (await svc.history(WHO, "task:t1"))[0]["event"] == "undone"
        with pytest.raises(NotFound):
            await svc.undo_changeset(WHO, "")  # nothing left to undo
    finally:
        await finish(s, session)


async def test_a_move_to_a_named_date_and_to_one_that_cannot_be_read(rig):
    fill(rig)
    svc, s, session = await service(rig)
    try:
        far = await svc.preview_move(WHO, "", to="25th November 2026", day="today", priority_below="urgent") if TODAY < datetime(2026, 11, 25).date() else None
        if far is not None:
            assert far["target"] == "2026-11-25" and {m["ref"] for m in far["moves"]} <= {"task:t1", "task:t2", "followUp:f1"} and "task:t3" not in {m["ref"] for m in far["moves"]}  # the urgent one is left
        with pytest.raises(ValidationFailed):
            await svc.preview_move(WHO, "", to="31 February", day="today")
        with pytest.raises(ValidationFailed):
            await svc.preview_move(WHO, "", to="2020-01-01", day="today")  # the past
    finally:
        await finish(s, session)


async def test_one_item_is_changed_at_once_a_fixed_one_needs_the_owners_word_and_a_hearing_cannot_be_changed_here(rig):
    fill(rig)
    svc, s, session = await service(rig)
    try:
        out = await svc.command(WHO, "", "followUp:f1", "complete")
        assert out["ok"] and rig.data.tables["follow-ups"][0]["outcome"]["status"] == "done" and "undo" in out["undo"].lower()
        assert (await svc.history(WHO, "followUp:f1"))[0]["event"] == "completed"
        moved = await svc.command(WHO, "", "task:t1", "move", to="tomorrow", start=14 * 60)
        assert "Moved to" in moved["did"] and rig.data.tables["tasks"][0]["slot"]["start"] == 840
        with pytest.raises(ValidationFailed, match="fixed"):
            await svc.command(WHO, "", "meeting:m1", "move", to="tomorrow")
        await svc.command(WHO, "", "meeting:m1", "move", to="tomorrow", include_fixed=True)
        assert rig.data.tables["meetings"][0]["slot"]["date"] == TOMORROW
        with pytest.raises(ValidationFailed, match="hearing"):
            await svc.command(WHO, "", "hearing:h9", "complete")
        with pytest.raises(NotFound):
            await svc.command(WHO, "", "task:nope", "complete")
        with pytest.raises(ValidationFailed):
            await svc.command(WHO, "", "task:t2", "move", to="whenever")
    finally:
        await finish(s, session)


async def test_an_email_that_needs_the_owner_is_kept_as_an_action_item_once_and_has_a_history(rig):
    fill(rig)
    svc, s, session = await service(rig)
    try:
        kw = dict(agent_name="Mail watch · S&B", workflow_id="watch-sb-u1", item_id="acc1:1:77", account="acc1", uid=77, folder="INBOX", subject="Listing of WP 12/2026", sender="registry@court.gov.in", summary="Listed for 3 December.", priority="high", label="now", due=TOMORROW, workspace="sb")
        a = await svc.record_mail(WHO, **kw)
        b = await svc.record_mail(WHO, **kw)  # judged again: still one item
        assert a["id"] == b["id"]
        found = [i for i in await svc.find(WHO, "", day=TOMORROW) if i.kind == "mail"]
        assert len(found) == 1 and found[0].priority == "high" and found[0].workspace == "sb" and "registry@court.gov.in" in found[0].title
        h = await svc.history(WHO, found[0].ref)
        assert h[0]["event"] == "created" and h[0]["actor"] == "Mail watch · S&B"
        out = await svc.command(WHO, "", found[0].ref, "complete")
        assert out["ok"] and not [i for i in await svc.find(WHO, "", day=TOMORROW) if i.kind == "mail"]
    finally:
        await finish(s, session)


async def test_a_dropped_or_stale_change_cannot_be_applied(rig):
    fill(rig)
    svc, s, session = await service(rig)
    try:
        p = await svc.preview_move(WHO, "", to="tomorrow", day="today")
        await svc.drop_changeset(WHO, p["changesetId"])
        with pytest.raises(Conflict):
            await svc.apply_changeset(WHO, "", p["changesetId"])
        with pytest.raises(NotFound):
            await svc.apply_changeset(WHO, "", "nope")
    finally:
        await finish(s, session)


# --- Chittu: the tools, the prompt, the day -------------------------------------------------------------------------------------

PLANNER = "/v1/agent/agents/daily-planner"


async def test_chittu_previews_a_bulk_move_and_applies_it_only_after_a_yes_and_undoes_it(rig):
    import json

    from app.domain.llm import ChatResult, ToolCall

    from .conftest import headers
    from .test_agent_api import choose, connect

    saved, _ = fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    rig.script += [ChatResult("", [ToolCall("c1", "preview_move", {"from_day": "today", "to": "tomorrow"})]), ChatResult("That's 4 things to tomorrow. The hearing and your meeting with Mr Rao stay. Go ahead?")]
    out = (await rig.client.post(f"{PLANNER}/chat", json={"message": "Clear out today and move everything to tomorrow."}, headers=headers())).json()
    assert "Go ahead" in out["answer"] and saved == []  # a preview changes nothing
    runs = (await rig.client.get(f"{PLANNER}/runs", headers=headers())).json()["items"]
    detail = (await rig.client.get(f"/v1/agent/runs/{runs[0]['id']}", headers=headers())).json()
    step = next(x for x in detail["steps"] if x["tool"] == "preview_move")
    cid = json.loads(step["result"])["changesetId"]
    assert cid and json.loads(step["result"])["willMove"] == 4
    rig.script += [ChatResult("", [ToolCall("c2", "apply_change", {"changeset_id": cid})]), ChatResult("Done, 4 moved to tomorrow. Say undo to put them back.")]
    out = (await rig.client.post(f"{PLANNER}/chat", json={"message": "yes", "history": [{"role": "user", "content": "Clear out today"}, {"role": "assistant", "content": "Go ahead?"}]}, headers=headers())).json()
    assert len(saved) == 4 and {r["id"]: r for r in rig.data.tables["tasks"]}["t2"]["dueDate"] == TOMORROW
    rig.script += [ChatResult("", [ToolCall("c3", "undo_last", {})]), ChatResult("Put back.")]
    await rig.client.post(f"{PLANNER}/chat", json={"message": "undo that"}, headers=headers())
    assert {r["id"]: r for r in rig.data.tables["tasks"]}["t2"]["dueDate"] == T


async def test_every_tool_of_chittu_is_described_in_full_and_his_prompt_is_a_full_brief(rig):
    from app.application.toolpacks import PackContext
    from app.application.toolpacks.actions import ActionsPack
    from app.agents.team import PLANNER_SYSTEM

    svc, s, session = await service(rig)
    try:
        tools = await ActionsPack().tools(PackContext(rig.data, T, actions=svc, who=WHO, token=""))
    finally:
        await finish(s, session)
    assert [t.spec.name for t in tools] == ["find_items", "which_day", "preview_move", "apply_change", "drop_change", "change_item", "undo_last", "item_history"]
    for t in tools:
        d = t.spec.description
        assert all(part in d for part in ("USE IT WHEN:", "RETURNS:", "EFFECT:")) and len(d.split()) >= 60, t.spec.name  # what it is for, when, what comes back, what it changes
    by = {t.spec.name: t.spec.description for t in tools}
    assert "ARGUMENTS:" in by["preview_move"] and "DO NOT USE IT WHEN:" in by["apply_change"] and "IF IT FAILS:" in by["change_item"] and "Read only" in by["find_items"]
    assert len(PLANNER_SYSTEM.split()) >= 2000 and all(h in PLANNER_SYSTEM for h in ("## Persona", "## Tasks", "## Context", "## Examples", "## Output", "## Guardrails"))
    assert "Chidambaram" in PLANNER_SYSTEM and "Chittu" in PLANNER_SYSTEM and "{{day_start}}" in PLANNER_SYSTEM


# --- Telegram: good morning, one thing at a time, good night -----------------------------------------------------------------------


async def paired(rig):
    from .test_alerts import press  # noqa: F401
    from .test_channels import send, telegram

    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.telegram.sent.clear()
    rig.telegram.keyboards.clear()
    return c


async def test_chittu_says_good_morning_brings_one_thing_at_a_time_and_says_good_night(rig):
    from app.presentation.deps import chittu_run

    from .conftest import headers  # noqa: F401
    from .test_alerts import press
    from .test_agent_api import connect

    fill(rig)
    await connect(rig)
    c = await paired(rig)
    await chittu_run(rig.app, WHO, "morning")()
    hello = rig.telegram.sent[-1][1]
    assert hello.startswith("☀️ Good morning, Arjun.") and "6 things" in hello and "Shall we start?" in hello  # a hello and a count: nothing else yet
    assert len(rig.telegram.sent) == 1
    keys = [b for row in rig.telegram.keyboards[-1] for b in row]
    assert [k["text"] for k in keys] == ["▶️ Start", "⏰ Later"] and all(len(k["callback_data"].encode()) <= 64 for k in keys)
    await chittu_run(rig.app, WHO, "morning")()  # a second trigger the same morning says nothing more
    assert len(rig.telegram.sent) == 1
    await press(rig, c, keys[0]["callback_data"], update=2)
    first = rig.telegram.edits[-1][2]
    assert first.startswith("1 of 6 · ⚖️ Arguments") or first.startswith("1 of 6")  # the hearing is fixed and at a time, so it comes first
    assert len(rig.telegram.edits) == 1  # one item, not the whole list
    # settle them one by one: each press brings the next
    seen = [first]
    for n in range(2, 8):
        board = [b for row in (rig.telegram.edit_buttons_shown[-1] or []) for b in row] if rig.telegram.edit_buttons_shown else []
        data = next(b["callback_data"] for b in (board or []) if b["callback_data"].split(":")[1] in ("d", "k"))
        await press(rig, c, data, update=10 + n)
        seen.append(rig.telegram.edits[-1][2])
    assert any("That's everything for now" in t for t in seen) or "That's everything for now" in rig.telegram.edits[-1][2]
    assert (await rig.client.get(f"{PLANNER}/runs", headers=headers())).status_code == 200
    await chittu_run(rig.app, WHO, "night")()
    night = rig.telegram.sent[-1][1]
    assert night.startswith("🌙 Good night, Arjun.") and "I'll be here at six" in night


async def test_chittu_with_nothing_to_bring_says_so_and_does_not_ask_to_start(rig):
    from app.presentation.deps import chittu_run

    from .test_agent_api import connect

    rig.data.tables = {"tasks": [], "follow-ups": [], "meetings": [], "plans": []}
    await connect(rig)
    await paired(rig)
    await chittu_run(rig.app, WHO, "morning")()
    assert "Nothing needs you today" in rig.telegram.sent[-1][1] and rig.telegram.keyboards[-1] is None


async def test_chittu_asks_once_more_if_the_good_morning_is_not_answered_and_never_a_third_time_and_his_times_can_be_set(rig):
    from app.presentation.deps import chittu_run

    from .conftest import headers
    from .test_agent_api import connect

    fill(rig)
    await connect(rig)
    await paired(rig)
    await chittu_run(rig.app, WHO, "morning")()
    assert len(rig.telegram.sent) == 1
    await chittu_run(rig.app, WHO, "remind")()
    assert "Still here whenever you're ready: 6 things." in rig.telegram.sent[-1][1] and len(rig.telegram.sent) == 2
    await chittu_run(rig.app, WHO, "remind")()
    assert len(rig.telegram.sent) == 2  # once only
    # their times, and whether he greets at all, are the owner's to set
    base = {"enabled": False, "time": "22:00", "timezone": "Asia/Kolkata", "workspaces": ["sb"]}
    r = await rig.client.put("/v1/agent/day-plans/settings", json={**base, "chittu": {"morning": "05:30", "remindAfter": 0}}, headers=headers())
    assert r.status_code == 200 and r.json()["settings"]["chittu"] == {"enabled": True, "morning": "05:30", "night": "22:00", "remindAfter": 0}
    keys = rig.app.state.daily.keys(f"{WHO.user_id}#chittu-")
    assert sorted(k.rsplit("-", 1)[1] for k in keys) == ["morning", "night"]  # no reminder when it is set to 0
    r = await rig.client.put("/v1/agent/day-plans/settings", json={**base, "chittu": {"enabled": False}}, headers=headers())
    assert r.json()["settings"]["chittu"]["enabled"] is False and r.json()["settings"]["chittu"]["morning"] == "05:30"  # what was set before is kept
    assert rig.app.state.daily.keys(f"{WHO.user_id}#chittu-") == []
    assert (await rig.client.put("/v1/agent/day-plans/settings", json={**base, "chittu": {"morning": "25:00"}}, headers=headers())).status_code == 422
