from .conftest import headers
from .test_workflows import BASE, config

CASES = "/v1/agent/agents/cases"
LIBRARIAN = "/v1/agent/agents/librarian"


async def agents(rig, who=None):
    return {a["id"]: a for a in (await rig.client.get("/v1/agent/settings", headers=who or headers())).json()["agents"]}


async def put(rig, agent, workspaces, who=None):
    return await rig.client.put(f"{agent}/workspaces", json={"workspaces": workspaces}, headers=who or headers())


async def test_an_agent_starts_in_the_workspaces_its_definition_names(rig):
    listed = await agents(rig)
    assert listed["cases"]["workspaces"] == ["sb"]  # the case agent belongs to S&B
    assert listed["librarian"]["workspaces"] == []  # no default: it shows only under "all workspaces"


async def test_an_agent_can_be_assigned_to_several_workspaces_in_the_apps_order(rig):
    assert (await put(rig, LIBRARIAN, ["media", "sb", "sb", "personal"])).status_code == 204
    assert (await agents(rig))["librarian"]["workspaces"] == ["personal", "sb", "media"]


async def test_it_can_be_taken_out_of_every_workspace(rig):
    await put(rig, CASES, [])
    assert (await agents(rig))["cases"]["workspaces"] == []  # an empty choice is a choice: it doesn't fall back to the default


async def test_unknown_workspaces_and_agents_are_refused(rig):
    bad = await put(rig, CASES, ["sb", "moon"])
    assert bad.status_code == 422 and "moon" in bad.json()["error"]["message"]
    assert (await put(rig, "/v1/agent/agents/nope", ["sb"])).status_code == 404


async def test_assignments_are_per_person(rig):
    await put(rig, LIBRARIAN, ["fsa"])
    other = headers("b@example.com", "u2")
    assert (await agents(rig, other))["librarian"]["workspaces"] == []
    assert (await agents(rig))["librarian"]["workspaces"] == ["fsa"]


async def test_an_agent_you_build_can_be_given_its_workspaces_and_loses_them_when_deleted(rig):
    r = await rig.client.post(BASE, json={"name": "Task sorter", "config": config(), "workspaces": ["fsa", "sb"]}, headers=headers())
    assert r.status_code == 201 and r.json()["workspaces"] == ["sb", "fsa"]
    wid = r.json()["id"]
    assert (await agents(rig))[wid]["workspaces"] == ["sb", "fsa"]
    assert (await put(rig, f"/v1/agent/agents/{wid}", ["media"])).status_code == 204
    assert (await agents(rig))[wid]["workspaces"] == ["media"]
    assert (await rig.client.delete(f"{BASE}/{wid}", headers=headers())).status_code == 204
    assert wid not in await agents(rig)
    assert (await put(rig, f"/v1/agent/agents/{wid}", ["sb"])).status_code == 404

    no_ws = await rig.client.post(BASE, json={"name": "Unfiled", "config": config()}, headers=headers())
    assert no_ws.json()["workspaces"] == []
