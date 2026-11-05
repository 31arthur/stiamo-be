from app.domain.llm import ChatResult, ToolCall

from .conftest import headers
from .test_agent_api import choose, connect
from .test_planning import PLANNER, TOMORROW, fill

C = "/v1/agent/conversations"


async def ready(rig):
    fill(rig)
    await connect(rig)
    await choose(rig, agent=PLANNER)


async def start(rig, agent="daily-planner", workspace="sb", who=None):
    r = await rig.client.post(C, json={"agentId": agent, "workspace": workspace}, headers=who or headers())
    assert r.status_code == 201, r.text
    return r.json()


async def say(rig, cid, message, who=None):
    return await rig.client.post(f"{C}/{cid}/messages", json={"message": message}, headers=who or headers())


async def test_a_thread_remembers_what_was_said_and_titles_itself(rig):
    await ready(rig)
    c = await start(rig)
    assert c["title"] == "New chat" and c["turns"] == []
    rig.script += [ChatResult("Tomorrow is clear.")]
    first = (await say(rig, c["id"], "What does tomorrow look like?")).json()
    assert first["turn"]["answer"] == "Tomorrow is clear." and first["turn"]["question"] == "What does tomorrow look like?"
    assert first["conversation"]["title"] == "What does tomorrow look like?" and first["conversation"]["turns"] == 1
    rig.script += [ChatResult("Then call Hemla first.")]
    await say(rig, c["id"], "And who should I call?")
    assert rig.llm_log[-1]["roles"].count("user") == 2 and "assistant" in rig.llm_log[-1]["roles"]  # the second question went with the first exchange
    full = (await rig.client.get(f"{C}/{c['id']}", headers=headers())).json()
    assert [t["question"] for t in full["turns"]] == ["What does tomorrow look like?", "And who should I call?"] and full["title"] == "What does tomorrow look like?"


async def test_threads_belong_to_a_workspace_and_all_shows_every_one(rig):
    await ready(rig)
    sb, personal = await start(rig, workspace="sb"), await start(rig, workspace="personal")
    ids = lambda r: [i["id"] for i in r.json()["items"]]  # noqa: E731
    assert ids(await rig.client.get(C, params={"workspace": "sb"}, headers=headers())) == [sb["id"]]
    assert ids(await rig.client.get(C, params={"workspace": "personal"}, headers=headers())) == [personal["id"]]
    assert set(ids(await rig.client.get(C, params={"workspace": "all"}, headers=headers()))) == {sb["id"], personal["id"]}
    rig.script += [ChatResult("ok")]
    await say(rig, sb["id"], "hello")
    newest_first = ids(await rig.client.get(C, headers=headers()))
    assert newest_first[0] == sb["id"]  # the one just talked in is on top


async def test_a_proposal_in_a_thread_shows_as_saved_after_it_is_approved(rig):
    await ready(rig)
    c = await start(rig)
    rig.script += [ChatResult("", [ToolCall("a", "add_task", {"title": "Draft the rejoinder", "due": TOMORROW})]), ChatResult("Proposed.")]
    card = (await say(rig, c["id"], "add a task to draft the rejoinder")).json()["turn"]["found"][0]
    assert card["kind"] == "proposal" and card["status"] == "pending"
    await rig.client.post(f"/v1/agent/proposals/{card['id']}/apply", headers=headers())
    again = (await rig.client.get(f"{C}/{c['id']}", headers=headers())).json()
    assert again["turns"][0]["found"][0]["status"] == "applied"  # reopened later, the card says so


async def test_a_thread_can_be_renamed_and_deleted_and_is_only_ever_the_owners(rig):
    await ready(rig)
    c = await start(rig)
    stranger = headers("other@example.com", "u2")
    assert (await rig.client.get(f"{C}/{c['id']}", headers=stranger)).status_code == 404
    assert (await say(rig, c["id"], "hi", stranger)).status_code == 404
    assert (await rig.client.delete(f"{C}/{c['id']}", headers=stranger)).status_code == 404
    renamed = await rig.client.patch(f"{C}/{c['id']}", json={"title": "The Alur week"}, headers=headers())
    assert renamed.json()["title"] == "The Alur week"
    assert (await rig.client.delete(f"{C}/{c['id']}", headers=headers())).status_code == 204
    assert (await rig.client.get(f"{C}/{c['id']}", headers=headers())).status_code == 404
    assert (await rig.client.get(C)).status_code == 401


async def test_it_refuses_an_unknown_agent_a_bad_workspace_and_says_when_no_model_is_chosen(rig):
    assert (await rig.client.post(C, json={"agentId": "nope", "workspace": "sb"}, headers=headers())).status_code == 404
    assert (await rig.client.post(C, json={"agentId": "daily-planner", "workspace": "mars"}, headers=headers())).status_code == 422
    c = await start(rig)  # no key, no model yet
    r = await say(rig, c["id"], "plan tomorrow")
    assert r.status_code == 422 and "model" in r.json()["error"]["message"]
    assert (await rig.client.get(f"{C}/{c['id']}", headers=headers())).json()["turns"] == []  # a failed question isn't kept
