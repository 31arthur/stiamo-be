from app.application.reminders import reminder_buttons
from app.domain.dial import call_button, call_rows, call_url, name_of, phone_of, phone_text

from .conftest import headers
from .test_availability import day, planner, press, script
from .test_dayplans import PLANS
from .test_planning import TOMORROW

APP = "https://stiamo.example.com"


def test_the_number_and_name_are_read_from_a_contact_line():
    assert phone_of("Hemla · +91 98100 22114") == "+919810022114" and phone_text("Hemla · +91 98100 22114") == "+91 98100 22114"
    assert name_of("Hemla · +91 98100 22114") == "Hemla" and phone_of("Hemla") is None and phone_of(None) is None
    assert phone_of("Ada (011) 2345-6789") == "01123456789"


def test_the_call_button_opens_a_page_on_the_apps_own_address_with_the_number_in_the_fragment():
    url = call_url(APP, "Hemla Naik · +91 98100 22114")
    assert url == f"{APP}/call.html#n=+919810022114&name=Hemla%20Naik"  # a fragment: never sent to a server
    assert call_button(APP, "Hemla · +91 98100 22114") == {"text": "📞 Call Hemla", "url": call_url(APP, "Hemla · +91 98100 22114")}
    assert call_url(APP, "Hemla") is None and call_url("http://insecure.example.com", "+919810022114") is None  # no number, or no https: no button
    assert call_button(APP, "+919810022114", "Call the advocate")["text"] == "📞 Call Call the advocate"[:24 + 7]  # no name: the title


def test_one_button_per_number_and_at_most_four():
    calls = [{"title": f"c{i}", "contact": f"P{i} · +91 98100 0000{i}"} for i in range(6)] + [{"title": "dup", "contact": "Again · +91 98100 00001"}, {"title": "none", "contact": "No number"}]
    rows = call_rows(APP, calls)
    assert len(rows) == 4 and all(len(r) == 1 and "url" in r[0] for r in rows)
    assert len(call_rows(APP, calls[6:])) == 1  # the duplicate and the one without a number
    assert call_rows("", calls) == []


def test_a_reminder_has_a_call_button_beside_done_for_each_call_with_a_number():
    pending = [{"id": "a", "title": "Call Hemla", "contact": "Hemla · +91 98100 22114"}, {"id": "b", "title": "Chase", "contact": "no number"}]
    rows = reminder_buttons("k", pending, APP)
    assert [b.get("callback_data") for b in rows[0]] == ["rm:d:a", None] and "url" in rows[0][1] and len(rows[1]) == 1
    assert [b["callback_data"] for r in reminder_buttons("k", pending) for b in r] == ["rm:d:a", "rm:d:b", "rm:s:k", "rm:t:k"]  # without the app's address: as before


async def test_a_plan_shows_the_number_and_has_a_call_button_and_the_approval_keeps_it_one_tap_away(rig):
    bot = await planner(rig)
    day(rig, ())
    rig.data.tables["follow-ups"][0]["contact"] = "Hemla · +91 98100 22114"
    rig.app.state.settings.app_url = APP
    script(rig, "followUp", "f-sb", 600)
    plan = (await rig.client.post(f"{PLANS}/generate", json={"day": TOMORROW, "workspace": "sb"}, headers=headers())).json()
    assert plan["blocks"][0]["contact"] == "Hemla · +91 98100 22114"
    await rig.client.put(f"{PLANS}/settings", json={"enabled": False, "time": "21:00", "timezone": "Asia/Kolkata", "workspaces": ["sb"]}, headers=headers())
    from .test_leaks import evening

    script(rig, "followUp", "f-sb", 600)
    rig.telegram.sent.clear()
    rig.telegram.keyboards.clear()
    await evening(rig)
    text = next(t for _, t in rig.telegram.sent if "plan · " in t)
    assert "10:00" in text and "📞 +91 98100 22114" in text  # the number is in the message, so Telegram makes it tappable too
    keys = [b for k in rig.telegram.keyboards if k for row in k for b in row]
    assert any(b.get("callback_data", "").startswith("pl:a:") for b in keys)
    call = next(b for b in keys if "url" in b)
    assert call["text"] == "📞 Call Hemla" and call["url"] == f"{APP}/call.html#n=+919810022114&name=Hemla"

    # approving: the confirmation lists the number and still carries the Call button
    new = (await rig.client.get(PLANS, headers=headers())).json()["plans"][0]
    rig.telegram.edits.clear()
    rig.telegram.edit_buttons_shown.clear()
    await press(rig, bot, f"pl:a:{new['id']}", 2)
    assert "10:00–10:15 📞 Call Hemla · +91 98100 22114" in rig.telegram.edits[-1][2]
    assert rig.telegram.edit_buttons_shown[-1] == [[call]]
