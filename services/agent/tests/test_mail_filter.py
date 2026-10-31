import json

from app.domain.llm import ChatResult
from app.domain.workflows import Item, Rules, protected_by

from .conftest import headers
from .test_agent_api import choose, connect

BASE = "/v1/agent/workflows"
LABELS = [
    {"name": "keep", "description": "A genuine business opportunity or real correspondence."},
    {"name": "spam", "description": "Marketing, newsletters, cold sales, anything unrelated."},
]


def config(**over):
    cfg = {
        "source": {"kind": "mail", "accountId": "acc1", "folder": "INBOX"},
        "filters": {"sinceDays": 365, "newOnly": True, "limit": 50},
        "prompt": "Keep only business opportunities.",
        "labels": LABELS,
        "decider": "model",
        "defaultThreshold": 0.85,
        "rules": {"protectedDomains": ["bwint.org"], "protectedPhrases": [], "protectCorrespondents": True},
        "action": {"trashLabel": "spam", "auto": True},
    }
    cfg.update(over)
    return cfg


def answer(label, confidence=0.95):
    return ChatResult(json.dumps({"label": label, "confidence": confidence, "reason": "because"}))


async def make(rig, **over):
    await connect(rig)
    r = await rig.client.post(BASE, json={"name": "Spam filter", "description": "", "config": config(**over)}, headers=headers())
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    await choose(rig, agent=f"/v1/agent/agents/{wid}")
    return wid


def test_protected_by_domain_phrase_and_correspondents():
    rules = Rules(protected_domains=["bwint.org"], protected_phrases=["migrant forum"], protect_correspondents=True)
    mail = lambda addr, title="x", text="": Item("1:1", title, text, None, {"from": addr})  # noqa: E731
    assert protected_by(mail("a@bwint.org"), rules, set()) == "protected domain"
    assert protected_by(mail("a@eu.bwint.org"), rules, set()) == "protected domain"
    assert protected_by(mail("a@notbwint.org"), rules, set()) is None
    assert protected_by(mail("a@x.com", "About Migrant Forum"), rules, set()) == "protected phrase"
    assert protected_by(mail("pal@x.com"), rules, {"pal@x.com"}) == "someone you have written to"
    assert protected_by(mail("pal@x.com"), Rules(protect_correspondents=False), {"pal@x.com"}) is None


async def test_a_test_run_judges_but_never_moves_anything(rig):
    wid = await make(rig)
    rig.script += [answer("keep"), answer("spam")]
    out = (await rig.client.post(f"{BASE}/{wid}/test", json={}, headers=headers())).json()
    assert out["counts"] == {"items": 2, "proposed": 1, "needsReview": 0, "failed": 0, "kept": 1, "applied": 0, "protected": 2}
    by = {i["title"]: i for i in out["items"]}
    assert by["Partnership opportunity"]["status"] == "kept" and "action" not in by["Partnership opportunity"]
    assert by["50% off everything"]["action"] == "trash" and by["50% off everything"]["status"] == "proposed"
    assert rig.data.trashed == []


async def test_a_run_moves_confident_spam_to_trash_and_does_not_redo_it(rig):
    wid = await make(rig)
    rig.script += [answer("keep"), answer("spam", 0.9)]
    out = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert rig.data.trashed == [3]
    assert out["counts"]["applied"] == 1 and out["counts"]["kept"] == 1 and "moved to Trash" in out["summary"] and "2 protected" in out["summary"]
    again = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert again["counts"]["items"] == 0 and rig.data.trashed == [3]
    assert (await rig.client.get(f"{BASE}/{wid}/review", headers=headers())).json() == {"items": []}


async def test_an_unsure_mail_is_left_alone_for_review(rig):
    wid = await make(rig)
    rig.script += [answer("keep"), answer("spam", 0.6)]
    out = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert rig.data.trashed == [] and out["counts"]["needsReview"] == 1
    queue = (await rig.client.get(f"{BASE}/{wid}/review", headers=headers())).json()["items"]
    assert [q["title"] for q in queue] == ["50% off everything"]


async def test_without_auto_the_move_waits_for_approval(rig):
    wid = await make(rig, action={"trashLabel": "spam", "auto": False})
    rig.script += [answer("keep"), answer("spam")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert rig.data.trashed == []
    item = (await rig.client.get(f"{BASE}/{wid}/review", headers=headers())).json()["items"][0]
    assert item["status"] == "proposed" and item["id"] == "5:3"
    r = await rig.client.post(f"{BASE}/{wid}/review", json={"itemIds": [item["id"]], "action": "approve"}, headers=headers())
    assert r.json() == {"changed": 1, "moved": 1} and rig.data.trashed == [3]


async def test_dismissing_moves_nothing(rig):
    wid = await make(rig, action={"trashLabel": "spam", "auto": False})
    rig.script += [answer("keep"), answer("spam")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    r = await rig.client.post(f"{BASE}/{wid}/review", json={"itemIds": ["5:3"], "action": "dismiss"}, headers=headers())
    assert r.json() == {"changed": 1, "moved": 0} and rig.data.trashed == []


async def test_a_mailbox_that_cannot_be_read_says_why(rig):
    wid = await make(rig, source={"kind": "mail", "accountId": "down", "folder": "INBOX"})
    r = await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert r.status_code == 422 and "username and password" in r.json()["error"]["message"]


async def test_setup_rules(rig):
    bad = lambda **o: rig.client.post(BASE, json={"name": "x", "description": "", "config": config(**o)}, headers=headers())  # noqa: E731
    assert (await bad(source={"kind": "mail"})).status_code == 422
    assert (await bad(action={"trashLabel": "nope", "auto": True})).status_code == 422
    assert (await bad(source={"kind": "manual"})).status_code == 422  # Trash needs a mailbox
    ok = await bad(rules={"protectedDomains": ["@BWINT.org ", "*.mfasia.org", "bwint.org"], "protectedPhrases": [" MFA "]})
    assert ok.status_code == 201
    got = (await rig.client.get(f"{BASE}/{ok.json()['id']}", headers=headers())).json()["config"]["rules"]
    assert got["protectedDomains"] == ["bwint.org", "mfasia.org"] and got["protectedPhrases"] == ["mfa"]
