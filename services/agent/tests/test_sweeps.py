import asyncio

from app.application import sweeps as sweeps_module
from app.application.sweeps import SweepManager
from stiamo_common.errors import Conflict, ValidationFailed

from .conftest import headers
from .test_mail_filter import BASE, answer, make


async def settled(rig, wid, who=None):
    for _ in range(200):
        s = (await rig.client.get(f"{BASE}/{wid}/sweep", headers=who or headers())).json()["sweep"]
        if s and s["state"] != "running":
            return s
        await asyncio.sleep(0.05)
    raise AssertionError("the sweep never finished")


async def test_a_sweep_works_through_everything_without_anyone_watching(rig):
    wid = await make(rig, filters={"sinceDays": None, "newOnly": True, "limit": 1})
    rig.script += [answer("keep"), answer("keep")]
    started = await rig.client.post(f"{BASE}/{wid}/sweep", headers=headers())
    assert started.status_code == 202 and started.json()["sweep"]["state"] == "running"
    done = await settled(rig, wid)
    assert (done["state"], done["runs"], done["judged"], done["remaining"], done["inRange"]) == ("done", 2, 2, 0, 4)
    assert done["protected"] == 2 and done["kept"] == 2 and done["error"] is None and done["finishedAt"] and done["lastRunId"]
    runs = (await rig.client.get(f"{BASE}/{wid}/executions", headers=headers())).json()["items"]
    assert len(runs) == 2 and {r["id"] for r in runs} >= {done["lastRunId"]}  # each batch is an ordinary execution
    # it is still there to read afterwards
    assert (await rig.client.get(f"{BASE}/{wid}/sweep", headers=headers())).json()["sweep"]["state"] == "done"


async def test_a_sweep_moves_mail_like_any_run_and_stops_when_nothing_is_left(rig):
    wid = await make(rig, filters={"sinceDays": None, "newOnly": True, "limit": 100}, action={"trashLabel": "spam", "auto": True})
    rig.script += [answer("keep"), answer("spam")]
    await rig.client.post(f"{BASE}/{wid}/sweep", headers=headers())
    done = await settled(rig, wid)
    assert done["runs"] == 1 and done["moved"] == 1 and rig.data.trashed == [3]


async def test_a_hand_started_run_is_refused_while_a_sweep_is_going(rig):
    wid = await make(rig)
    release = asyncio.Event()

    async def blocked():
        await release.wait()
        return {"counts": {"items": 0}, "stats": {"remaining": 0}}

    rig.app.state.sweeps.start(wid, "u1", blocked)
    r = await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert r.status_code == 409 and "whole inbox" in r.json()["error"]["message"]
    assert (await rig.client.post(f"{BASE}/{wid}/sweep", headers=headers())).status_code == 409  # one sweep at a time
    assert (await rig.client.post(f"{BASE}/{wid}/test", json={}, headers=headers())).status_code != 409  # a test is fine
    release.set()
    assert (await settled(rig, wid))["state"] == "done"
    rig.script += [answer("keep"), answer("keep")]
    assert (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).status_code == 200  # free again


async def test_sweeps_are_private_and_only_for_agents_that_read_something(rig):
    wid = await make(rig)
    other = headers("b@example.com", "u2")
    assert (await rig.client.post(f"{BASE}/{wid}/sweep", headers=other)).status_code == 404
    assert (await rig.client.get(f"{BASE}/{wid}/sweep", headers=other)).status_code == 404
    assert (await rig.client.get(f"{BASE}/{wid}/sweep", headers=headers())).json() == {"sweep": None}
    manual = (await rig.client.post(BASE, json={"name": "m", "description": "", "config": {"source": {"kind": "manual"}, "prompt": "x", "labels": [{"name": "a", "description": "A"}, {"name": "b", "description": "B"}]}}, headers=headers())).json()["id"]
    assert (await rig.client.post(f"{BASE}/{manual}/sweep", headers=headers())).status_code == 422


async def test_it_can_be_stopped_after_the_run_in_progress():
    manager = SweepManager()
    gate = asyncio.Event()
    calls = 0

    async def forever():
        nonlocal calls
        calls += 1
        await gate.wait()
        gate.clear()
        return {"runId": f"r{calls}", "counts": {"items": 10, "applied": 4}, "stats": {"remaining": 500, "inRange": 600}}

    s = manager.start("w", "u", forever)
    await asyncio.sleep(0.01)
    manager.stop("w")
    assert s.state == "running" and s.stop_requested  # the run in progress is not cut short
    gate.set()
    await asyncio.sleep(0.05)
    assert (s.state, s.runs, s.judged, s.moved, s.remaining, calls) == ("stopped", 1, 10, 4, 500, 1)
    assert not manager.is_active("w")


async def test_a_failure_ends_it_with_a_reason_and_a_busy_agent_is_waited_for(monkeypatch):
    monkeypatch.setattr(sweeps_module, "WAIT_SECONDS", 0.01)
    manager = SweepManager()
    tries = 0

    async def busy_then_fine():
        nonlocal tries
        tries += 1
        if tries < 3:
            raise Conflict("busy")
        return {"counts": {"items": 1}, "stats": {"remaining": 0}}

    s = manager.start("a", "u", busy_then_fine)
    await asyncio.sleep(0.2)
    assert (s.state, s.runs, tries) == ("done", 1, 3)

    async def broken():
        raise ValidationFailed("Choose a provider and a model for this agent first.")

    f = manager.start("b", "u", broken)
    await asyncio.sleep(0.05)
    assert f.state == "failed" and f.error == "Choose a provider and a model for this agent first."

    async def all_fail():
        return {"counts": {"items": 3, "failed": 3}, "stats": {"remaining": 90}}

    g = manager.start("c", "u", all_fail)
    await asyncio.sleep(0.05)
    assert (g.state, g.runs) == ("done", 1)  # it doesn't keep hammering a provider that is refusing everything


async def test_closing_the_service_ends_running_sweeps():
    manager = SweepManager()

    async def slow():
        await asyncio.sleep(60)

    s = manager.start("w", "u", slow)
    await asyncio.sleep(0.01)
    await manager.close()
    assert s.state == "stopped" and "restarted" in s.error
