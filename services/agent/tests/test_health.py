from .conftest import headers
from .test_agent_api import choose, connect
from .test_channels import send, telegram

H = "/v1/agent/health"
S = "/v1/agent/secrets"


async def health(rig):
    return (await rig.client.get(H, headers=headers())).json()


def by(out):
    return {i["id"]: i for i in out["items"]}


async def test_a_fresh_setup_says_what_is_missing_with_the_worst_first(rig):
    out = await health(rig)
    items = by(out)
    assert items["model"]["state"] == "error" and items["model"]["fix"] == "/agents/settings" and "No AI provider" in items["model"]["detail"]
    assert items["telegram"]["state"] == "error" and items["telegram"]["fix"] == "phone"
    assert items["watch-sb"]["state"] == "error" and "no model" in items["watch-sb"]["detail"].lower() and items["watch-sb"]["fix"].endswith("/settings")
    assert items["box-acc1"]["state"] == "ok" and items["box-acc1"]["test"] == {"kind": "mailbox", "id": "acc1"}
    assert items["public-url"]["state"] == "ok" and items["app-url"]["state"] == "warn" and items["deepgram"]["state"] == "off" and items["call"]["state"] == "off" and items["jev"]["state"] == "off"
    states = [i["state"] for i in out["items"]]
    assert states == sorted(states, key=["error", "warn", "ok", "off"].index) and out["errors"] == 4 and out["problems"] == 5  # no model, no Telegram, the watch has no model, S&B alerts have nowhere to go; and no app link


async def test_a_working_setup_is_green_and_each_connection_can_be_tried(rig):
    rig.app.state.settings.app_url = "https://stiamo.example.com"
    await connect(rig)
    await choose(rig, agent="/v1/agent/agents/cases")
    await rig.client.put(f"{S}/deepgram", json={"value": "dg-key-1234567"}, headers=headers())
    c = await telegram(rig, agent="assistant")
    unpaired = by(await health(rig))[f"tg-{c['id']}"]
    assert unpaired["state"] == "warn" and "no chat is linked" in unpaired["detail"]
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    items = by(await health(rig))
    assert items[f"tg-{c['id']}"]["state"] == "ok" and items[f"tg-{c['id']}"]["test"] == {"kind": "channel", "id": c["id"]} and "1 chat linked" in items[f"tg-{c['id']}"]["detail"]
    assert items["deepgram"]["state"] == "ok" and items["deepgram"]["test"] == {"kind": "secret", "id": "deepgram"}
    assert items["app-url"]["state"] == "ok" and items["deliveries"]["state"] == "ok" and items["deliveries"]["test"] == {"kind": "alert", "id": "sample"}
    assert items["ws-sb"]["state"] == "ok" and "general bot" in items["ws-sb"]["detail"]  # no S&B bot of its own: the general one carries it
    assert items["watch-sb"]["state"] != "error" or "model" not in items["watch-sb"]["detail"].lower()


async def test_a_workspace_bot_with_a_chat_takes_over_that_workspaces_alerts(rig):
    await connect(rig)
    bot = (await rig.client.post("/v1/agent/channels/telegram", json={"botToken": "456:def", "workspace": "sb"}, headers=headers())).json()
    assert by(await health(rig))["ws-sb"]["state"] == "error"  # nothing is linked anywhere
    await send(rig, bot, f"/start {bot['pairingCode']}", chat=888, update=1)
    assert "ws-sb" not in by(await health(rig))  # its own bot is linked: nothing to warn about


async def test_a_mailbox_that_cannot_sign_in_is_an_error_and_can_be_tried(rig):
    rig.data.accounts = [{"id": "down", "workspace": "sb", "label": "Chambers", "address": "x@sb.org", "error": "The server didn't accept that username and password."}]
    box = by(await health(rig))["box-down"]
    assert box["state"] == "error" and "username and password" in box["detail"] and box["fix"] == "/mail"
    bad = await rig.client.post(f"{H}/mailbox/down/test", headers=headers())
    assert bad.status_code == 422 and "username and password" in bad.json()["error"]["message"]
    rig.data.accounts.append({"id": "acc1", "workspace": "sb", "label": "Ok", "address": "y@sb.org"})
    ok = await rig.client.post(f"{H}/mailbox/acc1/test", headers=headers())
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert (await rig.client.post(f"{H}/mailbox/nope/test", headers=headers())).status_code == 422


async def test_the_call_is_ok_only_once_a_call_has_gone_through(rig):
    await rig.client.put("/v1/agent/reminders", json={"enabled": False, "voice": False, "call": True, "callUser": "@arjun"}, headers=headers())
    assert by(await health(rig))["call"]["state"] == "warn" and "no call has gone through" in by(await health(rig))["call"]["detail"]
    rig.caller.fail = True
    assert (await rig.client.post("/v1/agent/reminders/test-call", headers=headers())).status_code == 422
    assert by(await health(rig))["call"]["state"] == "warn"  # a failed one does not count
    rig.caller.fail = False
    await rig.client.post("/v1/agent/reminders/test-call", headers=headers())
    call = by(await health(rig))["call"]
    assert call["state"] == "ok" and "Rings @arjun" in call["detail"]


async def test_messages_that_did_not_arrive_are_a_warning_with_the_log_to_look_at(rig):
    c = await telegram(rig, agent="assistant")
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.telegram.fail_chats = {555}
    await rig.client.post("/v1/agent/alerts/test", headers=headers())
    item = by(await health(rig))["deliveries"]
    assert item["state"] == "warn" and item["fix"] == "log" and "didn't arrive" in item["detail"]


async def test_google_calendar_and_contacts_say_what_is_missing_or_broken(rig):
    assert "calendar" not in by(await health(rig))  # not asked: nothing is claimed
    rig.data.google = {"calendar": {"configured": False, "accounts": []}, "contacts": None}
    items = by(await health(rig))
    assert items["calendar"]["state"] == "off" and "client id" in items["calendar"]["detail"]
    assert items["contacts"]["state"] == "warn" and "didn't answer" in items["contacts"]["detail"]
    rig.data.google = {"calendar": {"configured": True, "accounts": []}, "contacts": {"configured": True, "accounts": [{"email": "a@sb.org", "status": "reconnect", "error": "Google no longer accepts this sign-in."}]}}
    items = by(await health(rig))
    assert items["calendar"]["state"] == "off" and items["calendar"]["fix"] == "/calendar"
    assert items["contacts"]["state"] == "error" and "a@sb.org" in items["contacts"]["detail"] and items["contacts"]["fix"] == "/contacts"
    rig.data.google = {"calendar": {"configured": True, "accounts": [{"email": "a@sb.org", "workspace": "sb", "status": "ok", "calendars": [{"enabled": True}, {"enabled": False}]}]}, "contacts": {"configured": True, "accounts": [{"email": "a@sb.org", "status": "ok", "contacts": 0}]}}
    items = by(await health(rig))
    assert items["calendar"]["state"] == "ok" and "1 account, 1 calendar shown" in items["calendar"]["detail"]
    assert items["calendar-send-sb"]["state"] == "warn" and "Send my schedule here" in items["calendar-send-sb"]["detail"] and items["calendar-send-sb"]["fix"] == "/calendar"
    rig.data.google["calendar"]["accounts"][0]["workspace"] = "sb"
    rig.data.google["calendar"]["accounts"][0]["calendars"][0]["export"] = True
    assert by(await health(rig))["calendar-send-sb"]["state"] == "ok"
    rig.data.google["calendar"]["sends"] = False
    assert by(await health(rig))["calendar-send"]["state"] == "warn"
    rig.data.google["calendar"]["sends"] = True
    assert items["contacts"]["state"] == "warn" and "People API" in items["contacts"]["detail"]
    rig.data.google["contacts"]["accounts"][0]["contacts"] = 212
    assert by(await health(rig))["contacts"]["detail"] == "212 contacts from 1 account."


async def test_health_belongs_to_one_person(rig):
    other = (await rig.client.get(H, headers=headers("b@example.com", "u2"))).json()
    assert by(other)["model"]["state"] == "error"
    assert (await rig.client.get(H)).status_code == 401
