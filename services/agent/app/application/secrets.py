"""Environment variables the person sets for their own account: write-only secrets the services use (a voice for the reminders, say).

A value is sealed with the server's key as soon as it arrives and is never sent back: the app can see that one is set, replace it, remove it
and test it, but never read it again. Only the names listed in `SECRETS` can be set, so nothing stored here is a surprise.
"""

from __future__ import annotations

from typing import Any

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import SettingsRepository, UserSettings, VoiceApi
from app.application.services import Who

#: id -> what the secret is. The id is also the name it is kept under in the person's sealed keys.
SECRETS: dict[str, dict[str, str]] = {
    "deepgram": {
        "name": "DEEPGRAM_API_KEY",
        "label": "Deepgram API key",
        "description": "Gives the call reminders a spoken voice on Telegram. Deepgram gives a new account free credit.",
        "helpUrl": "https://console.deepgram.com/",
    },
}
MAX_LENGTH = 300


class SecretService:
    def __init__(self, settings: SettingsRepository, voice: VoiceApi) -> None:
        self._settings, self._voice = settings, voice

    async def _load(self, who: Who) -> UserSettings:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        s.email = who.email or s.email
        return s

    @staticmethod
    def _known(secret_id: str) -> dict[str, str]:
        if secret_id not in SECRETS:
            raise NotFound("There is no such environment variable.")
        return SECRETS[secret_id]

    async def view(self, who: Who) -> list[dict[str, Any]]:
        """Each variable and whether it is set. The value itself is never in this."""
        s = await self._load(who)
        return [{"id": i, **meta, "set": bool(s.keys.get(i))} for i, meta in SECRETS.items()]

    async def put(self, who: Who, secret_id: str, value: str) -> list[dict[str, Any]]:
        self._known(secret_id)
        value = value.strip()
        if not value:
            raise ValidationFailed("Paste the value first.")
        if len(value) > MAX_LENGTH or any(ord(c) < 32 for c in value):
            raise ValidationFailed("That doesn't look like a key. Paste only the key itself.")
        s = await self._load(who)
        s.keys[secret_id] = value
        await self._settings.save(s)
        return await self.view(who)

    async def remove(self, who: Who, secret_id: str) -> list[dict[str, Any]]:
        self._known(secret_id)
        s = await self._load(who)
        s.keys.pop(secret_id, None)
        await self._settings.save(s)
        return await self.view(who)

    async def test(self, who: Who, secret_id: str) -> dict[str, Any]:
        """Checks the stored value against the service it is for, without showing it. Raises with the service's own complaint when it fails."""
        self._known(secret_id)
        value = (await self._load(who)).keys.get(secret_id)
        if not value:
            raise ValidationFailed("It isn't set yet.")
        if secret_id == "deepgram":
            await self._voice.check(value)
            return {"ok": True, "message": "Deepgram accepted the key."}
        raise ValidationFailed("There is no test for this one.")
