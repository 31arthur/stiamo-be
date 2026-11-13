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
    assert "📌 <b>Fixed</b>\n⚖️ <b>10:30</b> Arguments\n   ↳ <i>TUMWU-2026-001 · Kurnool court</i>" in out  # when it starts; the end of a hearing isn't for a glance
    assert "📞 <b>Calls to make</b>\n📞 <b>Call him again</b>\n   ↳ <i>Last try 29 Oct, asked to call back late · tried 2×</i>" in out
    assert "<i>First attempt</i>" in out  # the note no longer starts by saying the title again, nor carries the case number
    assert "💡 <i>Keep the evening free.</i>" in out and "🟢 <b>Free</b> 09:00–10:30, 11:30–21:00" in out
    assert "No hearings or meetings" not in out  # the answer is the summary: it is not said twice
    assert out.endswith("<i>— Daily planner</i>")


def test_the_same_briefing_on_whatsapp_uses_whatsapps_bold_and_on_plain_text_none():
    wa = render("Today is clear.", [CARD], 4000, "Daily planner", "whatsapp")
    assert "*Sat 7 Nov*" in wa and "*10:30* Arguments" in wa and "_— Daily planner_" in wa and "<" not in wa
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
    assert "+35 more in Stiamo" in render("Busy day.", [many], 4000, "Daily planner", "telegram")  # five a section on a phone, the rest counted
    out = render("Busy day.", [many], 220, "Daily planner", "telegram")
    assert len(out) <= 220 and MORE in out and out.endswith("<i>— Daily planner</i>") and out.count("<b>") == out.count("</b>") and out.count("<i>") == out.count("</i>")


def test_what_needs_a_yes_says_so_and_stays_even_when_the_rest_is_cut():
    proposal = {"kind": "proposal", "title": "New task: Call Hemla", "lines": ["Due Fri"]}
    out = render("Proposed.", [proposal, CARD], 300, None, "telegram")
    assert "<b>YES</b> to save · <b>NO</b> to drop" in out
    schedule = {"kind": "schedule", "proposalId": "p1", "blocks": [{"from": "09:30", "title": "Call"}]}
    assert "YES to book them · NO to drop" in render("ok", [schedule], 1000, None, "plain")


# --- short on a phone ---------------------------------------------------------------------------------------------------

FRIDAY = {
    "kind": "briefing", "date": "2026-11-13", "headline": "Fri 13 Nov (S&B): Alur workers' hearing at 15:00, two morning calls, no open tasks to schedule",
    "sections": [
        {"kind": "fixed", "items": [{"type": "hearing", "id": "h1", "title": "Next hearing", "time": "15:00–16:00", "caseRef": "TUMWU-2026-002", "place": "JCL Kurnool",
                                     "note": "Last hearing (27 Oct) recorded no order, so come ready with what to press for."}]},
        {"kind": "prepare", "items": [{"type": "hearing", "id": "h1", "title": "Next hearing", "time": "15:00–16:00", "caseRef": "TUMWU-2026-002",
                                       "note": "Review the Alur case file (wage sheets, pleas, Form D) so everything is in hand before the 15:00 hearing."}]},
        {"kind": "calls", "items": [
            {"type": "followUp", "id": "f1", "title": "Followup with Hemla Pawar", "time": "10:30–10:45", "channel": "call", "contact": "Jaipal Pawar Pune Work · +91 99706 60979", "tries": 1,
             "minutes": 15, "caseRef": "TUMWU-2026-001", "note": "Call Jaipal Pawar about Hemla Pawar (case TUMWU-2026-001) at 10:30 — he asked for a callback on 29 Oct, first attempt."},
            {"type": "followUp", "id": "f2", "title": "Followup with Labour Department", "time": "11:00–11:15", "channel": "call", "minutes": 15, "caseRef": "TUMWU-2026-001",
             "note": "Call the Labour Department (case TUMWU-2026-001) at 11:00 — first attempt."}]},
        {"kind": "later", "items": [{"type": "note", "note": "Sun 15 Nov — Chhat Puja (calendar event)."}, {"type": "note", "note": "Fri 20 Nov — action plan due: Initial Action Plan."},
                                    {"type": "note", "note": "Mon 23 Nov — a third thing that a phone doesn't need."}]},
    ],
    "free": [{"from": "09:00", "to": "10:30", "minutes": 90}, {"from": "10:45", "to": "11:00", "minutes": 15}, {"from": "11:15", "to": "15:00", "minutes": 225}, {"from": "16:00", "to": "21:00", "minutes": 300}],
}
PARAGRAPH = ("Friday is shaped around the 15:00 Alur workers' hearing at JCL Kurnool and two calls with fixed times. I found no open S&B tasks due on 13 Nov and nothing to push, "
             "so nothing new was scheduled and nothing was left out: the free windows stay open for whatever comes up.")


def test_a_plan_on_a_phone_is_short_each_thing_once_and_says_only_what_a_glance_needs():
    out = render(PARAGRAPH, [FRIDAY], 4000, "Daily planner", "plain", title="S&B plan ·")
    assert out.startswith("🗓️ S&B plan · Fri 13 Nov\nAlur workers' hearing at 15:00, two morning calls, no open tasks to schedule\n\n")  # the day is said once
    assert "Friday is shaped around" not in out  # the model's paragraph repeats the sections
    assert out.count("Next hearing") == 1 and "📂" not in out  # a hearing under Fixed and under Prepare is one line, with the preparation under it
    assert "⚖️ 15:00 Next hearing\n   ↳ Review the Alur case file (wage sheets, pleas, Form D) · TUMWU-2026-002 · JCL Kurnool" in out
    assert "📞 10:30 Followup with Hemla Pawar\n   ↳ He asked for a callback on 29 Oct, first attempt. · +91 99706 60979" in out  # the reason, then the number to ring
    assert "case TUMWU" not in out and "tried 1×" not in out and "15 min" not in out
    assert "Chhat Puja" in out and "Initial Action Plan" in out and "third thing" not in out and "+1 more in Stiamo" in out
    assert out.endswith("🟢 Free 11:15–15:00, 16:00–21:00")  # the two longest windows; no signature: the planner's own bot says it is the plan
    assert len(out) < 750


def test_a_long_headline_is_cut_and_the_models_extra_line_stays_only_when_it_adds_something_short():
    card = {"kind": "briefing", "date": "2026-11-13", "headline": "word " * 60, "sections": [{"kind": "work", "items": [{"type": "task", "title": "Draft", "priority": "urgent"}]}]}
    out = render("A short extra line.", [card], 4000, None, "plain", title="Plan ·")
    assert "…" in out.split("\n")[1] and len(out.split("\n")[1]) <= 111 and "A short extra line." in out and "❗Draft" in out
