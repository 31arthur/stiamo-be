"""Every kind of message a bot sends, captured as the phone would receive it (the fake Telegram keeps what it was asked to send), and kept
short: each kind has a budget, so a message that grows long again fails here before it reaches a phone.

To read them all in one place:  STIAMO_GALLERY=gallery.txt pytest tests/test_message_gallery.py
"""

import os
from types import SimpleNamespace

import pytest
from app.application.channels import NOT_LINKED, ChannelService
from app.domain.llm import ChatResult, ToolCall
from app.domain.phone import render, strip_markup

from .conftest import headers
from .test_agent_api import choose, connect
from .test_alerts import READ, alert_agent, alert_id_of
from .test_alerts import press as alert_press
from .test_availability import HOSPITAL, day, plan_to_approve, planner, press_data, script
from .test_availability import press as av_press
from .test_channels import send, telegram
from .test_editor import STORIES, ago, chapter, ready, story
from .test_editor import run as editor_run
from .test_leaks import connect_bot, evening
from .test_mail_filter import BASE, answer
from .test_planning import PLANNER, TOMORROW, fill
from .test_reminders import at, check
from .test_reminders import ready as reminders_ready

#: (kind, text as sent) in the order captured.
GALLERY: list[tuple[str, str]] = []

#: The most a kind of message may run to, in characters of what is read (tags and links not counted). A plan or an alert carries real content;
#: a confirmation or a question is a line or two.
BUDGET = {"plan": 900, "alert": 600, "draft": 700, "reminder": 450, "editor": 520, "interview": 300, "confirm": 400, "saved": 330, "question": 140, "hello": 330, "system": 160}


def mark(rig):
    return len(rig.telegram.sent), len(rig.telegram.edits)


def grab(rig, kind: str, label: str, since: tuple[int, int]) -> None:
    for _, t in rig.telegram.sent[since[0]:]:
        GALLERY.append((f"{kind}|{label}", t))
    for e in rig.telegram.edits[since[1]:]:
        GALLERY.append((f"{kind}|{label} (the message that said ⏳, now showing the result)", e[2]))


# --- pairing and errors ----------------------------------------------------------------------------------------------


async def test_pairing_and_stray_messages(rig):
    await connect(rig)
    master = await telegram(rig)
    m = mark(rig)
    await send(rig, master, f"/start {master['pairingCode']}", chat=555, update=1)
    grab(rig, "hello", "pairing: the master bot", m)
    m = mark(rig)
    await send(rig, master, "hello there", chat=999, update=2)  # a chat that was never paired
    grab(rig, "system", "a chat that isn't linked", m)
    assert NOT_LINKED in [t for _, t in rig.telegram.sent]
    m = mark(rig)
    await connect_bot(rig, "456:def", 556, workspace="sb", role="planner")
    grab(rig, "hello", "pairing: a workspace's bot", m)


# --- the plan, and what follows from its buttons ------------------------------------------------------------------------


async def test_the_evening_plan_the_time_question_and_their_answers(rig):
    bot = await planner(rig)
    day(rig)
    script(rig, "followUp", "f-sb", 600)
    m = mark(rig)
    await evening(rig)
    texts = [t for _, t in rig.telegram.sent[m[0]:]]
    for t in texts:
        GALLERY.append(("question|asked before planning, about a long event" if "Free for calls then?" in t else "plan|the evening plan, with Approve and Not this", t))
    assert HOSPITAL["title"] in "".join(texts)
    script(rig, "followUp", "f-sb", 600)
    m = mark(rig)
    await av_press(rig, bot, press_data(rig, "av:c:"), 2)
    for _, t in rig.telegram.sent[m[0]:]:
        GALLERY.append(("confirm|the answer to the time question" if t.startswith("✅") else "plan|the plan again with the answer", t))
    m = mark(rig)
    await av_press(rig, bot, press_data(rig, "pl:a:"), 3)
    grab(rig, "confirm", "plan approved", m)


async def test_a_plan_that_is_dropped(rig):
    bot, plan = await plan_to_approve(rig)
    m = mark(rig)
    await av_press(rig, bot, f"pl:x:{plan['id']}", 2)
    grab(rig, "confirm", "plan dropped", m)
    m = mark(rig)
    await av_press(rig, bot, f"pl:a:{plan['id']}", 3)  # pressed again, after it was dropped
    grab(rig, "system", "a button of a plan already dropped", m)


# --- yes and no to what the assistant proposed ------------------------------------------------------------------------


async def test_proposals_and_their_yes_and_no(rig):
    fill(rig)
    await connect(rig)
    c = await telegram(rig)
    await rig.client.put(f"/v1/agent/channels/{c['id']}/agent", json={"agent": "daily-planner"}, headers=headers())
    await choose(rig, agent=PLANNER)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    m = mark(rig)
    rig.script += [ChatResult("", [ToolCall("a", "add_task", {"title": "Call Hemla", "due": TOMORROW, "priority": "high"})]), ChatResult("I've proposed it. Approve to save.")]
    await send(rig, c, "add a task to call Hemla tomorrow", chat=555, update=2)
    grab(rig, "confirm", "a proposed task", m)
    m = mark(rig)
    await send(rig, c, "yes", chat=555, update=3)
    grab(rig, "saved", "after YES", m)
    for n in range(3):  # the same change proposed three times in a row, as in a long chat
        rig.script += [ChatResult("", [ToolCall(f"x{n}", "change_follow_up", {"id": "f1", "due": TOMORROW})]), ChatResult("Proposed.")]
        await send(rig, c, "move it", chat=555, update=10 + n)
    m = mark(rig)
    await send(rig, c, "yes", chat=555, update=20)
    grab(rig, "saved", "after YES to several, some the same", m)
    rig.script += [ChatResult("", [ToolCall("b", "add_task", {"title": "Never mind this"})]), ChatResult("Proposed.")]
    await send(rig, c, "add another", chat=555, update=30)
    m = mark(rig)
    await send(rig, c, "no", chat=555, update=31)
    grab(rig, "saved", "after NO", m)


# --- reminders ---------------------------------------------------------------------------------------------------------


async def test_the_call_reminder(rig):
    await reminders_ready(rig)
    m = mark(rig)
    await check(rig, at(9, 40))
    grab(rig, "reminder", "calls still pending", m)


# --- a mail alert and the replies drafted from it ---------------------------------------------------------------------


async def test_the_mail_alert_and_its_draft(rig):
    import json

    from .test_alerts import DRAFT

    wid, c = await alert_agent(rig)
    rig.script += [answer("urgent"), READ]
    m = mark(rig)
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    grab(rig, "alert", "an urgent email", m)
    aid = alert_id_of(rig)
    rig.script += [DRAFT]
    m = mark(rig)
    await alert_press(rig, c, f"al:{aid}:r0", update=2)
    grab(rig, "draft", "a reply drafted from a suggestion", m)
    assert rig.telegram.deleted_messages and not any("The details are above" in t for _, t in rig.telegram.sent)  # the "⏳" message is taken back, not left saying "Done"
    m = mark(rig)
    await alert_press(rig, c, f"al:{aid}:send", update=3)
    grab(rig, "confirm", "the reply sent", m)
    m = mark(rig)
    await alert_press(rig, c, f"al:{aid}:send", update=4)
    grab(rig, "system", "the same button pressed twice", m)
    assert json  # (kept for the editor's use of the same alert helpers)


# --- the story editor --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("quiet,label", [(9, "first step: a nudge"), (16, "second step: three ways to start"), (24, "third step: what went wrong"), (40, "fourth step: still the ending?")])
async def test_the_editor_ladder(rig, quiet, label):
    await ready(rig, {"stories": [story()], "chapters": [chapter("c1", "s1", quiet)]})
    rig.script += [ChatResult("not json"), ChatResult("still not json")]  # the model fails: the editor's own openings are used
    m = mark(rig)
    await editor_run(rig)
    for _, t in rig.telegram.sent[m[0]:]:
        GALLERY.append(("interview|a person to learn about" if "🎭" in t else "editor|" + label, t))


async def test_the_interviewer_and_the_writers_room(rig):
    from .test_editor import TODAY

    tables = {"stories": [story(), story("s2", title="Car loan", pacing="slow")],
              "chapters": [chapter("c1", "s1", 2), chapter("c2", "s2", 3, title="Quote in", hunches=[{"id": "h", "text": "They'll approve it"}])],
              "characters": [{"id": "ravi", "name": "Ravi Kumar", "home": "personal", "phones": ["+919845012345"]}],
              "parts": [{"id": "p1", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "part": "agent"}], "evidence": [], "scenes": []}
    await ready(rig, tables)
    day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[TODAY.weekday()]
    await rig.client.put("/v1/agent/editor/settings", json={"enabled": True, "roomDay": day}, headers=headers())
    m = mark(rig)
    await editor_run(rig)
    for _, t in rig.telegram.sent[m[0]:]:
        GALLERY.append(("interview|a person to learn about" if "🎭" in t else "editor|the weekly writers' room", t))


async def test_the_editor_asks_about_a_hunch_and_a_step(rig):
    task = {"id": "t1", "title": "Email HR for Form 16", "workspace": "personal", "related": {"kind": "story", "id": "s1"}, "outcome": {"status": "open"}, "dueDate": ago(2)}
    await ready(rig, {"stories": [story()], "chapters": [chapter("c1", "s1", 9)], "tasks": [task]})
    m = mark(rig)
    await editor_run(rig)
    grab(rig, "editor", "first step, about an open step", m)
    assert STORIES


# --- what the assistant's own answers look like -------------------------------------------------------------------------


def test_cases_documents_and_the_fixed_messages():
    cases = [{"kind": "case", "reference": "TUMWU-2026-002", "title": "Alur 84 Workers NHAI Case", "nextHearing": {"date": "2026-11-13"}},
             {"kind": "case", "reference": "TUMWU-2026-004", "title": "Vempalli NHAI 105 Workers Case"}]
    GALLERY.append(("answer|a list of cases", strip_markup(render("Two matches.", cases, 4000, "Case agent", "telegram"))))
    docs = [{"kind": "document", "name": "Wage Breakdown Alur.pdf", "webViewLink": "https://drive.google.com/file/d/1/view"}]
    GALLERY.append(("answer|documents", strip_markup(render("Found it.", docs, 4000, "Case agent", "telegram"))))
    GALLERY.append(("answer|nothing found", strip_markup(render("", [], 4000, None, "telegram"))))
    for ws in (None, "sb"):
        GALLERY.append(("hello|" + ("the master bot's hello" if ws is None else "a workspace bot's hello"), ChannelService._hello(SimpleNamespace(public={"workspace": ws} if ws else {}))))


# --- read them, and hold them to their budgets -------------------------------------------------------------------------


def _plain(text: str) -> str:
    return strip_markup(text)


def test_every_kind_of_message_is_short_and_they_can_all_be_read_in_one_place():
    path = os.environ.get("STIAMO_GALLERY")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            for kind, text in GALLERY:
                f.write(f"===== {kind} · {len(_plain(text))} characters =====\n{_plain(text)}\n\n")
    over = [(kind, len(_plain(text))) for kind, text in GALLERY if len(_plain(text)) > BUDGET.get(kind.split("|")[0], 400)]
    assert not over, f"too long for a phone: {over}"
