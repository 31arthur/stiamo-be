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
