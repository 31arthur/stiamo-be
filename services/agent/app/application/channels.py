"""Telegram and WhatsApp (Twilio): connecting them, who may use them, and answering the messages that arrive."""

import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import Forbidden, NotFound, ValidationFailed

from app.application.availability import confirmation as availability_confirmation
from app.application.conversations import NEW_THREAD, ConversationService
from app.application.dayplans import approval_text
from app.application.ports import AccessTokens, Channel, ChannelRepository, TelegramApi, TwilioApi
from app.application.proposals import ProposalService, verdict_of
from app.application.runner import AgentRun
from app.application.services import AgentService, Who
from app.domain.agents import WORKSPACES
from app.domain.dial import call_rows
from app.domain.llm import AiError
from app.domain.orchestrator import ORCHESTRATOR_ID
from app.domain.phone import Style, Writer, render
from app.infrastructure.messaging import TELEGRAM_MAX, WHATSAPP_MAX, as_whatsapp, clip

log = logging.getLogger(__name__)

#: How much of a conversation a chat remembers, per sender.
REMEMBER = 12

#: How a workspace is named to the person who talks to its assistant.
WORKSPACE_NAMES = {"personal": "Personal", "sb": "S&B", "fsa": "FSA", "media": "Media"}

#: What a bot is for. An "assistant" bot answers and gets everything for its workspace; a "mail" bot gets the mail alerts and a "planner" bot the
#: day plans, so a workspace can keep separate bots for each. Chatting works on all of them, always within the bot's own workspace.
ROLES = {"assistant": "everything", "mail": "mail alerts", "planner": "the daily plan"}


def role_of(c: Channel) -> str:
    return c.public.get("role") or "assistant"


#: What a button says the moment it is pressed (the first two parts of its data).
PRESS_ACKS = {
    "pl:a": "Putting it on your planner…",
    "pl:x": "Dropping the plan…",
    "rm:d": "Marking it done…",
    "rm:s": "Setting the next reminder…",
    "rm:t": "Moving it to tomorrow…",
    "ws:sb": "Asking S&B…",
    "ws:fsa": "Asking FSA…",
    "ws:personal": "Asking Personal…",
    "ws:all": "Looking across everything…",
    "fw:0": "Got it…",
    "fw:1": "Got it…",
    "fw:2": "Got it…",
    "fw:3": "Got it…",
    "av:c": "Noting you can take calls…",
    "av:f": "Noting you're free…",
    "av:b": "Noting you're busy…",
    "ed:z": "Leaving it for now…",
    "ed:sd": "Marking it done…",
    "ed:o0": "Booking it…",
    "ed:o1": "Booking it…",
    "ed:o2": "Booking it…",
    "ed:r2": "Thinking of ways to start…",
}

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
        alerts: Any = None,
    ) -> None:
        self._repo, self._agent, self._tg, self._tw, self._tokens = repo, agent, telegram, twilio, tokens
        self._public, self._pairing = public_url.rstrip("/"), pairing_minutes
        self._memory = memory if memory is not None else {}
        self._proposals = proposals
        self._conversations = conversations
        #: What was sent and whether it arrived (see ports.DeliveryLog); None where nothing keeps one.
        self.deliveries: Any = None
        #: What the buttons under a reminder do (see reminders.py), and the voice notes that become text (see voicenotes.py). Set once both exist.
        self.reminder_actions: Any = None
        #: What the Approve and Not this buttons under a day plan do: a DayPlanService.
        self.plan_actions: Any = None
        #: What the buttons under a question about time inside an event do: an AvailabilityService.
        self.availability_actions: Any = None
        self.editor_actions: Any = None
        #: The app's https address, for the "Call …" buttons (Telegram only opens https links; the page there hands the number to the dialer).
        self.app_url: str = ""
        self.voice_notes: Any = None
        #: Urgent-email alerts: the buttons under them, and the words typed for "write my own" (see alerts.py). Set once both exist.
        self.alerts = alerts

    # --- what is connected -----------------------------------------------------------------------------

    def _webhook(self, kind: str, channel_id: str) -> str:
        if not self._public.startswith("https://"):
            raise ValidationFailed("The server doesn't know its public https address yet (AGENT_PUBLIC_URL), so Telegram and Twilio can't reach it.")
        return f"{self._public}/v1/agent/webhooks/{kind}/{channel_id}"

    def view(self, c: Channel) -> dict[str, Any]:
        out: dict[str, Any] = {"id": c.id, "kind": c.kind, **{k: v for k, v in c.public.items() if k != "ownerEmail"}, "agent": c.public.get("agent") or ORCHESTRATOR_ID, "createdAt": c.created_at.isoformat() if c.created_at else None}
        out["role"] = role_of(c)
        out["scope"] = c.public.get("workspace") or "all"  # what this bot can see and carries: one workspace, or everything
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

    async def _check_scope(self, who: Who, workspace: str | None, role: str, *, other_than: str | None = None) -> None:
        """A bot belongs to one workspace (and then has a purpose in it) or to all of them. A workspace keeps one bot for each purpose."""
        if workspace is not None and workspace not in WORKSPACES:
            raise ValidationFailed("That isn't one of your workspaces.")
        if role not in ROLES:
            raise ValidationFailed("A bot is for everything, mail alerts or the daily plan.")
        if role != "assistant" and not workspace:
            raise ValidationFailed("A mail or plan bot belongs to one workspace. Choose which.")
        if workspace:
            for existing in await self._repo.for_user(who.user_id):
                if existing.id != other_than and existing.kind == "telegram" and existing.public.get("workspace") == workspace and role_of(existing) == role:
                    raise ValidationFailed(f"{WORKSPACE_NAMES.get(workspace, workspace)} already has a bot for {ROLES[role]}. Remove it, or give this one another purpose.")

    async def connect_telegram(self, who: Who, bot_token: str, agent: str | None = None, workspace: str | None = None, role: str = "assistant") -> dict[str, Any]:
        """Connects a bot. With a `workspace` it belongs to that workspace alone: it sees, says and carries only that workspace's things. Without
        one it is the general bot, which sees every workspace."""
        bot_token = bot_token.strip()
        workspace = workspace or None
        await self._check_scope(who, workspace, role)
        if not bot_token:
            raise ValidationFailed("Paste the bot token from @BotFather first.")
        me = await self._tg.get_me(bot_token)
        for existing in await self._repo.for_user(who.user_id):
            if existing.kind == "telegram" and existing.public.get("botId") == me.get("id"):
                raise ValidationFailed("That bot is already connected.")
        cid, secret = str(uuid.uuid4()), secrets.token_urlsafe(32)
        await self._tg.set_webhook(bot_token, self._webhook("telegram", cid), secret)
        agent_id = self._agent_id(agent)
        c = Channel(cid, who.user_id, "telegram", {"botToken": bot_token, "webhookSecret": secret}, {"agent": agent_id, "ownerEmail": who.email, "botId": me.get("id"), "username": me.get("username", ""), "name": me.get("first_name", ""), "paired": [], **({"workspace": workspace, "role": role} if workspace else {})}, {}, datetime.now(UTC))
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

    async def set_scope(self, who: Who, channel_id: str, workspace: str | None, role: str = "assistant") -> dict[str, Any]:
        """Limits an existing bot to one workspace (and a purpose in it), or makes it the general bot again. What the chat remembered is forgotten:
        it was said under the old scope, so none of it may carry over."""
        c = await self._mine(who, channel_id)
        if c.kind != "telegram":
            raise ValidationFailed("Only Telegram bots can be limited to a workspace.")
        workspace = workspace or None
        await self._check_scope(who, workspace, role, other_than=c.id)
        public = {k: v for k, v in c.public.items() if k not in ("workspace", "role")}
        c.public = {**public, **({"workspace": workspace, "role": role} if workspace else {})}
        await self._repo.save(c)
        for key in [k for k in self._memory if k[0] == c.id]:
            del self._memory[key]
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
        text = self._hello(c)
        if c.kind == "telegram":
            paired = c.public.get("paired", [])
            if not paired:
                raise ValidationFailed("Pair a chat first: send the code to the bot.")
            for p in paired:
                await self._tg.send_message(c.secrets["botToken"], p["id"], text)
        else:
            for n in c.public.get("allowed", []):
                await self._tw.send_whatsapp(c.public["accountSid"], c.secrets["authToken"], c.public["from"], n, text)

    async def _logged(self, who: Who, kind: str, channel: str, target: str, ok: bool, error: str = "", summary: str = "", workspace: str | None = None) -> None:
        if self.deliveries is not None:
            try:
                await self.deliveries.add(who.user_id, kind, channel, target, ok, error, summary, workspace)
            except Exception:  # noqa: BLE001  keeping a record must never be why a message fails
                log.info("could not keep a delivery record")

    @staticmethod
    def _who(c: Channel, p: dict[str, Any]) -> str:
        return f"{'@' + c.public['username'] if c.public.get('username') else 'Telegram'} · {p.get('name') or p.get('id')}"

    async def announce(self, who: Who, text: str | Callable[[Style, int], str], kind: str = "message", summary: str = "", buttons: list[list[dict[str, str]]] | None = None) -> int:
        """Sends a message to every chat and number the person has connected. Returns how many it reached; one that fails doesn't stop the rest.
        `text` may be a function that writes the message for a chat (given its style and its length limit), so each gets it laid out its own way."""
        sent = 0
        for c in await self._repo.for_user(who.user_id):
            if c.public.get("workspace"):
                continue  # an email bot carries email alerts only
            try:
                if c.kind == "telegram":
                    for p in c.public.get("paired", []):
                        try:
                            if callable(text):
                                await self._tg.send_message(c.secrets["botToken"], p["id"], text("telegram", TELEGRAM_MAX), html=True, buttons=buttons)
                            else:
                                await self._tg.send_message(c.secrets["botToken"], p["id"], clip(text, TELEGRAM_MAX), buttons=buttons)
                        except Exception as exc:  # noqa: BLE001  one chat that fails doesn't stop the rest
                            await self._logged(who, kind, "telegram", self._who(c, p), False, str(getattr(exc, "message", None) or exc), summary)
                            log.warning("could not announce to chat %s of channel %s", p.get("id"), c.id)
                            continue
                        await self._logged(who, kind, "telegram", self._who(c, p), True, summary=summary)
                        sent += 1
                else:
                    for n in c.public.get("allowed", []):
                        body = text("whatsapp", WHATSAPP_MAX) if callable(text) else clip(text, WHATSAPP_MAX)
                        try:
                            await self._tw.send_whatsapp(c.public["accountSid"], c.secrets["authToken"], c.public["from"], n, body)
                        except Exception as exc:  # noqa: BLE001
                            await self._logged(who, kind, "whatsapp", n, False, str(getattr(exc, "message", None) or exc), summary)
                            continue
                        await self._logged(who, kind, "whatsapp", n, True, summary=summary)
                        sent += 1
            except Exception:  # noqa: BLE001
                log.warning("could not announce on channel %s", c.id)
        return sent

    # --- alerts: messages with buttons ----------------------------------------------------------------------------------

    async def _audience(self, who: Who, workspace: str | None, role: str) -> tuple[list[Channel], bool]:
        """The linked Telegram bots something about a workspace goes to, and whether they are that workspace's own. Its bot for that purpose, else
        its everything-bot; with neither, the general bots (which see every workspace anyway); never another workspace's bot."""
        bots = [c for c in await self._repo.for_user(who.user_id) if c.kind == "telegram" and c.public.get("paired")]
        own = [c for c in bots if workspace and c.public.get("workspace") == workspace]
        chosen = [c for c in own if role_of(c) == role] or [c for c in own if role_of(c) == "assistant"]
        if chosen:
            return chosen, True
        general = [c for c in bots if not c.public.get("workspace")]
        return (general, False) if general else (own, bool(own))

    async def push(self, who: Who, html: str, buttons: list[list[dict[str, str]]] | None = None, workspace: str | None = None, summary: str = "", kind: str = "alert", role: str = "mail") -> list[dict[str, Any]]:
        """Sends a message with buttons to the Telegram chats a workspace's alert or plan is for: its own bot for that purpose, or if it has none the
        general bots. Never another workspace's. Returns where each went, so the buttons can be taken away later."""
        audience, _ = await self._audience(who, workspace, role)
        refs: list[dict[str, Any]] = []
        for c in audience:
            for p in c.public.get("paired", []):
                try:
                    mid = await self._tg.send_message(c.secrets["botToken"], p["id"], clip(html, TELEGRAM_MAX), html=True, buttons=buttons)
                except Exception as exc:  # noqa: BLE001  one chat that fails doesn't stop the rest
                    await self._logged(who, kind, "telegram", self._who(c, p), False, str(getattr(exc, "message", None) or exc), summary, workspace)
                    log.warning("could not push to chat %s of channel %s", p["id"], c.id)
                    continue
                await self._logged(who, kind, "telegram", self._who(c, p), True, summary=summary, workspace=workspace)
                if mid:
                    refs.append({"channel": c.id, "chat": p["id"], "message": mid})
        return refs

    async def announce_plan(self, who: Who, text: Callable[[Style, int], str], workspace: str, buttons: list[list[dict[str, str]]]) -> int:
        """A day plan to the phone it is for: the workspace's own bot when it has a chat linked, otherwise every chat the person has connected
        (so a workspace without a bot still gets its plan). Returns how many chats it reached."""
        _, own = await self._audience(who, workspace, "planner")
        if not own:
            return await self.announce(who, text, kind="plan", buttons=buttons, summary=f"{workspace} plan" if workspace else "plan")
        sent = await self.push(who, text("telegram", TELEGRAM_MAX), buttons, workspace, f"{workspace} plan", kind="plan", role="planner")
        return len(sent)

    async def send_with(self, who: Who, workspace: str | None, channel_id: str, chat: str | None, text: str) -> int:
        """A message with one named Telegram bot (a workflow's Telegram node): to all of its linked chats, or to the one given. A bot that belongs to
        a workspace is only for work in that workspace. Returns how many chats it reached; a failure is said, not swallowed."""
        try:
            c = await self._mine(who, channel_id)
        except NotFound as exc:
            raise ValidationFailed("That Telegram bot isn't connected any more. Choose another credential.") from exc
        if c.kind != "telegram":
            raise ValidationFailed("That credential isn't a Telegram bot.")
        bound = c.public.get("workspace")
        if bound and bound != workspace:
            raise ValidationFailed(f"This bot belongs to {WORKSPACE_NAMES.get(bound, bound)} and only that workspace's workflows may use it.")
        paired = c.public.get("paired", [])
        targets = [p for p in paired if str(p["id"]) == chat] if chat else paired
        if not targets:
            raise ValidationFailed("That chat isn't linked to the bot: send it the pairing code first." if chat else "No chat is linked to that bot yet: send it the pairing code first.")
        sent = 0
        for p in targets:
            try:
                await self._tg.send_message(c.secrets["botToken"], p["id"], clip(text, TELEGRAM_MAX))
            except Exception as exc:  # noqa: BLE001
                await self._logged(who, "workflow", "telegram", self._who(c, p), False, str(getattr(exc, "message", None) or exc), text[:80], workspace)
                raise ValidationFailed(f"Telegram didn't take it: {getattr(exc, 'message', None) or exc}") from exc
            await self._logged(who, "workflow", "telegram", self._who(c, p), True, summary=text[:80], workspace=workspace)
            sent += 1
        return sent

    async def tell(self, who: Who, workspace: str, text: str, kind: str = "message", buttons: list[list[dict[str, str]]] | None = None) -> int:
        """A plain message about a workspace to the phone it belongs to (its own bot, else the master bot). Returns how many chats it reached."""
        import html as _html

        _, own = await self._audience(who, workspace or None, "assistant")
        if own:
            return len(await self.push(who, _html.escape(text), buttons, workspace, text[:80], kind=kind, role="assistant"))
        return await self.announce(who, text, kind=kind, summary=text[:80], buttons=buttons)

    async def phone_for(self, who: Who, workspace: str) -> str:
        """Where an email alert about a workspace would arrive: its own email bot ("workspace"), the general bot ("general"), or nowhere ("none"):
        a bot only counts once a chat is paired with it."""
        audience, own = await self._audience(who, workspace, "mail")
        if own:
            return "workspace"
        return "general" if audience else "none"

    async def say(self, channel_id: str, chat: int | str, html: str, buttons: list[list[dict[str, str]]] | None = None) -> int | None:
        c = await self._repo.get(channel_id)
        if c is None or c.kind != "telegram":
            return None
        return await self._tg.send_message(c.secrets["botToken"], chat, clip(html, TELEGRAM_MAX), html=True, buttons=buttons)

    async def strip(self, channel_id: str, chat: int | str, message_id: int) -> None:
        c = await self._repo.get(channel_id)
        if c is not None and c.kind == "telegram":
            await self._tg.edit_buttons(c.secrets["botToken"], chat, message_id, None)

    async def ack(self, channel_id: str, callback_id: str, text: str = "") -> None:
        c = await self._repo.get(channel_id)
        if c is not None and c.kind == "telegram":
            try:
                await self._tg.answer_callback(c.secrets["botToken"], callback_id, text)
            except Exception:  # noqa: BLE001  a spinner that won't stop is not worth failing for
                log.info("could not answer a button press")

    async def resync_webhooks(self) -> int:
        """Telegram sends button presses only to a webhook that asked for them. Bots connected before alerts existed are set up again, once,
        when the service starts. Returns how many were refreshed."""
        done = 0
        for c in await self._repo.all_telegram():
            try:
                await self._tg.set_webhook(c.secrets["botToken"], self._webhook("telegram", c.id), c.secrets["webhookSecret"])
                done += 1
            except Exception:  # noqa: BLE001
                log.warning("could not refresh the webhook of channel %s", c.id)
        return done

    async def remove(self, who: Who, channel_id: str) -> None:
        c = await self._mine(who, channel_id)
        if c.kind == "telegram":
            try:
                await self._tg.delete_webhook(c.secrets["botToken"])
            except ValidationFailed:
                log.warning("could not remove the Telegram webhook for %s", c.id)
        await self._repo.delete(c.id)

    # --- messages arriving -----------------------------------------------------------------------------

    @staticmethod
    def _hello(c: Channel) -> str:
        """What a new chat is told it can do. A workspace's bot is that workspace's assistant."""
        ws = c.public.get("workspace")
        if ws:
            name = WORKSPACE_NAMES.get(ws, ws)
            return f"I'm the {name} assistant. Ask me what's pending or what's on today, to plan tomorrow, to put things on your schedule (like “call Hemla tomorrow at 11”), or to add a task or follow-up. I show you what I'd save and you reply YES. I plan around everything else on your day. I only see {name}."
        return "I'm your Stiamo assistant, one for all your workspaces. Ask me what's pending, to plan tomorrow, to call someone, or to add a task. I work out which workspace each message is about (S&B, FSA or Personal) and ask when it isn't clear, and I keep each one's information apart."

    async def _answer(self, c: Channel, sender: str, text: str, channel_name: str, limit: int, style: Style) -> str | tuple[str, list[list[dict[str, str]]]]:
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
            # The assistant: it picks the agent for each message and keeps the thread, which is also visible in the web app. A bot of one workspace
            # answers about that workspace; the master bot (no workspace) works out which one each message is about, and asks when it can't tell.
            base = f"{c.kind}:{c.id}:{sender}"
            scope = c.public.get("workspace")
            if scope:
                return await self._ask_in(c, who, base, text, scope, channel_name, limit, style)
            if text.strip().lower() in NEW_THREAD:  # a new conversation on the master starts every workspace's thread afresh
                for k in ("sb", "fsa", "personal", "all"):
                    await self._conversations.channel_ask(who, self._tokens.mint(who.user_id, who.email), f"{base}:{k}", channel_name, text, c.id, k)
                return "Started a new conversation. What do you need?"
            ws = await self._conversations.pick_workspace(who, base, text)
            if ws is None:
                if style == "telegram":
                    return await self._ask_workspace(c, sender, text)
                ws = "all"  # WhatsApp can't offer buttons: the master looks across everything
            return await self._ask_in(c, who, f"{base}:{ws}", text, ws, channel_name, limit, style, master=True)
        if agent_id == ORCHESTRATOR_ID:
            agent_id = self._agent.registry.default_id
        key = (c.id, f"{agent_id}:{sender}")  # changing the channel's agent starts a fresh conversation
        try:
            # A bot pinned to one agent is held to the bot's workspace just the same: the scope belongs to the bot, whichever agent answers.
            run = await self._agent.chat(who, self._tokens.mint(who.user_id, who.email), agent_id, text, self._memory.get(key), channel_name, trigger=channel_name.lower(), channel_id=c.id, workspace=c.public.get("workspace") or None)
        except (ValidationFailed, AiError) as exc:
            return words(str(getattr(exc, "message", None) or exc))
        self._memory[key] = [m.to_json() for m in run.messages][-REMEMBER:]
        return format_reply(run, limit, style)

    async def _ask_in(self, c: Channel, who: Who, key: str, text: str, ws: str, channel_name: str, limit: int, style: Style, master: bool = False) -> str:
        """One message to the assistant, in the thread of one workspace ("all": every workspace together)."""
        try:
            answer, found, name = await self._conversations.channel_ask(who, self._tokens.mint(who.user_id, who.email), key, channel_name, text, c.id, ws)
        except (ValidationFailed, AiError) as exc:
            return Writer(style).text(str(getattr(exc, "message", None) or exc))
        if master and name:
            name = f"{name} · {WORKSPACE_NAMES.get(ws, 'All workspaces')}"  # signed with the workspace it worked in
        return format_answer(answer, found, limit, name, style)

    async def _ask_workspace(self, c: Channel, sender: str, text: str) -> tuple[str, list[list[dict[str, str]]]]:
        """The message doesn't say which workspace it is about: keep it, and ask with buttons. Pressing one answers it, in that workspace."""
        short = uuid.uuid4().hex[:8]
        pending = {**(c.state.get("pending") or {}), short: {"text": text[:2000], "sender": sender, "at": datetime.now(UTC).isoformat()}}
        c.state = {**c.state, "pending": dict(list(pending.items())[-5:])}
        await self._repo.save(c)
        row = [{"text": f"{'⚖️' if k == 'sb' else '💻' if k == 'fsa' else '🏠'} {WORKSPACE_NAMES[k]}", "callback_data": f"ws:{k}:{short}"} for k in ("sb", "fsa", "personal")]
        return "🗂️ Which workspace is that for?", [row, [{"text": "🌐 All of them", "callback_data": f"ws:all:{short}"}]]

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
            if update.get("callback_query"):
                await self._handle_press(c, update["callback_query"])
                return
            msg = update.get("message") or {}
            text, chat = (msg.get("text") or "").strip(), (msg.get("chat") or {}).get("id")
            voice = msg.get("voice") or msg.get("audio")
            if not text and voice and chat is not None and self.voice_notes is not None and any(p["id"] == chat for p in c.public.get("paired", [])):
                text = await self._heard(c, chat, voice)
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
                    await self._tg.send_message(token, chat, "Connected. " + self._hello(c))
                else:
                    await self._tg.send_message(token, chat, NOT_LINKED if not any(p["id"] == chat for p in paired) else self._hello(c))
                return
            if not any(p["id"] == chat for p in paired):
                await self._tg.send_message(token, chat, NOT_LINKED)
                return
            if self.alerts is not None and not text.startswith("/"):
                who = Who(c.user_id, c.public.get("ownerEmail", ""))
                if await self.alerts.take_text(who, self._tokens.mint(who.user_id, who.email), c.id, chat, text):
                    return
            await self._tg.send_typing(token, chat)
            reply = await self._answer(c, str(chat), text, "Telegram", TELEGRAM_MAX, "telegram")
            if isinstance(reply, tuple):
                await self._tg.send_message(token, chat, reply[0], buttons=reply[1])  # a question with buttons
            else:
                await self._tg.send_message(token, chat, reply, html=True)
        except Exception:  # noqa: BLE001
            log.exception("telegram update failed for channel %s", c.id)

    async def _handle_press(self, c: Channel, press: dict[str, Any]) -> None:
        """A button under a message was pressed, by someone in a chat paired with this bot. It is never silent: the press is answered at once
        (a toast), a message says it is being done, and that message becomes the result, or the reason it failed."""
        token = c.secrets["botToken"]
        callback_id = str(press.get("id", ""))
        message = press.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        data = str(press.get("data") or "")
        if chat is None or not any(p["id"] == chat for p in c.public.get("paired", [])):
            await self._toast(token, callback_id, "This chat isn't linked to Stiamo.")
            return
        who = Who(c.user_id, c.public.get("ownerEmail", ""))
        said = PRESS_ACKS.get(":".join(data.split(":")[:2]), "On it…")
        await self._toast(token, callback_id, f"⏳ {said}")
        holder = await self._holding(token, chat, f"⏳ {said}")
        try:
            result = await self._run_press(c, who, chat, message, data)
        except Exception as exc:  # noqa: BLE001  a button must always answer
            log.exception("a button press failed on channel %s", c.id)
            result = "⚠️ " + str(getattr(exc, "message", None) or "That didn't work.") + " Nothing was changed. Try again, or do it in Stiamo."
        buttons, html = None, False
        if isinstance(result, tuple):
            result, buttons, *rest = result
            html = bool(rest and rest[0])
        await self._settle(token, chat, holder, result, buttons, html)

    async def _toast(self, token: str, callback_id: str, text: str) -> None:
        try:
            await self._tg.answer_callback(token, callback_id, text)
        except Exception:  # noqa: BLE001  Telegram refuses an answer that is too old: the message below still says what happened
            log.info("could not answer a button press")

    async def _holding(self, token: str, chat: int | str, text: str) -> int | None:
        try:
            return await self._tg.send_message(token, chat, text)
        except Exception:  # noqa: BLE001
            log.info("could not say a button was received")
            return None

    async def _settle(self, token: str, chat: int | str, holder: int | None, text: str, buttons: list[list[dict[str, str]]] | None = None, html: bool = False) -> None:
        """The result goes into the message that said it was being done; if that can't be changed it is sent on its own."""
        if holder is not None:
            try:
                await self._tg.edit_message(token, chat, holder, text, html=html, buttons=buttons)
                return
            except Exception:  # noqa: BLE001
                log.info("could not change the holding message")
        try:
            await self._tg.send_message(token, chat, text, html=html, buttons=buttons)
        except Exception:  # noqa: BLE001
            log.warning("could not report what a button did")

    async def _strip(self, token: str, chat: int | str, message: dict[str, Any]) -> None:
        try:
            await self._tg.edit_buttons(token, chat, int(message.get("message_id") or 0), None)
        except Exception:  # noqa: BLE001
            log.info("could not take the buttons off a message")

    async def _run_press(self, c: Channel, who: Who, chat: int | str, message: dict[str, Any], data: str) -> str | tuple[Any, ...]:
        """Does what the button says and returns what to tell the person (and buttons to leave under it, if any)."""
        token = c.secrets["botToken"]
        access = self._tokens.mint(who.user_id, who.email)
        _, _, rest = (data.split(":", 2) + ["", ""])[:3]
        kind, action = data.split(":")[0], (data.split(":") + [""])[1]
        if kind == "rm" and self.reminder_actions is not None:
            return await self._reminder_press(c, chat, message, data, who, access)
        if kind == "pl" and self.plan_actions is not None:
            await self._strip(token, chat, message)
            if action == "a":
                done = await self.plan_actions.apply(who, access, rest)
                calls = call_rows(self.app_url, [b for b in done["plan"]["blocks"] if b["kind"] == "followUp"])
                return (approval_text(done), calls) if calls else approval_text(done)  # the calls it booked stay one tap away
            await self.plan_actions.dismiss(who, rest)
            return "👍 Dropped. Nothing was changed on your planner or in Google Calendar.\n\nSay “plan tomorrow” if you want a new one."
        if kind == "ws":
            pending = (c.state.get("pending") or {}).pop(rest, None)
            if pending is None or action not in ("sb", "fsa", "personal", "all"):
                return "That question is out of date. Send your message again."
            await self._repo.save(c)
            await self._strip(token, chat, message)
            reply = await self._ask_in(c, who, f"{c.kind}:{c.id}:{pending['sender']}:{action}", pending["text"], action, "Telegram", TELEGRAM_MAX, "telegram", master=True)
            return reply, None, True
        if kind == "fw":
            await self._strip(token, chat, message)
            return await self._agent.resume_flow(who, access, rest, int(action))
        if kind == "av" and self.availability_actions is not None:
            await self._strip(token, chat, message)
            return availability_confirmation(await self.availability_actions.answer(who, access, action, rest))
        if kind == "ed" and self.editor_actions is not None:
            await self._strip(token, chat, message)
            return await self.editor_actions.press(who, access, action, rest)
        if kind == "al" and self.alerts is not None:
            await self.alerts.on_callback(who, access, c.id, chat, int(message.get("message_id") or 0), "", data)
            return "✅ Done. The details are above."
        return "That button is from an older message and doesn't work any more. Open the latest one, or say what you need."

    async def _heard(self, c: Channel, chat: int | str, voice: dict[str, Any]) -> str:
        """A voice note as the words said, shown back so a mishearing is seen. Empty (and the reason said) when it can't be read."""
        token = c.secrets["botToken"]
        who = Who(c.user_id, c.public.get("ownerEmail", ""))
        try:
            await self._tg.send_typing(token, chat)
            audio = await self._tg.download_file(token, str(voice.get("file_id")))
            words = await self.voice_notes.transcribe(who, audio, str(voice.get("mime_type") or "audio/ogg"), int(voice.get("duration") or 0))
        except (ValidationFailed, AiError) as exc:
            await self._tg.send_message(token, chat, str(getattr(exc, "message", None) or exc))
            return ""
        await self._tg.send_message(token, chat, f"🎙️ {words}")
        return words

    async def _reminder_press(self, c: Channel, chat: int | str, message: dict[str, Any], data: str, who: Who, access: str) -> str:
        """A button under a reminder: done, remind again later, or tomorrow. A button pressed twice, or after the call moved, only says so."""
        token = c.secrets["botToken"]
        _, action, ident = (data.split(":", 2) + ["", ""])[:3]
        said = await self.reminder_actions.press(who, access, action, ident)
        rows = ((message.get("reply_markup") or {}).get("inline_keyboard")) or []
        left = [r for r in rows if not any(b.get("callback_data") == data for b in r)] if action == "d" else []
        try:  # a call done leaves the other buttons; anything else settles the reminder, so its buttons go
            await self._tg.edit_buttons(token, chat, int(message.get("message_id") or 0), left if any(b.get("callback_data", "").startswith("rm:d:") for r in left for b in r) else None)
        except Exception:  # noqa: BLE001
            log.info("could not update the buttons under a reminder")
        return said

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
