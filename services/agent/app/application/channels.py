"""Telegram and WhatsApp (Twilio): connecting them, who may use them, and answering the messages that arrive."""

import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import Forbidden, NotFound, ValidationFailed

from app.application.conversations import ConversationService
from app.application.ports import AccessTokens, Channel, ChannelRepository, TelegramApi, TwilioApi
from app.application.proposals import ProposalService, verdict_of
from app.application.runner import AgentRun
from app.application.services import AgentService, Who
from app.domain.llm import AiError
from app.domain.orchestrator import ORCHESTRATOR_ID
from app.domain.phone import Style, Writer, render
from app.infrastructure.messaging import TELEGRAM_MAX, WHATSAPP_MAX, as_whatsapp, clip

log = logging.getLogger(__name__)

#: How much of a conversation a chat remembers, per sender.
REMEMBER = 12

NOT_LINKED = "This chat isn't linked to a Stiamo account. Open AI agents → Channels in Stiamo to connect it."


def format_reply(run: AgentRun, limit: int, style: Style = "plain") -> str:
    """The answer, then the cases and documents it found, written for a phone's chat."""
    return render(run.answer, run.found, limit, style=style)


def format_answer(answer: str, found: list[dict[str, Any]], limit: int, agent: str | None = None, style: Style = "plain") -> str:
    """An answer and its cards written for a chat, signed with the agent that gave it when the assistant chose one."""
    return render(answer, found, limit, agent, style)


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
        proposals: ProposalService | None = None,
        conversations: ConversationService | None = None,
    ) -> None:
        self._repo, self._agent, self._tg, self._tw, self._tokens = repo, agent, telegram, twilio, tokens
        self._public, self._pairing = public_url.rstrip("/"), pairing_minutes
        self._memory = memory if memory is not None else {}
        self._proposals = proposals
        self._conversations = conversations

    # --- what is connected -----------------------------------------------------------------------------

    def _webhook(self, kind: str, channel_id: str) -> str:
        if not self._public.startswith("https://"):
            raise ValidationFailed("The server doesn't know its public https address yet (AGENT_PUBLIC_URL), so Telegram and Twilio can't reach it.")
        return f"{self._public}/v1/agent/webhooks/{kind}/{channel_id}"

    def view(self, c: Channel) -> dict[str, Any]:
        out: dict[str, Any] = {"id": c.id, "kind": c.kind, **{k: v for k, v in c.public.items() if k != "ownerEmail"}, "agent": c.public.get("agent") or ORCHESTRATOR_ID, "createdAt": c.created_at.isoformat() if c.created_at else None}
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

    async def connect_telegram(self, who: Who, bot_token: str, agent: str | None = None) -> dict[str, Any]:
        bot_token = bot_token.strip()
        if not bot_token:
            raise ValidationFailed("Paste the bot token from @BotFather first.")
        me = await self._tg.get_me(bot_token)
        for existing in await self._repo.for_user(who.user_id):
            if existing.kind == "telegram" and existing.public.get("botId") == me.get("id"):
                raise ValidationFailed("That bot is already connected.")
        cid, secret = str(uuid.uuid4()), secrets.token_urlsafe(32)
        await self._tg.set_webhook(bot_token, self._webhook("telegram", cid), secret)
        agent_id = self._agent_id(agent)
        c = Channel(cid, who.user_id, "telegram", {"botToken": bot_token, "webhookSecret": secret}, {"agent": agent_id, "ownerEmail": who.email, "botId": me.get("id"), "username": me.get("username", ""), "name": me.get("first_name", ""), "paired": []}, {}, datetime.now(UTC))
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

    async def connect_whatsapp(self, who: Who, account_sid: str, auth_token: str, sender: str, allowed: list[str], agent: str | None = None) -> dict[str, Any]:
        account_sid, auth_token = account_sid.strip(), auth_token.strip()
        numbers = [as_whatsapp(n) for n in allowed if n.strip()]
        if not account_sid or not auth_token or not sender.strip():
            raise ValidationFailed("The Account SID, Auth Token and the WhatsApp number to send from are all needed.")
        if not numbers:
            raise ValidationFailed("Add your own WhatsApp number, so only you can use the agent.")
        name = await self._tw.check_account(account_sid, auth_token)
        cid = str(uuid.uuid4())
        self._webhook("whatsapp", cid)  # fails early, with an explanation, when the server's address isn't set
        agent_id = self._agent_id(agent)
        c = Channel(cid, who.user_id, "whatsapp", {"authToken": auth_token}, {"agent": agent_id, "ownerEmail": who.email, "accountSid": account_sid, "account": name, "from": as_whatsapp(sender), "allowed": numbers}, {}, datetime.now(UTC))
        await self._repo.save(c)
        return self.view(c)

    def _agent_id(self, agent: str | None) -> str:
        """The assistant (which chooses the agent), or one agent of the team by id. The assistant is the default."""
        if not agent or agent == ORCHESTRATOR_ID:
            return ORCHESTRATOR_ID
        return self._agent.registry.get(agent).id

    async def set_agent(self, who: Who, channel_id: str, agent: str) -> dict[str, Any]:
        """Which agent answers on this channel."""
        c = await self._mine(who, channel_id)
        c.public = {**c.public, "agent": self._agent_id(agent)}
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

    async def announce(self, who: Who, text: str | Callable[[Style, int], str]) -> int:
        """Sends a message to every chat and number the person has connected. Returns how many it reached; one that fails doesn't stop the rest.
        `text` may be a function that writes the message for a chat (given its style and its length limit), so each gets it laid out its own way."""
        sent = 0
        for c in await self._repo.for_user(who.user_id):
            try:
                if c.kind == "telegram":
                    for p in c.public.get("paired", []):
                        if callable(text):
                            await self._tg.send_message(c.secrets["botToken"], p["id"], text("telegram", TELEGRAM_MAX), html=True)
                        else:
                            await self._tg.send_message(c.secrets["botToken"], p["id"], clip(text, TELEGRAM_MAX))
                        sent += 1
                else:
                    for n in c.public.get("allowed", []):
                        body = text("whatsapp", WHATSAPP_MAX) if callable(text) else clip(text, WHATSAPP_MAX)
                        await self._tw.send_whatsapp(c.public["accountSid"], c.secrets["authToken"], c.public["from"], n, body)
                        sent += 1
            except Exception:  # noqa: BLE001
                log.warning("could not announce on channel %s", c.id)
        return sent

    async def remove(self, who: Who, channel_id: str) -> None:
        c = await self._mine(who, channel_id)
        if c.kind == "telegram":
            try:
                await self._tg.delete_webhook(c.secrets["botToken"])
            except ValidationFailed:
                log.warning("could not remove the Telegram webhook for %s", c.id)
        await self._repo.delete(c.id)

    # --- messages arriving -----------------------------------------------------------------------------

    async def _answer(self, c: Channel, sender: str, text: str, channel_name: str, limit: int, style: Style) -> str:
        """Runs the agent for the channel's owner and returns what to say back, written for the chat."""
        words = Writer(style).text
        settings_email = c.public.get("ownerEmail", "")
        who = Who(c.user_id, settings_email)
        agent_id = c.public.get("agent") or ORCHESTRATOR_ID
        # A yes or a no is about what the agent just proposed in this chat, not a new question for it.
        verdict = verdict_of(text) if self._proposals else None
        if verdict is not None:
            settled = await self._proposals.settle_channel(who, self._tokens.mint(who.user_id, who.email), c.id, verdict)
            if settled is not None:
                return clip(words(settled), limit)
        if agent_id == ORCHESTRATOR_ID and self._conversations is not None:
            # The assistant: it picks the agent for each message and keeps the thread, which is also visible in the web app.
            try:
                answer, found, name = await self._conversations.channel_ask(who, self._tokens.mint(who.user_id, who.email), f"{c.kind}:{c.id}:{sender}", channel_name, text, c.id)
            except (ValidationFailed, AiError) as exc:
                return words(str(getattr(exc, "message", None) or exc))
            return format_answer(answer, found, limit, name, style)
        if agent_id == ORCHESTRATOR_ID:
            agent_id = self._agent.registry.default_id
        key = (c.id, f"{agent_id}:{sender}")  # changing the channel's agent starts a fresh conversation
        try:
            run = await self._agent.chat(who, self._tokens.mint(who.user_id, who.email), agent_id, text, self._memory.get(key), channel_name, trigger=channel_name.lower(), channel_id=c.id)
        except (ValidationFailed, AiError) as exc:
            return words(str(getattr(exc, "message", None) or exc))
        self._memory[key] = [m.to_json() for m in run.messages][-REMEMBER:]
        return format_reply(run, limit, style)

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
            await self._tg.send_message(token, chat, await self._answer(c, str(chat), text, "Telegram", TELEGRAM_MAX, "telegram"), html=True)
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
            reply = await self._answer(c, number, text, "WhatsApp", WHATSAPP_MAX, "whatsapp")
            await self._tw.send_whatsapp(c.public["accountSid"], c.secrets["authToken"], c.public["from"], number, reply)
        except Exception:  # noqa: BLE001
            log.exception("whatsapp message failed for channel %s", c.id)
