"""Speech from Deepgram (Aura): text in, MP3 out. Deepgram gives new accounts a free credit, and a reminder is a few hundred characters."""

import httpx
from stiamo_common.errors import ValidationFailed

from app.application.ports import VoiceApi
from app.domain.nudge import DEFAULT_VOICE

DEEPGRAM = "https://api.deepgram.com/v1"
#: Deepgram takes at most 2000 characters in one request.
MAX_CHARS = 2000


class DeepgramVoice(VoiceApi):
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def check(self, key: str) -> None:
        try:
            r = await self._http.get(f"{DEEPGRAM}/projects", headers={"Authorization": f"Token {key}"})
        except httpx.HTTPError as exc:
            raise ValidationFailed("Couldn't reach Deepgram. Try again in a moment.") from exc
        if r.status_code in (401, 403):
            raise ValidationFailed("Deepgram didn't accept that key.")
        if r.status_code >= 400:
            raise ValidationFailed(f"Deepgram answered {r.status_code}.")

    async def speak(self, key: str, text: str, model: str) -> bytes:
        try:
            r = await self._http.post(f"{DEEPGRAM}/speak", params={"model": model or DEFAULT_VOICE, "encoding": "mp3"}, headers={"Authorization": f"Token {key}"}, json={"text": text[:MAX_CHARS]})
        except httpx.HTTPError as exc:
            raise ValidationFailed("Couldn't reach Deepgram. Try again in a moment.") from exc
        if r.status_code >= 400:
            raise ValidationFailed(f"Deepgram couldn't make the voice message ({r.status_code}).")
        return r.content
