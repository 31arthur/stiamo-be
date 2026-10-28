TASK = {"title": "File affidavit", "workspace": "sb", "dueDate": "2026-03-12", "estimate": 45}
APPT = {"kind": "appointment", "title": "Visa interview", "slot": {"date": "2026-03-10", "start": 600, "duration": 30},
        "counterpart": "Ravi", "contact": "98450", "confirmations": ["day_before", "hour_before"]}


async def follow_ups(client, headers):
    return (await client.get("/v1/follow-ups", headers=headers)).json()["items"]


async def test_task_roundtrip_flattens_and_restores_nested_values(client, alice):
    body = {**TASK, "slot": {"date": "2026-03-12", "start": 540, "duration": 45},
            "related": {"kind": "case", "id": "c1"}, "planId": "p1", "order": 2}
    r = await client.put("/v1/tasks/t1", json=body, headers=alice)
    assert r.status_code == 200, r.text
    got = (await client.get("/v1/tasks/t1", headers=alice)).json()
    assert got["slot"] == body["slot"] and got["related"] == body["related"]
    assert got["order"] == 2 and got["planId"] == "p1"
    assert got["outcome"] == {"status": "open"}
    assert got["priority"] == "normal" and got["notes"] == ""


async def test_task_validation(client, alice):
    assert (await client.post("/v1/tasks", json={**TASK, "title": " "}, headers=alice)).status_code == 422
    assert (await client.post("/v1/tasks", json={**TASK, "dueDate": "12/03/2026"}, headers=alice)).status_code == 422
    assert (await client.post("/v1/tasks", json={**TASK, "estimate": 0}, headers=alice)).status_code == 422
    assert (await client.post("/v1/tasks", json={**TASK, "slot": {"date": "2026-03-12", "start": 2000, "duration": 5}},
                              headers=alice)).status_code == 422


async def test_recording_an_outcome_with_a_spawned_task(client, alice):
    await client.put("/v1/tasks/t1", json=TASK, headers=alice)
    r = await client.post("/v1/outcomes", headers=alice, json={
        "ref": {"kind": "task", "id": "t1"},
        "outcome": {"status": "partial", "comment": " Waiting on the registry ", "nextAction": "chase"},
        "spawn": {"kind": "task", "title": "Chase registry", "date": "2026-03-15"},
    })
    assert r.status_code == 200, r.text
    spawned = r.json()["spawned"]
    assert spawned["kind"] == "task"
    t1 = (await client.get("/v1/tasks/t1", headers=alice)).json()
    assert t1["outcome"]["status"] == "partial" and t1["outcome"]["comment"] == "Waiting on the registry"
    assert t1["outcome"]["recordedAt"].endswith("Z")
    follow = (await client.get(f"/v1/tasks/{spawned['id']}", headers=alice)).json()
    assert follow["related"] == {"kind": "task", "id": "t1"} and follow["workspace"] == "sb"
    activity = (await client.get("/v1/activity", params={"kind": "task", "id": "t1"}, headers=alice)).json()["items"]
    assert activity[0]["action"] == "outcome"


async def test_outcome_with_follow_up_spawn_and_reopening(client, alice):
    await client.put("/v1/follow-ups/f1", json={"title": "Call Ravi", "contact": "Ravi"}, headers=alice)
    r = await client.post("/v1/outcomes", headers=alice, json={
        "ref": {"kind": "followUp", "id": "f1"}, "outcome": {"status": "done"},
        "spawn": {"kind": "followUp", "title": "Call again", "date": "2026-03-20", "time": 600}})
    fu = (await client.get(f"/v1/follow-ups/{r.json()['spawned']['id']}", headers=alice)).json()
    assert fu["contact"] == "Ravi" and fu["due"] == {"date": "2026-03-20", "time": 600}
    await client.post("/v1/outcomes", headers=alice, json={"ref": {"kind": "followUp", "id": "f1"}, "outcome": {"status": "open"}})
    assert "recordedAt" not in (await client.get("/v1/follow-ups/f1", headers=alice)).json()["outcome"]


async def test_outcome_on_missing_or_unsupported_record(client, alice):
    r = await client.post("/v1/outcomes", headers=alice, json={"ref": {"kind": "task", "id": "nope"}, "outcome": {"status": "done"}})
    assert r.status_code == 404
    r = await client.post("/v1/outcomes", headers=alice, json={"ref": {"kind": "plan", "id": "p"}, "outcome": {"status": "done"}})
    assert r.status_code == 404
    r = await client.post("/v1/outcomes", headers=alice, json={"ref": {"kind": "task", "id": "x"}, "outcome": {"status": "done"},
                                                              "spawn": {"kind": "meeting", "title": "x"}})
    assert r.status_code == 422


async def test_appointment_generates_and_maintains_confirmation_calls(client, alice):
    r = await client.put("/v1/meetings/m1", json=APPT, headers=alice)
    assert r.status_code == 200, r.text
    items = await follow_ups(client, alice)
    assert {f["trigger"] for f in items} == {"day_before", "hour_before"}
    day_before = next(f for f in items if f["trigger"] == "day_before")
    assert day_before["due"] == {"date": "2026-03-09", "time": 600}
    assert day_before["contact"] == "98450" and day_before["subject"] == {"kind": "meeting", "id": "m1"}

    moved = {**APPT, "slot": {"date": "2026-03-14", "start": 600, "duration": 30}}
    await client.put("/v1/meetings/m1", json=moved, headers=alice)
    items = await follow_ups(client, alice)
    assert len(items) == 2
    assert next(f for f in items if f["trigger"] == "day_before")["due"]["date"] == "2026-03-13"

    await client.put("/v1/meetings/m1", json={**moved, "confirmations": ["day_before"]}, headers=alice)
    assert [f["trigger"] for f in await follow_ups(client, alice)] == ["day_before"]

    await client.delete("/v1/meetings/m1", headers=alice)
    assert await follow_ups(client, alice) == []


async def test_cancelling_an_appointment_removes_open_confirmations(client, alice):
    await client.put("/v1/meetings/m1", json=APPT, headers=alice)
    await client.post("/v1/outcomes", headers=alice, json={"ref": {"kind": "meeting", "id": "m1"}, "outcome": {"status": "cancelled"}})
    assert await follow_ups(client, alice) == []


async def test_a_confirmation_with_a_result_survives_deleting_the_appointment(client, alice):
    await client.put("/v1/meetings/m1", json=APPT, headers=alice)
    first = (await follow_ups(client, alice))[0]
    await client.post("/v1/outcomes", headers=alice, json={"ref": {"kind": "followUp", "id": first["id"]}, "outcome": {"status": "done"}})
    await client.delete("/v1/meetings/m1", headers=alice)
    assert [f["id"] for f in await follow_ups(client, alice)] == [first["id"]]


async def test_meeting_agenda_and_attendees_are_cleaned_and_kept(client, alice):
    body = {"title": "Kick-off", "slot": APPT["slot"], "attendees": [" Ana ", "", "Bo"],
            "agenda": [{"id": "a1", "topic": "Scope", "minutes": 20}, {"id": "a2", "topic": "  "}]}
    doc = (await client.put("/v1/meetings/m2", json=body, headers=alice)).json()
    assert doc["attendees"] == ["Ana", "Bo"]
    assert [a["topic"] for a in (await client.get("/v1/meetings/m2", headers=alice)).json()["agenda"]] == ["Scope"]


async def test_logging_attempts_walks_the_chase(client, alice):
    await client.put("/v1/follow-ups/f1", json={"title": "Call Ravi", "contact": "Ravi", "due": {"date": "2026-03-10"}}, headers=alice)
    at = {"date": "2026-03-10", "minute": 600}
    r = await client.post("/v1/follow-ups/f1/attempts", json={"result": "no_answer", "at": at}, headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggestion"]["time"] == 720 and body["followUp"]["due"] == {"date": "2026-03-10", "time": 720}
    assert len(body["followUp"]["attempts"]) == 1 and body["before"]["due"] == {"date": "2026-03-10"}
    assert body["followUp"]["outcome"]["status"] == "open"

    # an explicit next date wins; an explicit null leaves it undated
    r = await client.post("/v1/follow-ups/f1/attempts", json={"result": "left_message", "at": at, "next": {"date": "2026-03-18"}}, headers=alice)
    assert r.json()["followUp"]["due"] == {"date": "2026-03-18"}
    r = await client.post("/v1/follow-ups/f1/attempts", json={"result": "call_back", "at": at, "next": None, "switchTo": "message"}, headers=alice)
    assert "due" not in r.json()["followUp"] and r.json()["followUp"]["channel"] == "message"

    r = await client.post("/v1/follow-ups/f1/attempts", json={"result": "reached", "note": "Sorted", "at": at}, headers=alice)
    done = r.json()["followUp"]
    assert done["outcome"]["status"] == "done" and done["outcome"]["comment"] == "Sorted" and len(done["attempts"]) == 4

    r = await client.post("/v1/follow-ups/f1/suggestion", json={"result": "no_answer", "at": at}, headers=alice)
    assert r.json()["suggestion"]["reason"]


async def test_attempt_on_unknown_follow_up(client, alice):
    r = await client.post("/v1/follow-ups/zzz/attempts", json={"result": "reached"}, headers=alice)
    assert r.status_code == 404
