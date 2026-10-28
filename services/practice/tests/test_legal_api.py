from app.domain.cases import next_case_number


def case_body(**kw):
    return {"organization": "TUMWU", "type": "pow", "title": "Wages — Site 4", "client": "Gang of 12", **kw}


async def open_case(http, headers, id=None, **kw):
    r = await (http.put(f"/v1/cases/{id}", json=case_body(**kw), headers=headers) if id
               else http.post("/v1/cases", json=case_body(**kw), headers=headers))
    assert r.status_code in (200, 201), r.text
    return r.json()


async def hearing(client, headers, id, case_id="c1", date="2026-03-10", **kw):
    body = {"caseId": case_id, "date": date, "purpose": "Arguments", "duration": 30, **kw}
    r = await client.put(f"/v1/hearings/{id}", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- case numbers --------------------------------------------------------------------------------------------


def test_case_number_counts_on_from_the_highest_used():
    assert next_case_number("SA", 2026, []) == "SA-2026-001"
    assert next_case_number("SA", 2026, ["SA-2026-001", "SA-2026-007", "SA-2025-099", "NUMW-2026-050"]) == "SA-2026-008"
    assert next_case_number("SA", 2026, ["SA-2026-abc"]) == "SA-2026-001"


async def test_case_numbers_are_generated_per_organization_and_year(client, alice):
    a = await open_case(client, alice, filedOn="2026-02-01")
    b = await open_case(client, alice, filedOn="2026-02-02")
    c = await open_case(client, alice, filedOn="2025-12-31")
    d = await open_case(client, alice, organization="SA", filedOn="2026-05-05")
    assert [x["reference"] for x in (a, b, c, d)] == ["TUMWU-2026-001", "TUMWU-2026-002", "TUMWU-2025-001", "SA-2026-001"]


async def test_a_typed_reference_is_ignored_and_never_changes(client, alice):
    made = await open_case(client, alice, id="c1", reference="HACKED-1", filedOn="2026-02-01")
    assert made["reference"] == "TUMWU-2026-001"
    again = await open_case(client, alice, id="c1", reference="HACKED-2", title="Renamed", filedOn="2026-02-01")
    assert again["reference"] == "TUMWU-2026-001" and again["title"] == "Renamed"


async def test_deleting_a_case_never_reuses_a_lower_number_out_of_order(client, alice):
    await open_case(client, alice, id="c1", filedOn="2026-01-01")
    second = await open_case(client, alice, id="c2", filedOn="2026-01-01")
    await client.delete("/v1/cases/c1", headers=alice)
    third = await open_case(client, alice, id="c3", filedOn="2026-01-01")
    assert second["reference"] == "TUMWU-2026-002" and third["reference"] == "TUMWU-2026-003"


async def test_numbering_is_per_user(client, alice, bob):
    await open_case(client, alice, filedOn="2026-01-01")
    assert (await open_case(client, bob, filedOn="2026-01-01"))["reference"] == "TUMWU-2026-001"


async def test_case_validation(client, alice):
    assert (await client.post("/v1/cases", json=case_body(organization="XYZ"), headers=alice)).status_code == 422
    assert (await client.post("/v1/cases", json=case_body(title="  "), headers=alice)).status_code == 422
    assert (await client.post("/v1/cases", json=case_body(type="parking"), headers=alice)).status_code == 422


# --- counsel engagement ------------------------------------------------------------------------------------------


async def test_briefing_counsel_is_logged_on_both_sides_and_can_be_taken_back(client, alice):
    await open_case(client, alice, id="c1")
    await client.put("/v1/counsel/k1", json={"kind": "advocate", "name": "R. Iyer", "firm": "Iyer & Co"}, headers=alice)
    brief = {"engagement": {"counselId": "k1", "proBono": False, "fee": " ₹25,000 ", "scope": "Appear at hearings"}}
    r = await client.put("/v1/cases/c1/engagement", json=brief, headers=alice)
    assert r.status_code == 200 and r.json()["engagement"]["fee"] == "₹25,000"
    case_log = (await client.get("/v1/activity", params={"kind": "case", "id": "c1"}, headers=alice)).json()["items"]
    counsel_log = (await client.get("/v1/activity", params={"kind": "counsel", "id": "k1"}, headers=alice)).json()["items"]
    assert any("Briefed R. Iyer (Iyer & Co)" in i["summary"] for i in case_log)
    assert any("Briefed on" in i["summary"] for i in counsel_log)

    back = await client.put("/v1/cases/c1/engagement", json={"engagement": None}, headers=alice)
    assert "engagement" not in back.json()
    counsel_log = (await client.get("/v1/activity", params={"kind": "counsel", "id": "k1"}, headers=alice)).json()["items"]
    assert any("No longer briefed" in i["summary"] for i in counsel_log)


async def test_cannot_brief_counsel_that_does_not_exist(client, alice):
    await open_case(client, alice, id="c1")
    r = await client.put("/v1/cases/c1/engagement", json={"engagement": {"counselId": "ghost"}}, headers=alice)
    assert r.status_code == 422
    r = await client.post("/v1/cases", json=case_body(engagement={"counselId": "ghost"}), headers=alice)
    assert r.status_code == 422


async def test_removing_counsel_returns_their_cases_to_us(client, alice):
    await client.put("/v1/counsel/k1", json={"kind": "firm", "name": "Iyer LLP"}, headers=alice)
    await open_case(client, alice, id="c1", engagement={"counselId": "k1"})
    await open_case(client, alice, id="c2")
    assert (await client.get("/v1/cases/c1", headers=alice)).json()["engagement"]["counselId"] == "k1"
    await client.delete("/v1/counsel/k1", headers=alice)
    assert "engagement" not in (await client.get("/v1/cases/c1", headers=alice)).json()
    log = (await client.get("/v1/activity", params={"kind": "case", "id": "c1"}, headers=alice)).json()["items"]
    assert any("removed from the directory" in i["summary"] for i in log)


# --- hearings --------------------------------------------------------------------------------------------------


async def test_a_hearing_needs_real_cases(client, alice):
    r = await client.put("/v1/hearings/h1", json={"caseId": "nope", "date": "2026-03-10", "purpose": "x"}, headers=alice)
    assert r.status_code == 422
    await open_case(client, alice, id="c1")
    r = await client.put("/v1/hearings/h1", json={"caseId": "c1", "alsoCaseIds": ["nope"], "date": "2026-03-10", "purpose": "x"},
                         headers=alice)
    assert r.status_code == 422


async def test_hearing_roundtrip_keeps_every_field(client, alice):
    await open_case(client, alice, id="c1")
    body = {"time": 630, "forum": "Court 4", "bench": " Justice R ", "order": "Adjourned", "authorities": ["A v B", "A v B", " "],
            "actions": [{"id": "a1", "text": " File reply ", "due": "2026-03-20"}, {"id": "a2", "text": " "}]}
    await hearing(client, alice, "h1", **body)
    got = (await client.get("/v1/hearings/h1", headers=alice)).json()
    assert got["time"] == 630 and got["bench"] == "Justice R" and got["order"] == "Adjourned"
    assert got["authorities"] == ["A v B"] and got["actions"] == [{"id": "a1", "text": "File reply", "due": "2026-03-20"}]
    assert got["outcome"] == {"status": "open"}


async def test_hearings_of_one_sitting_merge_when_cases_are_joined(client, alice):
    for cid in ("c1", "c2", "c3"):
        await open_case(client, alice, id=cid)
    await hearing(client, alice, "h1", "c1", "2026-03-10", summary=None)
    await hearing(client, alice, "h2", "c2", "2026-03-10")          # same day, unwritten: folded in
    await hearing(client, alice, "h3", "c3", "2026-03-10", summary="Already written up")  # left alone
    await client.put("/v1/plans/p1", json={"title": "Prep", "hearingIds": ["h2"], "caseIds": ["c2"]}, headers=alice)
    await client.put("/v1/tasks/t1", json={"title": "Do", "related": {"kind": "hearing", "id": "h2"}}, headers=alice)

    r = await client.put("/v1/hearings/h1/cases", json={"caseIds": ["c2", "c3"]}, headers=alice)
    assert r.status_code == 200, r.text
    assert [h["id"] for h in r.json()["merged"]] == ["h2"]
    assert r.json()["hearing"]["alsoCaseIds"] == ["c2", "c3"]
    assert (await client.get("/v1/hearings/h2", headers=alice)).status_code == 404
    assert (await client.get("/v1/hearings/h3", headers=alice)).status_code == 200
    plan = (await client.get("/v1/plans/p1", headers=alice)).json()
    assert plan["hearingIds"] == ["h1"] and set(plan["caseIds"]) == {"c1", "c2"}
    assert (await client.get("/v1/tasks/t1", headers=alice)).json()["related"] == {"kind": "hearing", "id": "h1"}
    for cid in ("c1", "c2", "c3"):
        ids = [h["id"] for h in (await client.get(f"/v1/cases/{cid}/hearings", headers=alice)).json()["items"]]
        assert "h1" in ids


# --- writing a hearing up -----------------------------------------------------------------------------------------


async def test_recording_a_hearing_does_everything_in_one_step(client, alice):
    await open_case(client, alice, id="c1")
    await open_case(client, alice, id="c2")
    await hearing(client, alice, "h1", "c1", "2026-03-10", alsoCaseIds=["c2"])
    r = await client.post("/v1/hearings/h1/record", headers=alice, json={
        "summary": " Counsel for respondent sought time ", "outcome": {"status": "not_done", "comment": "Adjourned"},
        "next": {"date": "2026-04-14", "time": 600, "purpose": "Reply"},
        "plan": {"title": "Prepare reply", "objective": "File on time"},
        "notes": {"order": "Reply in 4 weeks", "authorities": ["X v Y"], "actions": [
            {"text": "Draft reply", "due": "2026-04-01"}, {"text": "Serve copy"}]},
        "caseUpdate": {"stage": "Reply", "status": "on_hold"}})
    assert r.status_code == 200, r.text
    out = r.json()
    h, nxt, plan = out["hearing"], out["nextHearing"], out["plan"]
    assert h["summary"] == "Counsel for respondent sought time"
    assert h["outcome"]["status"] == "not_done" and h["outcome"]["recordedAt"].endswith("Z")
    assert h["nextHearingId"] == nxt["id"] and nxt["alsoCaseIds"] == ["c2"] and nxt["purpose"] == "Reply"
    assert nxt["outcome"] == {"status": "open"} and nxt["duration"] == 30

    # the plan covers both cases and the next hearing, with the actions as its steps
    assert plan["caseIds"] == ["c1", "c2"] and plan["hearingIds"] == [nxt["id"]] and plan["dueDate"] == "2026-04-14"
    assert plan["origin"] == {"kind": "hearing", "id": "h1"} and plan["workspace"] == "sb"
    steps = (await client.get(f"/v1/plans/{plan['id']}/steps", headers=alice)).json()["items"]
    assert [(s["title"], s["date"]) for s in steps] == [("Draft reply", "2026-04-01"), ("Serve copy", "2026-04-14")]
    # each action remembers the task made for it
    assert [a["taskId"] for a in h["actions"]] == [s["id"] for s in steps]
    assert h["order"] == "Reply in 4 weeks" and h["authorities"] == ["X v Y"]

    case = (await client.get("/v1/cases/c1", headers=alice)).json()
    assert case["stage"] == "Reply" and case["status"] == "on_hold"
    # shows up in the diary of both cases, and the open hearing is now the next one
    for cid in ("c1", "c2"):
        diary = (await client.get("/v1/hearings/diary", params={"caseId": cid}, headers=alice)).json()["items"]
        assert [d["id"] for d in diary] == ["h1"]
        upcoming = (await client.get(f"/v1/cases/{cid}/next-hearing", params={"today": "2026-03-11"}, headers=alice)).json()
        assert upcoming["hearing"]["id"] == nxt["id"]


async def test_omitting_notes_keeps_what_is_there_and_providing_them_replaces_it(client, alice):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "h1", bench="Justice R", order="Interim stay")
    await client.post("/v1/hearings/h1/record", json={"summary": "one", "outcome": {"status": "done"}}, headers=alice)
    kept = (await client.get("/v1/hearings/h1", headers=alice)).json()
    assert kept["bench"] == "Justice R" and kept["order"] == "Interim stay"
    await client.post("/v1/hearings/h1/record", json={"summary": "two", "outcome": {"status": "done"},
                                                      "notes": {"reflections": "Bench is receptive"}}, headers=alice)
    replaced = (await client.get("/v1/hearings/h1", headers=alice)).json()
    assert "bench" not in replaced and "order" not in replaced and replaced["reflections"] == "Bench is receptive"


async def test_typed_plan_steps_win_over_actions(client, alice):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "h1")
    r = await client.post("/v1/hearings/h1/record", headers=alice, json={
        "summary": "s", "outcome": {"status": "done"}, "plan": {"title": "P", "steps": ["Typed one", {"title": "Typed two", "kind": "followUp"}]},
        "notes": {"actions": [{"text": "An action"}]}})
    out = r.json()
    steps = (await client.get(f"/v1/plans/{out['plan']['id']}/steps", headers=alice)).json()["items"]
    assert [s["title"] for s in steps] == ["Typed one", "Typed two"]
    assert "taskId" not in out["hearing"]["actions"][0]


async def test_recording_a_hearing_that_does_not_exist_or_has_no_case(client, alice):
    r = await client.post("/v1/hearings/zzz/record", json={"summary": "s", "outcome": {"status": "done"}}, headers=alice)
    assert r.status_code == 404


async def test_hearing_outcome_through_the_generic_endpoint(client, alice):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "h1")
    r = await client.post("/v1/outcomes", headers=alice, json={
        "ref": {"kind": "hearing", "id": "h1"}, "outcome": {"status": "partial", "comment": "Part heard"},
        "spawn": {"kind": "task", "title": "Prepare for part two", "date": "2026-03-17"}})
    assert r.status_code == 200, r.text
    task = (await client.get(f"/v1/tasks/{r.json()['spawned']['id']}", headers=alice)).json()
    assert task["workspace"] == "sb" and task["related"] == {"kind": "hearing", "id": "h1"}
    assert (await client.get("/v1/hearings/h1", headers=alice)).json()["outcome"]["status"] == "partial"


async def test_unrecorded_hearings_need_attention(client, alice):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "old", date="2026-03-01")
    await hearing(client, alice, "future", date="2026-03-30")
    await hearing(client, alice, "done", date="2026-02-01")
    await client.post("/v1/hearings/done/record", json={"summary": "s", "outcome": {"status": "done"}}, headers=alice)
    items = (await client.get("/v1/hearings/unrecorded", params={"today": "2026-03-10"}, headers=alice)).json()["items"]
    assert [h["id"] for h in items] == ["old"]


async def test_diary_search_matches_every_word_across_entry_and_case(client, alice):
    await open_case(client, alice, id="c1", client="Ramesh Kumar")
    await open_case(client, alice, id="c2", client="Other")
    await hearing(client, alice, "h1", "c1", date="2026-03-01")
    await hearing(client, alice, "h2", "c2", date="2026-03-02")
    await client.post("/v1/hearings/h1/record", json={"summary": "Wages order passed", "outcome": {"status": "done"}}, headers=alice)
    await client.post("/v1/hearings/h2/record", json={"summary": "Adjourned", "outcome": {"status": "not_done"}}, headers=alice)

    def ids(resp):
        return [h["id"] for h in resp.json()["items"]]

    assert ids(await client.get("/v1/hearings/diary", headers=alice)) == ["h2", "h1"]  # newest first
    assert ids(await client.get("/v1/hearings/diary", params={"search": "wages ramesh"}, headers=alice)) == ["h1"]
    assert ids(await client.get("/v1/hearings/diary", params={"search": "adjourned ramesh"}, headers=alice)) == []


# --- hearing actions -------------------------------------------------------------------------------------------------


async def test_actions_become_tasks_following_the_next_hearing_date(client, alice):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "h1", actions=[{"id": "a1", "text": "File reply", "due": "2026-03-20"}, {"id": "a2", "text": "Call clerk"}])
    nxt = await hearing(client, alice, "h2", date="2026-04-01")
    await client.put("/v1/hearings/h1", json={**(await client.get("/v1/hearings/h1", headers=alice)).json(), "nextHearingId": nxt["id"]}, headers=alice)

    r = await client.post("/v1/hearings/h1/actions/tasks", headers=alice)
    assert r.status_code == 200, r.text
    actions = r.json()["actions"]
    tasks = {a["id"]: (await client.get(f"/v1/tasks/{a['taskId']}", headers=alice)).json() for a in actions}
    assert tasks["a1"]["dueDate"] == "2026-03-20" and tasks["a2"]["dueDate"] == "2026-04-01"
    assert tasks["a1"]["related"] == {"kind": "case", "id": "c1"} and tasks["a1"]["estimate"] == 45
    again = await client.post("/v1/hearings/h1/actions/tasks", headers=alice)
    assert again.status_code == 422  # every action already has a task


async def test_pending_actions_follow_their_steps(client, alice):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "h1", actions=[{"id": "a1", "text": "One"}, {"id": "a2", "text": "Two"}])
    h = (await client.post("/v1/hearings/h1/actions/tasks", headers=alice)).json()
    assert len((await client.get("/v1/hearings/h1/pending-actions", headers=alice)).json()["items"]) == 2
    first = h["actions"][0]["taskId"]
    await client.post("/v1/outcomes", json={"ref": {"kind": "task", "id": first}, "outcome": {"status": "done"}}, headers=alice)
    left = (await client.get("/v1/hearings/h1/pending-actions", headers=alice)).json()["items"]
    assert [a["id"] for a in left] == ["a2"]


async def test_actions_to_plan(client, alice):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "h1", actions=[{"id": "a1", "text": "Collect affidavits", "due": "2026-03-18"}, {"id": "a2", "text": "Brief counsel"}])
    r = await client.post("/v1/hearings/h1/actions/plan", json={"title": "  "}, headers=alice)
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["title"] == "Prepare for the next hearing" and plan["caseIds"] == ["c1"]
    steps = (await client.get(f"/v1/plans/{plan['id']}/steps", headers=alice)).json()["items"]
    assert [s["title"] for s in steps] == ["Collect affidavits", "Brief counsel"]
    h = (await client.get("/v1/hearings/h1", headers=alice)).json()
    assert [a["taskId"] for a in h["actions"]] == [s["id"] for s in steps]
    assert (await client.post("/v1/hearings/h1/actions/plan", headers=alice)).status_code == 422


# --- deleting cases ----------------------------------------------------------------------------------------------------


async def test_deleting_a_case_keeps_shared_hearings_on_the_others(client, alice):
    for cid in ("c1", "c2"):
        await open_case(client, alice, id=cid)
    await hearing(client, alice, "solo", "c1", "2026-03-01")
    await hearing(client, alice, "shared", "c1", "2026-03-02", alsoCaseIds=["c2"])
    await client.put("/v1/plans/p1", json={"title": "Both", "caseIds": ["c1", "c2"], "hearingIds": ["solo", "shared"]}, headers=alice)

    assert (await client.delete("/v1/cases/c1", headers=alice)).status_code == 204
    assert (await client.get("/v1/hearings/solo", headers=alice)).status_code == 404
    shared = (await client.get("/v1/hearings/shared", headers=alice)).json()
    assert shared["caseId"] == "c2" and "alsoCaseIds" not in shared
    plan = (await client.get("/v1/plans/p1", headers=alice)).json()
    assert plan["caseIds"] == ["c2"] and plan["hearingIds"] == ["shared"]


async def test_plan_coverage_of_cases_and_hearings(client, alice):
    for cid in ("c1", "c2"):
        await open_case(client, alice, id=cid)
    await hearing(client, alice, "h1", "c1", alsoCaseIds=["c2"])
    await client.put("/v1/plans/p1", json={"title": "P"}, headers=alice)

    covered = (await client.post("/v1/plans/p1/cover", json={"kind": "hearing", "id": "h1"}, headers=alice)).json()
    assert covered["caseIds"] == ["c1", "c2"] and covered["hearingIds"] == ["h1"] and covered["workspace"] == "sb"
    assert (await client.post("/v1/plans/p1/cover", json={"kind": "case", "id": "ghost"}, headers=alice)).status_code == 422
    assert (await client.put("/v1/plans/p2", json={"title": "P", "caseIds": ["ghost"]}, headers=alice)).status_code == 422

    # dropping a hearing leaves the cases; dropping a case drops the hearings only it justified
    assert (await client.post("/v1/plans/p1/uncover", json={"kind": "hearing", "id": "h1"}, headers=alice)).json().get("hearingIds") is None
    await client.post("/v1/plans/p1/cover", json={"kind": "hearing", "id": "h1"}, headers=alice)
    only_c1 = (await client.post("/v1/plans/p1/uncover", json={"kind": "case", "id": "c2"}, headers=alice)).json()
    assert only_c1["caseIds"] == ["c1"] and only_c1["hearingIds"] == ["h1"]  # c1 still covers h1
    none = (await client.post("/v1/plans/p1/uncover", json={"kind": "case", "id": "c1"}, headers=alice)).json()
    assert "caseIds" not in none and "hearingIds" not in none


async def test_users_cannot_reach_each_others_cases_and_hearings(client, alice, bob):
    await open_case(client, alice, id="c1")
    await hearing(client, alice, "h1")
    assert (await client.get("/v1/cases/c1", headers=bob)).status_code == 404
    assert (await client.put("/v1/cases/c1/engagement", json={"engagement": None}, headers=bob)).status_code == 404
    assert (await client.post("/v1/hearings/h1/record", json={"summary": "s", "outcome": {"status": "done"}}, headers=bob)).status_code == 404
    assert (await client.put("/v1/hearings/h9", json={"caseId": "c1", "date": "2026-03-10", "purpose": "x"}, headers=bob)).status_code == 422
