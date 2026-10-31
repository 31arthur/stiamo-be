"""Telegram and WhatsApp (Twilio): connecting them, who may use them, and answering the messages that arrive."""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import Forbidden, NotFound, ValidationFailed

from app.application.ports import AccessTokens, Channel, ChannelRepository, TelegramApi, TwilioApi
from app.application.runner import AgentRun
from app.application.services import AgentService, Who
from app.domain.llm import AiError
from app.infrastructure.messaging import TELEGRAM_MAX, WHATSAPP_MAX, as_whatsapp, clip

log = logging.getLogger(__name__)

#: How much of a conversation a chat remembers, per sender.
REMEMBER = 12

NOT_LINKED = "This chat isn't linked to a Stiamo account. Open AI agents → Channels in Stiamo to connect it."


def format_reply(run: AgentRun, limit: int) -> str:
    """The answer, then the cases and documents it found, as plain text a phone can show."""
    parts = [run.answer or "I found nothing to say about that."]
    cases = [f for f in run.found if f["kind"] == "case"]
    docs = [f for f in run.found if f["kind"] == "document"]
    if cases:
        lines = []
        for c in cases[:8]:
            nxt = c.get("nextHearing")
            lines.append(f"• {c.get('reference', '')} {c.get('title', '')}" + (f" (next hearing {nxt['date']})" if nxt else ""))
        parts.append("Cases:\n" + "\n".join(lines))
    if docs:
        lines = [f"• {d.get('name', '')}" + (f"\n  {d['webViewLink']}" if d.get("webViewLink") else "") for d in docs[:8]]
        parts.append("Documents:\n" + "\n".join(lines))
    return clip("\n\n".join(parts), limit)


class ChannelService:
    def __init__(
        self,
        repo: ChannelRepository,
        agent: AgentService,
        telegram: TelegramApi,
        twilio: TwilioApi,
        tokens: AccessTokens,
        *,
        public_url: str,
        pairing_minutes: int = 30,
        memory: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._repo, self._agent, self._tg, self._tw, self._tokens = repo, agent, telegram, twilio, tokens
        self._public, self._pairing = public_url.rstrip("/"), pairing_minutes
        self._memory = memory if memory is not None else {}

    # --- what is connected -----------------------------------------------------------------------------

    def _webhook(self, kind: str, channel_id: str) -> str:
        if not self._public.startswith("https://"):
            raise ValidationFailed("The server doesn't know its public https address yet (AGENT_PUBLIC_URL), so Telegram and Twilio can't reach it.")
        return f"{self._public}/v1/agent/webhooks/{kind}/{channel_id}"

    def view(self, c: Channel) -> dict[str, Any]:
        out: dict[str, Any] = {"id": c.id, "kind": c.kind, **{k: v for k, v in c.public.items()}, "createdAt": c.created_at.isoformat() if c.created_at else None}
        out["webhookUrl"] = f"{self._public}/v1/agent/webhooks/{c.kind}/{c.id}" if self._public else None
        pairing = c.state.get("pairing")
        if c.kind == "telegram" and pairing and datetime.fromisoformat(pairing["expires"]) > datetime.now(UTC):
            out["pairingCode"] = pairing["code"]
            out["pairingLink"] = f"https://t.me/{c.public.get('username', '')}?start={pairing['code']}"
        return out

    async def lookup(self, channel_id: str) -> Channel | None:
        return await self._repo.get(channel_id)

    async def all_for(self, who: Who) -> list[dict[str, Any]]:
        return [self.view(c) for c in await self._repo.for_user(who.user_id)]

    async def _mine(self, who: Who, channel_id: str) -> Channel:
        c = await self._repo.get(channel_id)
        if c is None or c.user_id != who.user_id:
            raise NotFound("No such channel.")
        return c

    # --- Telegram --------------------------------------------------------------------------------------

    async def connect_telegram(self, who: Who, bot_token: str) -> dict[str, Any]:
        bot_token = bot_token.strip()
        if not bot_token:
            raise ValidationFailed("Paste the bot token from @BotFather first.")
        me = await self._tg.get_me(bot_token)
        for existing in await self._repo.for_user(who.user_id):
            if existing.kind == "telegram" and existing.public.get("botId") == me.get("id"):
                raise ValidationFailed("That bot is already connected.")
        cid, secret = str(uuid.uuid4()), secrets.token_urlsafe(32)
        await self._tg.set_webhook(bot_token, self._webhook("telegram", cid), secret)
        c = Channel(cid, who.user_id, "telegram", {"botToken": bot_token, "webhookSecret": secret}, {"ownerEmail": who.email, "botId": me.get("id"), "username": me.get("username", ""), "name": me.get("first_name", ""), "paired": []}, {}, datetime.now(UTC))
        await self._repo.save(c)
        return self.view(await self._new_pairing(c))

    async def _new_pairing(self, c: Channel) -> Channel:
        c.state = {**c.state, "pairing": {"code": secrets.token_hex(4).upper(), "expires": (datetime.now(UTC) + timedelta(minutes=self._pairing)).isoformat()}}
        await self._repo.save(c)
        return c

    async def pairing_code(self, who: Who, channel_id: str) -> dict[str, Any]:
        c = await self._mine(who, channel_id)
        if c.kind != "telegram":
            raise ValidationFailed("Only Telegram bots are paired with a code.")
        return self.view(await self._new_pairing(c))

    async def unpair(self, who: Who, channel_id: str, chat_id: str) -> dict[str, Any]:
        c = await self._mine(who, channel_id)
        c.public = {**c.public, "paired": [p for p in c.public.get("paired", []) if str(p["id"]) != chat_id]}
        await self._repo.save(c)
        return self.view(c)

    # --- WhatsApp (Twilio) -----------------------------------------------------------------------------

    async def connect_whatsapp(self, who: Who, account_sid: str, auth_token: str, sender: str, allowed: list[str]) -> dict[str, Any]:
        account_sid, auth_token = account_sid.strip(), auth_token.strip()
        numbers = [as_whatsapp(n) for n in allowed if n.strip()]
        if not account_sid or not auth_token or not sender.strip():
            raise ValidationFailed("The Account SID, Auth Token and the WhatsApp number to send from are all needed.")
        if not numbers:
            raise ValidationFailed("Add your own WhatsApp number, so only you can use the agent.")
        name = await self._tw.check_account(account_sid, auth_token)
        cid = str(uuid.uuid4())
        self._webhook("whatsapp", cid)  # fails early, with an explanation, when the server's address isn't set
        c = Channel(cid, who.user_id, "whatsapp", {"authToken": auth_token}, {"ownerEmail": who.email, "accountSid": account_sid, "account": name, "from": as_whatsapp(sender), "allowed": numbers}, {}, datetime.now(UTC))
        await self._repo.save(c)
        return self.view(c)

    async def set_allowed(self, who: Who, channel_id: str, allowed: list[str]) -> dict[str, Any]:
        c = await self._mine(who, channel_id)
        if c.kind != "whatsapp":
            raise ValidationFailed("Only WhatsApp channels have a list of numbers.")
        numbers = [as_whatsapp(n) for n in allowed if n.strip()]
        if not numbers:
            raise ValidationFailed("Keep at least one number.")
        c.public = {**c.public, "allowed": numbers}
        await self._repo.save(c)
        return self.view(c)

    # --- both ------------------------------------------------------------------------------------------

    async def send_test(self, who: Who, channel_id: str) -> None:
        c = await self._mine(who, channel_id)
        text = "Stiamo is connected. Ask me about a case."
        if c.kind == "telegram":
            paired = c.public.get("paired", [])
            if not paired:
                raise ValidationFailed("Pair a chat first: send the code to the bot.")
            for p in paired:
                await self._tg.send_message(c.secrets["botToken"], p["id"], text)
        else:
            for n in c.public.get("allowed", []):
                await self._tw.send_whatsapp(c.public["accountSid"], c.secrets["authToken"], c.public["from"], n, text)

    async def remove(self, who: Who, channel_id: str) -> None:
        c = await self._mine(who, channel_id)
        if c.kind == "telegram":
            try:
                await self._tg.delete_webhook(c.secrets["botToken"])
            except ValidationFailed:
                log.warning("could not remove the Telegram webhook for %s", c.id)
        await self._repo.delete(c.id)

    # --- messages arriving -----------------------------------------------------------------------------

    async def _answer(self, c: Channel, sender: str, text: str, channel_name: str, limit: int) -> str:
        """Runs the agent for the channel's owner and returns what to say back."""
        settings_email = c.public.get("ownerEmail", "")
        who = Who(c.user_id, settings_email)
        key = (c.id, sender)
        try:
            run = await self._agent.chat(who, self._tokens.mint(who.user_id, who.email), text, self._memory.get(key), channel_name)
        except (ValidationFailed, AiError) as exc:
            return str(getattr(exc, "message", None) or exc)
        self._memory[key] = [m.to_json() for m in run.messages][-REMEMBER:]
        return format_reply(run, limit)

    def check_telegram(self, c: Channel | None, secret_header: str | None) -> Channel:
        if c is None or c.kind != "telegram":
            raise NotFound("No such channel.")
        if not secret_header or not secrets.compare_digest(secret_header, c.secrets.get("webhookSecret", "")):
            raise Forbidden("Bad secret.")
        return c

    async def handle_telegram(self, c: Channel, update: dict[str, Any]) -> None:
        """One update from Telegram. Never raises: Telegram would retry, and a message that failed once will fail again."""
        try:
            uid = update.get("update_id")
            if isinstance(uid, int):
                if uid <= int(c.state.get("lastUpdate", -1)):
                    return  # Telegram sent this one before
                c.state = {**c.state, "lastUpdate": uid}
                await self._repo.save(c)
            msg = update.get("message") or {}
            text, chat = (msg.get("text") or "").strip(), (msg.get("chat") or {}).get("id")
            if not text or chat is None:
                return
            token = c.secrets["botToken"]
            paired = c.public.get("paired", [])
            if text.startswith("/start"):
                code = text.removeprefix("/start").strip().upper()
                pairing = c.state.get("pairing")
                if code and pairing and code == pairing["code"] and datetime.fromisoformat(pairing["expires"]) > datetime.now(UTC):
                    if all(p["id"] != chat for p in paired):
                        paired.append({"id": chat, "name": (msg.get("from") or {}).get("first_name", "")})
                    c.public = {**c.public, "paired": paired}
                    c.state = {k: v for k, v in c.state.items() if k != "pairing"}
                    await self._repo.save(c)
                    await self._tg.send_message(token, chat, "Connected. Ask me about a case or a document.")
                else:
                    await self._tg.send_message(token, chat, NOT_LINKED if not any(p["id"] == chat for p in paired) else "Ask me about a case or a document.")
                return
            if not any(p["id"] == chat for p in paired):
                await self._tg.send_message(token, chat, NOT_LINKED)
                return
            await self._tg.send_typing(token, chat)
            await self._tg.send_message(token, chat, await self._answer(c, str(chat), text, "Telegram", TELEGRAM_MAX))
        except Exception:  # noqa: BLE001
            log.exception("telegram update failed for channel %s", c.id)

    def check_whatsapp(self, c: Channel | None) -> Channel:
        if c is None or c.kind != "whatsapp":
            raise NotFound("No such channel.")
        return c

    async def handle_whatsapp(self, c: Channel, sender: str, body: str) -> None:
        try:
            number = as_whatsapp(sender)
            if number not in c.public.get("allowed", []):
                return  # a stranger gets no reply, so the number isn't confirmed to be in use
            text = body.strip()
            if not text:
                return
            reply = await self._answer(c, number, text, "WhatsApp", WHATSAPP_MAX)
            await self._tw.send_whatsapp(c.public["accountSid"], c.secrets["authToken"], c.public["from"], number, reply)
        except Exception:  # noqa: BLE001
            log.exception("whatsapp message failed for channel %s", c.id)
