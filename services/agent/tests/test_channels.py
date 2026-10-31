from app.domain.llm import ChatResult, ToolCall
from app.infrastructure.messaging import twilio_signature

from .conftest import PUBLIC, headers

KEY = "sk-test-1234567890"


async def ready(rig):
    await rig.client.put("/v1/agent/providers/openai", json={"apiKey": KEY}, headers=headers())
    await rig.client.put("/v1/agent/selection", json={"providerId": "openai", "modelId": "gpt-4o", "thinking": "off"}, headers=headers())


async def telegram(rig):
    r = await rig.client.post("/v1/agent/channels/telegram", json={"botToken": "123:abc"}, headers=headers())
    assert r.status_code == 201, r.text
    return r.json()


async def send(rig, channel, text, chat=555, update=1, secret=None):
    hook = rig.telegram.webhooks[-1]
    return await rig.client.post(
        f"/v1/agent/webhooks/telegram/{channel['id']}",
        json={"update_id": update, "message": {"text": text, "chat": {"id": chat}, "from": {"first_name": "Arj"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret or hook[1]},
    )


async def test_connecting_a_telegram_bot_sets_its_webhook_and_offers_a_pairing_code(rig):
    c = await telegram(rig)
    assert c["username"] == "stiamo_bot" and len(c["pairingCode"]) == 8
    assert c["pairingLink"] == f"https://t.me/stiamo_bot?start={c['pairingCode']}"
    assert rig.telegram.webhooks[0][0] == f"{PUBLIC}/v1/agent/webhooks/telegram/{c['id']}"
    assert "botToken" not in str(c) and "123:abc" not in str(c)


async def test_a_bad_bot_token_is_refused_and_a_bot_is_connected_once(rig):
    bad = await rig.client.post("/v1/agent/channels/telegram", json={"botToken": "bad"}, headers=headers())
    assert bad.status_code == 422
    await telegram(rig)
    again = await rig.client.post("/v1/agent/channels/telegram", json={"botToken": "123:abc"}, headers=headers())
    assert again.status_code == 422 and "already" in again.json()["error"]["message"]


async def test_the_webhook_needs_the_secret_telegram_was_given(rig):
    c = await telegram(rig)
    assert (await send(rig, c, "hi", secret="wrong")).status_code == 403
    missing = await rig.client.post(f"/v1/agent/webhooks/telegram/{c['id']}", json={})
    assert missing.status_code == 403
    assert (await rig.client.post("/v1/agent/webhooks/telegram/nope", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "x"})).status_code == 404


async def test_a_chat_pairs_with_the_code_and_a_stranger_does_not(rig):
    await ready(rig)
    c = await telegram(rig)
    await send(rig, c, "show me cases", chat=999, update=1)
    assert rig.telegram.sent[-1] == (999, "This chat isn't linked to a Stiamo account. Open AI agents → Channels in Stiamo to connect it.")
    assert rig.llm_log == []  # nothing reached the model

    await send(rig, c, "/start WRONG000", chat=555, update=2)
    assert "isn't linked" in rig.telegram.sent[-1][1]
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=3)
    assert rig.telegram.sent[-1][1].startswith("Connected")
    listed = (await rig.client.get("/v1/agent/channels", headers=headers())).json()["items"][0]
    assert listed["paired"] == [{"id": 555, "name": "Arj"}] and "pairingCode" not in listed


async def test_a_paired_chat_gets_the_agent_with_the_owners_access(rig):
    await ready(rig)
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", update=1)
    rig.script += [ChatResult("", [ToolCall("t", "search_cases", {"query": "NHAI"})]), ChatResult("One case matches.")]
    r = await send(rig, c, "NHAI cases?", update=2)
    assert r.status_code == 200 and r.json() == {"ok": True}
    chat, text = rig.telegram.sent[-1]
    assert chat == 555 and text.startswith("One case matches.") and "SA-2026-001 Alur NHAI wages" in text
    assert "Telegram" in rig.llm_log[0]["system"]


async def test_the_same_telegram_update_is_handled_once_and_a_chat_remembers_the_last_exchange(rig):
    await ready(rig)
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", update=1)
    rig.script += [ChatResult("First.")]
    await send(rig, c, "hello", update=2)
    await send(rig, c, "hello", update=2)  # Telegram retried
    assert len(rig.llm_log) == 1
    rig.script += [ChatResult("Second.")]
    await send(rig, c, "and then?", update=3)
    assert rig.llm_log[1]["roles"] == ["system", "user", "assistant", "user"]


async def test_a_failing_agent_still_answers_in_words(rig):
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", update=1)
    await send(rig, c, "hello", update=2)  # no provider connected yet
    assert "Choose a provider" in rig.telegram.sent[-1][1]


async def test_channels_belong_to_one_person(rig):
    c = await telegram(rig)
    other = headers("b@example.com", "u2")
    assert (await rig.client.get("/v1/agent/channels", headers=other)).json()["items"] == []
    assert (await rig.client.delete(f"/v1/agent/channels/{c['id']}", headers=other)).status_code == 404
    assert (await rig.client.post(f"/v1/agent/channels/{c['id']}/pairing", headers=other)).status_code == 404


async def test_disconnecting_removes_the_webhook_and_the_channel(rig):
    c = await telegram(rig)
    assert (await rig.client.delete(f"/v1/agent/channels/{c['id']}", headers=headers())).status_code == 204
    assert rig.telegram.deleted == 1
    assert (await rig.client.get("/v1/agent/channels", headers=headers())).json()["items"] == []


# --- WhatsApp through Twilio --------------------------------------------------------------------------

WA = {"accountSid": "AC123", "authToken": "tw-secret", "sender": "whatsapp:+14155238886", "allowed": ["+91 98765 43210"]}


async def whatsapp(rig):
    r = await rig.client.post("/v1/agent/channels/whatsapp", json=WA, headers=headers())
    assert r.status_code == 201, r.text
    return r.json()


async def hook(rig, c, params, token="tw-secret"):
    url = f"{PUBLIC}/v1/agent/webhooks/whatsapp/{c['id']}"
    return await rig.client.post(f"/v1/agent/webhooks/whatsapp/{c['id']}", data=params, headers={"X-Twilio-Signature": twilio_signature(token, url, params)})


async def test_connecting_whatsapp_checks_the_account_and_gives_the_webhook_to_paste_into_twilio(rig):
    c = await whatsapp(rig)
    assert c["webhookUrl"] == f"{PUBLIC}/v1/agent/webhooks/whatsapp/{c['id']}"
    assert c["allowed"] == ["whatsapp:+919876543210"] and c["account"] == "My Twilio" and "tw-secret" not in str(c)
    bad = await rig.client.post("/v1/agent/channels/whatsapp", json={**WA, "authToken": "bad"}, headers=headers())
    assert bad.status_code == 422
    none = await rig.client.post("/v1/agent/channels/whatsapp", json={**WA, "allowed": []}, headers=headers())
    assert none.status_code == 422


async def test_whatsapp_messages_need_twilios_signature(rig):
    c = await whatsapp(rig)
    params = {"From": "whatsapp:+919876543210", "Body": "hi"}
    assert (await hook(rig, c, params, token="wrong")).status_code == 403
    assert (await rig.client.post(f"/v1/agent/webhooks/whatsapp/{c['id']}", data=params)).status_code == 403


async def test_whatsapp_answers_the_owner_and_ignores_strangers(rig):
    await ready(rig)
    c = await whatsapp(rig)
    rig.script += [ChatResult("", [ToolCall("t", "search_cases", {"query": "wages"})]), ChatResult("Found one.")]
    r = await hook(rig, c, {"From": "whatsapp:+919876543210", "Body": "wage cases"})
    assert r.status_code == 200 and "<Response>" in r.text
    sender, to, text = rig.twilio.sent[-1]
    assert (sender, to) == ("whatsapp:+14155238886", "whatsapp:+919876543210") and text.startswith("Found one.")
    assert "WhatsApp" in rig.llm_log[0]["system"]
    before = len(rig.twilio.sent)
    await hook(rig, c, {"From": "whatsapp:+910000000000", "Body": "who are you"})
    assert len(rig.twilio.sent) == before and len(rig.llm_log) == 2  # no reply, nothing reached the model


async def test_the_allowed_numbers_can_be_changed_and_a_test_message_sent(rig):
    c = await whatsapp(rig)
    new = (await rig.client.put(f"/v1/agent/channels/{c['id']}/allowed", json={"allowed": ["+44 7700 900123", "+919876543210"]}, headers=headers())).json()
    assert new["allowed"] == ["whatsapp:+447700900123", "whatsapp:+919876543210"]
    assert (await rig.client.post(f"/v1/agent/channels/{c['id']}/test", headers=headers())).status_code == 204
    assert [t for _, t, _ in rig.twilio.sent] == ["whatsapp:+447700900123", "whatsapp:+919876543210"]
