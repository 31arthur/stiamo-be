from app.domain.cast import Character, Part, find_in_story, normalize_phone, same_name

from tests.test_stories_api import story


def test_one_number_written_many_ways_is_one_number():
    for raw in ("9845012345", "098450 12345", "+91 98450-12345", "91 9845012345", "0091 98450 12345"):
        assert normalize_phone(raw) == "+919845012345", raw
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"
    assert normalize_phone("12-34") is None and normalize_phone(None) is None


def test_a_name_in_a_chapter_finds_who_it_means_in_that_story():
    def char(i, name):
        return Character(id=i, user_id="u", created_at="x", updated_at="x", name=name)

    def part(c, *known):
        return Part(id=f"p{c}", user_id="u", created_at="x", updated_at="x", character_id=c, story_id="s", known_as=list(known))

    chars = {"a": char("a", "Ravi Kumar"), "b": char("b", "Meena")}
    parts = [part("a", "the agent"), part("b")]
    assert find_in_story("The agent", parts, chars).id == "a"
    assert find_in_story("ravi kumar", parts, chars).id == "a"
    assert find_in_story("meena", parts, chars).id == "b"
    assert find_in_story("HR", parts, chars) is None
    assert same_name("the Agent", "agent") and not same_name("", "")


async def test_joining_the_cast_finds_people_by_phone_before_making_them(client, alice):
    await story(client, alice)  # s1: a personal story
    await story(client, alice, id="s2", title="Gulbarga SMPL", workspace="sb", checkpoints=[])
    first = await client.post("/v1/characters/join", headers=alice, json={
        "id": "ch1", "name": "Ravi Kumar", "phones": ["098450 12345"], "storyId": "s1", "part": "agent", "knownAs": ["the agent"]})
    assert first.status_code == 200, first.text
    out = first.json()
    assert out["matched"] == "new" and out["character"]["phones"] == ["+919845012345"] and out["character"]["home"] == "personal"
    assert out["part"]["workspace"] == "personal" and out["part"]["knownAs"] == ["the agent"] and out["part"]["part"] == "agent"

    # the same number in another story, written another way: the same person, a second part, in that story's workspace
    again = (await client.post("/v1/characters/join", headers=alice, json={
        "name": "Mr Kumar", "phones": ["+91 98450-12345"], "organisation": "LoanMart", "storyId": "s2", "part": "witness"})).json()
    assert again["matched"] == "phone" and again["character"]["id"] == "ch1"
    assert again["character"]["name"] == "Ravi Kumar" and again["character"]["organisation"] == "LoanMart"  # added, not overwritten
    assert again["part"]["workspace"] == "sb" and again["part"]["knownAs"] == ["Mr Kumar"]

    # retrying the first join changes nothing
    retry = (await client.post("/v1/characters/join", headers=alice, json={"id": "ch1", "name": "Ravi Kumar", "storyId": "s1"})).json()
    assert retry["matched"] == "id" and retry["part"]["id"] == out["part"]["id"]
    assert len((await client.get("/v1/characters", headers=alice)).json()["items"]) == 1

    found = (await client.get("/v1/characters/by-phone", params={"phone": "9845012345"}, headers=alice)).json()
    assert found["character"]["id"] == "ch1"
    view = (await client.get("/v1/characters/ch1/view", headers=alice)).json()
    assert {p["story"]["title"] for p in view["parts"]} == {"Home loan", "Gulbarga SMPL"}


async def test_a_number_belongs_to_one_character(client, alice):
    await client.put("/v1/characters/a", json={"name": "A", "phones": ["9845012345"]}, headers=alice)
    clash = await client.put("/v1/characters/b", json={"name": "B", "phones": ["+919845012345"]}, headers=alice)
    assert clash.status_code == 409 and "A's" in clash.json()["error"]["message"]
    assert (await client.put("/v1/characters/a", json={"name": "A", "phones": ["9845012345", "bad"]}, headers=alice)).json()["phones"] == ["+919845012345"]


async def test_a_chapters_people_join_the_cast_and_a_call_to_them_carries_their_number(client, alice):
    await story(client, alice)
    await client.post("/v1/characters/join", headers=alice, json={"id": "ag", "name": "Ravi", "phones": ["9845012345"], "storyId": "s1", "knownAs": ["the agent"]})
    r = await client.post("/v1/stories/s1/chapters/write", headers=alice, json={
        "id": "c1", "date": "2026-11-12", "prose": "The agent called at seven. HR in my old office hasn't replied.",
        "people": [
            {"name": "The agent", "characterId": "ag", "scene": "called, apologetic", "quote": "The agent called at seven"},
            {"name": "Former HR", "create": True, "newId": "hr", "part": "gatekeeper", "scene": "hasn't replied", "quote": "HR in my old office hasn't replied"},
            {"name": "My neighbour"},
        ],
        "steps": [
            {"id": "f1", "kind": "followUp", "title": "Call the agent back", "contact": "the agent", "channel": "call"},
            {"id": "t1", "kind": "task", "title": "Email HR", "contact": "Former HR"},
        ]})
    assert r.status_code == 200, r.text
    ch = r.json()["chapter"]
    assert [(p["name"], p.get("characterId")) for p in ch["people"]] == [("The agent", "ag"), ("Former HR", "hr"), ("My neighbour", None)]
    call = (await client.get("/v1/follow-ups/f1", headers=alice)).json()
    assert call["characterId"] == "ag" and call["contact"] == "Ravi · +919845012345"  # the dial button's form
    task = (await client.get("/v1/tasks/t1", headers=alice)).json()
    assert task["characterId"] == "hr" and task["contact"] == "Former HR"  # no number known: the name as written
    cast = (await client.get("/v1/stories/s1/cast", headers=alice)).json()["items"]
    assert {(c["character"]["name"], c["part"]["part"]) for c in cast} == {("Ravi", ""), ("Former HR", "gatekeeper")}
    scenes = (await client.get("/v1/characters/ag/view", headers=alice)).json()["scenes"]
    assert [(s["text"], s["quote"], s["chapterId"], s["workspace"]) for s in scenes] == [("called, apologetic", "The agent called at seven", "c1", "personal")]
    # writing it again makes no second character, part or scene
    await client.post("/v1/stories/s1/chapters/write", headers=alice, json={
        "id": "c1", "prose": "x", "people": [{"name": "Former HR", "create": True, "newId": "hr", "scene": "hasn't replied"}]})
    assert len((await client.get("/v1/characters", headers=alice)).json()["items"]) == 2
    assert len((await client.get("/v1/scenes", headers=alice)).json()["items"]) == 2
    tl = (await client.get("/v1/stories/s1/timeline", headers=alice)).json()
    assert len(tl["cast"]) == 2
    thread = (await client.get("/v1/relations/thread", params={"kind": "character", "id": "ag"}, headers=alice)).json()
    assert {"kind": "followUp", "id": "f1"} in thread["members"]


async def test_merging_two_records_of_one_person(client, alice):
    await story(client, alice)
    await story(client, alice, id="s2", title="Car loan", checkpoints=[])
    await client.post("/v1/characters/join", headers=alice, json={"id": "a", "name": "Ravi", "phones": ["9845012345"], "storyId": "s1"})
    await client.post("/v1/characters/join", headers=alice, json={"id": "b", "name": "Ravi K", "phones": ["9000000001"], "storyId": "s1", "part": "agent"})
    await client.post("/v1/characters/join", headers=alice, json={"id": "b", "name": "Ravi K", "storyId": "s2"})
    await client.put("/v1/tasks/t1", json={"title": "Call him", "characterId": "b"}, headers=alice)
    r = await client.post("/v1/characters/b/merge", json={"into": "a"}, headers=alice)
    assert r.status_code == 200, r.text
    assert r.json()["phones"] == ["+919845012345", "+919000000001"]
    assert (await client.get("/v1/characters/b", headers=alice)).status_code == 404
    parts = (await client.get("/v1/characters/a/view", headers=alice)).json()["parts"]
    assert sorted(p["storyId"] for p in parts) == ["s1", "s2"]
    s1 = next(p for p in parts if p["storyId"] == "s1")
    assert s1["part"] == "agent" and "Ravi K" in s1["knownAs"]
    assert (await client.get("/v1/tasks/t1", headers=alice)).json()["characterId"] == "a"
    assert (await client.post("/v1/characters/a/merge", json={"into": "a"}, headers=alice)).status_code == 422


async def test_parts_and_scenes_take_their_storys_workspace_and_go_with_it(client, alice, bob):
    await story(client, alice, workspace="sb")
    await client.put("/v1/characters/a", json={"name": "A"}, headers=alice)
    p = (await client.put("/v1/parts/p1", json={"characterId": "a", "storyId": "s1", "workspace": "personal"}, headers=alice)).json()
    assert p["workspace"] == "sb"
    assert (await client.put("/v1/parts/p2", json={"characterId": "a", "storyId": "s1"}, headers=alice)).status_code == 409
    assert (await client.put("/v1/parts/p3", json={"characterId": "zz", "storyId": "s1"}, headers=alice)).status_code == 422
    sc = (await client.put("/v1/scenes/x1", json={"characterId": "a", "storyId": "s1", "date": "2026-11-12", "text": "said yes"}, headers=alice)).json()
    assert sc["workspace"] == "sb"
    assert (await client.get("/v1/characters/a", headers=bob)).status_code == 404
    await client.delete("/v1/stories/s1", headers=alice)
    assert (await client.get("/v1/parts/p1", headers=alice)).status_code == 404 and (await client.get("/v1/scenes/x1", headers=alice)).status_code == 404
    assert (await client.get("/v1/characters/a", headers=alice)).status_code == 200  # still in the cast
    await client.put("/v1/tasks/t1", json={"title": "Call", "characterId": "a"}, headers=alice)
    await client.delete("/v1/characters/a", headers=alice)
    assert "characterId" not in (await client.get("/v1/tasks/t1", headers=alice)).json()
