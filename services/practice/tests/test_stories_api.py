from app.domain.stories import Checkpoint, Story, current_checkpoint, insert_checkpoint

from tests.test_legal_api import hearing, open_case


def loan(**kw):
    return {
        "title": "Home loan", "workspace": "personal", "ending": "Disbursed before March, at 9% or less", "stakes": 5,
        "chance": 70, "pacing": "brisk",
        "checkpoints": [
            {"id": "k1", "title": "Pick a lender", "status": "reached"},
            {"id": "k2", "title": "Documents in"},
            {"id": "k3", "title": "Bank verification"},
            {"id": "k4", "title": "Sanction letter"},
        ],
        "bible": [{"key": "Amount", "value": " ₹25L "}, {"key": "  ", "value": "dropped"}],
        **kw,
    }


PROSE = ("The agent called at seven, sorry as always. The bank wants Form 16 for two years, not one. "
         "I'll write to HR on Monday. Something tells me they'll ask for the ITRs next.")


async def story(client, headers, id="s1", **kw):
    r = await client.put(f"/v1/stories/{id}", json=loan(**kw), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- the arc, as pure functions ------------------------------------------------------------------------------


def _story(*cps):
    return Story(id="s", user_id="u", created_at="2026-01-01T00:00:00.000Z", updated_at="2026-01-01T00:00:00.000Z",
                 title="t", checkpoints=list(cps))


def test_where_you_are_is_the_first_checkpoint_not_passed():
    s = _story(Checkpoint(id="a", title="A", status="reached"), Checkpoint(id="b", title="B", status="skipped"),
               Checkpoint(id="c", title="C"), Checkpoint(id="d", title="D"))
    assert current_checkpoint(s).id == "c"
    assert current_checkpoint(_story(Checkpoint(id="a", title="A", status="reached"))) is None


def test_a_twist_goes_in_just_before_where_the_story_is():
    arc = [Checkpoint(id="a", title="A", status="reached"), Checkpoint(id="b", title="B"), Checkpoint(id="c", title="C")]
    t = Checkpoint(id="t", title="T", added_by="twist")
    assert [c.id for c in insert_checkpoint(arc, t, None)] == ["a", "t", "b", "c"]
    assert [c.id for c in insert_checkpoint(arc, t, "c")] == ["a", "b", "t", "c"]
    done = [Checkpoint(id="a", title="A", status="reached")]
    assert [c.id for c in insert_checkpoint(done, t, None)] == ["a", "t"]


# --- the API -------------------------------------------------------------------------------------------------


async def test_a_story_is_saved_clean(client, alice):
    s = await story(client, alice)
    assert s["bible"] == [{"key": "Amount", "value": "₹25L"}]
    assert s["state"] == "writing" and s["pacing"] == "brisk"
    assert [c["status"] for c in s["checkpoints"]] == ["reached", "ahead", "ahead", "ahead"]
    bad = await client.put("/v1/stories/s2", json=loan(links=[{"kind": "task", "id": "t1"}]), headers=alice)
    assert bad.status_code == 422
    twice = await client.put("/v1/stories/s2", json=loan(checkpoints=[{"id": "x", "title": "A"}, {"id": "x", "title": "B"}]), headers=alice)
    assert twice.status_code == 422
    assert (await client.put("/v1/stories/s2", json=loan(stakes=9), headers=alice)).status_code == 422


async def test_writing_a_chapter_does_everything_in_one_step(client, alice):
    await story(client, alice)
    body = {
        "id": "ch1", "date": "2026-11-12", "title": "The Form 16 problem", "prose": PROSE, "beat": "setback",
        "source": "telegram", "chanceAfter": 60,
        "twists": [{"id": "tw1", "title": "Get old Form 16", "quote": "The bank wants Form 16 for two years"}],
        "steps": [
            {"id": "st1", "kind": "task", "title": "Email former HR for Form 16", "due": "2026-11-16", "quote": "I'll write to HR on Monday."},
            {"id": "st2", "kind": "followUp", "title": "Visit the old office", "due": "2026-11-18", "channel": "visit"},
        ],
        "hunches": [{"text": "They'll ask for the ITRs next", "quote": "they'll ask for the ITRs next"}],
        "people": [{"name": "Loan agent", "scene": "apologetic, the news is late again"}],
        "cliffhanger": "Will HR answer?",
        "waitingOn": {"on": "Former employer's HR", "until": "2026-11-18"},
    }
    r = await client.post("/v1/stories/s1/chapters/write", json=body, headers=alice)
    assert r.status_code == 200, r.text
    out = r.json()
    ch, st = out["chapter"], out["story"]
    assert ch["number"] == 1 and ch["prose"] == PROSE and ch["beat"] == "setback" and ch["chanceAfter"] == 60
    assert st["chance"] == 60 and st["waitingOn"] == {"on": "Former employer's HR", "until": "2026-11-18"}
    # the twist is now the checkpoint right where the story is
    assert [c["id"] for c in st["checkpoints"]] == ["k1", "tw1", "k2", "k3", "k4"]
    assert st["checkpoints"][1]["addedBy"] == "twist" and st["checkpoints"][1]["chapterId"] == "ch1"
    # the work is ordinary tasks and calls pointing at the story, in its workspace
    task = (await client.get("/v1/tasks/st1", headers=alice)).json()
    assert task["related"] == {"kind": "story", "id": "s1"} and task["workspace"] == "personal" and task["dueDate"] == "2026-11-16"
    call = (await client.get("/v1/follow-ups/st2", headers=alice)).json()
    assert call["subject"] == {"kind": "story", "id": "s1"} and call["channel"] == "visit" and call["due"] == {"date": "2026-11-18"}
    kinds = [n["kind"] for n in ch["notes"]]
    assert kinds == ["beat", "twist", "step", "step", "hunch", "person", "chance", "cliffhanger"]
    assert ch["notes"][2]["ref"] == {"kind": "task", "id": "st1"} and ch["notes"][2]["quote"] == "I'll write to HR on Monday."

    # writing it again (a retry) doubles nothing and keeps its number
    again = await client.post("/v1/stories/s1/chapters/write", json=body, headers=alice)
    assert again.status_code == 200 and again.json()["chapter"]["number"] == 1
    assert len([c for c in again.json()["story"]["checkpoints"] if c["id"] == "tw1"]) == 1
    assert len((await client.get("/v1/stories/s1/chapters", headers=alice)).json()["items"]) == 1

    # the next chapter reaches a checkpoint, answers the cliffhanger and lifts the wait
    r2 = await client.post("/v1/stories/s1/chapters/write", headers=alice, json={
        "prose": "HR sent both years by noon.", "beat": "progress", "date": "2026-11-17",
        "checkpoints": [{"id": "tw1", "status": "reached"}], "answers": ["ch1"], "clearWaiting": True})
    assert r2.status_code == 200, r2.text
    assert r2.json()["chapter"]["number"] == 2 and "waitingOn" not in r2.json()["story"]
    tl = (await client.get("/v1/stories/s1/timeline", headers=alice)).json()
    assert tl["here"]["id"] == "k2" and tl["progress"] == 0.4
    assert [e["chapter"]["number"] for e in tl["entries"]] == [1, 2]
    assert tl["cliffhangers"] == []
    assert [h["text"] for h in tl["hunches"]] == ["They'll ask for the ITRs next"]
    assert {t["id"] for t in tl["openTasks"]} == {"st1"} and {f["id"] for f in tl["openFollowUps"]} == {"st2"}


async def test_a_chapter_is_checked_against_its_story(client, alice, bob):
    await story(client, alice)
    r = await client.post("/v1/stories/s1/chapters/write", json={"prose": "   "}, headers=alice)
    assert r.status_code == 422
    r = await client.post("/v1/stories/s1/chapters/write", json={"prose": "x", "checkpoints": [{"id": "nope", "status": "reached"}]}, headers=alice)
    assert r.status_code == 422
    r = await client.post("/v1/stories/s1/chapters/write", json={"prose": "x", "twists": [{"title": "T", "before": "nope"}]}, headers=alice)
    assert r.status_code == 422
    assert (await client.post("/v1/stories/s1/chapters/write", json={"prose": "x"}, headers=bob)).status_code == 404
    assert (await client.post("/v1/stories/zzz/chapters/write", json={"prose": "x"}, headers=alice)).status_code == 404
    # a plain chapter through the generic endpoint needs a real story, and gets the next number
    assert (await client.put("/v1/chapters/c1", json={"storyId": "zzz", "date": "2026-11-12", "prose": "x"}, headers=alice)).status_code == 422
    c = (await client.put("/v1/chapters/c1", json={"storyId": "s1", "date": "2026-11-12", "prose": "x", "number": 99}, headers=alice)).json()
    assert c["number"] == 1


async def test_recorded_hearings_of_linked_cases_are_chapters_by_themselves(client, alice):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "h1", "c1", "2026-11-10")
    await hearing(client, alice, "h2", "c1", "2026-12-20")  # not written up: not part of the story yet
    await client.post("/v1/hearings/h1/record", headers=alice, json={"summary": "Order reserved", "outcome": {"status": "done"}})
    await story(client, alice, workspace="sb", links=[{"kind": "case", "id": "c1"}])
    await client.post("/v1/stories/s1/chapters/write", json={"prose": "Applied for the order.", "date": "2026-11-01"}, headers=alice)
    await client.post("/v1/stories/s1/chapters/write", json={"prose": "Registry wants a notice.", "date": "2026-11-12"}, headers=alice)
    tl = (await client.get("/v1/stories/s1/timeline", headers=alice)).json()
    assert [(e["kind"], e["date"]) for e in tl["entries"]] == [("chapter", "2026-11-01"), ("hearing", "2026-11-10"), ("chapter", "2026-11-12")]
    assert tl["entries"][1]["hearing"]["summary"] == "Order reserved"


async def test_a_story_sits_in_the_thread_of_what_it_covers(client, alice):
    await open_case(client, alice, id="c1")
    await story(client, alice, workspace="sb", links=[{"kind": "case", "id": "c1"}])
    await client.post("/v1/stories/s1/chapters/write", json={"id": "ch1", "prose": "x", "steps": [{"id": "t1", "title": "Do it"}]}, headers=alice)
    r = await client.get("/v1/relations/thread", params={"kind": "story", "id": "s1"}, headers=alice)
    assert r.status_code == 200, r.text
    members = {(m["kind"], m["id"]) for m in r.json()["members"]}
    assert {("case", "c1"), ("chapter", "ch1"), ("task", "t1")} <= members


async def test_deleting_a_story_deletes_its_chapters_but_keeps_its_work(client, alice):
    await story(client, alice)
    await client.post("/v1/stories/s1/chapters/write", json={"id": "ch1", "prose": "x", "steps": [{"id": "t1", "title": "Do it"}]}, headers=alice)
    assert (await client.delete("/v1/stories/s1", headers=alice)).status_code == 204
    assert (await client.get("/v1/chapters/ch1", headers=alice)).status_code == 404
    assert (await client.get("/v1/tasks/t1", headers=alice)).status_code == 200
    hist = (await client.get("/v1/activity", params={"kind": "story", "id": "s1"}, headers=alice)).json()["items"]
    assert any(h["action"] == "deleted" for h in hist)


async def test_arc_templates(client, alice):
    r = await client.put("/v1/arc-templates/a1", json={"name": "Loan", "workspace": "personal",
                                                       "checkpoints": ["Pick a lender", " ", "Documents in", "Pick a lender"]}, headers=alice)
    assert r.status_code == 200, r.text
    assert r.json()["checkpoints"] == ["Pick a lender", "Documents in"]


async def test_the_timeline_shows_how_the_chance_moved(client, alice):
    await story(client, alice)
    await client.post("/v1/stories/s1/chapters/write", json={"prose": "a", "date": "2026-11-01", "chanceAfter": 65}, headers=alice)
    await client.post("/v1/stories/s1/chapters/write", json={"prose": "b", "date": "2026-11-05"}, headers=alice)
    await client.post("/v1/stories/s1/chapters/write", json={"prose": "c", "date": "2026-11-09", "chanceAfter": 55}, headers=alice)
    tl = (await client.get("/v1/stories/s1/timeline", headers=alice)).json()
    assert [(t["number"], t["chance"]) for t in tl["chanceTrend"]] == [(1, 65), (3, 55)]
