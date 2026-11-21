"""The app's Action items page (every workspace, one shape) and Rapport page."""

from .conftest import headers
from .test_actions import PLANNER, fill
from .test_agent_api import choose, connect


async def test_the_action_items_page_lists_everything_open_and_acts_on_it(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)
    got = (await rig.client.get("/v1/agent/action-items", headers=headers())).json()
    refs = {i["ref"] for i in got["items"]}
    assert {"task:t1", "task:t2", "followUp:f1", "meeting:m1"} <= refs and "task:t4" not in refs  # the done one is not open
    assert got["total"] == len(got["items"])
    done = await rig.client.post("/v1/agent/action-items/task:t2/act", json={"action": "complete"}, headers=headers())
    assert done.status_code == 200 and done.json()["changesetId"]
    again = {i["ref"] for i in (await rig.client.get("/v1/agent/action-items", headers=headers())).json()["items"]}
    assert "task:t2" not in again
    undone = await rig.client.post(f"/v1/agent/action-items/changes/{done.json()['changesetId']}/undo", headers=headers())
    assert undone.status_code == 200
    bad = await rig.client.post("/v1/agent/action-items/task:t1/act", json={"action": "explode"}, headers=headers())
    assert bad.status_code == 422
    assert (await rig.client.get("/v1/agent/action-items/history", headers=headers())).json()["events"]


async def test_the_rapport_page_starts_empty(rig):
    await connect(rig)
    got = (await rig.client.get("/v1/agent/rapport", headers=headers())).json()
    assert got["plans"] == [] and got["status"]["plans"] == 0
