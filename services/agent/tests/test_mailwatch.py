import json

from app.domain.llm import ChatResult
from app.domain.mailwatch import FOCUS, watch_config, watch_id, workspace_of
from app.main import watch_everyone

from .conftest import headers
from .test_agent_api import choose, connect
from .test_alerts import alert_id_of, last_keyboard
from .test_channels import send, telegram
from .test_mail_filter import answer

MW = "/v1/agent/mail-watch"
WF = "/v1/agent/workflows"


def box(i, workspace, address):
    return {"id": i, "workspace": workspace, "label": address, "address": address}


async def watches(rig, who=None):
    return (await rig.client.get(MW, headers=who or headers())).json()["workspaces"]


async def agents(rig):
    return (await rig.client.get(WF, headers=headers())).json()["items"]


def test_a_watch_is_made_for_each_kind_of_work_and_knows_which_workspace_it_is_for():
    cfg = watch_config("sb", ["b", "a"])
    assert cfg["source"] == {"kind": "mail", "accountIds": ["a", "b"], "folder": "INBOX"} and cfg["listen"] is True and cfg["notify"] == {"telegram": True, "call": False}
    assert [(lb["name"], lb["route"]) for lb in cfg["labels"]] == [("urgent", "alert"), ("important", "attention"), ("routine", "none")] and "legal and advocacy" in cfg["prompt"]
    assert set(FOCUS) == {"sb", "fsa", "media", "personal"} and all(watch_config(w, ["a"])["prompt"] for w in FOCUS)
    assert watch_id("sb", "abcdef1234567") == "watch-sb-abcdef12" and workspace_of("watch-sb-abcdef12") == "sb" and workspace_of("wf-123") is None and workspace_of("watch-nope-1") is None


async def test_a_workspace_with_a_mailbox_has_its_watch_without_anyone_building_it(rig):
    out = await watches(rig)
    sb = out[0]
    assert sb["workspace"] == "sb" and sb["enabled"] is True and sb["mailboxes"] == ["arjun@sb.example.com"] and sb["agentId"] == watch_id("sb", "u1")
    found = [a for a in await agents(rig) if a["id"] == sb["agentId"]]
    assert len(found) == 1 and found[0]["name"] == "Priority mail watch · S&B" and found[0]["config"]["listen"] is True and found[0]["config"]["source"]["accountIds"] == ["acc1"]
    assert sb["listening"] is True and sb["state"] in ("connecting", "live")
    again = await watches(rig)
    assert len([a for a in await agents(rig) if a["id"].startswith("watch-")]) == 1 and again[0]["agentId"] == sb["agentId"]  # not made twice


async def test_each_workspace_gets_its_own_and_a_new_mailbox_is_added_without_losing_what_was_changed(rig):
    rig.data.accounts = [box("a1", "sb", "x@sb.org"), box("f1", "fsa", "y@fsa.io")]
    await watches(rig)
    wid = watch_id("sb", "u1")
    sb = next(a for a in await agents(rig) if a["id"] == wid)
    edited = {**sb["config"], "prompt": "My own words about what is urgent."}
    assert (await rig.client.put(f"{WF}/{wid}", json={"name": sb["name"], "description": sb["description"], "config": edited}, headers=headers())).status_code == 200
    rig.data.accounts.append(box("a2", "sb", "z@sb.org"))
    out = await watches(rig)
    assert [w["workspace"] for w in out] == ["sb", "fsa"] and out[0]["mailboxes"] == ["x@sb.org", "z@sb.org"]
    now = next(a for a in await agents(rig) if a["id"] == wid)
    assert now["config"]["source"]["accountIds"] == ["a1", "a2"] and now["config"]["prompt"] == "My own words about what is urgent."


async def test_a_watch_can_be_paused_and_stays_paused_and_a_deleted_one_is_not_made_again(rig):
    await watches(rig)
    wid = watch_id("sb", "u1")
    paused = (await rig.client.put(f"{MW}/sb", json={"enabled": False}, headers=headers())).json()["workspaces"][0]
    assert paused["enabled"] is False and paused["listening"] is False
    assert next(a for a in await agents(rig) if a["id"] == wid)["config"]["listen"] is False
    assert (await watches(rig))[0]["listening"] is False  # looking again does not switch it back on
    resumed = (await rig.client.put(f"{MW}/sb", json={"enabled": True}, headers=headers())).json()["workspaces"][0]
    assert resumed["enabled"] is True and resumed["listening"] is True
    assert (await rig.client.delete(f"{WF}/{wid}", headers=headers())).status_code in (200, 204)
    gone = await watches(rig)
    assert gone[0]["agentId"] is None and gone[0]["enabled"] is False and not [a for a in await agents(rig) if a["id"] == wid]  # it counts as off
    assert (await rig.client.put(f"{MW}/nowhere", json={"enabled": False}, headers=headers())).status_code == 422


async def test_the_background_pass_makes_watches_for_people_who_have_not_opened_anything(rig):
    await connect(rig)  # this person now has settings (a provider key)
    assert not [a for a in await agents(rig) if a["id"].startswith("watch-")]
    await watch_everyone(rig.app)
    assert [a["id"] for a in await agents(rig) if a["id"].startswith("watch-")] == [watch_id("sb", "u1")]


async def test_it_says_what_stands_between_the_watch_and_the_phone(rig):
    first = (await watches(rig))[0]
    assert first["model"] is False and first["phone"] == "none"  # no model chosen, no bot paired
    await connect(rig)
    await choose(rig, agent=f"/v1/agent/agents/{watch_id('sb', 'u1')}")
    general = await telegram(rig)
    await send(rig, general, f"/start {general['pairingCode']}", chat=555, update=1)
    second = (await watches(rig))[0]
    assert second["model"] is True and second["phone"] == "general"
    bot = (await rig.client.post("/v1/agent/channels/telegram", json={"botToken": "456:def", "workspace": "sb"}, headers=headers())).json()
    assert (await watches(rig))[0]["phone"] == "general"  # a bot with nobody paired does not count yet
    await send(rig, bot, f"/start {bot['pairingCode']}", chat=888, update=1)
    assert (await watches(rig))[0]["phone"] == "workspace"


async def test_an_urgent_email_reaches_the_phone_with_nothing_built_by_hand(rig):
    rig.app.state.settings.app_url = "https://stiamo.example.com"
    await connect(rig)
    await watches(rig)
    wid = watch_id("sb", "u1")
    await choose(rig, agent=f"/v1/agent/agents/{wid}")
    general = await telegram(rig)
    await send(rig, general, f"/start {general['pairingCode']}", chat=555, update=1)
    rig.script += [answer("urgent"), ChatResult(json.dumps({"summary": "A client needs the details by Friday.", "priority": "high", "action": "Send details", "due": None, "event": None, "replies": [{"label": "Will do", "intent": "Say the details follow today."}]}))]
    me = next(x for x in await agents(rig) if x["id"] == wid)  # the fake mail is old: let the watch look further back than a day
    cfg = {**me["config"], "filters": {**me["config"]["filters"], "sinceDays": 365, "contains": "Partnership"}}
    await rig.client.put(f"{WF}/{wid}", json={"name": me["name"], "description": me["description"], "config": cfg}, headers=headers())
    await rig.client.post(f"{WF}/{wid}/run", json={}, headers=headers())
    alerts = [t for c, t in rig.telegram.sent if c == 555 and "URGENT" in t]
    assert alerts and "S&amp;B" in alerts[0]
    assert any("Will do" in b.get("text", "") for b in last_keyboard(rig)) and alert_id_of(rig)
