import json

import pytest
from app.application.scope import ScopedData
from app.application.stories import card_lines, resolve_people, to_body
from app.domain.aijobs import JobResult
from app.domain.llm import AiError, ChatResult, ToolCall

from .conftest import FakeData, headers
from .test_agent_api import choose, connect
from .test_channels import send
from .test_leaks import assert_clean, connect_bot
from .test_orchestrator import team_ready

STORIES = "/v1/agent/agents/stories"
LOAN = {"id": "s1", "title": "Home loan", "workspace": "personal", "state": "writing", "checkpoints": [{"id": "k1", "title": "Documents in"}]}
SITE = {"id": "s2", "title": "Client website", "workspace": "fsa", "state": "writing", "checkpoints": []}
COURT = {"id": "s3", "title": "Gulbarga SMPL", "workspace": "sb", "state": "writing", "checkpoints": []}
RAVI = {"id": "ravi", "name": "Ravi Kumar", "home": "personal", "phones": ["+919845012345"], "organisation": "LoanMart"}
MEENA = {"id": "meena", "name": "Meena", "home": "fsa", "phones": []}


def cast_data(data: FakeData):
    data.tables = {
        "stories": [LOAN, SITE, COURT],
        "characters": [RAVI, MEENA],
        "parts": [
            {"id": "p1", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "part": "agent", "knownAs": ["the agent"]},
            {"id": "p2", "characterId": "ravi", "storyId": "s3", "workspace": "sb", "part": "witness", "knownAs": []},
            {"id": "p3", "characterId": "meena", "storyId": "s2", "workspace": "fsa", "part": "client", "knownAs": []},
        ],
        "scenes": [
            {"id": "x1", "characterId": "ravi", "storyId": "s1", "workspace": "personal", "date": "2026-11-10", "text": "PEX-scene: late with the news"},
            {"id": "x2", "characterId": "ravi", "storyId": "s3", "workspace": "sb", "date": "2026-11-11", "text": "SBX-scene: agreed to testify"},
            {"id": "x3", "characterId": "meena", "storyId": "s2", "workspace": "fsa", "date": "2026-11-09", "text": "FSX-scene: wants it by Friday"},
        ],
    }


# --- who a chapter's names are ----------------------------------------------------------------------------------


def test_names_are_matched_in_the_story_first_then_the_cast_then_made_new():
    cast = [{"characterId": "ravi", "name": "Ravi Kumar", "knownAs": ["the agent"], "part": "agent"}]
    chars = [RAVI, MEENA, {"id": "m2", "name": "Meena", "home": "sb"}]
    ids = iter(["n1", "n2"])
    out = resolve_people([{"name": "The agent", "scene": "called"}, {"name": "Ravi Kumar"}, {"name": "Meena"}, {"name": "Former HR", "scene": "silent"}],
                         cast, chars, lambda: next(ids))
    assert [(x["name"], x.get("characterId"), x["known"]) for x in out] == [
        ("The agent", "ravi", "story"), ("Ravi Kumar", "ravi", "story"), ("Meena", None, "new"), ("Former HR", None, "new")]  # two Meenas: not guessed
    assert out[2]["newId"] == "n1" and out[3]["create"] is True
    body = to_body({"beat": "note", "people": []}, "x", title=None, day=None, source="app", new_id=lambda: "c", people=out)
    assert "known" not in body["people"][0] and body["people"][0]["characterId"] == "ravi"
    lines = card_lines(LOAN, body, JobResult({}, True, 1), out)
    assert "Cast: The agent (Ravi Kumar): called" in lines and "New in your cast: Former HR: silent" in lines and "Cast: Ravi Kumar" in lines


# --- workspaces --------------------------------------------------------------------------------------------------


async def test_a_workspace_sees_the_people_in_its_stories_but_only_its_own_scenes():
    data = FakeData()
    cast_data(data)
    sb = ScopedData(data, "sb")
    assert [c["id"] for c in await sb.records("characters")] == ["ravi"]  # he has a part in an S&B story; Meena doesn't
    assert [p["id"] for p in await sb.records("parts")] == ["p2"] and [s["id"] for s in await sb.records("scenes")] == ["x2"]
    fsa = ScopedData(data, "fsa")
    assert [c["id"] for c in await fsa.records("characters")] == ["meena"]
    with pytest.raises(AiError):
        await sb.join_cast({"name": "X", "storyId": "s2"})  # an FSA story
    with pytest.raises(AiError):
        await sb.join_cast({"name": "Meena", "characterId": "meena"})  # not in S&B's cast
    await sb.join_cast({"name": "New person", "storyId": "s3", "home": "fsa"})
    assert data.saved[-1] == ("cast", {"name": "New person", "storyId": "s3", "home": "sb"})


# --- the tools ------------------------------------------------------------------------------------------------------


async def chat(rig, calls, message="x"):
    rig.script += [ChatResult("", [ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(calls)]), ChatResult("Done.")]
    r = await rig.client.post(f"{STORIES}/chat", json={"message": message}, headers=headers())
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_chapters_people_are_the_cast_on_its_card(rig):
    cast_data(rig.data)
    await connect(rig)
    await choose(rig, agent=STORIES)
    notes = {"beat": "setback", "people": [{"name": "The agent", "scene": "late again", "quote": "The agent called late again"},
                                           {"name": "the bank manager", "scene": "wants ITRs", "quote": "the bank manager wants ITRs"}]}
    prose = "Home loan: The agent called late again, and the bank manager wants ITRs."
    rig.script += [ChatResult("", [ToolCall("c1", "write_chapter", {"text": prose})]), ChatResult(json.dumps(notes)), ChatResult("Ready.")]
    out = (await rig.client.post(f"{STORIES}/chat", json={"message": prose}, headers=headers())).json()
    card = next(f for f in out["found"] if f.get("kind") == "proposal")
    assert "Cast: The agent (Ravi Kumar): late again" in card["lines"] and "New in your cast: the bank manager: wants ITRs" in card["lines"]
    assert "Ravi Kumar (called: the agent)" in rig.llm_log[1]["last"]  # the job is told who is already in the story
    await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    people = rig.data.saved[-1][1]["people"]
    assert people[0]["characterId"] == "ravi" and people[1]["create"] is True and people[1]["newId"]


async def test_adding_someone_from_a_google_contact_and_finding_them_by_number(rig):
    cast_data(rig.data)
    rig.data.people = [{"id": "g1", "name": "Priya Shah", "organisation": "Acme", "phones": [{"value": "+91 90000 11111", "type": "mobile"}],
                        "emails": [{"value": "priya@acme.in"}], "workspaces": ["fsa"]}]
    await connect(rig)
    await choose(rig, agent=STORIES)
    out = await chat(rig, [("add_to_cast", {"name": "Priya", "from_contact": "Priya Shah", "story": "Client website", "part": "client", "stance": "for_us"})])
    card = next(f for f in out["found"] if f.get("kind") == "proposal")
    assert card["lines"][0] == "From your Google contact “Priya Shah”" and "Part in “Client website”: client · for us" in card["lines"]
    await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    body = rig.data.saved[-1][1]
    assert body["phones"] == ["+91 90000 11111"] and body["googleContactId"] == "g1" and body["storyId"] == "s2" and body["organisation"] == "Acme"

    again = await chat(rig, [("add_to_cast", {"name": "R. Kumar", "phone": "98450 12345", "story": "Gulbarga SMPL"})])
    card = next(f for f in again["found"] if f.get("kind") == "proposal")
    assert card["title"] == "Cast: Ravi Kumar" and card["lines"][0] == "Already in your cast as Ravi Kumar (same number)"
    await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    assert rig.data.saved[-1][1]["characterId"] == "ravi"


async def test_reading_a_character_across_their_stories(rig):
    cast_data(rig.data)
    await connect(rig)
    await choose(rig, agent=STORIES)
    await chat(rig, [("read_character", {"character": "the agent"})])
    seen = json.loads(rig.llm_log[-1]["last"])
    assert seen["name"] == "Ravi Kumar" and {p["story"] for p in seen["parts"]} == {"Home loan", "Gulbarga SMPL"}
    assert [s["date"] for s in seen["scenes"]] == ["2026-11-11", "2026-11-10"]
    await chat(rig, [("story_cast", {"story": "Client website"})])
    assert [m["name"] for m in json.loads(rig.llm_log[-1]["last"])["cast"]] == ["Meena"]


# --- leaks ----------------------------------------------------------------------------------------------------------


async def test_a_workspace_bot_never_reads_another_workspaces_scenes(rig):
    """Ravi is in an S&B story and a personal one. The S&B bot may know who he is, never what he did in the personal story."""
    await team_ready(rig)
    cast_data(rig.data)
    general = await connect_bot(rig, "123:abc", 555, agent="stories")
    start = len(rig.llm_log)
    rig.script += [ChatResult("", [ToolCall("a", "read_character", {"character": "Ravi Kumar"}), ToolCall("b", "story_cast", {"story": "Client website"})]), ChatResult("ok")]
    await send(rig, general, "Tell me about Ravi", chat=555, update=2)
    everything = "\n".join(e["everything"] for e in rig.llm_log[start:])
    assert "PEX-scene" in everything and "SBX-scene" in everything  # the control: the general bot reads every scene

    sb = await connect_bot(rig, "456:def", 556, agent="stories", workspace="sb")
    start = len(rig.llm_log)
    rig.script += [ChatResult("", [ToolCall("a", "read_character", {"character": "Ravi Kumar"}), ToolCall("b", "read_character", {"character": "Meena"}),
                                   ToolCall("c", "story_cast", {"story": "Client website"})]), ChatResult("ok")]
    await send(rig, sb, "Tell me about Ravi", chat=556, update=2)
    seen = "\n".join(e["everything"] for e in rig.llm_log[start:])
    assert "SBX-scene" in seen and "Ravi Kumar" in seen
    assert_clean(seen, "sb", "the S&B story editor")
    assert "Meena" not in seen.split("Tell me about Ravi")[-1]  # FSA's client isn't in S&B's cast
