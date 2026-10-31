"""Talking to Telegram and Twilio (WhatsApp)."""

import base64
import hashlib
import hmac
from typing import Any

import httpx
from stiamo_common.errors import ValidationFailed

from app.application.ports import TelegramApi, TwilioApi

TELEGRAM = "https://api.telegram.org"
TWILIO = "https://api.twilio.com/2010-04-01"
#: WhatsApp (through Twilio) takes at most 1600 characters in one message; Telegram 4096.
WHATSAPP_MAX = 1500
TELEGRAM_MAX = 4000


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class HttpTelegramApi(TelegramApi):
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _call(self, token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            r = await self._http.post(f"{TELEGRAM}/bot{token}/{method}", json=payload or {})
        except httpx.HTTPError as exc:
            raise ValidationFailed("Couldn't reach Telegram. Try again in a moment.") from exc
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not body.get("ok"):
            if r.status_code in (401, 404):
                raise ValidationFailed("Telegram doesn't recognise that bot token. Copy it again from @BotFather.")
            raise ValidationFailed(f"Telegram refused: {body.get('description', r.status_code)}")
        return body.get("result")

    async def get_me(self, token: str) -> dict[str, Any]:
        return await self._call(token, "getMe")

    async def set_webhook(self, token: str, url: str, secret: str) -> None:
        await self._call(token, "setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message"], "drop_pending_updates": True})

    async def delete_webhook(self, token: str) -> None:
        await self._call(token, "deleteWebhook", {"drop_pending_updates": True})

    async def send_message(self, token: str, chat_id: int | str, text: str) -> None:
        await self._call(token, "sendMessage", {"chat_id": chat_id, "text": clip(text, TELEGRAM_MAX), "disable_web_page_preview": True})

    async def send_typing(self, token: str, chat_id: int | str) -> None:
        await self._call(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})


class HttpTwilioApi(TwilioApi):
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def check_account(self, sid: str, auth_token: str) -> str:
        try:
            r = await self._http.get(f"{TWILIO}/Accounts/{sid}.json", auth=(sid, auth_token))
        except httpx.HTTPError as exc:
            raise ValidationFailed("Couldn't reach Twilio. Try again in a moment.") from exc
        if r.status_code in (401, 403, 404):
            raise ValidationFailed("Twilio didn't accept that Account SID and Auth Token.")
        if r.status_code != 200:
            raise ValidationFailed(f"Twilio answered {r.status_code}.")
        return r.json().get("friendly_name", "")

    async def send_whatsapp(self, sid: str, auth_token: str, sender: str, to: str, text: str) -> None:
        try:
            r = await self._http.post(f"{TWILIO}/Accounts/{sid}/Messages.json", auth=(sid, auth_token), data={"From": as_whatsapp(sender), "To": as_whatsapp(to), "Body": clip(text, WHATSAPP_MAX)})
        except httpx.HTTPError as exc:
            raise ValidationFailed("Couldn't reach Twilio. Try again in a moment.") from exc
        if r.status_code >= 300:
            detail = ""
            try:
                detail = r.json().get("message", "")
            except ValueError:
                pass
            raise ValidationFailed(f"Twilio couldn't send the message ({r.status_code}). {detail}".strip())


def as_whatsapp(number: str) -> str:
    """`+91 98765 43210` and `whatsapp:+919876543210` are the same number."""
    digits = "".join(ch for ch in number.removeprefix("whatsapp:") if ch.isdigit() or ch == "+")
    return f"whatsapp:{digits}"


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """What Twilio signs a webhook with: the URL followed by every field, sorted by name, HMAC-SHA1 with the Auth Token."""
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def valid_twilio_signature(auth_token: str, url: str, params: dict[str, str], header: str | None) -> bool:
    return bool(header) and hmac.compare_digest(twilio_signature(auth_token, url, params), header or "")
