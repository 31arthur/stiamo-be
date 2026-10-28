from app.domain.agenda import clamp_block


def test_blocks_stay_inside_the_visible_day():
    assert clamp_block(0, 30) == (360, 30)
    assert clamp_block(1430, 60) == (1380, 60)
    assert clamp_block(600, 1) == (600, 5)
    assert clamp_block(600, 99999) == (360, 1080)


async def seed(client, h):
    """A small practice: a case with a hearing and a plan, a client project, a goal, and a hand-made link."""
    await client.put("/v1/cases/c1", json={"organization": "SA", "title": "Wages — Site 4", "filedOn": "2026-01-10"}, headers=h)
    await client.put("/v1/counsel/k1", json={"kind": "firm", "name": "Iyer LLP"}, headers=h)
    await client.put("/v1/cases/c1/engagement", json={"engagement": {"counselId": "k1"}}, headers=h)
    await client.put("/v1/hearings/h1", json={"caseId": "c1", "date": "2026-03-10", "time": 630, "purpose": "Arguments"}, headers=h)
    r = await client.post("/v1/hearings/h1/record", headers=h, json={
        "summary": "Adjourned", "outcome": {"status": "not_done"}, "next": {"date": "2026-04-14", "purpose": "Reply"},
        "plan": {"title": "Prepare reply"}, "notes": {"actions": [{"text": "Draft reply"}]}})
    return r.json()


async def test_thread_walks_up_and_down_and_includes_peers(client, alice):
    rec = await seed(client, alice)
    plan_id, next_id = rec["plan"]["id"], rec["nextHearing"]["id"]
    step = (await client.get(f"/v1/plans/{plan_id}/steps", headers=alice)).json()["items"][0]
    await client.put("/v1/clients/k9", json={"kind": "company", "name": "Acme"}, headers=alice)
    await client.post("/v1/links", headers=alice, json={"a": {"kind": "case", "id": "c1"}, "b": {"kind": "client", "id": "k9"},
                                                        "titles": {"a": "Case", "b": "Acme"}})

    r = await client.get("/v1/relations/thread", params={"kind": "case", "id": "c1"}, headers=alice)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["ancestors"] == [{"ref": {"kind": "counsel", "id": "k1"}, "kind": "counsel", "direction": "parent"}]
    root = t["tree"]
    assert root["ref"] == {"kind": "case", "id": "c1"}
    hearings = {c["ref"]["id"]: c for c in root["children"]}
    # every hearing of the case hangs under it, and each record shows once in the tree
    assert set(hearings) == {"h1", next_id} and all(c["kind"] == "hearing" for c in hearings.values())
    below = [c["ref"] for h in hearings.values() for c in h["children"]]
    assert below == [{"kind": "plan", "id": plan_id}]
    members = {(m["kind"], m["id"]) for m in t["members"]}
    assert {("case", "c1"), ("hearing", "h1"), ("hearing", next_id), ("counsel", "k1"), ("client", "k9")} <= members
    assert [p["ref"]["id"] for p in t["peers"]] == ["k9"] and t["peers"][0]["linkId"]

    # a step sits under its plan, which sits under the hearing it prepares for, not twice under the case
    s = (await client.get("/v1/relations/thread", params={"kind": step["ref"]["kind"], "id": step["id"]}, headers=alice)).json()
    chain = [a["ref"]["kind"] for a in s["ancestors"]]
    assert chain[0] == "plan" and "hearing" in chain and "case" in chain


async def test_thread_of_a_missing_record_is_404(client, alice):
    r = await client.get("/v1/relations/thread", params={"kind": "task", "id": "ghost"}, headers=alice)
    assert r.status_code == 404


async def test_a_threads_history_merges_its_members(client, alice):
    rec = await seed(client, alice)
    items = (await client.get("/v1/relations/thread/history", params={"kind": "hearing", "id": "h1"}, headers=alice)).json()["items"]
    kinds = {i["ref"]["kind"] for i in items}
    assert {"hearing", "plan"} <= kinds
    assert items == sorted(items, key=lambda i: (i["at"], i["id"]), reverse=True)
    assert rec["plan"]["id"] in {i["ref"]["id"] for i in items}


async def test_agenda_collects_every_kind_in_order(client, alice):
    await seed(client, alice)
    h = alice
    await client.put("/v1/tasks/t1", json={"title": "Untimed", "dueDate": "2026-03-10", "priority": "high"}, headers=h)
    await client.put("/v1/tasks/t2", json={"title": "Timed", "slot": {"date": "2026-03-10", "start": 540, "duration": 45}}, headers=h)
    await client.put("/v1/meetings/m1", headers=h, json={
        "kind": "appointment", "title": "Visa", "slot": {"date": "2026-03-10", "start": 600, "duration": 30},
        "counterpart": "Ravi", "confirmations": ["day_before"]})
    await client.put("/v1/tasks/other", json={"title": "Elsewhere", "dueDate": "2026-05-01"}, headers=h)

    items = (await client.get("/v1/agenda", params={"start": "2026-03-09", "end": "2026-03-10"}, headers=h)).json()["items"]
    assert [(i["date"], i["title"]) for i in items] == [
        ("2026-03-09", "Confirm appointment — Ravi"),
        ("2026-03-10", "Timed"), ("2026-03-10", "Visa"), ("2026-03-10", "Hearing — Wages — Site 4"),
        ("2026-03-10", "Untimed")]
    by = {i["title"]: i for i in items}
    assert by["Confirm appointment — Ravi"]["pinned"] is True and by["Visa"]["slot"] == {"start": 600, "duration": 30}
    assert by["Hearing — Wages — Site 4"]["movable"] is False and by["Hearing — Wages — Site 4"]["subtitle"].startswith("SA-2026-001")
    assert "slot" not in by["Untimed"] and by["Untimed"]["priority"] == "high" and by["Untimed"]["duration"] == 30
    assert by["Visa"]["meetingKind"] == "appointment" and by["Timed"]["pinned"] is False


async def test_scheduling_moves_entries_and_hands_back_the_old_state(client, alice):
    await client.put("/v1/tasks/t1", json={"title": "Task", "dueDate": "2026-03-10"}, headers=alice)
    await client.put("/v1/follow-ups/f1", json={"title": "Call", "due": {"date": "2026-03-10"}, "duration": 15}, headers=alice)
    await client.put("/v1/meetings/m1", json={"title": "Sync", "slot": {"date": "2026-03-10", "start": 600, "duration": 30}}, headers=alice)

    r = await client.put("/v1/agenda/schedule", headers=alice, json={
        "ref": {"kind": "task", "id": "t1"}, "date": "2026-03-11", "block": {"start": 100, "duration": 1}})
    assert r.status_code == 200, r.text
    assert r.json()["before"]["entity"]["dueDate"] == "2026-03-10"
    t1 = (await client.get("/v1/tasks/t1", headers=alice)).json()
    assert t1["slot"] == {"date": "2026-03-11", "start": 360, "duration": 5} and t1["dueDate"] == "2026-03-10"

    await client.put("/v1/agenda/schedule", headers=alice, json={"ref": {"kind": "task", "id": "t1"}, "date": "2026-03-12", "block": None})
    t1 = (await client.get("/v1/tasks/t1", headers=alice)).json()
    assert "slot" not in t1 and t1["dueDate"] == "2026-03-10"

    await client.put("/v1/agenda/schedule", headers=alice, json={
        "ref": {"kind": "followUp", "id": "f1"}, "date": "2026-03-11", "block": {"start": 720, "duration": 20}})
    f1 = (await client.get("/v1/follow-ups/f1", headers=alice)).json()
    assert f1["due"] == {"date": "2026-03-11", "time": 720} and f1["duration"] == 20

    await client.put("/v1/agenda/schedule", headers=alice, json={
        "ref": {"kind": "meeting", "id": "m1"}, "date": "2026-03-13", "block": {"start": 840, "duration": 60}})
    assert (await client.get("/v1/meetings/m1", headers=alice)).json()["slot"] == {"date": "2026-03-13", "start": 840, "duration": 60}


async def test_scheduling_rules(client, alice):
    await client.put("/v1/meetings/m1", json={"title": "Sync", "slot": {"date": "2026-03-10", "start": 600, "duration": 30}}, headers=alice)
    r = await client.put("/v1/agenda/schedule", json={"ref": {"kind": "meeting", "id": "m1"}, "date": "2026-03-11", "block": None}, headers=alice)
    assert r.status_code == 422
    r = await client.put("/v1/agenda/schedule", json={"ref": {"kind": "hearing", "id": "h"}, "date": "2026-03-11", "block": None}, headers=alice)
    assert r.status_code == 422
    r = await client.put("/v1/agenda/schedule", json={"ref": {"kind": "task", "id": "zz"}, "date": "2026-03-11", "block": None}, headers=alice)
    assert r.status_code == 404


async def test_agenda_and_threads_are_private(client, alice, bob):
    await seed(client, alice)
    assert (await client.get("/v1/agenda", params={"start": "2026-03-01", "end": "2026-04-30"}, headers=bob)).json()["items"] == []
    assert (await client.get("/v1/relations/thread", params={"kind": "case", "id": "c1"}, headers=bob)).status_code == 404


async def test_everything_can_be_listed_for_a_live_view(client, alice, bob):
    await seed(client, alice)
    await client.post("/v1/links", headers=alice, json={"a": {"kind": "case", "id": "c1"}, "b": {"kind": "counsel", "id": "k1"},
                                                        "titles": {"a": "Case", "b": "Counsel"}})
    everything = (await client.get("/v1/activity", params={"limit": 5}, headers=alice)).json()
    assert len(everything["items"]) == 5 and everything["nextCursor"]
    seen, after = [], None
    while True:
        page = (await client.get("/v1/activity", params={"limit": 7, **({"after": after} if after else {})}, headers=alice)).json()
        seen += [i["id"] for i in page["items"]]
        after = page["nextCursor"]
        if not after:
            break
    assert len(seen) == len(set(seen)) > 10
    assert len((await client.get("/v1/links", headers=alice)).json()["items"]) == 1
    assert (await client.get("/v1/links", headers=bob)).json()["items"] == []
    assert (await client.get("/v1/activity", headers=bob)).json()["items"] == []
    assert (await client.get("/v1/activity", params={"kind": "case"}, headers=alice)).status_code == 422


async def test_saving_a_hearing_reports_what_was_folded_in(client, alice):
    for cid in ("c1", "c2"):
        await client.put(f"/v1/cases/{cid}", json={"organization": "SA", "title": cid}, headers=alice)
    await client.put("/v1/hearings/h2", json={"caseId": "c2", "date": "2026-03-10", "purpose": "x"}, headers=alice)
    r = await client.put("/v1/hearings/h1/listing", headers=alice,
                         json={"caseId": "c1", "alsoCaseIds": ["c2"], "date": "2026-03-10", "purpose": "Arguments"})
    assert r.status_code == 200, r.text
    assert r.json()["hearing"]["id"] == "h1" and [h["id"] for h in r.json()["merged"]] == ["h2"]
