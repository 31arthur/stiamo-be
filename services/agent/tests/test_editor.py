"""The story editor: quiet stories against their own pace, the ladder of questions, what each tap does, the writers' room, and the
planner's "push this"."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain import editor as ed
from app.domain.llm import ChatResult, ToolCall

from .conftest import headers
from .test_agent_api import choose, connect
from .test_availability import press
from .test_leaks import connect_bot

TODAY = datetime.now(ZoneInfo("Asia/Kolkata")).date()
STORIES = "/v1/agent/agents/stories"
OPEN = {"status": "open"}


def ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def ahead(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def story(id="s1", pacing="brisk", **kw):
    return {"id": id, "title": kw.pop("title", "Home loan"), "workspace": kw.pop("workspace", "personal"), "state": "writing", "pacing": pacing,
            "ending": "Disbursed before March", "chance": 60, "stakes": 5, "createdAt": f"{ago(60)}T00:00:00.000Z",
            "checkpoints": [{"id": "k1", "title": "Pick a lender", "status": "reached"}, {"id": "k2", "title": "Documents in", "status": "ahead"}], **kw}


def chapter(id, story_id, days_ago, **kw):
    return {"id": id, "storyId": story_id, "number": kw.pop("number", 1), "date": ago(days_ago), "prose": kw.pop("prose", "The agent called. The bank wants Form 16."),
            "title": kw.pop("title", ""), "people": kw.pop("people", [{"name": "The agent"}]), "hunches": kw.pop("hunches", []), **kw}


# --- the rules ---------------------------------------------------------------------------------------------------


def test_a_story_is_quiet_against_its_own_pace():
    today = TODAY.isoformat()
    s = story(pacing="brisk")
    assert ed.momentum(s, [chapter("c", "s1", 3)], today).state == "moving"
    m = ed.momentum(s, [chapter("c", "s1", 16)], today)
    assert (m.state, m.rung, m.quiet_days, m.pace_days) == ("quiet", 2, 16, 7)
    assert ed.momentum(story(pacing="slow"), [chapter("c", "s1", 16)], today).rung == 0  # 16 days is nothing for a slow story
    assert ed.momentum(s, [chapter("c", "s1", 100)], today).rung == 4  # the top of the ladder, no further
    waiting = story(waitingOn={"on": "the court", "until": ahead(5)})
    assert ed.momentum(waiting, [chapter("c", "s1", 40)], today).state == "waiting"  # the clock is paused
    ended_wait = story(waitingOn={"on": "the court", "until": ago(8)})
    m = ed.momentum(ended_wait, [chapter("c", "s1", 40)], today)
    assert (m.state, m.quiet_days, m.rung) == ("quiet", 8, 1)  # it starts again when the wait ends
    assert ed.momentum(story(state="resting"), [], today).state == "resting"
    assert ed.momentum(story(), [], today).quiet_days == 60  # never written in: counted from the start


def test_the_push_score_weighs_worth_readiness_dates_and_quiet():
    today = TODAY.isoformat()
    loan, court = story(), story("s2", title="Court", chance=80, stakes=2)
    m_loan, m_court = ed.momentum(loan, [chapter("c", "s1", 16)], today), ed.momentum(court, [chapter("d", "s2", 1)], today)
    assert ed.push_score(loan, m_loan, [], today) == round(0.6 * 5 * 1.3, 3)
    assert ed.push_score(court, m_court, [{"dueDate": ahead(3)}], today) == round(0.8 * 2 * 1.5, 3)
    waiting = story(waitingOn={"on": "x", "until": ahead(9)})
    assert ed.push_score(waiting, ed.momentum(waiting, [], today), [], today) == round(0.6 * 5 * 0.2 * 1.0, 3)
    assert ed.push_score(story(state="ended"), ed.momentum(story(state="ended"), [], today), [], today) == 0
    assert "quiet 16 days (2× its pace)" in ed.why(loan, m_loan, [])


def test_each_step_of_the_ladder_is_sent_once_and_a_new_chapter_starts_it_again():
    today = TODAY.isoformat()
    m = ed.momentum(story(), [chapter("c", "s1", 9)], today)
    assert ed.rung_to_send(m, {}, today) == 1
    mem = ed.remember({}, m, 1, today)
    assert ed.rung_to_send(m, mem, today) == 0
    later = ed.momentum(story(), [chapter("c", "s1", 15)], today)
    assert ed.rung_to_send(later, mem, today) == 2
    assert ed.rung_to_send(later, {**mem, "snoozed": ahead(3)}, today) == 0
    moved = ed.momentum(story(), [chapter("c", "s1", 15), chapter("d", "s1", 1, number=2)], today)
    assert ed.reset_if_moved(mem, moved) == {}


def test_previously_and_hunches_come_from_the_persons_own_words():
    chapters = [chapter("c1", "s1", 20, title="The Form 16 problem"),
                chapter("c2", "s1", 9, number=2, prose="HR answered. They want a letter first! More later.",
                        hunches=[{"id": "h1", "text": "They'll ask for ITRs next"}])]
    s = story(waitingOn={"on": "HR"})
    assert ed.previously(s, chapters) == f"last chapter ({ago(9)}): HR answered.; now at “Documents in”; waiting on HR"
    c, h = ed.due_hunch(chapters, 7, TODAY.isoformat())
    assert (c["id"], h["id"]) == ("c2", "h1")
    assert ed.due_hunch(chapters, 14, TODAY.isoformat()) is None  # not its moment yet
    openings = ed.fallback_openings(story(), ["The agent"], TODAY.isoformat())
    assert len(openings) == 3 and openings[0]["step"]["contact"] == "The agent" and openings[0]["step"]["kind"] == "followUp"


# --- the editor on the phone ---------------------------------------------------------------------------------------


async def ready(rig, tables):
    await connect(rig)
    await choose(rig, agent=STORIES)
    rig.data.tables = {"tasks": [], "follow-ups": [], **tables}
    return await connect_bot(rig, "111:pe", 601, workspace="personal", role="planner")


def last_buttons(rig, prefix="ed:"):
    return [b["callback_data"] for k in rig.telegram.keyboards if k for row in k for b in row if b["callback_data"].startswith(prefix)]


async def run(rig):
    r = await rig.client.post("/v1/agent/editor/run", json={}, headers=headers())
    assert r.status_code == 200, r.text
    return r.json()["sent"]


async def test_a_quiet_story_gets_three_openings_and_a_tap_books_its_step(rig):
    bot = await ready(rig, {
        "stories": [story()], "chapters": [chapter("c1", "s1", 16, people=[{"name": "The agent"}])],
        "characters": [{"id": "ravi", "name": "Ravi Kumar", "home": "personal", "phones": ["+919845012345"]}],
        "parts": [{"id": "p1", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "knownAs": ["The agent"]}],
    })
    rig.script += [ChatResult(json.dumps({"openings": [
        {"opening": "You call the agent and ask for a date.", "step": {"kind": "followUp", "title": "Ask the agent for a date", "due": ahead(1), "channel": "call", "contact": "The agent"}},
        {"opening": "You download the old Form 16 yourself.", "step": {"kind": "task", "title": "Download the old Form 16", "due": None}},
        {"opening": "You visit the second lender.", "step": {"kind": "followUp", "title": "Visit the second lender", "channel": "visit"}},
    ]}))]
    sent = await run(rig)
    assert [x for x in sent if "rung" in x] == [{"workspace": "personal", "story": "s1", "rung": 2}]
    text = next(t for _, t in rig.telegram.sent if "Three ways" in t)
    assert "Home loan" in text and "16 days without a chapter" in text and "Three ways the next chapter could start" in text
    assert "Previously:" in text and "1. You call the agent and ask for a date." in text
    job = next(x for x in rig.llm_log if "People in the story:" in x["last"])
    assert "People in the story: The agent" in job["last"] and job["tools"] == []

    assert await run(rig) == []  # one message per workspace per day
    first = next(d for d in last_buttons(rig) if d.startswith("ed:o0:"))
    await press(rig, bot, first, 2)
    table, record = rig.data.saved[-1]
    assert table == "follow-ups" and record["subject"] == {"kind": "story", "id": "s1"} and record["workspace"] == "personal"
    assert record["characterId"] == "ravi" and record["contact"] == "Ravi Kumar · +919845012345" and record["due"] == {"date": ahead(1)}
    assert "Booked" in rig.telegram.edits[-1][2] or any("Booked" in t for _, t in rig.telegram.sent)


async def test_the_first_step_asks_about_an_open_step_and_done_marks_it(rig):
    task = {"id": "t1", "title": "Email HR for Form 16", "workspace": "personal", "related": {"kind": "story", "id": "s1"}, "outcome": OPEN, "dueDate": ago(2)}
    bot = await ready(rig, {"stories": [story()], "chapters": [chapter("c1", "s1", 9)], "tasks": [task]})
    assert await run(rig) == [{"workspace": "personal", "story": "s1", "rung": 1}]
    assert "Did this happen: “Email HR for Form 16”?" in rig.telegram.sent[-1][1]
    await press(rig, bot, next(d for d in last_buttons(rig) if d.startswith("ed:sd:")), 2)
    table, record = rig.data.saved[-1]
    assert table == "tasks" and record["id"] == "t1" and record["outcome"]["status"] == "done"


async def test_a_hunch_whose_moment_has_come_is_asked_about(rig):
    ch = chapter("c1", "s1", 9, hunches=[{"id": "h1", "text": "They'll ask for ITRs next"}])
    bot = await ready(rig, {"stories": [story()], "chapters": [ch]})
    await run(rig)
    assert "Your hunch from" in rig.telegram.sent[-1][1] and "They'll ask for ITRs next" in rig.telegram.sent[-1][1]
    await press(rig, bot, next(d for d in last_buttons(rig) if d.startswith("ed:hy:")), 2)
    table, record = rig.data.saved[-1]
    assert table == "chapters" and record["hunches"][0]["cameTrue"] is True


async def test_the_top_of_the_ladder_asks_about_the_ending_and_can_rest_the_story(rig):
    bot = await ready(rig, {"stories": [story()], "chapters": [chapter("c1", "s1", 40)]})
    assert (await run(rig))[0]["rung"] == 4
    assert "Is “Disbursed before March” still the ending you want?" in rig.telegram.sent[-1][1]
    await press(rig, bot, next(d for d in last_buttons(rig) if d.startswith("ed:erest:")), 2)
    table, record = rig.data.saved[-1]
    assert table == "stories" and record["state"] == "resting"


async def test_not_now_leaves_a_story_for_its_pace(rig):
    bot = await ready(rig, {"stories": [story()], "chapters": [chapter("c1", "s1", 9)]})
    await run(rig)
    await press(rig, bot, next(d for d in last_buttons(rig) if d.startswith("ed:z:")), 2)
    view = (await rig.client.get("/v1/agent/editor", headers=headers())).json()
    assert view["stories"][0]["snoozedUntil"] == ahead(7) and view["settings"] == {"enabled": True, "roomDay": "sun"}
    stale = await press(rig, bot, next(d for d in last_buttons(rig) if d.startswith("ed:z:")), 3)
    assert stale.status_code == 200 and any("out of date" in e[2] for e in rig.telegram.edits)


async def test_the_writers_room_sums_up_the_week_on_its_day(rig):
    await ready(rig, {"stories": [story(), story("s2", title="Car loan", pacing="slow")],
                      "chapters": [chapter("c1", "s1", 16), chapter("c2", "s2", 2, title="Quote in", hunches=[{"id": "h", "text": "They'll approve"}])]})
    day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[TODAY.weekday()]
    await rig.client.put("/v1/agent/editor/settings", json={"enabled": True, "roomDay": day}, headers=headers())
    rig.script += [ChatResult("not json"), ChatResult("still not json")]  # the openings job fails: the editor falls back to its own
    sent = await run(rig)
    assert {"workspace": "personal", "room": True} in sent
    room = next(t for _, t in rig.telegram.sent if "Writers' room" in t)
    assert "Moved: Car loan (1)" in room and "Gone quiet: Home loan (16 days)" in room and "“They'll approve” (Car loan)" in room
    assert "Your next hour" in room and "Home loan" in room.split("Your next hour")[1]
    ladder = next(t for _, t in rig.telegram.sent if "Three ways" in t)
    assert "1. You ask The agent where things stand." in ladder  # the fallback openings, from the story itself


async def test_switched_off_it_sends_nothing(rig):
    await ready(rig, {"stories": [story()], "chapters": [chapter("c1", "s1", 16)]})
    await rig.client.put("/v1/agent/editor/settings", json={"enabled": False, "roomDay": "sun"}, headers=headers())
    assert await run(rig) == []
    assert (await rig.client.put("/v1/agent/editor/settings", json={"enabled": True, "roomDay": "someday"}, headers=headers())).status_code == 422


# --- the planner and the story editor ------------------------------------------------------------------------------


async def test_the_planner_is_told_which_story_to_push(rig):
    rig.data.tables = {"tasks": [{"id": "t1", "title": "Email HR", "workspace": "personal", "related": {"kind": "story", "id": "s1"}, "outcome": OPEN}],
                       "follow-ups": [], "stories": [story(), story("s2", title="Car loan", chance=20, stakes=1)], "chapters": [chapter("c1", "s1", 16)]}
    await connect(rig)
    await choose(rig, agent=STORIES)
    rig.script += [ChatResult("", [ToolCall("a", "stories_to_push", {})]), ChatResult("ok")]
    await rig.client.post(f"{STORIES}/chat", json={"message": "Which story should I push?"}, headers=headers())
    out = json.loads(rig.llm_log[-1]["last"])
    assert [s["title"] for s in out["stories"]] == ["Home loan", "Car loan"]
    assert out["stories"][0]["openSteps"] == [{"type": "task", "id": "t1", "title": "Email HR", "due": None}]


async def test_a_story_can_be_changed_by_proposal(rig):
    rig.data.tables = {"stories": [story()]}
    await connect(rig)
    await choose(rig, agent=STORIES)
    rig.script += [ChatResult("", [ToolCall("a", "change_story", {"story": "Home loan", "ending": "Disbursed by April", "chance": 45, "state": "resting"})]), ChatResult("ok")]
    out = (await rig.client.post(f"{STORIES}/chat", json={"message": "x"}, headers=headers())).json()
    card = next(f for f in out["found"] if f.get("kind") == "proposal")
    assert card["lines"] == ["Ending: Disbursed by April", "Chance: 60 → 45%", "Rest it (no questions until you write in it)"]
    await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    table, record = rig.data.saved[-1]
    assert table == "stories" and record["ending"] == "Disbursed by April" and record["chance"] == 45 and record["title"] == "Home loan"


async def test_a_chapter_card_suggests_the_chance_it_leaves(rig):
    rig.data.tables = {"stories": [story()]}
    await connect(rig)
    await choose(rig, agent=STORIES)
    prose = "Home loan: the bank wants Form 16 for two years now."
    rig.script += [ChatResult("", [ToolCall("c1", "write_chapter", {"text": prose})]), ChatResult('{"beat": "setback"}'),
                   ChatResult('{"chance": 52, "reason": "another document"}'), ChatResult("Ready.")]
    out = (await rig.client.post(f"{STORIES}/chat", json={"message": prose}, headers=headers())).json()
    card = next(f for f in out["found"] if f.get("kind") == "proposal")
    assert "Chance of the ending: 60% → 52% (another document). Say if you see it differently." in card["lines"]
    await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    assert rig.data.saved[-1][1]["chanceAfter"] == 52


async def test_the_evening_run_brings_the_editor_along(rig):
    from .test_leaks import evening

    await ready(rig, {"stories": [story()], "chapters": [chapter("c1", "s1", 9)]})
    await evening(rig)  # nothing to plan, but a story has gone quiet
    assert any("Previously:" in t and "Home loan" in t for _, t in rig.telegram.sent)
