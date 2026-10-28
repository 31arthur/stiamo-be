async def make_plan(client, headers, steps=None, **plan):
    body = {"plan": {"title": "Prepare", "objective": " Be ready ", "workspace": "sb", "status": "active",
                     "dueDate": "2026-03-20", **plan},
            "steps": steps if steps is not None else ["Draft", {"title": "Call clerk", "kind": "followUp"},
                                                     {"title": "Meet counsel", "kind": "appointment", "time": 660}]}
    r = await client.post("/v1/plans/with-steps", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def steps_of(client, headers, plan_id):
    return (await client.get(f"/v1/plans/{plan_id}/steps", headers=headers)).json()["items"]


async def test_plan_with_steps_of_every_kind(client, alice):
    plan = await make_plan(client, alice)
    assert plan["objective"] == "Be ready"
    steps = await steps_of(client, alice, plan["id"])
    assert [s["kind"] for s in steps] == ["task", "followUp", "appointment"]
    assert [s["order"] for s in steps] == [0, 1, 2]
    assert all(s["date"] == "2026-03-20" for s in steps)
    assert steps[2]["time"] == 660
    # the appointment is a real meeting with its confirmation calls
    fus = (await client.get("/v1/follow-ups", headers=alice)).json()["items"]
    assert {f.get("trigger") for f in fus} == {None, "day_before", "hour_before"}
    assert (await client.get(f"/v1/plans/{plan['id']}/progress", headers=alice)).json() == {
        "total": 3, "closed": 0, "done": 0, "progress": 0.0}


async def test_step_own_date_and_no_date(client, alice):
    plan = await make_plan(client, alice, steps=[
        {"title": "own day", "dueDate": "2026-03-25"}, {"title": "no day", "dueDate": None}, "plan day"])
    own, none, inherit = await steps_of(client, alice, plan["id"])
    assert own["date"] == "2026-03-25" and "date" not in none and inherit["date"] == "2026-03-20"


async def test_add_step_goes_to_the_end(client, alice):
    plan = await make_plan(client, alice, steps=["one", "two"])
    await client.post(f"/v1/plans/{plan['id']}/steps", json={"title": " three "}, headers=alice)
    steps = await steps_of(client, alice, plan["id"])
    assert [s["title"] for s in steps] == ["one", "two", "three"] and steps[-1]["order"] == 2
    assert (await client.post(f"/v1/plans/{plan['id']}/steps", json={"title": "  "}, headers=alice)).status_code == 422


async def test_reorder_across_kinds(client, alice):
    plan = await make_plan(client, alice)
    steps = await steps_of(client, alice, plan["id"])
    ids = [s["id"] for s in reversed(steps)]
    assert (await client.put(f"/v1/plans/{plan['id']}/steps/order", json={"orderedIds": ids}, headers=alice)).status_code == 204
    assert [s["id"] for s in await steps_of(client, alice, plan["id"])] == ids


async def test_changing_the_plan_day_moves_only_steps_that_followed_it(client, alice):
    plan = await make_plan(client, alice, steps=[{"title": "own", "dueDate": "2026-03-25"}, "follows"])
    r = await client.put(f"/v1/plans/{plan['id']}/due-date", json={"date": "2026-04-01"}, headers=alice)
    assert r.json()["moved"] == 1 and r.json()["plan"]["dueDate"] == "2026-04-01"
    own, follows = await steps_of(client, alice, plan["id"])
    assert own["date"] == "2026-03-25" and follows["date"] == "2026-04-01"


async def test_progress_follows_step_outcomes(client, alice):
    plan = await make_plan(client, alice, steps=["a", "b"])
    a, _ = await steps_of(client, alice, plan["id"])
    await client.post("/v1/outcomes", json={"ref": a["ref"], "outcome": {"status": "done"}}, headers=alice)
    assert (await client.get(f"/v1/plans/{plan['id']}/progress", headers=alice)).json()["progress"] == 0.5


async def test_converting_a_step_keeps_its_identity_and_links(client, alice):
    plan = await make_plan(client, alice, steps=["Call the registry"])
    step = (await steps_of(client, alice, plan["id"]))[0]
    other = await client.put("/v1/counsel/c1", json={"kind": "firm", "name": "Firm"}, headers=alice)
    assert other.status_code == 200
    await client.post("/v1/links", json={"a": step["ref"], "b": {"kind": "counsel", "id": "c1"},
                                         "titles": {"a": "Call the registry", "b": "Firm"}}, headers=alice)

    r = await client.post(f"/v1/steps/task/{step['id']}/convert", json={"to": "followUp"}, headers=alice)
    assert r.status_code == 200 and r.json() == {"kind": "followUp", "id": step["id"]}
    converted = (await steps_of(client, alice, plan["id"]))[0]
    assert converted["kind"] == "followUp" and converted["id"] == step["id"] and converted["order"] == 0
    assert (await client.get(f"/v1/tasks/{step['id']}", headers=alice)).status_code == 404
    links = (await client.get("/v1/links", params={"kind": "followUp", "id": step["id"]}, headers=alice)).json()["items"]
    assert len(links) == 1
    # converting to the same kind is a no-op
    same = await client.post(f"/v1/steps/followUp/{step['id']}/convert", json={"to": "followUp"}, headers=alice)
    assert same.json()["kind"] == "followUp"


async def test_convert_to_appointment_and_back_keeps_the_outcome(client, alice):
    plan = await make_plan(client, alice, steps=["Do it"])
    step = (await steps_of(client, alice, plan["id"]))[0]
    await client.post("/v1/outcomes", json={"ref": step["ref"], "outcome": {"status": "done", "comment": "ok"}}, headers=alice)
    await client.post(f"/v1/steps/task/{step['id']}/convert", json={"to": "appointment"}, headers=alice)
    meeting = (await client.get(f"/v1/meetings/{step['id']}", headers=alice)).json()
    assert meeting["kind"] == "appointment" and meeting["outcome"]["status"] == "done"
    assert meeting["slot"]["start"] == 600 and meeting["slot"]["date"] == "2026-03-20"


async def test_set_step_date_for_each_kind(client, alice):
    plan = await make_plan(client, alice)
    task_, follow, appt = await steps_of(client, alice, plan["id"])
    for step, kind in ((task_, "task"), (follow, "followUp"), (appt, "meeting")):
        r = await client.put(f"/v1/steps/{kind}/{step['id']}/date", json={"date": "2026-05-05"}, headers=alice)
        assert r.status_code == 204, r.text
    assert {s["date"] for s in await steps_of(client, alice, plan["id"])} == {"2026-05-05"}
    r = await client.put(f"/v1/steps/meeting/{appt['id']}/date", json={"date": None}, headers=alice)
    assert r.status_code == 422


async def test_duplicate_resets_progress_and_covers_nothing(client, alice):
    plan = await make_plan(client, alice, steps=["a", "b"])
    a, _ = await steps_of(client, alice, plan["id"])
    await client.post("/v1/outcomes", json={"ref": a["ref"], "outcome": {"status": "done"}}, headers=alice)
    await client.put(f"/v1/plans/{plan['id']}/doc", json={"doc": [{"type": "paragraph"}], "firstEditOfSession": True}, headers=alice)
    copy = (await client.post(f"/v1/plans/{plan['id']}/duplicate", headers=alice)).json()
    assert copy["title"] == "Prepare (copy)" and copy["status"] == "draft" and copy["doc"] == [{"type": "paragraph"}]
    steps = await steps_of(client, alice, copy["id"])
    assert [s["title"] for s in steps] == ["a", "b"] and all(s["outcome"]["status"] == "open" for s in steps)
    assert (await client.get(f"/v1/plans/{plan['id']}/progress", headers=alice)).json()["done"] == 1


async def test_plan_doc_autosave_is_quiet(client, alice):
    plan = await make_plan(client, alice, steps=[])
    for first in (True, False, False):
        await client.put(f"/v1/plans/{plan['id']}/doc", json={"doc": [1], "firstEditOfSession": first}, headers=alice)
    got = (await client.get(f"/v1/plans/{plan['id']}", headers=alice)).json()
    assert got["doc"] == [1]
    history = (await client.get("/v1/activity", params={"kind": "plan", "id": plan["id"]}, headers=alice)).json()["items"]
    assert [h["summary"] for h in history].count("Edited the plan page") == 1


async def test_deleting_a_plan_keeps_its_steps_as_standalone_records(client, alice):
    plan = await make_plan(client, alice)
    steps = await steps_of(client, alice, plan["id"])
    await client.delete(f"/v1/plans/{plan['id']}", headers=alice)
    assert (await client.get(f"/v1/plans/{plan['id']}", headers=alice)).status_code == 404
    task_ = (await client.get(f"/v1/tasks/{steps[0]['id']}", headers=alice)).json()
    assert "planId" not in task_ and "order" not in task_
    assert (await client.get(f"/v1/meetings/{steps[2]['id']}", headers=alice)).status_code == 200


async def test_coverage_of_a_task_is_exclusive_and_reversible(client, alice):
    plan = await make_plan(client, alice, steps=[], workspace="personal")
    await client.put("/v1/tasks/t1", json={"title": "Pay fee", "workspace": "fsa", "related": {"kind": "case", "id": "c9"}}, headers=alice)
    covered = (await client.post(f"/v1/plans/{plan['id']}/cover", json={"kind": "task", "id": "t1"}, headers=alice)).json()
    assert covered["taskId"] == "t1" and covered["workspace"] == "fsa" and covered["caseIds"] == ["c9"]
    free = (await client.post(f"/v1/plans/{plan['id']}/uncover", json={"kind": "task", "id": "t1"}, headers=alice)).json()
    assert "taskId" not in free
    assert (await client.post(f"/v1/plans/{plan['id']}/cover", json={"kind": "task", "id": "ghost"}, headers=alice)).status_code == 422
    assert (await client.post(f"/v1/plans/{plan['id']}/cover", json={"kind": "goal", "id": "x"}, headers=alice)).status_code == 422


async def test_plan_cannot_be_about_its_own_step(client, alice):
    plan = await make_plan(client, alice, steps=["inner"])
    step = (await steps_of(client, alice, plan["id"]))[0]
    r = await client.post(f"/v1/plans/{plan['id']}/cover", json=step["ref"], headers=alice)
    assert r.status_code == 422


async def test_users_cannot_see_each_others_plans(client, alice, bob):
    plan = await make_plan(client, alice)
    assert (await client.get(f"/v1/plans/{plan['id']}/steps", headers=bob)).status_code == 404
    assert (await client.post(f"/v1/plans/{plan['id']}/duplicate", headers=bob)).status_code == 404


# --- goals ---------------------------------------------------------------------------------------------------------

GOAL = {"title": "Clear the backlog", "horizon": "quarter", "startDate": "2026-01-01", "targetDate": "2026-03-31",
        "keyResults": [{"id": "k1", "label": "Cases closed", "start": 0, "target": 10, "current": 2, "unit": "cases"}]}


async def test_goal_validation(client, alice):
    assert (await client.put("/v1/goals/g1", json={**GOAL, "targetDate": "2026-01-01"}, headers=alice)).status_code == 422
    bad_kr = {**GOAL, "keyResults": [{"id": "k", "label": "x", "start": 1, "target": 1, "current": 1}]}
    assert (await client.put("/v1/goals/g1", json=bad_kr, headers=alice)).status_code == 422
    assert (await client.put("/v1/goals/g1", json={**GOAL, "planIds": ["ghost"]}, headers=alice)).status_code == 422
    assert (await client.put("/v1/goals/g1", json={**GOAL, "title": " "}, headers=alice)).status_code == 422


async def test_goal_plans_progress_and_cascade(client, alice):
    plan = await make_plan(client, alice, steps=["a", "b"])
    g = await client.put("/v1/goals/g1", json={**GOAL, "planIds": [plan["id"], plan["id"]]}, headers=alice)
    assert g.status_code == 200 and g.json()["planIds"] == [plan["id"]]
    a, _ = await steps_of(client, alice, plan["id"])
    await client.post("/v1/outcomes", json={"ref": a["ref"], "outcome": {"status": "done"}}, headers=alice)

    prog = (await client.get("/v1/goals/g1/progress", params={"today": "2026-02-15"}, headers=alice)).json()
    assert prog["plans"] == 0.5 and prog["keyResults"] == 0.2 and round(prog["progress"], 2) == 0.35
    assert prog["health"] in {"on_track", "at_risk", "off_track"} and prog["daysLeft"] == 44

    await client.delete(f"/v1/plans/{plan['id']}", headers=alice)
    assert (await client.get("/v1/goals/g1", headers=alice)).json()["planIds"] == []


async def test_link_and_unlink_a_plan_to_a_goal(client, alice):
    plan = await make_plan(client, alice, steps=[])
    await client.put("/v1/goals/g1", json=GOAL, headers=alice)
    await client.put("/v1/goals/g2", json=GOAL, headers=alice)
    for goal_id in ("g1", "g2"):
        assert (await client.put(f"/v1/plans/{plan['id']}/goals/{goal_id}", headers=alice)).status_code == 204
    assert (await client.get("/v1/goals/g2", headers=alice)).json()["planIds"] == [plan["id"]]
    await client.delete(f"/v1/plans/{plan['id']}/goals/g1", headers=alice)
    assert (await client.get("/v1/goals/g1", headers=alice)).json()["planIds"] == []
    assert (await client.put(f"/v1/plans/{plan['id']}/goals/nope", headers=alice)).status_code == 404


async def test_check_ins_and_key_result_updates(client, alice):
    await client.put("/v1/goals/g1", json=GOAL, headers=alice)
    r = await client.post("/v1/goals/g1/check-ins", json={"date": "2026-02-01", "confidence": 4, "note": " good "}, headers=alice)
    assert r.status_code == 201 and r.json()["checkIns"][0]["note"] == "good"
    assert (await client.post("/v1/goals/g1/check-ins", json={"date": "2026-02-01", "confidence": 9}, headers=alice)).status_code == 422
    r = await client.put("/v1/goals/g1/key-results/k1", json={"current": 7}, headers=alice)
    assert r.json()["keyResults"][0]["current"] == 7
    assert (await client.put("/v1/goals/g1/key-results/zz", json={"current": 1}, headers=alice)).status_code == 404
