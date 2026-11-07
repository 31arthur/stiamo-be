"""Voice notes: what is said into Telegram becomes text, then goes to the assistant like anything typed. Uses the person's own Deepgram key."""

from __future__ import annotations

from stiamo_common.errors import ValidationFailed

from app.application.ports import SettingsRepository, VoiceApi
from app.application.reminders import VOICE_KEY
from app.application.services import Who

#: A longer note than this is not read: it is probably not a quick instruction.
MAX_SECONDS = 180
MAX_BYTES = 10_000_000


class VoiceNotes:
    def __init__(self, settings: SettingsRepository, voice: VoiceApi) -> None:
        self._settings, self._voice = settings, voice

    async def transcribe(self, who: Who, audio: bytes, mime: str, seconds: int = 0) -> str:
        if seconds > MAX_SECONDS or len(audio) > MAX_BYTES:
            raise ValidationFailed("That voice note is too long. Keep it under three minutes.")
        s = await self._settings.get(who.user_id)
        key = (s.keys if s else {}).get(VOICE_KEY)
        if not key:
            raise ValidationFailed("To use voice notes, add your Deepgram key under Developer → Environment.")
        text = await self._voice.transcribe(key, audio, mime)
        if not text:
            raise ValidationFailed("I couldn't hear anything in that voice note. Try again, a little closer.")
        return text
