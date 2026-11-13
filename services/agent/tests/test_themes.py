import pytest
from app.domain.alerts import (
    alert_buttons,
    alert_text,
    clean_replies,
    mail_url,
    parse_callback,
    parse_draft,
    reply_subject,
)
from app.domain.themes import clean_theme, clean_themes, first_name, pick_theme, render_email
from stiamo_common.errors import ValidationFailed

LABOUR = {"name": "Labour Dept", "emoji": "🏛️", "accent": "#8B1E3F", "match": {"domains": ["@labour.gov.in", "*.nic.in"], "addresses": ["Registrar@x.org"], "phrases": ["Conciliation"]}, "tone": "Formal.", "greeting": "Respected {name},", "signoff": "Yours faithfully,", "signature": "Arjun\nS&B Chambers", "letterhead": {"title": "S&B Chambers", "tagline": "Advocates", "footer": "Privileged & confidential."}}


def test_a_theme_is_checked_and_tidied():
    t = clean_theme(LABOUR)
    assert t["accent"] == "#8b1e3f" and t["match"] == {"domains": ["labour.gov.in", "nic.in"], "addresses": ["registrar@x.org"], "phrases": ["conciliation"]} and len(t["id"]) == 10
    plain = clean_theme({"name": "  Plain  "})
    assert plain["greeting"] == "Dear {name}," and plain["signoff"] == "Yours sincerely," and plain["language"] == "English" and plain["accent"] == "#1f4e79"
    for bad in ({}, {"name": "x", "accent": "red"}):
        with pytest.raises(ValidationFailed):
            clean_theme(bad)


def test_only_one_theme_is_the_default_and_ids_stay_unique():
    out = clean_themes([{**LABOUR, "id": "a", "default": True}, {"name": "B", "id": "a", "default": True}, {"name": "C"}])
    assert [t["default"] for t in out] == [True, False, False] and len({t["id"] for t in out}) == 3
    with pytest.raises(ValidationFailed):
        clean_themes([{"name": "x"}] * 31)


def test_the_theme_is_found_by_address_then_domain_then_words_then_the_default():
    a, b, c, d = (clean_theme({**LABOUR, "id": "labour"}), clean_theme({"name": "Court", "id": "court", "match": {"domains": ["court.in"]}}), clean_theme({"name": "Words", "id": "words", "match": {"phrases": ["tender"]}}), clean_theme({"name": "Fallback", "id": "fb", "default": True}))
    themes = [d, c, b, a]
    assert pick_theme(themes, "Registrar@x.org")["id"] == "labour"
    assert pick_theme(themes, "clerk@dist.court.in")["id"] == "court"
    assert pick_theme(themes, "anyone@else.com", "Notice of TENDER opening")["id"] == "words"
    assert pick_theme(themes, "anyone@else.com", "Hello")["id"] == "fb"
    assert pick_theme([a], "anyone@else.com") is None


def test_a_reply_is_dressed_in_the_letterhead_with_the_greeting_signoff_and_signature():
    text, html = render_email(clean_theme(LABOUR), "We confirm.\n\nDetails follow.", "Priya")
    assert text.startswith("Respected Priya,\n\nWe confirm.") and "Yours faithfully,\nArjun\nS&B Chambers" in text and text.endswith("-- \nPrivileged & confidential.".replace("-- \n", "--\n"))
    assert "#8b1e3f" in html and "S&amp;B Chambers" in html and "Respected Priya," in html and "Privileged &amp; confidential." in html and "<script" not in html
    plain_text, plain_html = render_email(None, "Noted.", "")
    assert plain_text.startswith("Dear Sir/Madam,") and plain_html == ""
    assert "&lt;b&gt;" in render_email(clean_theme(LABOUR), "<b>hi</b>", "X")[1]  # what a model wrote is never taken as markup
    assert first_name("Priya Nair") == "Priya" and first_name("priya@x.org") == "" and first_name("") == ""


def test_the_alert_looks_unlike_the_quiet_messages_and_offers_the_buttons():
    a = {"id": "abc123def456", "subject": "Register <now>", "sender_name": "Registrar", "sender_address": "r@x.org", "summary": "Confirm by Friday.", "action": "Confirm attendance", "due": "2026-11-13", "replies": clean_replies([{"label": "Confirm", "intent": "Say yes."}, {"label": "", "intent": "x"}, "junk"])}
    text = alert_text(a, clean_theme(LABOUR), "S&B")
    assert text.startswith("🚨 <b>URGENT · S&amp;B</b>") and "Register &lt;now&gt;" in text and "🏛️ Labour Dept" in text and "⚡ <b>Confirm attendance</b>" in text and "<code>r@x.org</code>" in text
    rows = alert_buttons(a, "https://app.example.com/sb/mail?a=1&f=INBOX&m=4")
    assert rows[0] == [{"text": "🔗 Open the email in Stiamo", "url": "https://app.example.com/sb/mail?a=1&f=INBOX&m=4"}]
    assert rows[1] == [{"text": "1️⃣ Confirm", "callback_data": "al:abc123def456:r0"}] and [b["callback_data"] for b in rows[-1]] == ["al:abc123def456:own", "al:abc123def456:done"]
    assert all(len(b.get("callback_data", "")) <= 64 for row in rows for b in row) and alert_buttons(a, "")[0][0].get("callback_data")  # no link: no link button


def test_the_helpers_that_read_buttons_links_and_drafts():
    assert parse_callback("al:abc123def456:send") == ("abc123def456", "send") and parse_callback("al:abc123def456:r2") == ("abc123def456", "r2")
    assert parse_callback("al:abc123def456:r3") is None and parse_callback("x") is None and parse_callback("al:ABC:send") is None
    assert mail_url("https://app.example.com/", "sb", "a 1", "Sent Items", 9) == "https://app.example.com/sb/mail?a=a%201&f=Sent%20Items&m=9" and mail_url("", "sb", "a", "INBOX", 1) == ""
    assert reply_subject("Hearing") == "Re: Hearing" and reply_subject("RE: Hearing") == "RE: Hearing" and reply_subject("") == "Re: (no subject)"
    assert parse_draft('Sure: {"subject": "Re: X", "body": "Yes."}') == ("Re: X", "Yes.") and parse_draft('{"subject": null, "body": "Yes."}') == (None, "Yes.") and parse_draft("Just words") == (None, "Just words")
