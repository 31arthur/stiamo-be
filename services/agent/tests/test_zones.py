"""Where the owner is: their day, their times and what they say follow it, and the records stay on the home (India) clock. The tools convert; the model never does."""

import json
from datetime import UTC, datetime

from app.domain.llm import ChatResult, ToolCall
from app.domain.zones import convert, resolve_zone, say, where_is_the_owner

from .conftest import headers
from .test_actions import PLANNER, fill
from .test_agent_api import choose, connect


def test_places_become_zones_and_times_convert_with_the_day_changing_when_it_must():
    assert [resolve_zone(x) for x in ("Qatar", "Brazil", "São Paulo", "Brazil time", "Rio", "new york", "Asia/Qatar", "nowhere land")] == ["Asia/Qatar", "America/Sao_Paulo", "America/Sao_Paulo", "America/Sao_Paulo", "America/Sao_Paulo", "America/New_York", "Asia/Qatar", None]
    assert convert("2026-11-21", 20 * 60 + 15, "America/Sao_Paulo", "Asia/Kolkata") == ("2026-11-22", 4 * 60 + 45)  # 8:15 PM in Brazil is the next morning in India
    assert convert("2026-11-22", 16 * 60, "Asia/Qatar", "Asia/Kolkata") == ("2026-11-22", 18 * 60 + 30)
    assert convert("2026-11-23", 145, "Asia/Kolkata", "Asia/Kolkata") == ("2026-11-23", 145)
    assert say("2026-11-21", 20 * 60 + 15, "America/Sao_Paulo") == "8:15 PM São Paulo (4:45 AM India, Sun 22 Nov)" and say("2026-11-23", 145, "Asia/Kolkata") == "2:25 AM"


def test_a_trip_ends_by_itself_on_the_day_the_owner_said_they_are_back():
    trip = {"timezone": "Asia/Qatar", "home": "Asia/Kolkata", "backOn": "2026-11-23"}
    assert where_is_the_owner(trip, datetime(2026, 11, 21, 10, tzinfo=UTC)) == ("Asia/Qatar", "Asia/Kolkata")
    assert where_is_the_owner(trip, datetime(2026, 11, 24, 10, tzinfo=UTC)) == ("Asia/Kolkata", "Asia/Kolkata")
    assert where_is_the_owner(None, datetime(2026, 11, 21, tzinfo=UTC)) == ("Asia/Kolkata", "Asia/Kolkata")


async def ask(rig, message, calls, reply="Done."):
    rig.script += [ChatResult("", [ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(calls)]), ChatResult(reply)]
    return (await rig.client.post(f"{PLANNER}/chat", json={"message": message}, headers=headers())).json()


async def last_steps(rig):
    runs = (await rig.client.get(f"{PLANNER}/runs", headers=headers())).json()["items"]
    return {s["tool"]: json.loads(s["result"]) for s in (await rig.client.get(f"/v1/agent/runs/{runs[0]['id']}", headers=headers())).json()["steps"]}


async def settings(rig):
    return (await rig.client.get("/v1/agent/day-plans", headers=headers())).json()["settings"]


async def test_saying_where_you_are_moves_the_owners_clock_and_going_home_puts_it_back(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    await ask(rig, "I'm in Qatar till Monday", [("set_timezone", {"place": "Qatar", "until": "2099-11-23"})])
    out = (await last_steps(rig))["set_timezone"]
    assert out["zone"] == "Asia/Qatar" and out["label"] == "Qatar" and out["home"] == "India" and out["backOn"] == "2099-11-23" and out["utcOffset"] == "+03:00"
    assert (await settings(rig))["timezone"] == "Asia/Qatar"
    system = (await rig.client.post(f"{PLANNER}/prompt/preview", json={"question": "x"}, headers=headers())).json()["system"]
    assert "is in Qatar (UTC+03:00)" in system and "kept on India time" in system  # the prompt says where they are, and which clock the records are on
    await ask(rig, "I'm back", [("set_timezone", {"place": "India"})])
    assert (await settings(rig))["timezone"] == "Asia/Kolkata"
    await ask(rig, "I'm in Narnia", [("set_timezone", {"place": "Narnia"})])
    assert "don't know which time zone" in (await last_steps(rig))["set_timezone"]["error"] and (await settings(rig))["timezone"] == "Asia/Kolkata"


async def test_times_said_in_other_places_are_converted_by_the_tool_shown_on_both_clocks_and_stored_on_the_home_clock(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    await ask(rig, "I'm in Brazil", [("set_timezone", {"place": "Brazil"})])
    # the flight is on Brazil's clock (where the owner is); landing in Qatar is on Qatar's; getting home is on India's
    await ask(rig, "my trip", [
        ("add_task", {"title": "Flight to Qatar", "date": "2026-11-21", "start": "20:15"}),
        ("add_task", {"title": "Land in Qatar", "date": "2026-11-22", "start": "16:00", "timezone": "Qatar"}),
        ("add_task", {"title": "Land in Hyderabad", "date": "2026-11-23", "start": "02:25", "timezone": "India"}),
    ])
    cards = (await rig.client.get("/v1/agent/proposals", headers=headers())).json()["items"]
    by = {c["title"].removeprefix("New task: "): c for c in cards}
    assert "8:15 PM São Paulo (4:45 AM India, Sun 22 Nov)" in " ".join(by["Flight to Qatar"]["lines"])
    assert "4:00 PM Qatar (6:30 PM India)" in " ".join(by["Land in Qatar"]["lines"]) and "2:25 AM" in " ".join(by["Land in Hyderabad"]["lines"]) and "India (" not in " ".join(by["Land in Hyderabad"]["lines"])
    for c in cards:
        assert (await rig.client.post(f"/v1/agent/proposals/{c['id']}/apply", headers=headers())).status_code == 200
    saved = {r["title"]: r for r in rig.data.tables["tasks"]}
    assert saved["Flight to Qatar"]["slot"] == {"date": "2026-11-22", "start": 4 * 60 + 45, "duration": 30}  # stored on the home clock, the day changed by the code
    assert saved["Land in Qatar"]["slot"]["start"] == 18 * 60 + 30 and saved["Land in Hyderabad"]["slot"] == {"date": "2026-11-23", "start": 145, "duration": 30}
    r = await ask(rig, "x", [("add_task", {"title": "Bad", "date": "2026-11-22", "start": "10:00", "timezone": "Narnia"})])
    assert "don't know which time zone" in (await last_steps(rig))["add_task"]["error"]


async def test_items_are_shown_on_the_clock_the_owner_is_on_and_a_move_to_a_time_is_read_on_it(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    await ask(rig, "I'm in Qatar", [("set_timezone", {"place": "Qatar"})])
    await ask(rig, "today", [("find_items", {"day": "today", "query": "Draft"})])
    item = (await last_steps(rig))["find_items"]["items"][0]
    # the task is stored at 10:00 India time; in Qatar (2h30 behind) it is 07:30
    assert item["title"] == "Draft the rejoinder" and item["time"] == "07:30"
    await ask(rig, "move it", [("change_item", {"ref": "task:t1", "action": "move", "to": "tomorrow", "start_minute": 16 * 60})])
    did = (await last_steps(rig))["change_item"]["did"]
    assert did.endswith("16:00: Draft the rejoinder") or "16:00" in did  # said back on the clock the owner is on
    stored = {r["id"]: r for t in rig.data.tables.values() for r in t}["t1"]["slot"]["start"]
    assert stored == 16 * 60 + 150  # 4 PM in Qatar is 18:30 India: that is what is stored
