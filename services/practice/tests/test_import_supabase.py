from app.tools.import_supabase import import_export

NOW = "2026-02-01T10:00:00.000Z"


def row(user, **doc):
    return {"userId": user, "data": doc}


def export_for(user="alice-id"):
    return {"exportedAt": "2026-02-02T00:00:00+00:00", "tables": {
        "counsel": [row(user, id="k1", kind="firm", name="Iyer LLP", expertise=["pow"], proBono=False, status="active", notes="", userId=user)],
        "clients": [row(user, id="cl1", kind="company", name="Acme", status="active", notes="")],
        "projects": [
            row(user, id="pr1", title="Site", clientId="cl1", status="active", description=""),
            row(user, id="pr-orphan", title="Lost", clientId="gone", status="active", description="")],
        "cases": [
            row(user, id="c1", reference="SA-2026-001", organization="SA", type="pil", title="Public interest", status="active",
                summary="", engagement={"counselId": "k1", "proBono": True}, createdAt=NOW, updatedAt=NOW),
            row(user, id="c2", reference="SA-2026-002", organization="SA", type="mystery", title="Old type", status="active", summary=""),
            row(user, id="c3", reference="SA-2026-001", organization="SA", title="Same number twice", status="active", summary=""),
            row(user, id="c4", reference="SA-2026-004", organization="SA", title="Gone counsel", status="active", summary="",
                engagement={"counselId": "vanished", "proBono": False}),
            row(user, id="c5", reference="SA-2026-005", organization="NOPE", title="Bad org", status="active", summary="")],
        "hearings": [
            row(user, id="h1", caseId="c1", alsoCaseIds=["c2", "ghost"], date="2026-03-10", time=630, duration=30,
                purpose="Arguments", outcome={"status": "open"}, order="Interim stay", legacyField="ignored"),
            row(user, id="h-orphan", caseId="ghost", date="2026-03-11", duration=30, purpose="x", outcome={"status": "open"}),
            row(user, id="h-bad", caseId="c1", date="11/03/2026", duration=30, purpose="x", outcome={"status": "open"})],
        "plans": [
            row(user, id="p1", title="Old plan", workspace="sb", status="active", caseId="c1", hearingId="h1", objective=""),
            row(user, id="p2", title="Dangling", workspace="sb", status="active", caseIds=["c1", "ghost"], hearingIds=["ghost"], objective="")],
        "goals": [row(user, id="g1", title="Goal", horizon="quarter", startDate="2026-01-01", targetDate="2026-03-31",
                      planIds=["p1", "missing"], keyResults=[], checkIns=[], status="active")],
        "tasks": [
            row(user, id="t1", title="Do", workspace="sb", priority="normal", estimate=30, planId="p1", order=0,
                outcome={"status": "done", "recordedAt": NOW}),
            row(user, id="t-bad", title="Bad", estimate=0)],
        "follow_ups": [row(user, id="f1", title="Call", channel="call", workspace="sb", duration=15, outcome={"status": "open"},
                           attempts=[{"date": "2026-02-01", "minute": 600, "result": "no_answer"}])],
        "meetings": [row(user, id="m1", kind="appointment", title="Visa", workspace="fsa", mode="in_person", attendees=["A"],
                         slot={"date": "2026-03-10", "start": 600, "duration": 30}, agenda=[], confirmations=["day_before"],
                         outcome={"status": "open"})],
        "links": [row(user, id="l1", a={"kind": "case", "id": "c1"}, b={"kind": "client", "id": "cl1"})],
        "activity": [row(user, id="a1", at=NOW, ref={"kind": "case", "id": "c1"}, title="SA-2026-001", action="created", summary="Case created")],
    }}


async def run(app, export, dry_run=False):
    async with app.state.session_factory() as session:
        report = await import_export(session, export, dry_run)
        await session.commit()
    return report


def skipped(report):
    return {(s["table"], s["id"]) for s in report.skipped}


async def test_import_keeps_what_is_valid_and_reports_the_rest(app, client, alice):
    report = await run(app, export_for())
    assert skipped(report) == {("projects", "pr-orphan"), ("cases", "c3"), ("cases", "c5"), ("hearings", "h-orphan"),
                               ("hearings", "h-bad"), ("tasks", "t-bad")}
    assert report.imported["cases"] == 3 and report.imported["hearings"] == 1 and report.imported["plans"] == 2

    cases = {c["id"]: c for c in (await client.get("/v1/cases", headers=alice)).json()["items"]}
    assert set(cases) == {"c1", "c2", "c4"}
    assert cases["c1"]["type"] == "public_interest" and cases["c1"]["engagement"]["proBono"] is True
    assert cases["c1"]["createdAt"] == NOW  # history is preserved
    assert cases["c2"]["type"] == "other" and "engagement" not in cases["c4"]

    h1 = (await client.get("/v1/hearings/h1", headers=alice)).json()
    assert h1["alsoCaseIds"] == ["c2"] and h1["order"] == "Interim stay" and "legacyField" not in h1

    plans = {p["id"]: p for p in (await client.get("/v1/plans", headers=alice)).json()["items"]}
    assert plans["p1"]["caseIds"] == ["c1"] and plans["p1"]["hearingIds"] == ["h1"]  # old single ids folded into lists
    assert plans["p2"]["caseIds"] == ["c1"] and "hearingIds" not in plans["p2"]
    assert (await client.get("/v1/goals/g1", headers=alice)).json()["planIds"] == ["p1"]
    assert (await client.get("/v1/follow-ups/f1", headers=alice)).json()["attempts"][0]["result"] == "no_answer"
    assert (await client.get("/v1/meetings/m1", headers=alice)).json()["confirmations"] == ["day_before"]
    assert (await client.get("/v1/plans/p1/progress", headers=alice)).json()["done"] == 1
    assert len((await client.get("/v1/links", params={"kind": "case", "id": "c1"}, headers=alice)).json()["items"]) == 1
    assert (await client.get("/v1/activity", params={"kind": "case", "id": "c1"}, headers=alice)).json()["items"][0]["summary"] == "Case created"


async def test_importing_twice_changes_nothing(app, client, alice):
    await run(app, export_for())
    before = {t: (await client.get(f"/v1/{t}", headers=alice)).json()["items"] for t in ("cases", "plans", "tasks", "hearings")}
    await run(app, export_for())
    after = {t: (await client.get(f"/v1/{t}", headers=alice)).json()["items"] for t in before}
    assert before == after


async def test_dry_run_writes_nothing(app, client, alice):
    report = await run(app, export_for(), dry_run=True)
    assert report.imported["cases"] == 3
    assert (await client.get("/v1/cases", headers=alice)).json()["items"] == []


async def test_each_users_data_stays_theirs(app, client, alice, bob):
    export = export_for("alice-id")
    export["tables"]["cases"] += [row("bob-id", id="bc1", reference="SA-2026-001", organization="SA", title="Bob's", status="active", summary="")]
    report = await run(app, export)
    assert ("cases", "bc1") not in skipped(report)  # the same case number for another user is fine
    assert [c["id"] for c in (await client.get("/v1/cases", headers=bob)).json()["items"]] == ["bc1"]
    assert "bc1" not in {c["id"] for c in (await client.get("/v1/cases", headers=alice)).json()["items"]}


async def test_imported_records_behave_like_any_other(app, client, alice):
    await run(app, export_for())
    # the next generated case number carries on from the highest imported one (005 was refused: bad organization)
    made = (await client.post("/v1/cases", json={"organization": "SA", "title": "New", "filedOn": "2026-06-01"}, headers=alice)).json()
    assert made["reference"] == "SA-2026-005"
    # and the appointment keeps generating its confirmation call when it moves
    m = (await client.get("/v1/meetings/m1", headers=alice)).json()
    m["slot"]["date"] = "2026-03-20"
    await client.put("/v1/meetings/m1", json=m, headers=alice)
    fus = (await client.get("/v1/follow-ups", headers=alice)).json()["items"]
    assert [f["due"]["date"] for f in fus if f.get("trigger")] == ["2026-03-19"]
