"""Telegram voice calls through CallMeBot (https://www.callmebot.com): it rings a Telegram account and reads the text aloud.

It is a third party and not part of Telegram: the person authorizes it once (send /start to @CallMeBot_txtbot), and it speaks at most 256 characters.
"""

import httpx
from stiamo_common.errors import ValidationFailed

from app.application.ports import CallApi

CALLMEBOT = "https://api.callmebot.com/start.php"


class CallMeBot(CallApi):
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def call(self, user: str, text: str, lang: str, repeat: int) -> None:
        # cc=no: the written message goes from Stiamo's own bot, so CallMeBot's text copy would only repeat it.
        params = {"user": user, "text": text[:256], "lang": lang, "rpt": str(repeat), "cc": "no"}
        try:
            r = await self._http.get(CALLMEBOT, params=params)
        except httpx.HTTPError as exc:
            raise ValidationFailed("Couldn't reach CallMeBot. Try again in a moment.") from exc
        body = r.text.strip()
        if r.status_code >= 400 or "error" in body.lower():
            detail = " ".join(body.split())[:160]
            raise ValidationFailed(f"CallMeBot couldn't place the call. {detail}".strip())
