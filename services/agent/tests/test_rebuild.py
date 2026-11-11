"""Side by side: an older mail-sorting agent, and the workflow rebuilt from it, are given the same mailbox and the same answers from the model.
They should keep the same mail out of the judging, move the same mail to Trash, and leave the same mail alone."""

import json

from app.domain.llm import ChatResult

from .conftest import headers
from .test_mail_filter import BASE, answer, config, make

RUN = lambda a: f"/v1/agent/agents/{a}/flow/run"  # noqa: E731


async def rebuild(rig, wid):
    r = await rig.client.post(f"{BASE}/{wid}/rebuild", headers=headers())
    assert r.status_code == 201, r.text
    return r.json()


async def go(rig, agent, scheduled=True):
    r = await rig.client.post(RUN(agent), json={"message": "go", "scheduled": scheduled}, headers=headers())
    assert r.status_code == 200, r.text


async def proposed_trash(rig):
    cards = (await rig.client.get("/v1/agent/proposals", headers=headers())).json()["items"]
    return [p for p in cards if p["action"] == "mail.trash"]


async def old_review(rig, wid):
    return (await rig.client.get(f"{BASE}/{wid}/review", headers=headers())).json()["items"]


def analysis(summary="Worth a look."):
    return ChatResult(json.dumps({"summary": summary, "priority": "normal", "action": "Look at it", "due": None, "event": None}))


async def test_the_rebuilt_agent_keeps_the_same_mail_out_and_proposes_the_same_trash(rig):
    wid = await make(rig, action={"trashLabel": "spam", "auto": False})
    rig.script += [answer("keep"), answer("spam", 0.9)]  # the lead, then the shop (the friend and the union are kept by the rules)
    old = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    old_trash = {i["title"] for i in old["items"] if i.get("action") == "trash"}
    old_kept_by_rules = old["counts"]["protected"]
    assert old_trash == {"50% off everything"} and old_kept_by_rules == 2 and rig.data.trashed == []

    new = await rebuild(rig, wid)
    assert new["name"].endswith("(canvas)") and any("Review" in x for x in new["notes"])
    rig.script += [answer("keep"), answer("spam", 0.9)]
    await go(rig, new["id"])
    cards = await proposed_trash(rig)
    assert len(cards) == 1 and cards[0]["lines"][0].startswith("50% off everything")  # the same mail
    runs = (await rig.client.get(f"/v1/agent/agents/{new['id']}/runs", headers=headers())).json()["items"]
    detail = (await rig.client.get(f"/v1/agent/runs/{runs[0]['id']}", headers=headers())).json()
    trace = {t["node"]: t for t in detail["trace"]}
    assert trace["safe"]["outputs"] == [2, 2]  # the same two kept by the rules: the friend written to, the union's domain
    assert rig.data.trashed == []


async def test_a_mail_that_is_not_the_trash_label_is_kept_however_unsure_and_an_unsure_trash_goes_to_look_at_not_to_trash(rig):
    wid = await make(rig, action={"trashLabel": "spam", "auto": True})
    rig.script += [answer("keep", 0.3), answer("spam", 0.5)]  # not sure it is a keep (kept anyway); not sure it is spam (not moved)
    old = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    by = {i["title"]: i for i in old["items"]}
    assert by["Partnership opportunity"]["status"] == "kept" and by["50% off everything"]["status"] == "needs_review" and rig.data.trashed == []

    new = await rebuild(rig, wid)
    rig.script += [answer("keep", 0.3), answer("spam", 0.5), analysis("A shop that may be spam.")]
    await go(rig, new["id"])
    assert rig.data.trashed == [] and await proposed_trash(rig) == []  # nothing is moved or proposed for the unsure one
    queue = (await rig.client.get("/v1/agent/attention", headers=headers())).json()
    assert queue["count"] == 1 and queue["items"][0]["title"] == "50% off everything"  # it comes to the person instead of waiting in Review


async def test_the_rebuilt_agent_moves_confident_spam_at_once_when_the_old_one_did_and_does_not_redo_it(rig):
    wid = await make(rig, action={"trashLabel": "spam", "auto": True})
    new = await rebuild(rig, wid)
    node = next(x for x in (await rig.client.get(f"/v1/agent/agents/{new['id']}/flow", headers=headers())).json()["nodes"] if x["type"] == "msg.trash")
    assert node["parameters"]["auto"] is True
    rig.script += [answer("keep"), answer("spam", 0.9)]
    await go(rig, new["id"])
    assert rig.data.trashed == [3]  # moved at once, as the old one did
    before = len(rig.llm_log)
    await go(rig, new["id"])
    assert len(rig.llm_log) == before and rig.data.trashed == [3]  # and nothing is judged twice


async def test_the_setup_travels_the_prompt_labels_thresholds_rules_and_model(rig):
    wid = await make(rig, labels=[{"name": "keep", "description": "Real mail.", "threshold": 0.6}, {"name": "spam", "description": "Junk.", "threshold": 0.9}], rules={"protectedDomains": ["bwint.org"], "protectedPhrases": ["hearing"], "protectedAddresses": ["a@b.org"], "protectCorrespondents": False}, batchSize=4, refinements=["Newsletters from the union are not spam."])
    new = await rebuild(rig, wid)
    nodes = {x["id"]: x for x in (await rig.client.get(f"/v1/agent/agents/{new['id']}/flow", headers=headers())).json()["nodes"]}
    sort, safe = nodes["sort"]["parameters"], nodes["safe"]["parameters"]
    assert "Keep only business opportunities." in sort["prompt"] and "Newsletters from the union are not spam." in sort["prompt"]  # the added points travel with the prompt
    assert [(x["name"], x["threshold"]) for x in sort["labels"]] == [("keep", 0.0), ("spam", 0.9)]  # a keep is kept however unsure
    assert sort["batchSize"] == 4 and sort["model"]["modelId"] == "gpt-4o"
    assert safe == {"correspondents": False, "domains": ["bwint.org"], "addresses": ["a@b.org"], "phrases": ["hearing"]}
    assert nodes["mail"]["parameters"]["accounts"] == ["acc1"] and nodes["mail"]["parameters"]["folder"] == "INBOX"


async def test_only_agents_that_sort_a_mailbox_can_be_rebuilt_and_the_old_one_is_left_alone(rig):
    wid = await make(rig, source={"kind": "table", "table": "tasks"}, action={})
    refused = await rig.client.post(f"{BASE}/{wid}/rebuild", headers=headers())
    assert refused.status_code >= 400 and "sort a mailbox" in refused.text
    mail = await make(rig)
    await rebuild(rig, mail)
    assert (await rig.client.get(f"{BASE}/{mail}", headers=headers())).status_code == 200  # still there, unchanged
