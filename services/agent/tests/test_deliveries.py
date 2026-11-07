from .conftest import headers
from .test_alerts import READ, alert_agent
from .test_channels import send, telegram
from .test_mail_filter import BASE, answer

D = "/v1/agent/deliveries"


async def log(rig, **q):
    return (await rig.client.get(D, params=q, headers=headers())).json()


async def test_an_alert_that_arrives_is_kept_with_where_it_went_and_what_it_was_about(rig):
    wid, _ = await alert_agent(rig)
    rig.script += [answer("urgent"), READ]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    out = await log(rig)
    first = out["items"][0]
    assert (first["kind"], first["channel"], first["ok"], first["workspace"]) == ("alert", "telegram", True, "sb")
    assert first["target"] == "@stiamo_bot · Arj" and first["summary"] == "Partnership opportunity" and first["at"].endswith("Z")
    assert out["last24h"] == {"sent": 1, "failed": 0}


async def test_a_message_telegram_refuses_is_kept_with_the_reason_and_the_other_chats_still_get_it(rig):
    wid, c = await alert_agent(rig)
    code = (await rig.client.post("/v1/agent/channels/" + c["id"] + "/pairing", headers=headers())).json()["pairingCode"]
    await send(rig, c, f"/start {code}", chat=777, update=5)
    rig.telegram.fail_chats = {555}
    rig.script += [answer("urgent"), READ]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    items = (await log(rig))["items"]
    assert sorted((d["ok"], d["error"][:8]) for d in items if d["kind"] == "alert") == [(False, "Telegram"), (True, "")]
    failed = (await log(rig, failed=True))["items"]
    assert len(failed) == 1 and "bot was blocked" in failed[0]["error"] and (await log(rig))["last24h"] == {"sent": 1, "failed": 1}
    assert any(chat == 777 for chat, _ in rig.telegram.sent)  # the working chat got it


async def test_a_reminder_and_the_call_that_goes_with_it_are_both_kept(rig):
    from datetime import UTC, datetime, timedelta

    from .test_reminders import DAY, call, check

    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.data.tables = {"follow-ups": [call("a", 540, "Call him again")], "meetings": [], "tasks": []}
    await rig.client.put("/v1/agent/reminders", json={"enabled": True, "voice": False, "call": True, "callUser": "@arjun"}, headers=headers())
    when = datetime(2026, 11, 6, 9, 40, tzinfo=UTC) - timedelta(hours=5, minutes=30)
    assert DAY == "2026-11-06" and (await check(rig, when))["rang"] is True
    kinds = {(d["kind"], d["channel"], d["ok"]) for d in (await log(rig))["items"]}
    assert ("reminder", "telegram", True) in kinds and ("call", "call", True) in kinds
    rig.caller.fail = True
    when2 = when + timedelta(hours=1)
    await check(rig, when2)
    failed = (await log(rig, failed=True))["items"]
    assert failed and failed[0]["kind"] == "call" and failed[0]["target"] == "@arjun" and "couldn't place the call" in failed[0]["error"]


async def test_the_log_belongs_to_one_person(rig):
    await telegram(rig)
    await rig.client.post("/v1/agent/reminders/test", headers=headers())
    other = (await rig.client.get(D, headers=headers("b@example.com", "u2"))).json()
    assert other["items"] == [] and other["last24h"] == {"sent": 0, "failed": 0}
    assert (await rig.client.get(D)).status_code == 401

