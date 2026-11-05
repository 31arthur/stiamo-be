from app.domain.phone import MORE, Writer, render, strip_markup

CARD = {
    "kind": "briefing",
    "date": "2026-11-07",
    "headline": "Open day — three follow-ups to clear",
    "summary": "No hearings or meetings. Whole day free.",
    "sections": [
        {"kind": "fixed", "items": [{"type": "hearing", "title": "Arguments", "time": "10:30–11:30", "caseRef": "TUMWU-2026-001", "place": "Kurnool court"}]},
        {
            "kind": "calls",
            "items": [
                {"type": "followUp", "title": "Call him again", "note": "Call him again — last try 29 Oct, asked to call back late", "channel": "call", "tries": 2},
                {"type": "followUp", "title": "Followup with Hemla Pawar", "note": "Follow up with Hemla Pawar (TUMWU-2026-001) — first attempt", "caseRef": "TUMWU-2026-001"},
                {"type": "note", "note": "Keep the evening free."},
            ],
        },
    ],
    "free": [{"from": "09:00", "to": "10:30", "minutes": 90}, {"from": "11:30", "to": "21:00", "minutes": 570}],
}


def test_a_briefing_on_telegram_is_laid_out_with_bold_icons_and_a_quiet_line_for_the_why():
    out = render("Today is clear.", [CARD], 4000, "Daily planner", "telegram")
    assert out.startswith("🗓️ <b>Sat 7 Nov</b>\nOpen day — three follow-ups to clear\n\nToday is clear.")
    assert "📌 <b>Fixed</b>\n⚖️ <b>10:30–11:30</b> Arguments\n   ↳ <i>TUMWU-2026-001 · Kurnool court</i>" in out
    assert "📞 <b>Calls to make</b>\n📞 <b>Call him again</b>\n   ↳ <i>Last try 29 Oct, asked to call back late · tried 2×</i>" in out
    assert "<i>First attempt · TUMWU-2026-001</i>" in out  # the note no longer starts by saying the title again
    assert "💡 <i>Keep the evening free.</i>" in out and "🟢 <b>Free</b> 09:00–10:30, 11:30–21:00" in out
    assert "No hearings or meetings" not in out  # the answer is the summary: it is not said twice
    assert out.endswith("<i>— Daily planner</i>")


def test_the_same_briefing_on_whatsapp_uses_whatsapps_bold_and_on_plain_text_none():
    wa = render("Today is clear.", [CARD], 4000, "Daily planner", "whatsapp")
    assert "*Sat 7 Nov*" in wa and "*10:30–11:30* Arguments" in wa and "_— Daily planner_" in wa and "<" not in wa
    plain = render("", [CARD], 4000, None, "plain")
    assert "No hearings or meetings. Whole day free." in plain and "*" not in plain and "<" not in plain  # no answer: the card's own summary leads


def test_what_the_model_wrote_is_escaped_and_its_markdown_becomes_the_chats_own():
    w = Writer("telegram")
    assert w.text("## Plan\n- **Call** Hemla & <b>pals</b>\n- see [the order](https://x.test/a?b=1&c=2)") == (
        '<b>Plan</b>\n• <b>Call</b> Hemla &amp; &lt;b&gt;pals&lt;/b&gt;\n• see <a href="https://x.test/a?b=1&amp;c=2">the order</a>'
    )
    assert Writer("whatsapp").text("**Call** Hemla") == "*Call* Hemla" and Writer("plain").text("**Call** [it](https://x.test)") == "Call it (https://x.test)"
    assert strip_markup("<b>a &amp; b</b>") == "a & b"


def test_a_message_too_long_for_the_chat_loses_whole_sections_from_the_end_never_half_a_tag():
    many = {"kind": "briefing", "date": "2026-11-07", "headline": "Busy", "sections": [{"kind": "work", "items": [{"type": "task", "title": f"Task number {i} with a rather long title to use up room"} for i in range(40)]}, {"kind": "later", "items": [{"type": "task", "title": "Later"}]}], "free": []}
    out = render("Busy day.", [many], 600, "Daily planner", "telegram")
    assert len(out) <= 600 and MORE in out and out.endswith("<i>— Daily planner</i>") and out.count("<b>") == out.count("</b>") and out.count("<i>") == out.count("</i>")


def test_what_needs_a_yes_says_so_and_stays_even_when_the_rest_is_cut():
    proposal = {"kind": "proposal", "title": "New task: Call Hemla", "lines": ["Due Fri"]}
    out = render("Proposed.", [proposal, CARD], 300, None, "telegram")
    assert "Reply <b>YES</b> to save, or <b>NO</b> to drop." in out
    schedule = {"kind": "schedule", "proposalId": "p1", "blocks": [{"from": "09:30", "title": "Call"}]}
    assert "Reply YES to put these on your planner" in render("ok", [schedule], 1000, None, "plain")
