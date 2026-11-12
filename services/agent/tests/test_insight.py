"""Gauging a person: the codebooks and jobs, labelling scenes, the interviewer, "how will they take it?", people in a text, and leaks."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.application.aijobs import JobRunner
from app.application.cast_insight import code_scenes, evidence_id, predict, settle
from app.application.scope import ScopedData
from app.domain import interviewer as iv
from app.domain.aijobs import Problem
from app.domain.insight_jobs import CONSTRUCT, INSIGHT_JOBS, LENS_CODERS, PEOPLE_IN_TEXT, WORDING
from app.domain.job_evals import SCORERS
from app.domain.lenses import CODEBOOK, REACTIONS, codebook_text, moves_for
from app.domain.llm import AiError, ChatResult, ToolCall

from .conftest import FakeData, FakeDecisions, headers
from .test_agent_api import choose, connect
from .test_availability import press
from .test_leaks import assert_clean, connect_bot
from .test_stories import Script

TODAY = datetime.now(ZoneInfo("Asia/Kolkata")).date()
T = TODAY.isoformat()
STORIES = "/v1/agent/agents/stories"


def ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


LOAN = {"id": "s1", "title": "Home loan", "workspace": "personal", "state": "writing", "pacing": "brisk", "stakes": 5, "chance": 60, "checkpoints": [], "createdAt": f"{ago(3)}T00:00:00Z"}
COURT = {"id": "s3", "title": "Gulbarga SMPL", "workspace": "sb", "state": "writing", "pacing": "slow", "stakes": 4, "checkpoints": [], "createdAt": f"{ago(3)}T00:00:00Z"}
PROFILE = {"lenses": {"under_pressure": {"label": "goes_quiet", "confidence": "high", "evidence": 4, "share": 0.9, "weight": 3.6},
                      "wants": {"label": "money", "also": "avoiding_blame", "confidence": "medium", "evidence": 2, "share": 0.6, "weight": 1.8}},
           "trackRecord": {"tries": 10, "answerRate": {"call": 0.3, "message": 0.8}, "bestHours": ["18:00–19:00"], "promises": {"kept": 2, "of": 6, "avgDaysLate": 4.0}},
           "conflicts": []}
RAVI = {"id": "ravi", "name": "Ravi Kumar", "home": "personal", "phones": ["+919845012345"], "profile": PROFILE}


def runners_from(model):
    return lambda name: JobRunner(model, "cheap")


# --- the codebooks and jobs -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("job", list(INSIGHT_JOBS.values()), ids=lambda j: j.name)
def test_every_example_an_insight_job_shows_passes_its_own_checks(job):
    for given, ideal in job.examples:
        assert job.check(json.loads(json.dumps(ideal)), given).dropped == []


def test_every_label_is_written_down_with_examples():
    for lens, book in CODEBOOK.items():
        assert book["asks"] and book["labels"]
        for name, d in book["labels"].items():
            assert d["means"] and len(d["sounds_like"]) == 2 and d["phrase"], (lens, name)
    text = codebook_text("under_pressure")
    assert "goes_quiet:" in text and "not_shown" in text and "Not this:" in text


def test_a_coder_labels_only_from_the_scene_or_says_not_shown():
    coder = LENS_CODERS["under_pressure"]
    given = {"name": "The agent", "scene": "didn't answer for a week", "quote": "He didn't answer for a week after the bank said no"}
    assert coder.check({"quote": "didn't answer for a week", "label": "Goes Quiet", "strength": "weak"}, given).output == {"label": "goes_quiet", "quote": "didn't answer for a week", "strength": "weak"}
    assert coder.check({"label": "not_shown"}, given).output["label"] is None
    with pytest.raises(Problem):
        coder.check({"quote": "He was furious", "label": "confronts"}, given)  # not in the scene
    with pytest.raises(Problem):
        coder.check({"quote": "didn't answer", "label": "sulks"}, given)  # not in the codebook


def test_people_numbers_and_comparisons_must_come_from_the_text():
    out = PEOPLE_IN_TEXT.check({"people": [
        {"name": "R. Nayak", "phones": ["08472-263401", "99999 00000"], "emails": ["nayak@gov.in"], "quote": "R. Nayak"},
        {"name": "Somebody Else", "phones": []}]}, {"text": "Commissioner R. Nayak, office 08472-263401."})
    assert out.output["people"] == [{"name": "R. Nayak", "title": None, "organisation": None, "phones": ["08472-263401"], "emails": []}]
    assert len(out.dropped) == 2
    with pytest.raises(Problem):
        CONSTRUCT.check({"pole_a": "punctual", "pole_b": "tardy"}, {"answer": "He promises everything, she promises little."})
    with pytest.raises(Problem):
        WORDING.check({"message": "Please send it by the 15th."}, {"news": "The bank wants the ITR."})


def test_the_playbook_turns_a_reaction_into_moves():
    moves = moves_for("goes_quiet", "money", "own_word")
    assert moves[0].startswith("Send the one-line fact first") and "Say what it means for their money" in moves and "Quote what they said or promised" in moves
    assert moves_for("nonsense", None, None) == moves_for("takes_it_well", None, None)


def test_the_test_sets_load_and_score_every_insight_job():
    from app.scripts.eval_jobs import load

    for name in INSIGHT_JOBS:
        cases = load(name)
        assert cases, name
        for c in cases:
            INSIGHT_JOBS[name].render(c["input"])
            assert SCORERS[name](c["name"], {}, c["expect"]).points >= 1
    assert len(load("lens_under_pressure")) >= 6 and sum(len(load(f"lens_{k}")) for k in CODEBOOK) == len(load("lens_coding"))  # each coder reads its share of the shared file


# --- labelling scenes ----------------------------------------------------------------------------------------------------


async def test_new_scenes_are_routed_then_labelled_once():
    data = FakeData()
    data.tables = {"stories": [LOAN], "characters": [RAVI], "scenes": [
        {"id": "x1", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "date": ago(2), "text": "didn't answer for a week", "quote": "He didn't answer for a week after the bank said no"},
        {"id": "x2", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "date": ago(1), "text": "old", "coded": True}]}
    model = Script({"lenses": ["under_pressure", "astrology"]}, {"quote": "He didn't answer for a week", "label": "goes_quiet", "strength": "clear"})
    out = await code_scenes(data, runners_from(model))
    assert out == {"coded": 1, "evidence": 1, "waiting": 0}
    (t1, ev), (t2, sc) = data.saved
    assert t1 == "evidence" and ev["id"] == evidence_id("x1", "under_pressure") and ev["label"] == "goes_quiet" and ev["sceneId"] == "x1" and ev["date"] == ago(2)
    assert t2 == "scenes" and sc["coded"] is True
    assert "Person: Ravi Kumar" in model.asked[0][-1] and "Story: Home loan" in model.asked[0][-1]
    nothing = await code_scenes(data, lambda name: None)
    assert nothing["reason"].startswith("no model")


# --- the interviewer ---------------------------------------------------------------------------------------------------


def person(evidence=(), scenes=(), calls=(), profile=None, quiet=(), part=None, story=LOAN, cid="ravi", name="Ravi Kumar"):
    c = {"id": cid, "name": name, "profile": profile}
    return iv.Person(c, [(part or {"storyId": story["id"], "part": "agent", "sway": "shapes"}, story)], list(evidence), list(scenes), list(calls), set(quiet))


def test_the_interviewer_picks_the_way_of_asking_for_the_moment():
    new = iv.choose(person(), T, {}, True, [])
    assert new.method == "sheet" and new.lens == "wants" and "new in “Home loan”" in new.text
    touch = iv.choose(person(evidence=[{"source": "scene"}], calls=[{"attempts": [{"date": ago(1), "result": "reached"}]}]), T, {}, True, [])
    assert touch.method == "debrief" and touch.specific
    pending = {"id": "p1", "source": "prediction", "verdict": "pending", "date": ago(2), "about": "the bank wants more", "storyId": "s1"}
    settle_it = iv.choose(person(evidence=[pending], calls=[{"attempts": [{"date": T, "result": "reached"}]}]), T, {}, True, [])
    assert settle_it.method == "debrief_prediction" and settle_it.prediction["id"] == "p1"
    conflict = {"lenses": {"under_pressure": {"label": "goes_quiet", "conflict": ["goes_quiet", "confronts"], "confidence": "low"}}, "conflicts": ["under_pressure"]}
    check = iv.choose(person(evidence=[{"source": "scene"}], profile=conflict), T, {}, True, [])
    assert check.method == "check" and [o for o, _ in check.options] == ["goes_quiet", "confronts", "depends"]
    why = iv.choose(person(evidence=[{"source": "scene"}]), T, {"ravi": {"sheet": T}}, True, [])
    assert why.method == "why" and why.options == []
    moment = iv.choose(person(evidence=[{"source": "scene"}], scenes=[{"text": "a"}, {"text": "b"}], profile={"lenses": {"wants": {"label": "money", "confidence": "high"}}}), T, {}, True, [])
    assert moment.method == "moment"
    guess = iv.choose(person(evidence=[{"source": "answer"}], profile={"lenses": {"wants": {"label": "money", "confidence": "high"}}}, quiet={"s1"}), T, {}, True, [])
    assert guess.method == "what_would" and [o for o, _ in guess.options] == list(REACTIONS)
    tap = iv.choose(person(evidence=[{"source": "answer"}], scenes=[{"id": "x", "date": ago(1), "text": "didn't answer for a week"}],
                           profile={"lenses": {"wants": {"label": "money", "confidence": "high"}}}), T, {}, True, [])
    assert tap.method == "quick_tap" and tap.specific and tap.text.startswith("Thinking of when Ravi Kumar didn't answer for a week: ")
    assert iv.choose(person(), T, {"ravi": {"rest": ago(-3)}}, True, []) is None and iv.choose(person(), T, {"ravi": {"on": T}}, True, []) is None


def test_compare_two_comes_once_a_week_and_the_budget_holds():
    a = person(evidence=[{"source": "answer"}], profile={"lenses": {"wants": {"label": "money", "confidence": "high"}}})
    b = person(evidence=[{"source": "answer"}], profile={"lenses": {"wants": {"label": "money", "confidence": "high"}}}, cid="sam", name="Sam")
    q = iv.choose(a, T, {}, False, [a, b])
    assert q.method == "compare" and q.other["id"] == "sam" and "compare Ravi Kumar and Sam:" in q.text
    plan = iv.plan([a, b, person(cid="new1", name="N1"), person(cid="new2", name="N2")], T, {}, False)
    assert len(plan) == iv.PER_DAY and sum(1 for x in plan if x.method == "compare") <= 1


def test_the_sketch_says_only_what_is_fairly_sure():
    text = iv.sketch("Ravi", PROFILE)
    assert text.startswith("Ravi goes quiet under bad news, is moved by money and avoids the blame.")
    assert "Reach them by message (answers 80%), best 18:00–19:00." in text and "Kept 2 of 6 promised dates, about 4.0 days late otherwise." in text
    assert iv.sketch("Sam", None) == "Not much is known about Sam yet."


# --- how will they take it ------------------------------------------------------------------------------------------------


async def test_a_prediction_is_three_votes_moves_from_the_playbook_and_a_draft():
    data = FakeData()
    data.tables = {"characters": [RAVI], "evidence": [
        {"id": "e1", "characterId": "ravi", "lens": "under_pressure", "label": "goes_quiet", "source": "scene", "date": ago(5), "quote": "didn't answer for a week"}]}
    model = Script({"reaction": "goes_quiet"}, {"reaction": "confronts"}, {"reaction": "goes_quiet"},
                   {"message": "The bank wants the 2023 ITR as well. Can you tell me by when you'll have it? I'll call you this evening."})
    out = await predict(data, runners_from(model), None, None, RAVI, "The bank wants the 2023 ITR as well.", LOAN, T)
    assert out["label"] == "goes_quiet" and out["sure"] == 0.67 and out["votes"] == {"goes_quiet": 2, "confronts": 1}
    assert out["moves"][0].startswith("Send the one-line fact first") and out["channel"] == "message" and out["bestHours"] == ["18:00–19:00"]
    assert "2023 ITR" in out["message"] and out["sketch"].startswith("Ravi Kumar goes quiet")
    assert "didn't answer for a week" in model.asked[0][-1] and "under pressure: goes quiet under bad news (high, 4 pieces)" in model.asked[0][-1]
    table, kept = data.saved[-1]
    assert table == "evidence" and kept["source"] == "prediction" and kept["predictedBy"] == "model" and kept["verdict"] == "pending" and kept["storyId"] == "s1"

    data.tables["evidence"].append(kept)
    done = await settle(data, RAVI, "goes_quiet", T, "s1")
    assert done["settled"] == ("right", "model", "goes_quiet")
    scored, new = [x[1] for x in data.saved[-2:]]
    assert scored["verdict"] == "right" and new["source"] == "answer" and new["label"] == "goes_quiet" and new["specific"] is True


async def test_the_decision_model_answers_with_probabilities_when_it_is_on():
    data = FakeData()
    data.tables = {"characters": [RAVI]}
    log: list = []
    out = await predict(data, runners_from(Script({"message": "Short and plain."})), FakeDecisions(log), "jev", RAVI, "The bank wants more.", None, T)
    assert out["label"] == REACTIONS[0] and out["probabilities"][REACTIONS[0]] == 0.9 and log[0]["questions"]["reaction"]["type"] == "choice"


# --- on the phone ---------------------------------------------------------------------------------------------------------


async def test_the_interviewer_asks_on_the_workspace_bot_and_a_tap_becomes_evidence(rig):
    await connect(rig)
    await choose(rig, agent=STORIES)
    rig.data.tables = {"stories": [LOAN], "chapters": [], "tasks": [], "follow-ups": [], "characters": [{**RAVI, "profile": None}],
                       "parts": [{"id": "p1", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "part": "agent"}], "evidence": [], "scenes": []}
    bot = await connect_bot(rig, "111:pe", 601, workspace="personal", role="planner")
    sent = (await rig.client.post("/v1/agent/editor/run", json={}, headers=headers())).json()["sent"]
    assert {"workspace": "personal", "person": "ravi", "method": "sheet", "lens": "wants"} in sent
    text = next(t for _, t in rig.telegram.sent if "Ravi Kumar" in t and "want most" in t)
    assert "🎭" in text and "Home loan" in text
    buttons = [b["callback_data"] for k in rig.telegram.keyboards if k for row in k for b in row if b["callback_data"].startswith("iv:")]
    assert buttons[0].startswith("iv:0:") and any(b.startswith("iv:x:") for b in buttons)
    await press(rig, bot, buttons[0], 2)
    table, record = rig.data.saved[-1]
    assert table == "evidence" and record["lens"] == "wants" and record["label"] == "money" and record["method"] == "sheet" and record["storyId"] == "s1"
    assert (await rig.client.post("/v1/agent/editor/run", json={}, headers=headers())).json()["sent"] == []  # once a day


async def test_breaking_news_through_the_story_editor(rig):
    rig.data.tables = {"stories": [LOAN], "characters": [RAVI], "parts": [{"id": "p1", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "knownAs": ["the agent"]}], "evidence": []}
    await connect(rig)
    await choose(rig, agent=STORIES)
    rig.script += [ChatResult("", [ToolCall("a", "how_will_they_take_it", {"person": "the agent", "news": "The bank wants one more document."})]),
                   *[ChatResult('{"reaction": "goes_quiet"}')] * 3, ChatResult('{"message": "The bank wants one more document. When can you get it?"}'), ChatResult("He'll likely go quiet.")]
    await rig.client.post(f"{STORIES}/chat", json={"message": "How will the agent take it if I tell him the bank wants one more document?"}, headers=headers())
    out = json.loads(rig.llm_log[-1]["last"])
    assert out["person"] == "Ravi Kumar" and out["label"] == "goes_quiet" and out["sure"] == 1.0 and out["message"].startswith("The bank wants")
    rig.script += [ChatResult("", [ToolCall("a", "how_they_took_it", {"person": "Ravi Kumar", "reaction": "confronts"})]), ChatResult("Noted.")]
    rig.data.tables["evidence"] = [x[1] for x in rig.data.saved if x[0] == "evidence"]
    await rig.client.post(f"{STORIES}/chat", json={"message": "He actually shouted at me"}, headers=headers())
    said = json.loads(rig.llm_log[-1]["last"])
    assert said["prediction"] == "my guess “goes quiet under bad news” was wrong"


async def test_people_from_a_news_item_and_a_comparison_become_cards(rig):
    rig.data.tables = {"stories": [COURT], "characters": [RAVI, {"id": "meera", "name": "Meera Rao", "home": "sb"}],
                       "parts": [{"id": "p", "characterId": "ravi", "storyId": "s3", "workspace": "sb"}, {"id": "q", "characterId": "meera", "storyId": "s3", "workspace": "sb"}]}
    await connect(rig)
    await choose(rig, agent=STORIES)
    rig.script += [ChatResult(json.dumps({"people": [{"name": "R. Nayak", "title": "Assistant Labour Commissioner", "phones": ["08472-263401"], "emails": [], "quote": "R. Nayak"}]}))]
    news = "Assistant Labour Commissioner R. Nayak said the inspection would resume. Call 08472-263401."
    r = await rig.client.post("/v1/agent/cast/from-text", json={"text": news, "story": "Gulbarga"}, headers=headers())
    assert r.status_code == 200, r.text
    card = r.json()["cards"][0]
    assert card["lines"][0] == "R. Nayak, Assistant Labour Commissioner · 08472-263401" and "Each joins “Gulbarga SMPL”" in card["lines"][1]
    await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    table, body = rig.data.saved[-1]
    assert table == "cast" and body["storyId"] == "s3" and body["phones"] == ["08472-263401"] and body["part"] == "Assistant Labour Commissioner"

    answer = "Ravi promises everything and delivers late; Meera promises little and always delivers."
    rig.script += [ChatResult("", [ToolCall("a", "compare_people", {"first": "Ravi Kumar", "second": "Meera Rao", "text": answer})]),
                   ChatResult(json.dumps({"name": "Promises", "pole_a": "promises everything", "pole_b": "promises little", "first_sits": "a", "second_sits": "b"})), ChatResult("ok")]
    out = (await rig.client.post(f"{STORIES}/chat", json={"message": f"compare Ravi and Meera: {answer}"}, headers=headers())).json()
    card = next(f for f in out["found"] if f.get("kind") == "proposal")
    assert card["lines"] == ["A lens of your own: Promises: promises everything ↔ promises little", "Ravi Kumar: promises everything", "Meera Rao: promises little"]
    await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    lens = next(b for t, b in rig.data.saved if t == "lenses")
    marks = [b for t, b in rig.data.saved if t == "evidence"]
    assert [(m["characterId"], m["lens"], m["label"]) for m in marks] == [("ravi", f"custom:{lens['id']}", "a"), ("meera", f"custom:{lens['id']}", "b")]


async def test_a_workspace_reads_only_its_own_evidence_but_the_profile_travels():
    data = FakeData()
    data.tables = {"stories": [LOAN, COURT], "characters": [RAVI],
                   "parts": [{"id": "p1", "characterId": "ravi", "storyId": "s1", "workspace": "personal"}, {"id": "p2", "characterId": "ravi", "storyId": "s3", "workspace": "sb"}],
                   "evidence": [{"id": "e1", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "lens": "under_pressure", "label": "goes_quiet", "source": "scene", "quote": "PEX-went silent", "date": ago(3)},
                                {"id": "e2", "characterId": "ravi", "storyId": "s3", "workspace": "sb", "lens": "moved_by", "label": "authority", "source": "scene", "quote": "SBX-the order moved him", "date": ago(2)}],
                   "lenses": [{"id": "l1", "name": "Promises"}]}
    sb = ScopedData(data, "sb")
    assert [e["id"] for e in await sb.records("evidence")] == ["e2"] and (await sb.records("characters"))[0]["profile"] == PROFILE
    assert [x["id"] for x in await sb.records("lenses")] == ["l1"]
    model = Script(*[{"reaction": "goes_quiet"}] * 3, {"message": "ok"})
    await predict(sb, runners_from(model), None, None, RAVI, "The order is delayed.", COURT, T)
    sent = "\n".join(m for call in model.asked for m in call)
    assert "SBX-" in sent
    assert_clean(sent, "sb", "a prediction made from S&B")
    with pytest.raises(AiError):
        await sb.save_record("evidence", {"characterId": "ravi", "storyId": "s1", "lens": "wants", "label": "money"})  # a personal story
