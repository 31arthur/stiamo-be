import json

from app.domain.llm import ChatResult
from app.domain.phone import strip_markup

from .conftest import headers
from .test_channels import send, telegram
from .test_mail_filter import BASE, answer, make

T = "/v1/agent/themes"
THEME = {
    "name": "Client Co", "emoji": "🏛️", "accent": "#8b1e3f", "match": {"domains": ["client.io"]}, "tone": "Formal and deferential.", "greeting": "Respected {name},", "signoff": "Yours faithfully,",
    "signature": "Arjun\nS&B Chambers", "letterhead": {"title": "S&B Chambers", "tagline": "Advocates", "footer": "Privileged & confidential."},
}
READ = ChatResult(json.dumps({
    "summary": "The client wants the delegate details and a confirmation by Friday or the place is released.", "priority": "high", "action": "Confirm and send delegate details", "due": "2026-11-13", "event": None,
    "replies": [{"label": "Confirm attendance", "intent": "Confirm we will attend; details follow today."}, {"label": "Ask for time", "intent": "Ask for two more days."}, {"label": "Decline", "intent": "Decline politely."}],
}))
DRAFT = ChatResult(json.dumps({"subject": None, "body": "We confirm that we will attend. The delegate details will follow today."}))


async def alert_agent(rig):
    """A mail agent in S&B whose urgent label pings the phone."""
    rig.app.state.settings.app_url = "https://app.example.com"
    wid = await make(
        rig,
        labels=[{"name": "urgent", "description": "Needs us to share or register by a deadline.", "route": "alert"}, {"name": "routine", "description": "Nothing to do soon."}],
        action={"trashLabel": None, "auto": False}, rules={"protectedDomains": [], "protectedPhrases": [], "protectCorrespondents": False},
        filters={"sinceDays": 365, "newOnly": True, "limit": 50, "contains": "Partnership"},
    )
    assert (await rig.client.put(T, json={"themes": [THEME]}, headers=headers())).status_code == 200
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    return wid, c


async def press(rig, channel, data, update, chat=555, message=101):
    hook = rig.telegram.webhooks[-1]
    body = {"update_id": update, "callback_query": {"id": f"cb{update}", "data": data, "message": {"message_id": message, "chat": {"id": chat}}}}
    r = await rig.client.post(f"/v1/agent/webhooks/telegram/{channel['id']}", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": hook[1]})
    assert r.status_code == 200
    return r


def last_keyboard(rig):
    return [b for row in rig.telegram.keyboards[-1] for b in row]


def alert_id_of(rig):
    return next(b["callback_data"] for b in last_keyboard(rig) if "callback_data" in b).split(":")[1]


async def test_an_urgent_email_pings_the_phone_at_once_with_a_link_and_suggested_replies(rig):
    wid, c = await alert_agent(rig)
    rig.script += [answer("urgent"), READ]
    run = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert run["counts"]["queued"] == 1  # it is in the Attention queue too
    text = rig.telegram.sent[-1][1]
    assert text.startswith("🚨🚨 <b>URGENT · S&amp;B</b> 🚨🚨") and "Partnership opportunity" in text and "🏛️ Client Co" in text and "Confirm and send delegate details" in text
    buttons = last_keyboard(rig)
    assert buttons[0] == {"text": "🔗 Open the email in Stiamo", "url": "https://app.example.com/sb/mail?a=acc1&f=INBOX&m=4"}
    assert [b["text"] for b in buttons[1:4]] == ["1️⃣ Confirm attendance", "2️⃣ Ask for time", "3️⃣ Decline"] and [b["text"] for b in buttons[4:]] == ["✍️ Write my own", "👌 Handled"]
    assert "replies" in rig.llm_log[-1]["system"]  # the reading asked for suggested replies
    sent = len(rig.telegram.sent)
    again = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert again["counts"]["items"] == 0 and len(rig.telegram.sent) == sent  # not pinged twice


async def test_a_test_run_never_pings(rig):
    wid, _ = await alert_agent(rig)
    before = len(rig.telegram.sent)
    rig.script += [answer("urgent"), READ]
    await rig.client.post(f"{BASE}/{wid}/test", json={}, headers=headers())
    assert len(rig.telegram.sent) == before


async def test_pressing_a_reply_drafts_it_in_the_theme_and_send_sends_it_from_the_mailbox_only_then(rig):
    wid, c = await alert_agent(rig)
    rig.script += [answer("urgent"), READ]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    alert_id = alert_id_of(rig)

    rig.script += [DRAFT]
    await press(rig, c, f"al:{alert_id}:r0", update=2)
    assert "Tone: Formal and deferential." in rig.llm_log[-1]["system"] and "Confirm we will attend" in rig.llm_log[-1]["system"] and "Subject: Partnership opportunity" in rig.llm_log[-1]["last"]
    draft = strip_markup(rig.telegram.sent[-1][1])
    assert "Draft reply · Client Co" in draft and "To: lead@client.io" in draft and "Subject: Re: Partnership opportunity" in draft and "We confirm that we will attend." in draft
    assert [b["text"] for b in last_keyboard(rig)] == ["📤 Send", "🔄 Redo", "✖️ Cancel"] and rig.data.mail_sent == []  # nothing goes without Send

    await press(rig, c, f"al:{alert_id}:send", update=3)
    account, message = rig.data.mail_sent[0]
    assert account == "acc1" and message["to"] == ["lead@client.io"] and message["subject"] == "Re: Partnership opportunity"
    assert message["text"].startswith("Respected Lead,\n\nWe confirm that we will attend.") and "Yours faithfully,\nArjun\nS&B Chambers" in message["text"]
    assert "#8b1e3f" in message["html"] and "Advocates" in message["html"]
    assert "Sent</b> to lead@client.io" in rig.telegram.sent[-1][1] and rig.telegram.stripped  # the buttons come off the alert
    await press(rig, c, f"al:{alert_id}:send", update=4)
    assert "already dealt with" in rig.telegram.sent[-1][1] and len(rig.data.mail_sent) == 1  # never twice


async def test_redo_writes_again_cancel_drops_and_handled_closes_it(rig):
    wid, c = await alert_agent(rig)
    rig.script += [answer("urgent"), READ]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    alert_id = alert_id_of(rig)
    rig.script += [DRAFT, ChatResult(json.dumps({"subject": "Re: Your registration", "body": "A second version."}))]
    await press(rig, c, f"al:{alert_id}:r1", update=2)
    await press(rig, c, f"al:{alert_id}:redo", update=3)
    assert "Ask for two more days." in rig.llm_log[-1]["system"] and "Subject: Re: Your registration" in strip_markup(rig.telegram.sent[-1][1]) and "A second version." in rig.telegram.sent[-1][1]
    await press(rig, c, f"al:{alert_id}:x", update=4)
    assert "Draft dropped" in rig.telegram.sent[-1][1]
    await press(rig, c, f"al:{alert_id}:done", update=5)
    assert "Marked as handled" in rig.telegram.sent[-1][1]
    await press(rig, c, f"al:{alert_id}:r0", update=6)
    assert "already dealt with" in rig.telegram.sent[-1][1] and rig.data.mail_sent == []


async def test_write_my_own_takes_what_you_type_next_and_a_stranger_cannot_press(rig):
    wid, c = await alert_agent(rig)
    rig.script += [answer("urgent"), READ]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    alert_id = alert_id_of(rig)
    before, asked = len(rig.telegram.sent), len(rig.llm_log)
    await press(rig, c, f"al:{alert_id}:r0", update=2, chat=999)  # a chat that is not paired
    assert len(rig.telegram.sent) == before and len(rig.llm_log) == asked

    await press(rig, c, f"al:{alert_id}:own", update=3)
    assert "Tell me what to say" in rig.telegram.sent[-1][1]
    rig.script += [DRAFT]
    await send(rig, c, "tell them yes but only on Monday", chat=555, update=4)
    assert 'In their own words, they want to say: "tell them yes but only on Monday"' in rig.llm_log[-1]["system"] and "Draft reply" in rig.telegram.sent[-1][1]
    shown = len(rig.telegram.sent)
    await send(rig, c, "and what is on my plan?", chat=555, update=5)
    assert len(rig.telegram.sent) == shown + 1 and "Draft reply" not in rig.telegram.sent[-1][1]  # only the first message after the button was for the alert


async def test_a_sample_alert_shows_the_look_and_never_sends_anything(rig):
    wid, c = await alert_agent(rig)
    assert (await rig.client.post("/v1/agent/alerts/test", headers=headers())).json() == {"sent": 1}
    assert "URGENT" in rig.telegram.sent[-1][1] and "This is a sample alert" in rig.telegram.sent[-1][1]
    alert_id = alert_id_of(rig)
    rig.script += [DRAFT]
    await press(rig, c, f"al:{alert_id}:r0", update=2)
    await press(rig, c, f"al:{alert_id}:send", update=3)
    assert "sample alert, so nothing was sent" in rig.telegram.sent[-1][1] and rig.data.mail_sent == []


async def test_themes_are_kept_per_person_and_previewed(rig):
    assert (await rig.client.put(T, json={"themes": [THEME, {"name": "Second", "default": True}]}, headers=headers())).json()["themes"][1]["default"] is True
    assert [t["name"] for t in (await rig.client.get(T, headers=headers())).json()["themes"]] == ["Client Co", "Second"]
    assert (await rig.client.get(T, headers=headers("b@example.com", "u2"))).json() == {"themes": []}
    bad = await rig.client.put(T, json={"themes": [{"name": ""}]}, headers=headers())
    assert bad.status_code == 422
    shown = (await rig.client.post(f"{T}/preview", json={"theme": THEME}, headers=headers())).json()
    assert shown["text"].startswith("Respected Priya,") and "#8b1e3f" in shown["html"]


WS = "/v1/agent/channels/telegram"


async def email_bot(rig, workspace="sb", token="456:def", chat=888):
    r = await rig.client.post(WS, json={"botToken": token, "workspace": workspace}, headers=headers())
    assert r.status_code == 201, r.text
    bot = r.json()
    await send(rig, bot, f"/start {bot['pairingCode']}", chat=chat, update=1)
    return bot


async def test_a_workspace_email_bot_gets_its_alerts_and_nothing_else_does(rig):
    wid, general = await alert_agent(rig)
    bot = await email_bot(rig)
    assert bot["workspace"] == "sb"
    chats_before = len(rig.telegram.sent)
    rig.script += [answer("urgent"), READ]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    new = rig.telegram.sent[chats_before:]
    assert [chat for chat, _ in new] == [888] and "URGENT" in new[0][1]  # the S&B bot only: the general chat heard nothing
    # what goes to every chat (here a test reminder) stays off the email bot
    sent = len(rig.telegram.sent)
    await rig.client.post("/v1/agent/reminders/test", headers=headers())
    assert all(chat != 888 for chat, _ in rig.telegram.sent[sent:]) and any(chat == 555 for chat, _ in rig.telegram.sent[sent:])


async def test_one_email_bot_per_workspace_and_only_for_real_workspaces(rig):
    await email_bot(rig)
    again = await rig.client.post(WS, json={"botToken": "789:ghi", "workspace": "sb"}, headers=headers())
    assert again.status_code == 422 and "already has an email bot" in again.json()["error"]["message"]
    other = await rig.client.post(WS, json={"botToken": "789:ghi", "workspace": "fsa"}, headers=headers())
    assert other.status_code == 201 and other.json()["workspace"] == "fsa"
    bad = await rig.client.post(WS, json={"botToken": "999:xyz", "workspace": "nowhere"}, headers=headers())
    assert bad.status_code == 422


async def attention_agent(rig, **notify):
    """An S&B mail agent that only queues for Attention, with an option to send updates to the phone."""
    return await make(
        rig,
        labels=[{"name": "issue", "description": "Needs the owner.", "route": "attention"}, {"name": "fyi", "description": "Nothing to do."}],
        action={"trashLabel": None, "auto": False}, rules={"protectedDomains": [], "protectedPhrases": [], "protectCorrespondents": False},
        filters={"sinceDays": 365, "newOnly": True, "limit": 50, "contains": "Partnership"}, notify=notify,
    )


def read(priority):
    return ChatResult(json.dumps({"summary": "The lead asks for a quote.", "priority": priority, "action": "Reply with a quote", "due": None, "event": None, "replies": [{"label": "Send a quote", "intent": "Say a quote follows."}]}))


def another(rig, uid):
    rig.data.mail.append({"uid": uid, "subject": f"Partnership: notice {uid}", "sender": {"name": "Lead", "address": "lead@client.io"}, "snippet": "Respond today", "date": f"2026-10-{uid:02d}T10:00:00Z"})


async def test_an_agent_that_sends_updates_tells_the_phone_quietly_and_loudly_when_it_is_priority(rig):
    rig.app.state.settings.app_url = "https://app.example.com"
    wid = await attention_agent(rig, telegram=True)
    await email_bot(rig)
    rig.script += [answer("issue"), read("normal")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert rig.telegram.sent[-1][0] == 888 and rig.telegram.sent[-1][1].startswith("📬 <b>Needs you · S&amp;B</b>") and "URGENT" not in rig.telegram.sent[-1][1]
    assert "replies" in rig.llm_log[-1]["system"] and any("Send a quote" in b["text"] for b in last_keyboard(rig))  # a quiet update still offers a reply
    another(rig, 9)  # the same agent, when the model finds the next one high priority
    rig.script += [answer("issue"), read("high")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert rig.telegram.sent[-1][1].startswith("🚨🚨 <b>URGENT · S&amp;B</b>")


async def test_an_agent_with_updates_off_says_nothing_for_attention_mail(rig):
    wid = await attention_agent(rig)
    await email_bot(rig)
    sent = len(rig.telegram.sent)
    rig.script += [answer("issue"), read("high")]
    run = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert run["counts"]["queued"] == 1 and len(rig.telegram.sent) == sent  # it is in Attention, and that is all


async def test_a_call_is_made_for_priority_mail_only_and_not_again_within_fifteen_minutes(rig):
    from datetime import UTC, datetime, timedelta

    wid = await attention_agent(rig, telegram=True, call=True)
    await email_bot(rig)
    t0 = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    clock = {"now": t0}
    rig.app.state.clock = lambda: clock["now"]
    await rig.client.put("/v1/agent/reminders", json={"enabled": False, "voice": False, "call": True, "callUser": "@arjun"}, headers=headers())
    rig.script += [answer("issue"), read("normal")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert rig.caller.calls == []  # an update is not worth a call

    another(rig, 9)
    rig.script += [answer("issue"), read("high")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    user, said, lang, repeat = rig.caller.calls[0]
    assert user == "@arjun" and said == "Stiamo. Urgent email in S&B, from Lead: Partnership: notice 9. Check Telegram." and len(said) <= 256

    another(rig, 10)
    clock["now"] = t0 + timedelta(minutes=5)
    rig.script += [answer("issue"), read("high")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert len(rig.caller.calls) == 1  # the same burst: the Telegram alerts say the rest
    another(rig, 11)
    clock["now"] = t0 + timedelta(minutes=20)
    rig.script += [answer("issue"), read("high")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert len(rig.caller.calls) == 2


async def test_no_call_without_a_number_to_ring(rig):
    wid = await attention_agent(rig, telegram=True, call=True)
    await email_bot(rig)
    rig.script += [answer("issue"), read("high")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())  # no number is set under Call reminders
    assert rig.caller.calls == [] and "URGENT" in rig.telegram.sent[-1][1]


async def test_no_call_when_the_agent_did_not_ask_for_calls(rig):
    wid = await attention_agent(rig, telegram=True)
    await email_bot(rig)
    await rig.client.put("/v1/agent/reminders", json={"enabled": False, "voice": False, "call": True, "callUser": "@arjun"}, headers=headers())
    rig.script += [answer("issue"), read("high")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert rig.caller.calls == [] and "URGENT" in rig.telegram.sent[-1][1]


async def test_spoken_reminders_are_off_unless_asked_for(rig):
    got = (await rig.client.put("/v1/agent/reminders", json={"enabled": True}, headers=headers())).json()
    assert got["voice"] is False
    assert (await rig.client.get("/v1/agent/reminders", headers=headers())).json()["voice"] is False
