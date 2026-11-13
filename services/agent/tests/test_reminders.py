from datetime import UTC, datetime, timedelta

from app.domain.phone import strip_markup

from .conftest import headers
from .test_channels import send, telegram

R = "/v1/agent/reminders"
S = "/v1/agent/secrets"
DAY = "2026-11-06"


def at(hour, minute=0):
    """A moment on DAY by the clock in India (the default zone), as the server's UTC clock would show it."""
    return datetime(2026, 11, 6, hour, minute, tzinfo=UTC) - timedelta(hours=5, minutes=30)


def call(i, start, title, status="open"):
    return {"id": i, "title": title, "channel": "call", "due": {"date": DAY, "time": start}, "duration": 15, "outcome": {"status": status}}


async def ready(rig):
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.data.tables = {"follow-ups": [call("a", 540, "Call him again"), call("b", 555, "Followup with Hemla Pawar")], "meetings": [], "tasks": []}
    assert (await rig.client.put(R, json={"enabled": True}, headers=headers())).json()["enabled"] is True


async def check(rig, when):
    rig.app.state.clock = lambda: when
    return (await rig.client.post(f"{R}/check", headers=headers())).json()["reminded"]


async def test_the_reminders_come_after_the_calls_end_then_an_hour_half_an_hour_and_ten_minutes_later_in_writing(rig):
    await ready(rig)
    assert await check(rig, at(9, 39)) is None  # the calls end at 09:30: ten minutes on is 09:40
    first = await check(rig, at(9, 40))
    assert first == {"calls": ["a", "b"], "step": 1, "rang": False}
    text = strip_markup(rig.telegram.sent[-1][1])
    assert "🔔 2 calls still pending" in text and "Call him again" in text and "Followup with Hemla Pawar" in text and "Mark them done" not in text and "— Stiamo" not in text
    assert not hasattr(rig.telegram, "voices")  # no audio is ever sent: a real call is what rings
    assert await check(rig, at(9, 41)) is None and await check(rig, at(10, 39)) is None  # not again for an hour
    assert (await check(rig, at(10, 40)))["step"] == 2
    assert await check(rig, at(11, 9)) is None and (await check(rig, at(11, 10)))["step"] == 3
    assert (await check(rig, at(11, 20)))["step"] == 4
    assert await check(rig, at(12, 0)) is None  # that was the last


async def test_a_call_marked_done_is_left_out_and_all_done_means_silence(rig):
    await ready(rig)
    rig.data.tables["follow-ups"][0]["outcome"] = {"status": "done"}
    assert (await check(rig, at(9, 40)))["calls"] == ["b"]
    assert "1 call still pending" in strip_markup(rig.telegram.sent[-1][1])
    rig.data.tables["follow-ups"][1]["outcome"] = {"status": "done"}
    assert await check(rig, at(10, 40)) is None


async def test_it_waits_for_a_free_minute_and_stays_quiet_outside_the_working_day(rig):
    await ready(rig)
    rig.data.tables["meetings"] = [{"id": "m1", "title": "Union leaders", "slot": {"date": DAY, "start": 9 * 60 + 30, "duration": 60}, "outcome": {"status": "open"}}]
    assert await check(rig, at(9, 40)) is None and await check(rig, at(10, 29)) is None  # in a meeting
    assert (await check(rig, at(10, 30)))["step"] == 1  # the first free minute
    rig.data.tables["follow-ups"] = [call("z", 20 * 60 + 30, "Late call")]
    assert await check(rig, at(21, 30)) is None  # past nine in the evening


async def test_nothing_is_sent_while_the_reminders_are_off(rig):
    await ready(rig)
    await rig.client.put(R, json={"enabled": False}, headers=headers())
    assert await check(rig, at(9, 40)) is None
    assert "voice" not in (await rig.client.get(R, headers=headers())).json()  # (an older screen may still send it; it does nothing)


async def test_a_test_reminder_can_be_sent_to_see_it(rig):
    await ready(rig)
    out = (await rig.client.post(f"{R}/test", headers=headers())).json()
    assert out == {"sent": 1, "rang": False} and "A test reminder" in strip_markup(rig.telegram.sent[-1][1])


async def test_it_rings_on_telegram_each_time_when_that_is_on_and_a_failed_call_does_not_stop_the_message(rig):
    await ready(rig)
    bad = await rig.client.put(R, json={"enabled": True, "voice": True, "call": True, "callUser": "me"}, headers=headers())
    assert bad.status_code == 422 and "@yourname" in bad.json()["error"]["message"]
    on = (await rig.client.put(R, json={"enabled": True, "voice": True, "call": True, "callUser": "@arjun"}, headers=headers())).json()
    assert on["call"] is True and on["callUser"] == "@arjun" and on["callVoice"] == "en-GB-Standard-B"
    first = await check(rig, at(9, 40))
    assert first["rang"] is True and len(rig.caller.calls) == 1
    user, said, lang, repeat = rig.caller.calls[0]
    assert (user, lang, repeat) == ("@arjun", "en-GB-Standard-B", 2) and said.startswith("Stiamo. 2 calls pending: Call him again; Followup with Hemla Pawar") and len(said) <= 256
    rig.caller.fail = True
    second = await check(rig, at(10, 40))
    assert second["step"] == 2 and second["rang"] is False and "🔔" in rig.telegram.sent[-1][1]  # the message still went
    rig.caller.fail = False
    await rig.client.put(R, json={"enabled": True, "voice": True}, headers=headers())  # call switched off
    await check(rig, at(11, 10))
    assert len(rig.caller.calls) == 1


async def test_the_test_reminder_rings_too(rig):
    await ready(rig)
    await rig.client.put(R, json={"enabled": True, "voice": False, "call": True, "callUser": "+919876543210"}, headers=headers())
    out = (await rig.client.post(f"{R}/test", headers=headers())).json()
    assert out["rang"] is True and rig.caller.calls[-1][0] == "+919876543210"


async def test_the_call_can_be_tested_on_its_own_and_says_why_it_failed(rig):
    await ready(rig)
    TC = f"{R}/test-call"
    assert (await rig.client.post(TC, headers=headers())).status_code == 422  # nobody to ring yet
    assert (await rig.client.post(TC, json={"callUser": "me"}, headers=headers())).status_code == 422
    out = (await rig.client.post(TC, json={"callUser": "@arjun"}, headers=headers())).json()  # before anything is saved
    assert out == {"called": True, "user": "@arjun"}
    user, said, lang, repeat = rig.caller.calls[-1]
    assert (user, repeat) == ("@arjun", 1) and "test call" in said and len(rig.telegram.sent) == 1  # only the call: no message, no voice
    await rig.client.put(R, json={"enabled": True, "voice": False, "call": True, "callUser": "+919876543210"}, headers=headers())
    assert (await rig.client.post(TC, headers=headers())).json()["user"] == "+919876543210"  # the saved one
    rig.caller.fail = True
    failed = await rig.client.post(TC, headers=headers())
    assert failed.status_code == 422 and "Calls are disabled in Telegram" in failed.json()["error"]["message"]
