from datetime import UTC, datetime, timedelta

from app.domain.llm import ChatResult

from .conftest import headers
from .test_alerts import press
from .test_channels import ready, send, telegram
from .test_reminders import call, check

S = "/v1/agent/secrets"


def at(hour, minute=0):
    return datetime(2026, 11, 6, hour, minute, tzinfo=UTC) - timedelta(hours=5, minutes=30)


async def reminded(rig):
    """Two calls lined up that nobody did, and the reminder that went out ten minutes after they ended."""
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.data.tables = {"follow-ups": [call("a", 540, "Call him again"), call("b", 555, "Followup with Hemla Pawar")], "meetings": [], "tasks": []}
    await rig.client.put("/v1/agent/reminders", json={"enabled": True, "voice": False}, headers=headers())
    assert (await check(rig, at(9, 40)))["step"] == 1
    return c


def buttons(rig):
    return [b for row in rig.telegram.keyboards[-1] or [] for b in row]


async def test_a_reminder_comes_with_a_button_for_each_call_and_for_later_and_tomorrow(rig):
    await reminded(rig)
    assert [b["callback_data"] for b in buttons(rig)] == ["rm:d:a", "rm:d:b", "rm:s:a", "rm:t:a"]
    assert [b["text"] for b in buttons(rig)][:2] == ["✅ Call him again", "✅ Followup with Hemla Pawar"] and all(len(b["callback_data"]) <= 64 for b in buttons(rig))


async def test_done_marks_that_call_done_with_a_try_that_reached_them_and_is_said_back(rig):
    c = await reminded(rig)
    await press(rig, c, "rm:d:a", update=2)
    table, record = rig.data.saved[-1]
    assert table == "follow-ups" and record["id"] == "a" and record["outcome"]["status"] == "done" and record["outcome"]["recordedAt"]
    assert record["attempts"][-1] == {"date": "2026-11-06", "minute": 9 * 60 + 40, "result": "reached", "channel": "call"}
    assert rig.telegram.sent[-1][1] == "✅ Done: Call him again." and rig.telegram.answered
    rig.data.tables["follow-ups"][0]["outcome"] = {"status": "done"}
    await press(rig, c, "rm:d:a", update=3)
    assert rig.telegram.sent[-1][1] == "That one is already done." and len(rig.data.saved) == 1  # pressed twice: once


async def test_the_keyboard_keeps_the_other_calls_when_one_is_done(rig):
    c = await reminded(rig)
    hook = rig.telegram.webhooks[-1]
    shown = rig.telegram.keyboards[-1]
    body = {"update_id": 2, "callback_query": {"id": "cb2", "data": "rm:d:a", "message": {"message_id": 101, "chat": {"id": 555}, "reply_markup": {"inline_keyboard": shown}}}}
    await rig.client.post(f"/v1/agent/webhooks/telegram/{c['id']}", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": hook[1]})
    _, _, left = rig.telegram.stripped[-1]
    assert [b["callback_data"] for row in left for b in row] == ["rm:d:b", "rm:s:a", "rm:t:a"]


async def test_later_pushes_the_next_reminder_out_by_half_an_hour(rig):
    c = await reminded(rig)
    await press(rig, c, "rm:s:a", update=2)
    assert rig.telegram.sent[-1][1] == "⏰ I'll remind you again at 10:10."
    assert await check(rig, at(10, 9)) is None and (await check(rig, at(10, 10)))["step"] == 2  # the ladder said an hour: this was half


async def test_tomorrow_moves_the_calls_of_that_reminder_and_the_reminders_stop(rig):
    c = await reminded(rig)
    await press(rig, c, "rm:t:a", update=2)
    moved = {r["id"]: r["due"]["date"] for t, r in rig.data.saved if t == "follow-ups"}
    assert moved == {"a": "2026-11-07", "b": "2026-11-07"} and rig.telegram.sent[-1][1] == "📅 Moved 2 calls to tomorrow."
    assert all(r["due"]["time"] in (540, 555) for t, r in rig.data.saved)  # the same time, another day


async def test_a_chat_that_is_not_paired_cannot_press_the_buttons(rig):
    c = await reminded(rig)
    before = len(rig.data.saved)
    await press(rig, c, "rm:d:a", update=2, chat=999)
    assert len(rig.data.saved) == before


async def voice(rig, channel, update, seconds=5, file_id="f1"):
    hook = rig.telegram.webhooks[-1]
    body = {"update_id": update, "message": {"voice": {"file_id": file_id, "duration": seconds, "mime_type": "audio/ogg"}, "chat": {"id": 555}, "from": {"first_name": "Arj"}}}
    return await rig.client.post(f"/v1/agent/webhooks/telegram/{channel['id']}", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": hook[1]})


async def test_a_voice_note_is_heard_shown_back_and_answered_like_a_typed_message(rig):
    await ready(rig)
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    rig.telegram.files["f1"] = b"add a task to call Hemla tomorrow"
    await voice(rig, c, 2)
    assert "Deepgram key" in rig.telegram.sent[-1][1] and rig.llm_log == []  # no key: it says what to do, nothing reaches the model
    await rig.client.put(f"{S}/deepgram", json={"value": "dg-key-1234567"}, headers=headers())
    rig.script += [ChatResult("Noted: a task to call Hemla.")]
    await voice(rig, c, 3)
    sent = [t for chat, t in rig.telegram.sent if chat == 555]
    assert sent[-2] == "🎙️ add a task to call Hemla tomorrow" and sent[-1].startswith("Noted: a task to call Hemla.")
    assert rig.llm_log[-1]["last"].endswith("add a task to call Hemla tomorrow")


async def test_a_long_or_silent_voice_note_says_so_and_a_stranger_gets_nothing(rig):
    await ready(rig)
    c = await telegram(rig)
    await send(rig, c, f"/start {c['pairingCode']}", chat=555, update=1)
    await rig.client.put(f"{S}/deepgram", json={"value": "dg-key-1234567"}, headers=headers())
    rig.telegram.files["f1"] = b""
    await voice(rig, c, 2)
    assert "couldn't hear anything" in rig.telegram.sent[-1][1]
    rig.telegram.files["f2"] = b"x"
    await voice(rig, c, 3, seconds=400, file_id="f2")
    assert "too long" in rig.telegram.sent[-1][1] and rig.llm_log == []
    hook = rig.telegram.webhooks[-1]
    before = len(rig.telegram.sent)
    body = {"update_id": 4, "message": {"voice": {"file_id": "f1", "duration": 3}, "chat": {"id": 999}, "from": {"first_name": "X"}}}
    await rig.client.post(f"/v1/agent/webhooks/telegram/{c['id']}", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": hook[1]})
    assert len(rig.telegram.sent) == before  # an unpaired chat's voice is not even downloaded
