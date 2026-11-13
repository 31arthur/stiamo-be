"""No bot can see, say or be sent anything about a workspace that is not its own.

Every workspace's data carries a unique canary (SBX-, FSX-, PEX-). Each bot is asked to plan the day, and then **everything sent to the model**
(the prompt, every tool result) and everything said on the phone is searched for the other workspaces' canaries. The general bot, which is
meant to see everything, is the control: it must find all three, so a clean result for the others means something.
"""

from app.application.scope import PlannerData
from app.application.services import Who
from app.domain.llm import ChatResult, ToolCall
from app.presentation import deps

from .conftest import headers
from .test_channels import send, telegram
from .test_dayplans import PLANS
from .test_orchestrator import team_ready
from .test_planning import TOMORROW

ME = Who("u1", "arjun@example.com")
CANARY = {"sb": "SBX-", "fsa": "FSX-", "personal": "PEX-"}
CH = "/v1/agent/channels"


def seed(rig):
    """Three workspaces' tasks, calls, meetings, calendar events, and S&B's hearing and case: every title carries its workspace's canary."""
    rig.data.extra_hearings = [{"id": "h9", "caseId": "c1", "date": TOMORROW, "time": 600, "duration": 60, "forum": "SBX-forum", "purpose": "SBX-hearing", "outcome": {"status": "open"}}]
    rig.data._cases[0]["title"] = "SBX-case"
    open_ = {"status": "open"}
    rig.data.tables = {
        "tasks": [
            {"id": "t-sb", "title": "SBX-task", "workspace": "sb", "dueDate": TOMORROW, "estimate": 30, "outcome": open_},
            {"id": "t-fsa", "title": "FSX-task", "workspace": "fsa", "dueDate": TOMORROW, "estimate": 30, "outcome": open_},
            {"id": "t-pe", "title": "PEX-task", "workspace": "personal", "dueDate": TOMORROW, "estimate": 30, "outcome": open_},
        ],
        "follow-ups": [
            {"id": "f-sb", "title": "SBX-call", "workspace": "sb", "channel": "call", "contact": "SBX-person", "due": {"date": TOMORROW, "time": 700}, "duration": 15, "outcome": open_},
            {"id": "f-fsa", "title": "FSX-call", "workspace": "fsa", "channel": "call", "contact": "FSX-person", "due": {"date": TOMORROW, "time": 730}, "duration": 15, "outcome": open_},
            {"id": "f-pe", "title": "PEX-call", "workspace": "personal", "channel": "call", "contact": "PEX-person", "due": {"date": TOMORROW, "time": 760}, "duration": 15, "outcome": open_},
        ],
        "meetings": [
            {"id": "m-sb", "title": "SBX-meeting", "workspace": "sb", "slot": {"date": TOMORROW, "start": 780, "duration": 30}, "outcome": open_},
            {"id": "m-fsa", "title": "FSX-meeting", "workspace": "fsa", "slot": {"date": TOMORROW, "start": 820, "duration": 30}, "outcome": open_},
            {"id": "m-pe", "title": "PEX-meeting", "workspace": "personal", "slot": {"date": TOMORROW, "start": 860, "duration": 30}, "outcome": open_},
        ],
        "plans": [],
    }
    rig.data.calendar = [
        {"id": f"g-{ws}", "date": TOMORROW, "startMinute": 900 + i * 40, "endMinute": 930 + i * 40, "allDay": False, "title": f"{CANARY[ws]}event", "location": f"{CANARY[ws]}place", "workspace": ws}
        for i, ws in enumerate(("sb", "fsa", "personal"))
    ]


def foreign(ws: str) -> list[str]:
    return [c for w, c in CANARY.items() if w != ws]


def assert_clean(text: str, ws: str, where: str) -> None:
    leaked = [c for c in foreign(ws) if c in text]
    assert not leaked, f"{where}: {ws} saw {leaked}"


async def ask_bot(rig, c, chat, update, text="Plan my day", ids=()):
    """Sends a message to a bot with the model scripted to read the day, and returns everything the model was sent for it."""
    start = len(rig.llm_log)
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {"date": TOMORROW}), ToolCall("b", "upcoming", {}), ToolCall("c", "free_time", {"date": TOMORROW})]), ChatResult("Here is the day.")]
    await send(rig, c, text, chat=chat, update=update)
    return "\n".join(e["everything"] for e in rig.llm_log[start:])


async def connect_bot(rig, token, chat, **body):
    r = await rig.client.post(f"{CH}/telegram", json={"botToken": token, **body}, headers=headers())
    assert r.status_code == 201, r.text
    c = r.json()
    await send(rig, c, f"/start {c['pairingCode']}", chat=chat, update=1)
    return c


async def test_each_workspace_bot_sends_its_model_only_its_own_workspaces_data(rig):
    await team_ready(rig)
    seed(rig)
    general = await connect_bot(rig, "123:abc", 555)
    seen_general = await ask_bot(rig, general, 555, 2, "Plan my day across all workspaces")
    assert all(c in seen_general for c in CANARY.values())  # the control: asked to look at everything, the master sees everything, so the search can find a leak
    for n, ws in enumerate(("sb", "fsa", "personal")):
        c = await connect_bot(rig, f"{n}00:ws{n}", 600 + n, workspace=ws)
        seen = await ask_bot(rig, c, 600 + n, 2)
        assert CANARY[ws] in seen, f"{ws}'s bot should see its own work"
        assert_clean(seen, ws, f"the {ws} assistant")
    # S&B's own case, hearing and documents belong to S&B alone
    fsa_seen = await ask_bot(rig, await connect_bot(rig, "999:fsa2", 700, workspace="fsa", role="mail"), 700, 2, "What cases are there?")
    assert "SBX-" not in fsa_seen


async def test_a_bot_pinned_to_one_agent_is_still_held_to_its_workspace(rig):
    """The leak: a bot made from the Daily planner's own Channels page was not limited to a workspace, so it answered about all of them."""
    await team_ready(rig)
    seed(rig)
    pinned = await connect_bot(rig, "123:abc", 555, agent="daily-planner")  # no workspace: the general planner bot
    everything = await ask_bot(rig, pinned, 555, 2)
    assert all(c in everything for c in CANARY.values())
    only_sb = await connect_bot(rig, "456:def", 556, agent="daily-planner", workspace="sb", role="planner")
    seen = await ask_bot(rig, only_sb, 556, 2)
    assert CANARY["sb"] in seen
    assert_clean(seen, "sb", "the S&B planner bot")


async def test_a_bot_that_is_limited_afterwards_forgets_what_it_saw_before(rig):
    await team_ready(rig)
    seed(rig)
    pinned = await connect_bot(rig, "123:abc", 555, agent="daily-planner")
    assert "FSX-" in await ask_bot(rig, pinned, 555, 2)  # it could see FSA while it was the general bot
    r = await rig.client.put(f"{CH}/{pinned['id']}/scope", json={"workspace": "sb", "role": "planner"}, headers=headers())
    assert r.status_code == 200 and r.json()["scope"] == "sb" and r.json()["role"] == "planner"
    after = await ask_bot(rig, pinned, 555, 3, "And now?")
    assert "SBX-" in after
    assert_clean(after, "sb", "the same bot after it was limited to S&B")  # not even from its memory of the chat
    back = await rig.client.put(f"{CH}/{pinned['id']}/scope", json={"workspace": None}, headers=headers())
    assert back.json()["scope"] == "all" and back.json()["role"] == "assistant"


async def test_an_assistant_thread_begun_under_another_scope_is_not_carried_on(rig):
    await team_ready(rig)
    seed(rig)
    c = await connect_bot(rig, "123:abc", 555)  # the assistant, as the general bot
    assert "FSX-" in await ask_bot(rig, c, 555, 2, "Plan my day across all workspaces")
    await rig.client.put(f"{CH}/{c['id']}/scope", json={"workspace": "sb"}, headers=headers())
    after = await ask_bot(rig, c, 555, 3, "Plan my day again")
    assert_clean(after, "sb", "the assistant after it was limited to S&B")  # a new thread: nothing the old one held (it had said FSX-…) is in the preface


async def test_one_bot_cannot_be_limited_in_ways_that_make_no_sense(rig):
    await team_ready(rig)
    c = await connect_bot(rig, "123:abc", 555)
    for bad in ({"workspace": "nowhere"}, {"workspace": None, "role": "mail"}, {"workspace": "sb", "role": "boss"}):
        assert (await rig.client.put(f"{CH}/{c['id']}/scope", json=bad, headers=headers())).status_code == 422
    other = await connect_bot(rig, "456:def", 556, workspace="sb", role="mail")
    clash = await rig.client.put(f"{CH}/{c['id']}/scope", json={"workspace": "sb", "role": "mail"}, headers=headers())
    assert clash.status_code == 422 and "already has a bot for mail alerts" in clash.json()["error"]["message"]
    ok = await rig.client.put(f"{CH}/{c['id']}/scope", json={"workspace": "sb", "role": "planner"}, headers=headers())  # another purpose in S&B is fine
    assert ok.status_code == 200 and other["role"] == "mail"
    assert (await rig.client.put(f"{CH}/nope/scope", json={"workspace": "sb"}, headers=headers())).status_code == 404
    assert (await rig.client.put(f"{CH}/{c['id']}/scope", json={"workspace": "sb"}, headers=headers("x@example.com", "u2"))).status_code == 404


async def evening(rig):
    async with rig.app.state.session_factory() as session:
        made = await deps.build_day_plans(rig.app, session).evening(ME, "minted-for-u1")
        await session.commit()
    return made


def plan(rig, task, start):
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {}), ToolCall("b", "propose_schedule", {"blocks": [{"kind": "task", "id": task, "start": start, "duration": 30}]})]), ChatResult("The day.")]


async def test_each_workspaces_evening_plan_reaches_only_its_own_bot(rig):
    await team_ready(rig)
    seed(rig)
    general = await connect_bot(rig, "123:abc", 555)
    bots = {ws: await connect_bot(rig, f"{n}00:ws{n}", 600 + n, workspace=ws) for n, ws in enumerate(("sb", "fsa", "personal"))}
    await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "workspaces": ["sb", "fsa", "personal"]}, headers=headers())
    for task, start in (("t-sb", 1030), ("t-fsa", 1100), ("t-pe", 1170)):
        plan(rig, task, start)
    rig.telegram.sent.clear()
    llm_start = len(rig.llm_log)
    made = await evening(rig)
    assert sorted(p["workspace"] for p in made) == ["fsa", "personal", "sb"]
    said = {}
    for chat, text in rig.telegram.sent:
        said.setdefault(chat, []).append(text)
    for n, ws in enumerate(("sb", "fsa", "personal")):
        mine = "\n".join(said.get(600 + n, []))
        assert CANARY[ws] in mine and "plan · " in mine, f"{ws}'s bot should have its plan"
        assert_clean(mine, ws, f"the {ws} bot's plan message")
    assert 555 not in said  # nothing went to the general bot: every workspace has its own
    # and while planning, each workspace's model saw only its own work, with the rest of the day as bare busy time
    for e, ws in zip(rig.llm_log[llm_start:], ("sb", "sb", "fsa", "fsa", "personal", "personal"), strict=False):
        assert_clean(e["everything"], ws, f"the {ws} planner's prompt")
    assert "Busy (another workspace)" in "\n".join(e["everything"] for e in rig.llm_log[llm_start:])
    assert bots and general


async def test_a_plan_goes_to_the_workspaces_plan_bot_not_its_mail_bot_and_never_to_another_workspace(rig):
    await team_ready(rig)
    seed(rig)
    mail = await connect_bot(rig, "111:mail", 601, workspace="sb", role="mail")
    planner = await connect_bot(rig, "222:plan", 602, workspace="sb", role="planner")
    other = await connect_bot(rig, "333:fsa", 603, workspace="fsa")
    await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "workspaces": ["sb"]}, headers=headers())
    plan(rig, "t-sb", 1030)
    rig.telegram.sent.clear()
    await evening(rig)
    assert [chat for chat, text in rig.telegram.sent if "plan · " in text] == [602]  # only the S&B plan bot
    assert mail and planner and other
    # with no plan bot, the S&B everything-bot carries it; with neither, the general bot does
    await rig.client.delete(f"{CH}/{planner['id']}", headers=headers())
    await rig.client.delete(f"{CH}/{mail['id']}", headers=headers())
    everything = await connect_bot(rig, "444:all", 604, workspace="sb")
    plan(rig, "t-sb", 1030)
    rig.telegram.sent.clear()
    await evening(rig)
    assert [chat for chat, text in rig.telegram.sent if "plan · " in text] == [604] and everything


async def test_other_workspaces_commitments_are_only_ever_bare_busy_time(rig):
    seed(rig)
    sb = PlannerData(rig.data, "sb")
    events = await sb.calendar_events(TOMORROW, TOMORROW)
    text = str(events)
    assert_clean(text, "sb", "S&B's calendar for planning")
    assert "SBX-event" in text  # its own, in full
    busy = [e for e in events if e["title"] == "Busy (another workspace)"]
    assert busy and all(set(e) == {"id", "date", "startMinute", "endMinute", "allDay", "title", "calendar"} for e in busy)  # when, and nothing else
    assert (820, 850) in {(e["startMinute"], e["endMinute"]) for e in busy}  # FSA's meeting still blocks the time
    fsa = str(await PlannerData(rig.data, "fsa").calendar_events(TOMORROW, TOMORROW))
    assert "SBX-" not in fsa and "PEX-" not in fsa and "SBX-hearing" not in fsa
    assert await PlannerData(rig.data, "fsa").hearings() == [] and await PlannerData(rig.data, "fsa").cases() == []


async def test_nothing_without_a_workspace_label_is_visible_to_a_workspace(rig):
    from app.application.scope import ScopedData

    rig.data.tables = {"tasks": [{"id": "x", "title": "No label", "outcome": {"status": "open"}}], "follow-ups": [], "meetings": [], "plans": [{"id": "p"}]}
    rig.data.calendar = [{"id": "e", "date": TOMORROW, "title": "No label either", "startMinute": 600, "endMinute": 630}]
    for ws in ("sb", "fsa", "personal"):
        view = ScopedData(rig.data, ws)
        assert await view.records("tasks") == [] and await view.calendar_events(TOMORROW, TOMORROW) == []
    assert len(await ScopedData(rig.data, "sb").records("plans")) == 1 and await ScopedData(rig.data, "fsa").records("plans") == []  # action plans are the practice's


async def test_a_workspace_cannot_drive_save_or_read_what_belongs_elsewhere(rig):
    import pytest
    from app.application.scope import ScopedData
    from app.domain.llm import AiError

    fsa = ScopedData(rig.data, "fsa")
    assert await fsa.drive_available() is False and await fsa.drive_search("x") == [] and await fsa.drive_suggest(["x"]) == []
    assert await ScopedData(rig.data, "sb").drive_available() is True
    with pytest.raises(AiError, match="another workspace"):
        await fsa.save_record("tasks", {"id": "t", "workspace": "sb", "title": "x"})
    with pytest.raises(AiError, match="another workspace"):
        await fsa.save_record("cases", {"id": "c"})
    await fsa.save_record("tasks", {"id": "t", "workspace": "fsa", "title": "x"})


async def test_the_bots_scope_is_in_the_channel_list_and_health(rig):
    await team_ready(rig)
    c = await connect_bot(rig, "456:def", 556, workspace="sb", role="planner")
    g = await connect_bot(rig, "123:abc", 555)
    listed = {x["id"]: x for x in (await rig.client.get(CH, headers=headers())).json()["items"]}
    assert (listed[c["id"]]["scope"], listed[c["id"]]["role"]) == ("sb", "planner") and (listed[g["id"]]["scope"], listed[g["id"]]["role"]) == ("all", "assistant")
    items = {i["id"]: i for i in (await rig.client.get("/v1/agent/health", headers=headers())).json()["items"]}
    assert "Sees only S&B" in items[f"tg-{c['id']}"]["detail"] and "daily plan" in items[f"tg-{c['id']}"]["label"]
    assert "master assistant" in items[f"tg-{g['id']}"]["detail"]


async def test_a_mail_or_plan_bot_needs_a_workspace(rig):
    r = await rig.client.post(f"{CH}/telegram", json={"botToken": "123:abc", "role": "planner"}, headers=headers())
    assert r.status_code == 422 and "belongs to one workspace" in r.json()["error"]["message"]
    assert telegram and r


async def say(rig, bot, text, chat, update):
    """A message to one particular bot (each has its own webhook secret)."""
    secret = next(sec for url, sec in reversed(rig.telegram.webhooks) if bot["id"] in url)
    return await send(rig, bot, text, chat=chat, update=update, secret=secret)


async def planner_bot(rig, token, chat, ws):
    return await connect_bot(rig, token, chat, agent="daily-planner", workspace=ws, role="planner")


async def propose(rig, bot, chat, update, task, start):
    """Asks a planner bot to plan, with the model proposing one task at a time; returns what the model was told and what the bot said."""
    start_at = len(rig.llm_log)
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {"date": TOMORROW}), ToolCall("b", "propose_schedule", {"date": TOMORROW, "blocks": [{"kind": "task", "id": task, "start": start, "duration": 30}]})]), ChatResult("Planned.")]
    sent = len(rig.telegram.sent)
    await say(rig, bot, "Plan tomorrow", chat, update)
    return "\n".join(e["everything"] for e in rig.llm_log[start_at:]), "\n".join(t for _, t in rig.telegram.sent[sent:])


async def test_planner_bots_for_two_workspaces_plan_around_each_others_calendar_but_show_only_their_own_tasks(rig):
    await team_ready(rig)
    seed(rig)  # S&B: hearing 10:00-11:00, meeting 13:00; FSA: call 12:10, meeting 13:40; Google events at 15:00 (S&B), 15:40 (FSA), 16:20 (personal)
    sb = await planner_bot(rig, "111:sbp", 601, "sb")
    fsa = await planner_bot(rig, "222:fsap", 602, "fsa")

    # each sees only its own tasks, and the rest of the day as bare busy time
    sb_saw, sb_said = await propose(rig, sb, 601, 2, "t-sb", 1020)
    fsa_saw, fsa_said = await propose(rig, fsa, 602, 2, "t-fsa", 1080)
    assert "SBX-task" in sb_saw and "FSX-" not in sb_saw and "PEX-" not in sb_saw
    assert "FSX-task" in fsa_saw and "SBX-" not in fsa_saw and "PEX-" not in fsa_saw
    assert_clean(sb_said, "sb", "what the S&B planner bot said")
    assert_clean(fsa_said, "fsa", "what the FSA planner bot said")
    assert "Busy (another workspace)" in sb_saw and "Busy (another workspace)" in fsa_saw  # each takes the other's calendar into account

    # ...so each one's schedule is checked against the other's time: FSA's 13:40 meeting blocks S&B, S&B's 10:00 hearing blocks FSA
    _, said = await propose(rig, sb, 601, 3, "t-sb", 820)  # 13:40 is FSA's meeting
    assert "SBX-task" not in said  # no schedule card was made: the clash was refused
    _, said = await propose(rig, fsa, 602, 3, "t-fsa", 610)  # 10:10 is S&B's hearing
    assert "FSX-task" not in said

    # a Google event from another workspace's calendar blocks it too (S&B's 15:00 event, FSA's planner)
    _, said = await propose(rig, fsa, 602, 4, "t-fsa", 900)
    assert "FSX-task" not in said


async def test_what_one_workspace_books_becomes_busy_time_for_the_next(rig):
    await team_ready(rig)
    seed(rig)
    sb = await planner_bot(rig, "111:sbp", 601, "sb")
    fsa = await planner_bot(rig, "222:fsap", 602, "fsa")
    await propose(rig, sb, 601, 2, "t-sb", 1020)
    await say(rig, sb, "yes", 601, 3)  # S&B's plan is approved: its task now has a time (17:00)
    assert any(t == "tasks" and r["id"] == "t-sb" and r["slot"]["start"] == 1020 for t, r in rig.data.saved)
    # the stored task is what the next workspace reads: apply the approved slot to the data, as the practice service would
    for t in rig.data.tables["tasks"]:
        if t["id"] == "t-sb":
            t["slot"] = {"date": TOMORROW, "start": 1020, "duration": 30}
    saw, said = await propose(rig, fsa, 602, 2, "t-fsa", 1030)  # overlaps S&B's newly booked 17:00-17:30
    assert "FSX-task" not in said  # refused: it is busy
    assert "SBX-" not in saw  # and FSA's planner was never told what the busy time is
    _, said = await propose(rig, fsa, 602, 3, "t-fsa", 1080)  # 18:00 is free
    assert "FSX-task" in said


async def test_an_agent_is_shown_as_its_workflow_and_a_run_records_its_workspace(rig):
    await team_ready(rig)
    seed(rig)
    f = (await rig.client.get("/v1/agent/agents/daily-planner/flow", headers=headers())).json()
    assert [n["type"] for n in f["nodes"]] == ["trigger.chat", "ai.agent", "respond"] and f["custom"] is False
    tools = {t["name"]: t for t in f["tools"]}
    assert {"day_agenda", "propose_schedule"} <= set(tools) and tools["day_agenda"]["pack"] == "agenda" and tools["day_agenda"]["enabled"] is True
    assert "Daily Planner" in f["defaults"]["system"] and "tomorrow" in f["variables"]
    # a tool switched off in the prompt settings shows as off: the workflow's tools are the agent's real ones
    saved = (await rig.client.get("/v1/agent/agents/daily-planner", headers=headers())).json()["prompt"]
    await rig.client.put("/v1/agent/agents/daily-planner/prompt", json={**saved, "tools": {"free_time": {"enabled": False}}}, headers=headers())
    f = (await rig.client.get("/v1/agent/agents/daily-planner/flow", headers=headers())).json()
    assert next(t for t in f["tools"] if t["name"] == "free_time")["enabled"] is False

    # a run under a workspace records it, and shows it in the run detail
    sb = await connect_bot(rig, "456:def", 556, workspace="sb")
    await ask_bot(rig, sb, 556, 2)
    runs = (await rig.client.get("/v1/agent/agents/daily-planner/runs", headers=headers())).json()["items"]
    detail = (await rig.client.get(f"/v1/agent/runs/{runs[0]['id']}", headers=headers())).json()
    assert detail["workspace"] == "sb"
    if rig.app.state.settings.engine == "flow":  # (the classic fallback keeps no node trace)
        assert [t["node"] for t in detail["trace"]] == ["trigger", "agent", "respond"]


# --- one master bot: it works out the workspace of each message, and keeps each one's information apart ----------------------------


def test_the_workspace_a_message_is_about_is_read_from_its_words():
    from app.domain.orchestrator import workspace_of

    assert workspace_of("what hearings does S&B have tomorrow") == "sb" and workspace_of("send the invoice to the FSA client") == "fsa"
    assert workspace_of("renew my passport") == "personal" and workspace_of("what is pending across all workspaces") == "all"
    assert workspace_of("what is pending?") is None and workspace_of("hearing about the FSA") is None  # words of two workspaces: not guessed


async def test_one_master_bot_works_in_the_workspace_each_message_is_about_and_never_mixes_them(rig):
    await team_ready(rig)
    seed(rig)
    master = await connect_bot(rig, "123:abc", 555)  # the one bot: no workspace
    sb_saw = await ask_bot(rig, master, 555, 2, "Plan my day for S&B")
    assert CANARY["sb"] in sb_saw
    assert_clean(sb_saw, "sb", "the master, asked about S&B")
    sticky = await ask_bot(rig, master, 555, 3, "and what else is pending?")  # no workspace named: it stays in S&B
    assert_clean(sticky, "sb", "the master, carrying on")
    fsa_saw = await ask_bot(rig, master, 555, 4, "now the same for FSA")
    assert CANARY["fsa"] in fsa_saw
    assert_clean(fsa_saw, "fsa", "the master, asked about FSA")  # not even from the S&B talk just before: its context is S&B's thread
    assert "SBX-" not in fsa_saw
    reply = [t for _, t in rig.telegram.sent if "Daily planner" in t or "Follow-up" in t]
    assert any("· S&amp;B" in t for t in reply) and any("· FSA" in t for t in reply)  # each answer says which workspace it worked in
    threads = (await rig.client.get("/v1/agent/conversations", headers=headers())).json()["items"]
    assert sorted(t["workspace"] for t in threads) == ["fsa", "sb"]  # a thread per workspace under the one chat


async def test_the_master_asks_which_workspace_when_it_cannot_tell_then_answers_in_that_one(rig):
    from .test_availability import press

    await team_ready(rig)
    seed(rig)
    master = await connect_bot(rig, "123:abc", 555)
    start = len(rig.llm_log)
    rig.telegram.keyboards.clear()
    await say(rig, master, "plan my day", 555, 2)
    assert len(rig.llm_log) == start  # nothing reached a model: it asked first
    assert rig.telegram.sent[-1][1] == "🗂️ Which workspace is that for?"
    keys = [b["callback_data"] for row in rig.telegram.keyboards[-1] for b in row]
    assert [k.split(":")[1] for k in keys] == ["sb", "fsa", "personal", "all"]
    rig.script += [ChatResult("", [ToolCall("a", "day_agenda", {"date": TOMORROW}), ToolCall("b", "upcoming", {})]), ChatResult("Here is FSA's day.")]
    await press(rig, master, next(k for k in keys if k.startswith("ws:fsa:")), 3, chat=555)
    seen = "\n".join(e["everything"] for e in rig.llm_log[start:])
    assert "plan my day" in seen and CANARY["fsa"] in seen
    assert_clean(seen, "fsa", "the master, after FSA was chosen")
    assert "Here is FSA's day." in rig.telegram.sent[-1][1] and "· FSA" in rig.telegram.sent[-1][1]
    await press(rig, master, next(k for k in keys if k.startswith("ws:fsa:")), 4, chat=555)  # pressed again: the question is gone
    assert "out of date" in rig.telegram.sent[-1][1]


async def test_a_new_conversation_on_the_master_clears_every_workspaces_thread(rig):
    await team_ready(rig)
    seed(rig)
    master = await connect_bot(rig, "123:abc", 555)
    await ask_bot(rig, master, 555, 2, "Plan my day for S&B")
    await say(rig, master, "/new", 555, 3)
    assert "Started a new conversation" in rig.telegram.sent[-1][1]
    again = await ask_bot(rig, master, 555, 4, "what else is pending?")  # no recent workspace any more: it asks rather than guessing
    assert again == "" and rig.telegram.sent[-1][1] == "🗂️ Which workspace is that for?"
