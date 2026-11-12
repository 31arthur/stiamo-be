import json

import pytest
from app.application.aijobs import JobRunner
from app.application.scope import ScopedData
from app.application.stories import card_lines, find_story, to_body
from app.domain.aijobs import JobResult, Problem, as_messages, calendar, find_quote, parse_json
from app.domain.llm import AiError, ChatResult, ToolCall
from app.domain.story_jobs import FIND_STORY, JOBS, MARGIN_NOTES, story_named

from .conftest import FakeData, headers
from .test_agent_api import choose, connect

STORIES = "/v1/agent/agents/stories"
PROSE = ("The agent called at seven, sorry as always. The bank wants Form 16 for two years, not one. "
         "I'll write to HR on Monday. Something tells me they'll ask for the ITRs next.")
LOAN = {"id": "s1", "title": "Home loan", "workspace": "personal", "ending": "Disbursed before March", "state": "writing",
        "checkpoints": [{"id": "k1", "title": "Pick a lender", "status": "reached"}, {"id": "k2", "title": "Documents in", "status": "ahead"}]}
CAR = {"id": "s2", "title": "Car loan", "workspace": "personal", "state": "writing", "checkpoints": []}
COURT = {"id": "s3", "title": "Gulbarga SMPL", "workspace": "sb", "state": "writing", "checkpoints": []}


def notes_input(prose=PROSE):
    return {"prose": prose, "today": "2026-11-12", "date": "2026-11-12",
            "story": {"title": "Home loan", "ending": "", "here": "k2", "people": [], "checkpoints": LOAN["checkpoints"]}}


class Script:
    """A model that answers from a list, for the runner on its own."""

    def __init__(self, *replies):
        self.replies, self.asked = list(replies), []

    async def chat(self, *, model, messages, tools, thinking):
        self.asked.append([m.content for m in messages])
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return ChatResult(r if isinstance(r, str) else json.dumps(r))


# --- reading and checking a reply --------------------------------------------------------------------------------


def test_json_is_found_however_it_is_wrapped():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('Sure! Here it is:\n```json\n{"a": {"b": "}"}}\n```\nHope that helps') == {"a": {"b": "}"}}
    assert parse_json('The answer is {"a": [1, 2]} as asked') == {"a": [1, 2]}
    for bad in ("no json here", '{"a": 1', '{"a": tru}'):
        with pytest.raises(Problem):
            parse_json(bad)


def test_a_quote_must_be_the_persons_words():
    text = "The agent called at seven,  sorry as always. It’s “late” again."
    assert find_quote("the agent called at seven, sorry", text) == "The agent called at seven,  sorry"
    assert find_quote("It's \"late\" again", text) == "It’s “late” again"
    assert find_quote("The agent rang at seven", text) is None  # a paraphrase is not a quote
    assert find_quote("at", text) is None and find_quote(None, text) is None


def test_the_calendar_lets_a_model_look_days_up():
    table = calendar("2026-11-12", 7)
    assert table.splitlines()[0] == "Thu 12 Nov = 2026-11-12 (today)"
    assert "Mon 16 Nov = 2026-11-16" in table


@pytest.mark.parametrize("job", list(JOBS.values()), ids=lambda j: j.name)
def test_every_example_a_job_shows_the_model_passes_its_own_checks(job):
    """A worked example that fails the checks would teach the model to fail them."""
    for given, ideal in job.examples:
        checked = job.check(json.loads(json.dumps(ideal)), given)
        assert checked.dropped == [], checked.dropped
    roles = [r for r, _ in as_messages(job, job.examples[0][0])]
    assert roles[0] == "system" and roles[-1] == "user" and roles.count("assistant") == len(job.examples)


def test_margin_notes_keep_only_what_is_pinned_to_the_chapter():
    out = MARGIN_NOTES.check({
        "beat": "SETBACK",
        "checkpoints": [{"id": "k2", "status": "reached", "quote": "made up words here"}, {"id": "nope", "status": "reached", "quote": "The agent called"}],
        "twists": [{"title": "Get old Form 16", "before": "zzz", "quote": "The bank wants Form 16 for two years"},
                   {"title": "Documents in", "quote": "The bank wants Form 16"}],
        "steps": [{"kind": "follow_up", "title": "Write to HR", "due": "Monday", "channel": "carrier pigeon", "quote": "I'll write to HR on Monday"},
                  {"kind": "task", "title": "Pay the fee", "quote": "Pay the fee today"}],
        "people": [{"name": "The agent", "scene": "apologetic", "quote": "sorry as always"}],
        "hunches": [{"text": "ITRs next", "quote": "they'll ask for the ITRs next"}],
        "cliffhanger": {"question": "Will HR answer?", "quote": "invented"},
    }, notes_input())
    o = out.output
    assert o["beat"] == "setback" and o["checkpoints"] == []
    assert o["twists"] == [{"title": "Get old Form 16", "before": None, "quote": "The bank wants Form 16 for two years"}]
    assert o["steps"] == [{"kind": "followUp", "title": "Write to HR", "due": None, "contact": None, "quote": "I'll write to HR on Monday", "channel": "call"}]
    assert [p["name"] for p in o["people"]] == ["The agent"] and len(o["hunches"]) == 1 and o["cliffhanger"] is None
    assert len(out.dropped) == 5  # two checkpoints, the twist already in the arc, the invented step, the cliffhanger


def test_a_reply_with_no_real_quotes_is_sent_back():
    with pytest.raises(Problem):
        MARGIN_NOTES.check({"beat": "progress", "steps": [{"title": "A", "quote": "nothing like it"}, {"title": "B", "quote": "nor this"}]}, notes_input())
    assert MARGIN_NOTES.check({"beat": "lull"}, notes_input()).output["beat"] == "lull"  # saying nothing is fine


def test_which_story_is_checked_against_the_list():
    i = {"message": "x", "stories": [LOAN, CAR]}
    assert FIND_STORY.check({"story": "s9"}, i).output["story"] is None
    assert FIND_STORY.check({"story": None, "unsure_between": ["s1", "s9", "s2"]}, i).output["unsure_between"] == ["s1", "s2"]


# --- the runner --------------------------------------------------------------------------------------------------


async def test_a_job_is_asked_again_once_with_what_was_wrong():
    model = Script("Here you go: beat is setback", {"beat": "setback"})
    result = await JobRunner(model, "cheap-model").run(MARGIN_NOTES, notes_input())
    assert result.ok and result.attempts == 2 and result.output["beat"] == "setback" and result.model == "cheap-model"
    assert "can't be used" in model.asked[1][-1]


async def test_a_job_that_keeps_failing_falls_back_to_nothing_invented():
    result = await JobRunner(Script("nope", "still nope"), "m").run(MARGIN_NOTES, notes_input())
    assert not result.ok and result.attempts == 2 and result.output["steps"] == [] and len(result.problems) == 2
    down = await JobRunner(Script(AiError("rate limited")), "m").run(MARGIN_NOTES, notes_input())
    assert not down.ok and down.attempts == 0 and "rate limited" in down.problems[0]


# --- finding the story -------------------------------------------------------------------------------------------


def test_a_named_story_is_found_by_code():
    stories = [LOAN, CAR, COURT]
    assert story_named("Home loan: the agent called", stories) == "s1"
    assert story_named("Update on gulbarga smpl, registry wants a notice", stories) == "s3"
    assert story_named("The bank called about the loan", stories) is None


async def test_the_model_is_asked_only_when_code_cant_tell():
    model = Script({"story": None, "unsure_between": ["s1", "s2"], "reason": "both loans"})
    found = await find_story("The bank wants salary slips", [LOAN, CAR], JobRunner(model, "m"))
    assert found.status == "ask" and [s["id"] for s in found.choices] == ["s1", "s2"] and found.by == "model"
    named = await find_story("Home loan: bank called", [LOAN, CAR], JobRunner(Script(), "m"))
    assert named.status == "found" and named.story["id"] == "s1" and named.by == "code"
    by_key = await find_story("anything", [LOAN, CAR], None, key="car")
    assert by_key.story["id"] == "s2"


def test_the_card_says_what_will_be_saved():
    notes = {"beat": "setback", "checkpoints": [], "twists": [{"title": "Get old Form 16", "before": "k2", "quote": "q"}],
             "steps": [{"kind": "followUp", "title": "Visit the office", "due": "2026-11-18", "channel": "visit", "contact": "HR", "quote": "q"}],
             "people": [], "hunches": [{"text": "ITRs next", "quote": "q"}], "cliffhanger": None, "waiting_on": {"on": "HR", "until": None, "quote": "q"}, "title": None}
    ids = iter(f"id{n}" for n in range(10))
    body = to_body(notes, PROSE, title=None, day=None, source="telegram", new_id=lambda: next(ids))
    assert body["id"] == "id0" and body["prose"] == PROSE and body["twists"][0]["id"] == "id1" and body["steps"][0]["channel"] == "visit"
    assert body["waitingOn"] == {"on": "HR"} and "date" not in body
    lines = card_lines(LOAN, body, JobResult(notes, True, 1))
    assert lines[0] == "Beat: setback" and "Twist: new checkpoint “Get old Form 16”, before “Documents in”" in lines
    assert any(line.startswith("Visit: Visit the office · HR") for line in lines)
    assert "Your words are saved exactly as you wrote them." in lines


# --- workspaces --------------------------------------------------------------------------------------------------


async def test_a_workspace_writes_only_its_own_stories():
    data = FakeData()
    data.tables = {"stories": [LOAN, COURT], "chapters": [{"id": "c1", "storyId": "s1"}, {"id": "c2", "storyId": "s3"}]}
    sb = ScopedData(data, "sb")
    assert [s["id"] for s in await sb.records("stories")] == ["s3"]
    assert await sb.chapters("s1") == [] and [c["id"] for c in await sb.chapters("s3")] == ["c2"]
    with pytest.raises(AiError):
        await sb.write_chapter("s1", {"prose": "x"})
    await sb.write_chapter("s3", {"prose": "x"})
    assert data.saved[-1][0] == "chapter"


# --- the whole path ------------------------------------------------------------------------------------------------


async def test_a_message_becomes_a_chapter_card_and_approving_writes_it(rig):
    rig.data.tables = {"stories": [LOAN]}
    await connect(rig)
    await choose(rig, agent=STORIES)
    notes = {"beat": "setback", "checkpoints": [], "twists": [{"title": "Get old Form 16", "before": None, "quote": "The bank wants Form 16 for two years"}],
             "steps": [{"kind": "task", "title": "Write to HR", "due": "2026-11-16", "quote": "I'll write to HR on Monday"}],
             "people": [{"name": "The agent", "scene": "sorry again", "quote": "sorry as always"}], "hunches": [], "cliffhanger": None, "waiting_on": None, "title": "The Form 16 problem"}
    message = f"Home loan: {PROSE}"
    rig.script += [ChatResult("", [ToolCall("c1", "write_chapter", {"text": message, "source": "telegram"})]), ChatResult(json.dumps(notes)), ChatResult("Ready to approve.")]
    r = await rig.client.post(f"{STORIES}/chat", json={"message": message}, headers=headers())
    assert r.status_code == 200, r.text
    card = next(f for f in r.json()["found"] if f.get("kind") == "proposal")
    assert card["title"] == "Chapter for “Home loan”: The Form 16 problem"
    assert "Twist: new checkpoint “Get old Form 16”" in card["lines"] and any(line.startswith("Task: Write to HR") for line in card["lines"])
    job_call = rig.llm_log[1]
    assert "Calendar:" in job_call["everything"] and "<<<" in job_call["last"] and job_call["tools"] == []  # a small job, no tools
    assert rig.data.saved == []  # nothing yet
    applied = await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    assert applied.status_code == 200, applied.text
    table, body = rig.data.saved[-1]
    assert table == "chapter" and body["story"] == "s1" and body["prose"] == message and body["source"] == "telegram"
    assert body["steps"][0]["title"] == "Write to HR" and body["twists"][0]["title"] == "Get old Form 16"


async def test_the_app_drafts_a_chapter_the_same_way(rig):
    rig.data.tables = {"stories": [LOAN, CAR]}
    await connect(rig)
    await choose(rig, agent=STORIES)
    rig.script += [ChatResult(json.dumps({"story": None, "unsure_between": ["s1", "s2"]}))]
    ask = (await rig.client.post("/v1/agent/stories/chapters/draft", json={"prose": "The bank wants salary slips."}, headers=headers())).json()
    assert [s["title"] for s in ask["ask"]] == ["Home loan", "Car loan"]
    rig.script += [ChatResult('{"beat": "lull"}')]
    out = (await rig.client.post("/v1/agent/stories/chapters/draft", json={"prose": "Quiet week.", "story": "s2"}, headers=headers())).json()
    assert out["proposed"] and out["story"]["id"] == "s2" and out["notesOk"]
    assert (await rig.client.post("/v1/agent/stories/chapters/draft", json={"prose": "  "}, headers=headers())).status_code == 422


async def test_each_job_can_run_on_its_own_model(rig):
    await connect(rig)
    await choose(rig, agent=STORIES)
    jobs = (await rig.client.get("/v1/agent/jobs", headers=headers())).json()["items"]
    assert {j["name"] for j in jobs} == {"find_story", "margin_notes"} and all(not j["ownChoice"] for j in jobs)
    r = await rig.client.put("/v1/agent/jobs/margin_notes/model", json={"provider": "openai", "model": "gpt-4o"}, headers=headers())
    assert r.status_code == 200, r.text
    mine = next(j for j in r.json()["items"] if j["name"] == "margin_notes")
    assert mine["ownChoice"] and mine["selection"]["modelId"] == "gpt-4o"
    rig.data.tables = {"stories": [LOAN]}
    rig.script += [ChatResult('{"beat": "lull"}')]
    await rig.client.post("/v1/agent/stories/chapters/draft", json={"prose": "Quiet week.", "story": "s1"}, headers=headers())
    assert rig.llm_log[-1]["model"] == "gpt-4o"
    back = await rig.client.put("/v1/agent/jobs/margin_notes/model", json={}, headers=headers())
    assert not next(j for j in back.json()["items"] if j["name"] == "margin_notes")["ownChoice"]
    assert (await rig.client.put("/v1/agent/jobs/nope/model", json={}, headers=headers())).status_code == 404


# --- the test sets ---------------------------------------------------------------------------------------------------


def test_the_test_sets_load_and_score():
    from app.domain.job_evals import SCORERS, SetScore
    from app.scripts.eval_jobs import load

    for name in ("margin_notes", "find_story"):
        cases = load(name)
        assert len(cases) >= 6
        for c in cases:
            JOBS[name].render(c["input"])  # every case is a valid input for its job
    # a reply that matches what was expected scores full marks; one that invents doesn't
    perfect = {"beat": "setback", "checkpoints": [], "twists": [{"title": "x"}], "steps": [{"kind": "task", "due": "2026-11-13"}, {"kind": "followUp"}]}
    expect = load("margin_notes")[0]["expect"]
    s = SCORERS["margin_notes"]("c", perfect, expect)
    assert s.got == s.points
    lazy = SCORERS["margin_notes"]("c", {"beat": "note"}, expect)
    assert lazy.got < lazy.points and lazy.misses
    score = SetScore("margin_notes", "m", [s, lazy])
    assert 0 < score.agreement < 1 and not score.passed
