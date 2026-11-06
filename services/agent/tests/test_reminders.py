from datetime import UTC, datetime, timedelta

from app.domain.phone import strip_markup

from .conftest import headers
from .test_channels import send, telegram

R = "/v1/agent/reminders"
DAY = "2026-11-06"


def at(hour, minute=0):
    """A moment on DAY by the clock in India (the default zone), as the server's UTC clock would show it."""
    return datetime(2026, 11, 6, hour, minute, tzinfo=UTC) - timedelta(hours=5, minutes=30)


def call(i, start, title, status="open"):
    return {"id": i, "title": title, "channel": "call", "due": {"date": DAY, "time": start}, "duration": 15, "outcome": {"status": status}}


async def ready(rig, voice=True):
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.data.tables = {"follow-ups": [call("a", 540, "Call him again"), call("b", 555, "Followup with Hemla Pawar")], "meetings": [], "tasks": []}
    assert (await rig.client.put(f"{R}/voice-key", json={"apiKey": "dg-key-1234567"}, headers=headers())).json()["voiceKey"] == {"connected": True, "last4": "4567"}
    assert (await rig.client.put(R, json={"enabled": True, "voice": voice}, headers=headers())).json()["enabled"] is True


async def check(rig, when):
    rig.app.state.clock = lambda: when
    return (await rig.client.post(f"{R}/check", headers=headers())).json()["reminded"]


async def test_the_reminders_come_after_the_calls_end_then_an_hour_half_an_hour_and_ten_minutes_later_with_a_voice(rig):
    await ready(rig)
    assert await check(rig, at(9, 39)) is None  # the calls end at 09:30: ten minutes on is 09:40
    first = await check(rig, at(9, 40))
    assert first == {"calls": ["a", "b"], "step": 1, "spoken": True}
    text = strip_markup(rig.telegram.sent[-1][1])
    assert "🔔 2 calls still pending" in text and "Call him again" in text and "Followup with Hemla Pawar" in text and "Mark them done in Stiamo" in text
    chat, audio, _ = rig.telegram.voices[-1]
    assert chat == 555 and b"2 calls are still pending" in audio and b"1, Call him again; 2, Followup with Hemla Pawar" in audio
    assert await check(rig, at(9, 41)) is None and await check(rig, at(10, 39)) is None  # not again for an hour
    assert (await check(rig, at(10, 40)))["step"] == 2
    assert await check(rig, at(11, 9)) is None and (await check(rig, at(11, 10)))["step"] == 3
    assert (await check(rig, at(11, 20)))["step"] == 4 and b"last reminder" in rig.telegram.voices[-1][1]
    assert await check(rig, at(12, 0)) is None and len(rig.telegram.voices) == 4  # that was the last


async def test_a_call_marked_done_is_left_out_and_all_done_means_silence(rig):
    await ready(rig)
    rig.data.tables["follow-ups"][0]["outcome"] = {"status": "done"}
    assert (await check(rig, at(9, 40)))["calls"] == ["b"]
    assert b"A call is still pending" in rig.telegram.voices[-1][1]
    rig.data.tables["follow-ups"][1]["outcome"] = {"status": "done"}
    assert await check(rig, at(10, 40)) is None and len(rig.telegram.voices) == 1


async def test_it_waits_for_a_free_minute_and_stays_quiet_outside_the_working_day(rig):
    await ready(rig)
    rig.data.tables["meetings"] = [{"id": "m1", "title": "Union leaders", "slot": {"date": DAY, "start": 9 * 60 + 30, "duration": 60}, "outcome": {"status": "open"}}]
    assert await check(rig, at(9, 40)) is None and await check(rig, at(10, 29)) is None  # in a meeting
    assert (await check(rig, at(10, 30)))["step"] == 1  # the first free minute
    rig.data.tables["follow-ups"] = [call("z", 20 * 60 + 30, "Late call")]
    assert await check(rig, at(21, 30)) is None  # past nine in the evening


async def test_without_a_voice_key_or_with_the_voice_off_the_reminder_is_written_only(rig):
    await ready(rig, voice=False)
    assert (await check(rig, at(9, 40)))["spoken"] is False and rig.telegram.voices == [] and rig.voice.said == []
    await rig.client.put(R, json={"enabled": True, "voice": True}, headers=headers())
    await rig.client.delete(f"{R}/voice-key", headers=headers())
    assert (await check(rig, at(10, 40)))["spoken"] is False and rig.telegram.voices == []


async def test_nothing_is_sent_while_the_reminders_are_off_and_a_bad_key_is_refused(rig):
    await ready(rig)
    await rig.client.put(R, json={"enabled": False, "voice": True}, headers=headers())
    assert await check(rig, at(9, 40)) is None and rig.telegram.voices == []
    bad = await rig.client.put(f"{R}/voice-key", json={"apiKey": "bad-key"}, headers=headers())
    assert bad.status_code == 422
    assert (await rig.client.get(R, headers=headers())).json()["voiceKey"]["connected"] is True  # the good one is kept


async def test_a_test_reminder_can_be_sent_to_hear_the_voice(rig):
    await ready(rig)
    out = (await rig.client.post(f"{R}/test", headers=headers())).json()
    assert out == {"sent": 1, "spoken": True} and "A test reminder" in strip_markup(rig.telegram.sent[-1][1])
