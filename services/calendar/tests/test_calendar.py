from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from app.application.service import _within
from app.domain.calendar import (
    Event,
    Link,
    Source,
    body_for_follow_up,
    body_for_task,
    desired_events,
    digest,
    event_from_google,
    plan_export,
    view,
)
from sqlalchemy import text

from .conftest import APP, event, headers

PRIMARY = [{"id": "a@x.org", "summary": "Work", "primary": True, "accessRole": "owner", "backgroundColor": "#0b8043", "timeZone": "Asia/Kolkata"}, {"id": "team@x.org", "summary": "Team", "accessRole": "reader", "selected": False}]
SOON = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()


def src(**over) -> Source:
    base = dict(id="s1", account_id="a1", user_id="u1", google_id="g", name="Work", color="#111111", role="owner", timezone="Asia/Kolkata")
    return Source(**{**base, **over})


# --- the pieces ---------------------------------------------------------------------------------------------------


def test_a_google_event_is_read_as_it_is_and_the_ones_to_skip_are_skipped():
    timed = event_from_google(event("e1", "Hearing prep", "2026-11-05T09:30:00+05:30", "2026-11-05T11:00:00+05:30", hangoutLink="https://meet.google.com/x", attendees=[{}, {}]), src())
    assert timed and timed.start == datetime(2026, 11, 5, 4, 0, tzinfo=UTC) and timed.meet == "https://meet.google.com/x" and timed.attendees == 2 and timed.id == "s1:e1"
    day = event_from_google(event("e2", "Holiday", "2026-11-12", "2026-11-14"), src())
    assert day and day.all_day and day.end == datetime(2026, 11, 14, tzinfo=UTC)
    assert event_from_google(event("e3", "Gone", "2026-11-05", "2026-11-06", status="cancelled"), src()) is None
    assert event_from_google(event("e4", "Ours", "2026-11-05", "2026-11-06", extendedProperties={"private": {"stiamoRef": "meeting:m1"}}), src()) is None
    assert event_from_google({"id": "e5", "start": {}, "end": {}}, src()) is None


def test_events_are_shown_on_the_clock_of_the_person():
    e = Event("s1:e", "u1", "s1", "e", "Call", "", "", False, datetime(2026, 11, 5, 18, 0, tzinfo=UTC), datetime(2026, 11, 5, 19, 30, tzinfo=UTC), "", "")
    v = view(e, "Asia/Kolkata")  # 23:30 to 01:00 the next day in India
    assert v["date"] == "2026-11-05" and v["startMinute"] == 23 * 60 + 30 and v["endMinute"] == 24 * 60 and v["endDate"] == "2026-11-06"
    assert view(e, "UTC")["startMinute"] == 18 * 60 and view(e, "UTC")["endDate"] == "2026-11-05"
    allday = Event("s1:h", "u1", "s1", "h", "Holiday", "", "", True, datetime(2026, 11, 12, tzinfo=UTC), datetime(2026, 11, 14, tzinfo=UTC), "", "")
    assert view(allday, "Asia/Kolkata")["date"] == "2026-11-12" and view(allday, "Asia/Kolkata")["endDate"] == "2026-11-13"


def test_what_should_be_in_google_follows_the_open_meetings_and_hearings():
    today = datetime(2026, 11, 1, tzinfo=UTC).date()
    meetings = [{"id": "m1", "title": "Union leaders", "workspace": "sb", "slot": {"date": "2026-11-05", "start": 600, "duration": 60}, "location": "Office", "outcome": {"status": "open"}}, {"id": "m2", "title": "Old", "workspace": "sb", "slot": {"date": "2026-09-01", "start": 600, "duration": 60}}, {"id": "m3", "title": "Off", "workspace": "sb", "slot": {"date": "2026-11-06", "start": 600, "duration": 60}, "outcome": {"status": "cancelled"}}]
    hearings = [{"id": "h1", "caseId": "c1", "date": "2026-11-07", "time": 900, "duration": 45, "purpose": "Arguments", "forum": "JCL Kurnool"}, {"id": "h2", "caseId": "c1", "date": "2026-11-08", "purpose": "Orders"}]
    out = desired_events(meetings, hearings, [{"id": "c1", "reference": "SA-2026-001", "title": "Alur", "court": "JCL"}], "Asia/Kolkata", today)
    assert sorted(out) == ["hearing:h1", "hearing:h2", "meeting:m1"]
    ws, body = out["meeting:m1"]
    assert ws == "sb" and body["summary"] == "Union leaders" and body["start"]["dateTime"].startswith("2026-11-05T10:00:00+05:30") and body["end"]["dateTime"].startswith("2026-11-05T11:00:00+05:30")
    assert body["extendedProperties"]["private"]["stiamoRef"] == "meeting:m1" and body["location"] == "Office"
    ws, hearing = out["hearing:h1"]
    assert ws == "sb" and hearing["summary"] == "Hearing: Arguments (SA-2026-001)" and hearing["location"] == "JCL Kurnool"
    assert out["hearing:h2"][1]["start"] == {"date": "2026-11-08"}  # no time of day: an all-day entry


def test_tasks_and_follow_ups_go_on_the_calendar_at_their_time_or_all_day():
    planned = body_for_task({"id": "t1", "title": "Draft the rejoinder", "priority": "high", "notes": "Use the wage list", "slot": {"date": "2026-11-05", "start": 660, "duration": 90}}, "Asia/Kolkata")
    assert planned["summary"] == "Task: Draft the rejoinder" and planned["start"]["dateTime"].startswith("2026-11-05T11:00:00+05:30") and planned["end"]["dateTime"].startswith("2026-11-05T12:30:00+05:30")
    assert planned["description"] == "Use the wage list\nPriority: high" and planned["extendedProperties"]["private"]["stiamoRef"] == "task:t1"
    assert body_for_task({"id": "t2", "title": "Pay the fee", "dueDate": "2026-11-06"}, "Asia/Kolkata")["start"] == {"date": "2026-11-06"}
    assert body_for_task({"id": "t3", "title": "Someday"}, "Asia/Kolkata") is None
    call = body_for_follow_up({"id": "f1", "title": "the advocate", "channel": "call", "contact": "Ravindra", "due": {"date": "2026-11-05", "time": 900}, "duration": 20}, "Asia/Kolkata")
    assert call["summary"] == "Call: the advocate" and call["description"] == "With Ravindra" and call["end"]["dateTime"].startswith("2026-11-05T15:20:00+05:30")
    assert body_for_follow_up({"id": "f2", "title": "Hemla", "channel": "message", "due": {"date": "2026-11-07"}}, "Asia/Kolkata")["summary"] == "Message: Hemla"
    assert body_for_follow_up({"id": "f3", "title": "No day"}, "Asia/Kolkata") is None


def test_only_open_tasks_and_follow_ups_stay_on_the_calendar():
    today = datetime(2026, 11, 1, tzinfo=UTC).date()
    tasks = [{"id": "a", "title": "Open", "dueDate": "2026-11-05", "workspace": "sb"}, {"id": "b", "title": "Done", "dueDate": "2026-11-05", "outcome": {"status": "done"}}, {"id": "c", "title": "Long ago", "dueDate": "2026-08-01"}, {"id": "d", "title": "No day"}]
    follow_ups = [{"id": "x", "title": "Call", "due": {"date": "2026-11-05"}, "workspace": "sb"}, {"id": "y", "title": "Finished", "due": {"date": "2026-11-05"}, "outcome": {"status": "done"}}]
    out = desired_events([], [], [], "Asia/Kolkata", today, tasks=tasks, follow_ups=follow_ups)
    assert sorted(out) == ["followUp:x", "task:a"] and out["task:a"][0] == "sb"  # a finished one is not wanted, so it is taken down from Google


def test_a_big_first_run_is_spread_over_several():
    removals, changes, additions = _within(5, list(range(2)), list(range(2)), list(range(10)))
    assert (len(removals), len(changes), len(additions)) == (2, 2, 1)
    assert _within(0, [1], [2], [3]) == ([], [], [])


def test_the_export_plan_adds_changes_moves_and_removes_only_what_it_must():
    body = lambda t: {"summary": t}  # noqa: E731
    desired = {"meeting:new": ("sb", body("n")), "meeting:same": ("sb", body("s")), "meeting:edited": ("sb", body("e2")), "meeting:moved": ("sb", body("m")), "meeting:nowhere": ("fsa", body("f"))}
    links = {
        "meeting:same": Link("u1", "meeting:same", "A", "g1", digest(body("s"))),
        "meeting:edited": Link("u1", "meeting:edited", "A", "g2", digest(body("e1"))),
        "meeting:moved": Link("u1", "meeting:moved", "B", "g3", digest(body("m"))),
        "meeting:gone": Link("u1", "meeting:gone", "A", "g4", "x"),
        "meeting:nowhere": Link("u1", "meeting:nowhere", "B", "g5", "x"),
    }
    plan = plan_export(desired, links, {"sb": "A"})
    assert [(r, s) for r, s, _ in plan.insert] == [("meeting:new", "A"), ("meeting:moved", "A")]
    assert [link.ref for link, _ in plan.update] == ["meeting:edited"]
    assert sorted(link.ref for link in plan.delete) == ["meeting:gone", "meeting:moved", "meeting:nowhere"]  # moved is taken down in B; one with nowhere to go is taken down too


# --- connecting ---------------------------------------------------------------------------------------------------


async def test_it_needs_a_sign_in_and_says_when_google_is_not_set_up(rig):
    assert (await rig.client.get("/v1/calendar/status")).status_code == 401
    assert (await rig.client.get("/v1/calendar/events", params={"from": "2026-11-01", "to": "2026-11-30"})).status_code == 401
    assert (await rig.client.post("/v1/calendar/oauth/start", json={"workspace": "nope"}, headers=headers())).status_code == 422
    rig.app.state.settings.google_client_id = ""
    assert "isn't set up" in (await rig.client.post("/v1/calendar/oauth/start", json={"workspace": "sb"}, headers=headers())).json()["error"]["message"]


async def test_starting_sends_the_person_to_google_with_a_signed_state(rig):
    r = await rig.client.post("/v1/calendar/oauth/start", json={"workspace": "fsa", "email": "me@x.org"}, headers=headers())
    q = parse_qs(urlparse(r.json()["url"]).query)
    assert q["state"][0].count(".") == 2 and q["login_hint"] == ["me@x.org"]


async def test_coming_back_from_google_connects_the_account_and_finds_its_calendars(rig):
    r = await rig.connect("a@x.org", "sb", PRIMARY)
    assert r.status_code == 303 and r.headers["location"] == f"{APP}/sb/calendar?calendar_connected=a%40x.org"
    st = await rig.status()
    (account,) = st["accounts"]
    assert account["email"] == "a@x.org" and account["workspace"] == "sb" and account["status"] == "ok" and st["configured"] is True
    assert len(account["calendars"]) == 2
    by = {c["name"]: c for c in account["calendars"]}
    assert by["Work"]["enabled"] is True and by["Work"]["writable"] is True and by["Work"]["color"] == "#0b8043"
    assert by["Team"]["enabled"] is False and by["Team"]["writable"] is False  # a calendar Google doesn't show is not shown here either
    async with rig.app.state.session_factory() as s:
        sealed = (await s.execute(text("select refresh_sealed from calendar_accounts"))).scalar_one()
    assert "r-a@x.org" not in sealed  # the refresh token is kept sealed


async def test_a_bad_state_a_refusal_or_a_stranger_goes_back_with_a_reason(rig):
    r = await rig.client.get("/v1/calendar/oauth/callback", params={"code": "x", "state": "not-a-state"}, follow_redirects=False)
    assert r.status_code == 303 and "calendar_error" in r.headers["location"] and "expired" in r.headers["location"]
    denied = await rig.client.get("/v1/calendar/oauth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "did+not+allow" in denied.headers["location"]
    rig.app.state.settings.allowed_google_emails = ["mine@x.org"]
    refused = await rig.connect("other@x.org")
    assert "calendar_error" in refused.headers["location"] and "allowed" in refused.headers["location"]
    assert (await rig.status())["accounts"] == []


async def test_google_giving_no_lasting_access_is_explained(rig):
    rig.google.connect("a@x.org", PRIMARY, refresh=None)
    state = await rig.start()
    r = await rig.client.get("/v1/calendar/oauth/callback", params={"code": "code-a@x.org", "state": state}, follow_redirects=False)
    assert "lasting+access" in r.headers["location"] and (await rig.status())["accounts"] == []


async def test_connecting_the_same_account_again_updates_it_rather_than_doubling_it(rig):
    await rig.connect("a@x.org", "sb", PRIMARY)
    await rig.connect("a@x.org", "sb", PRIMARY)
    assert len((await rig.status())["accounts"]) == 1
    await rig.connect("a@x.org", "fsa", PRIMARY)  # the same Google account in another workspace is another connection
    assert len((await rig.status())["accounts"]) == 2


# --- reading ------------------------------------------------------------------------------------------------------


async def two_workspaces(rig):
    await rig.connect("a@x.org", "sb", PRIMARY)
    await rig.connect("b@y.org", "personal", [{"id": "b@y.org", "summary": "Home", "primary": True, "accessRole": "owner", "backgroundColor": "#8e24aa", "timeZone": "Asia/Kolkata"}])
    rig.google.items["a@x.org"] = [event("w1", "Union meeting", "2026-11-05T10:00:00+05:30", "2026-11-05T11:00:00+05:30"), event("w2", "Diwali", "2026-11-08", "2026-11-09"), event("w3", "Ours", "2026-11-05", "2026-11-06", extendedProperties={"private": {"stiamoRef": "meeting:m1"}})]
    rig.google.items["b@y.org"] = [event("h1", "Dentist", "2026-11-05T17:00:00+05:30", "2026-11-05T17:30:00+05:30")]


async def test_every_workspaces_calendars_are_combined_and_each_can_be_asked_for_alone(rig):
    await two_workspaces(rig)
    out = await rig.sync()
    assert out["pulled"] == 3 and out["errors"] == []
    everything = await rig.events()
    assert [(e["title"], e["workspace"]) for e in everything] == [("Union meeting", "sb"), ("Dentist", "personal"), ("Diwali", "sb")]  # the one Stiamo sent itself is not repeated
    union = everything[0]
    assert union["date"] == "2026-11-05" and union["startMinute"] == 600 and union["endMinute"] == 660 and union["color"] == "#0b8043" and union["calendar"] == "Work" and union["account"] == "a@x.org"
    assert [e["title"] for e in await rig.events(workspace="personal")] == ["Dentist"]
    assert [e["title"] for e in await rig.events(workspace="sb")] == ["Union meeting", "Diwali"]
    assert (await rig.events(start="2026-12-01", to="2026-12-31")) == []
    assert (await rig.events(who=headers("u2", "other@example.com"))) == []  # someone else sees none of it


async def test_a_calendar_can_be_hidden_and_shown_again(rig):
    await two_workspaces(rig)
    await rig.sync()
    work = next(c for a in (await rig.status())["accounts"] if a["email"] == "a@x.org" for c in a["calendars"] if c["name"] == "Work")
    assert (await rig.client.put(f"/v1/calendar/sources/{work['id']}", json={"enabled": False, "color": "#112233"}, headers=headers())).status_code == 204
    assert [e["title"] for e in await rig.events()] == ["Dentist"]
    await rig.client.put(f"/v1/calendar/sources/{work['id']}", json={"enabled": True}, headers=headers())
    assert len(await rig.events()) == 3 and (await rig.events())[0]["color"] == "#112233"
    assert (await rig.client.put(f"/v1/calendar/sources/{work['id']}", json={"color": "red"}, headers=headers())).status_code == 422
    assert (await rig.client.put(f"/v1/calendar/sources/{work['id']}", json={"enabled": False}, headers=headers("u2", "o@x.org"))).status_code == 404


async def test_the_next_read_is_only_what_changed(rig):
    await two_workspaces(rig)
    await rig.sync()
    first = len(rig.google.reads)
    rig.google.changes["a@x.org"] = [event("w1", "Union meeting (moved)", "2026-11-06T10:00:00+05:30", "2026-11-06T11:00:00+05:30"), event("w2", "Diwali", "2026-11-08", "2026-11-09", status="cancelled")]
    await rig.sync()
    assert [t for _, t in rig.google.reads[first:] if t]  # it went on from where it was
    assert [(e["title"], e["date"]) for e in await rig.events(workspace="sb")] == [("Union meeting (moved)", "2026-11-06")]  # changed in place, cancelled one gone


async def test_when_google_forgets_the_changes_feed_everything_is_read_again(rig):
    await two_workspaces(rig)
    await rig.sync()
    rig.google.expired.add("a@x.org")
    rig.google.items["a@x.org"] = [event("w9", "Fresh start", "2026-11-09T09:00:00+05:30", "2026-11-09T10:00:00+05:30")]
    await rig.sync()
    assert [e["title"] for e in await rig.events(workspace="sb")] == ["Fresh start"]


async def test_the_time_zone_is_the_persons_own_and_is_checked(rig):
    await two_workspaces(rig)
    await rig.sync(timezone="UTC")
    assert (await rig.events(workspace="sb"))[0]["startMinute"] == 4 * 60 + 30  # 10:00 in India is 04:30 UTC
    assert (await rig.client.post("/v1/calendar/sync", json={"timezone": "Mars/Base"}, headers=headers())).status_code == 422
    assert (await rig.status())["timezone"] == "UTC"


async def test_an_account_google_no_longer_accepts_is_marked_for_reconnecting(rig):
    await two_workspaces(rig)
    rig.google.live.clear()
    rig.app.state.access._by.clear()
    out = await rig.sync()
    assert len(out["errors"]) == 2 and all("no longer allows" in e for e in out["errors"])
    assert {a["status"] for a in (await rig.status())["accounts"]} == {"reconnect"}
    assert "Connect it again" in (await rig.status())["accounts"][0]["error"]


async def test_disconnecting_forgets_the_account_and_its_events_and_tells_google(rig):
    await two_workspaces(rig)
    await rig.sync()
    account = next(a for a in (await rig.status())["accounts"] if a["email"] == "a@x.org")
    assert (await rig.client.delete(f"/v1/calendar/accounts/{account['id']}", headers=headers("u2", "o@x.org"))).status_code == 404
    assert (await rig.client.delete(f"/v1/calendar/accounts/{account['id']}", headers=headers())).status_code == 204
    assert rig.google.revoked == ["r-a@x.org"]
    assert [e["title"] for e in await rig.events()] == ["Dentist"] and len((await rig.status())["accounts"]) == 1


# --- sending meetings and hearings --------------------------------------------------------------------------------


async def with_export(rig):
    await rig.connect("a@x.org", "sb", PRIMARY)
    work = next(c for c in (await rig.status())["accounts"][0]["calendars"] if c["name"] == "Work")
    assert (await rig.client.put(f"/v1/calendar/sources/{work['id']}", json={"export": True}, headers=headers())).status_code == 204
    rig.practice.tables["cases"] = [{"id": "c1", "reference": "SA-2026-001", "title": "Alur"}]
    rig.practice.tables["meetings"] = [{"id": "m1", "title": "Union leaders", "workspace": "sb", "slot": {"date": SOON, "start": 600, "duration": 60}, "outcome": {"status": "open"}}, {"id": "m2", "title": "Dentist", "workspace": "personal", "slot": {"date": SOON, "start": 1000, "duration": 30}}]
    rig.practice.tables["hearings"] = [{"id": "h1", "caseId": "c1", "date": SOON, "time": 840, "purpose": "Arguments", "outcome": {"status": "open"}}]
    rig.practice.tables["tasks"] = [{"id": "t1", "title": "Draft the rejoinder", "workspace": "sb", "slot": {"date": SOON, "start": 660, "duration": 90}, "outcome": {"status": "open"}}, {"id": "t2", "title": "Buy stamps", "workspace": "personal", "dueDate": SOON}]
    rig.practice.tables["follow-ups"] = [{"id": "f1", "title": "the advocate", "channel": "call", "workspace": "sb", "due": {"date": SOON, "time": 900}, "outcome": {"status": "open"}}]
    return work


async def test_meetings_and_hearings_go_to_the_calendar_chosen_for_their_workspace(rig):
    await with_export(rig)
    out = await rig.sync()
    assert out["sent"] == {"added": 4, "changed": 0, "removed": 0}  # the dentist and the stamps are personal, and nothing is chosen for personal
    sent = {b["extendedProperties"]["private"]["stiamoRef"]: (cal, b) for verb, cal, _, b in rig.google.written if verb == "insert"}
    assert sorted(sent) == ["followUp:f1", "hearing:h1", "meeting:m1", "task:t1"] and sent["task:t1"][1]["summary"] == "Task: Draft the rejoinder" and sent["followUp:f1"][1]["summary"] == "Call: the advocate" and sent["meeting:m1"][0] == "a@x.org" and sent["hearing:h1"][1]["summary"] == "Hearing: Arguments (SA-2026-001)"
    assert rig.practice.tokens and all(t for t in rig.practice.tokens)
    again = await rig.sync()
    assert again["sent"] == {"added": 0, "changed": 0, "removed": 0}  # nothing changed, so nothing is sent twice
    assert len([w for w in rig.google.written if w[0] == "insert"]) == 4


async def test_a_change_in_stiamo_changes_google_and_a_deleted_one_is_taken_down(rig):
    await with_export(rig)
    await rig.sync()
    rig.practice.tables["meetings"][0]["title"] = "Union leaders (moved)"
    rig.practice.tables["hearings"] = []
    rig.practice.tables["tasks"][0]["slot"]["start"] = 720  # moved on the planner
    rig.practice.tables["follow-ups"][0]["outcome"] = {"status": "done"}  # finished
    out = await rig.sync()
    assert out["sent"] == {"added": 0, "changed": 2, "removed": 2}
    verbs = [(v, b.get("summary")) for v, _, _, b in rig.google.written[4:]]
    assert ("update", "Task: Draft the rejoinder") in verbs
    assert ("update", "Union leaders (moved)") in verbs and any(v == "delete" for v, _ in verbs)


async def test_only_one_calendar_per_workspace_receives_and_a_read_only_one_cannot(rig):
    await rig.connect("a@x.org", "sb", [{"id": "one", "summary": "One", "primary": True, "accessRole": "owner"}, {"id": "two", "summary": "Two", "accessRole": "writer"}, {"id": "ro", "summary": "Read only", "accessRole": "reader"}])
    cals = {c["name"]: c["id"] for c in (await rig.status())["accounts"][0]["calendars"]}
    for name in ("One", "Two"):
        assert (await rig.client.put(f"/v1/calendar/sources/{cals[name]}", json={"export": True}, headers=headers())).status_code == 204
    flags = {c["name"]: c["export"] for c in (await rig.status())["accounts"][0]["calendars"]}
    assert flags == {"One": False, "Two": True, "Read only": False}
    assert (await rig.client.put(f"/v1/calendar/sources/{cals['Read only']}", json={"export": True}, headers=headers())).status_code == 422
