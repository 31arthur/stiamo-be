"""Urgent email, on the phone: an agent that watches a mailbox raises an alert the moment something that can't wait arrives.

The alert is a Telegram message with its own loud look, a link that opens the email in the app, and buttons: a few suggested replies, "write my own"
and "handled". Pressing a reply has a model write it in the organisation's theme (see domain/themes.py), shows the draft, and sends it from the
mailbox only when the person presses Send. Nothing is ever sent without that press.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any, Protocol

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import (
    Alert,
    AlertRepository,
    CallApi,
    DataGateway,
    LlmClient,
    SettingsRepository,
    UserSettings,
)
from app.application.reminders import reminder_settings, valid_call_user
from app.application.services import Who, selection_of
from app.domain.alerts import (
    AWAIT_MINUTES,
    alert_buttons,
    alert_text,
    call_text,
    clean_replies,
    draft_buttons,
    draft_text,
    mail_url,
    parse_callback,
    parse_draft,
    reply_prompt,
    reply_subject,
)
from app.domain.llm import AiError, Message
from app.domain.themes import first_name, render_email

log = logging.getLogger(__name__)

#: A call about priority mail is not made again for the same workspace within this long: the Telegram alerts say the rest.
CALL_COOLDOWN = timedelta(minutes=15)
WORKSPACE_LABELS = {"personal": "Personal", "sb": "S&B", "fsa": "FSA", "media": "Media"}


class Phone(Protocol):
    """What the alerts need of the phone chats (the channel service)."""

    async def push(self, who: Who, html: str, buttons: list[list[dict[str, str]]] | None, workspace: str | None = None, summary: str = "") -> list[dict[str, Any]]: ...

    async def say(self, channel_id: str, chat: int | str, html: str, buttons: list[list[dict[str, str]]] | None = None) -> int | None: ...

    async def strip(self, channel_id: str, chat: int | str, message_id: int) -> None: ...

    async def ack(self, channel_id: str, callback_id: str, text: str = "") -> None: ...


def _theme(s: UserSettings | None, alert: Alert) -> dict[str, Any] | None:
    return next((t for t in (s.themes if s else []) if t["id"] == alert.theme_id), None)


def _view(a: Alert) -> dict[str, Any]:
    return {"id": a.id, "subject": a.subject, "sender_name": a.sender_name, "sender_address": a.sender_address, "summary": a.summary, "action": a.action, "due": a.due, "replies": a.replies}


class AlertService:
    def __init__(
        self,
        alerts: AlertRepository,
        settings: SettingsRepository,
        llm_factory: Callable[[str, str], LlmClient],
        data_factory: Callable[[str], DataGateway],
        tokens: Any,
        phone: Phone,
        *,
        app_url: str = "",
        clock_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        caller: CallApi | None = None,
        calls: dict[tuple[str, str], datetime] | None = None,
        deliveries: Any = None,
    ) -> None:
        self._alerts, self._settings, self._llm, self._data, self._tokens, self._phone, self._app_url, self._now = alerts, settings, llm_factory, data_factory, tokens, phone, app_url, clock_now
        self._caller = caller
        self._deliveries = deliveries
        #: When a call about a workspace's priority mail was last placed, shared by every request of this process.
        self._calls = calls if calls is not None else {}

    # --- raising -----------------------------------------------------------------------------------------------

    async def raise_alert(
        self,
        who: Who,
        token: str,
        *,
        workflow_id: str | None,
        account_id: str,
        folder: str,
        uid: int,
        item_id: str,
        subject: str,
        sender_name: str,
        sender_address: str,
        message_id: str,
        analysis: dict[str, Any],
        sample: bool = False,
        call: bool = False,
    ) -> Alert | None:
        """Pings the phone about one email. Once per email: raising it again does nothing. Returns None when it was raised before."""
        if await self._alerts.by_item(who.user_id, item_id):
            return None
        s = await self._settings.get(who.user_id)
        workspace, label = "sb", WORKSPACE_LABELS["sb"]
        try:
            account = next((a for a in await self._data(token).mail_accounts() if a.get("id") == account_id), None)
        except AiError:
            account = None
        if account:
            workspace = account.get("workspace") or workspace
            label = WORKSPACE_LABELS.get(workspace, workspace)
        from app.domain.themes import pick_theme

        theme = pick_theme(s.themes if s else [], sender_address, subject, str(analysis.get("summary") or ""))
        now = self._now()
        alert = Alert(
            uuid.uuid4().hex[:12], who.user_id, item_id, workflow_id, workspace, account_id, folder, uid, subject, sender_name, sender_address, message_id,
            str(analysis.get("summary") or ""), str(analysis.get("action") or ""), str(analysis.get("due") or ""), str(analysis.get("priority") or "high"),
            clean_replies(analysis.get("replies")), theme["id"] if theme else None, "open", None, None, [], sample, now, now,
        )
        await self._alerts.save(alert)
        view = _view(alert)
        url = mail_url(self._app_url, workspace, account_id, folder, uid) if not sample else (self._app_url.rstrip("/") + f"/{workspace}/mail" if self._app_url else "")
        alert.chats = await self._phone.push(who, alert_text({**view, "priority": alert.priority, "sender_name": sender_name, "sender_address": sender_address}, theme, label), alert_buttons(view, url), workspace, subject)
        await self._alerts.save(alert)
        if call and alert.priority == "high" and not sample:
            await self._ring(who, s, workspace, label, sender_name or sender_address, subject)
        return alert

    async def _ring(self, who: Who, s: UserSettings | None, workspace: str, label: str, sender: str, subject: str) -> bool:
        """Rings the owner on Telegram about a priority email: once for a burst, and only if a number to ring is set under Call reminders."""
        cfg = reminder_settings(s.reminders if s else {})
        if self._caller is None or not valid_call_user(cfg["callUser"]):
            return False
        now = self._now()
        last = self._calls.get((who.user_id, workspace))
        if last is not None and now - last < CALL_COOLDOWN:
            return False
        try:
            await self._caller.call(cfg["callUser"], call_text(label, sender, subject), cfg["callVoice"], 2)
        except Exception as exc:  # noqa: BLE001  the alert is already on the phone
            log.warning("could not ring about priority mail for %s", who.user_id)
            await self._record(who, False, cfg["callUser"], str(getattr(exc, "message", None) or exc), subject, workspace)
            return False
        await self._record(who, True, cfg["callUser"], "", subject, workspace)
        self._calls[(who.user_id, workspace)] = now
        return True

    async def _record(self, who: Who, ok: bool, target: str, error: str, summary: str, workspace: str) -> None:
        if self._deliveries is not None:
            try:
                await self._deliveries.add(who.user_id, "call", "call", target, ok, error, summary, workspace)
            except Exception:  # noqa: BLE001
                log.info("could not keep a delivery record")

    async def sample(self, who: Who, token: str) -> Alert | None:
        """A made-up urgent email, to see what an alert looks like and to try the buttons. Nothing is sent to anyone from it."""
        s = await self._settings.get(who.user_id)
        theme = next((t for t in (s.themes if s else []) if t.get("default")), None) or next(iter(s.themes if s else []), None)
        domain = (theme["match"]["domains"][0] if theme and theme["match"]["domains"] else "example.org")
        return await self.raise_alert(
            who, token, workflow_id=None, account_id="", folder="INBOX", uid=0, item_id=f"sample:{uuid.uuid4().hex[:10]}", subject="Registration closes tomorrow: confirm your attendance",
            sender_name="Registrar", sender_address=f"registrar@{domain}", message_id="",
            analysis={
                "summary": "This is a sample alert. The registrar needs your confirmation of attendance and the delegate details by tomorrow evening, or your place is released.",
                "action": "Confirm attendance and send delegate details", "due": (self._now() + timedelta(days=1)).date().isoformat(), "priority": "high",
                "replies": [{"label": "Confirm attendance", "intent": "Confirm that we will attend and say the delegate details will follow today."}, {"label": "Ask for more time", "intent": "Politely ask for two more days to send the delegate details."}, {"label": "Decline politely", "intent": "Thank them and say we are unable to attend this time."}],
            },
            sample=True,
        )

    # --- the buttons -------------------------------------------------------------------------------------------

    async def _mine(self, who: Who, alert_id: str) -> Alert:
        a = await self._alerts.get(alert_id)
        if a is None or a.user_id != who.user_id:
            raise NotFound("That alert isn't there any more.")
        return a

    async def on_callback(self, who: Who, token: str, channel_id: str, chat: int | str, message_id: int, callback_id: str, data: str) -> None:
        """A button under an alert or a draft was pressed."""
        parsed = parse_callback(data)
        if parsed is None:
            await self._phone.ack(channel_id, callback_id)
            return
        await self._phone.ack(channel_id, callback_id)
        alert_id, code = parsed
        try:
            a = await self._mine(who, alert_id)
            if a.status != "open":
                await self._phone.say(channel_id, chat, "This one is already dealt with.")
            elif len(code) == 2 and code[0] == "r":
                n = int(code[1])
                if n >= len(a.replies):
                    return
                await self._draft(who, token, a, channel_id, chat, intent=a.replies[n]["intent"])
            elif code == "own":
                a.awaiting = {"chat": chat, "at": self._now().isoformat()}
                await self._alerts.save(a)
                await self._phone.say(channel_id, chat, "✍️ <b>Tell me what to say.</b> Just type it here, as roughly as you like, and I'll write it up properly. Send <i>cancel</i> to drop it.")
            elif code == "redo":
                d = a.draft or {}
                await self._draft(who, token, a, channel_id, chat, intent=d.get("intent") or "", instruction=d.get("instruction"))
            elif code == "send":
                await self._send(who, token, a, channel_id, chat)
            elif code == "x":
                a.draft = None
                await self._alerts.save(a)
                await self._phone.say(channel_id, chat, "Draft dropped. The email is still open: pick another reply, or open it.")
            elif code == "done":
                a.status, a.draft, a.awaiting = "handled", None, None
                await self._alerts.save(a)
                await self._strip(a)
                await self._phone.say(channel_id, chat, "👌 Marked as handled.")
        except (ValidationFailed, AiError, NotFound) as exc:
            await self._phone.say(channel_id, chat, f"⚠️ {getattr(exc, 'message', None) or exc}")

    async def take_text(self, who: Who, token: str, channel_id: str, chat: int | str, text: str) -> bool:
        """What the person typed after pressing "write my own". True when it was meant for that, so nothing else answers it."""
        a = await self._alerts.waiting_for(who.user_id, chat)
        if a is None or not a.awaiting:
            return False
        try:
            fresh = datetime.fromisoformat(a.awaiting["at"]) > self._now() - timedelta(minutes=AWAIT_MINUTES)
        except (KeyError, ValueError):
            fresh = False
        a.awaiting = None
        await self._alerts.save(a)
        if not fresh:
            return False
        if text.strip().lower() in ("cancel", "no", "stop"):
            await self._phone.say(channel_id, chat, "Okay, dropped. The email is still open.")
            return True
        try:
            await self._draft(who, token, a, channel_id, chat, intent="", instruction=text)
        except (ValidationFailed, AiError) as exc:
            await self._phone.say(channel_id, chat, f"⚠️ {getattr(exc, 'message', None) or exc}")
        return True

    # --- writing and sending -------------------------------------------------------------------------------------

    async def _email_text(self, token: str, a: Alert) -> str:
        if a.sample or not a.account_id:
            return f"From: {a.sender_name} <{a.sender_address}>\nSubject: {a.subject}\n\n{a.summary}"
        try:
            full = await self._data(token).mail_message(a.account_id, a.folder, a.uid)
            sender = full.get("sender") or {}
            return f"From: {sender.get('name', '')} <{sender.get('address', '')}>\nSubject: {full.get('subject', '')}\n\n{str(full.get('text') or '')[:4000]}"
        except (AiError, ValueError):
            return f"From: {a.sender_name} <{a.sender_address}>\nSubject: {a.subject}\n\n{a.summary}"

    async def _draft(self, who: Who, token: str, a: Alert, channel_id: str, chat: int | str, *, intent: str, instruction: str | None = None) -> None:
        """Has a model write the reply in the theme, then shows it with Send, Redo and Cancel."""
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        sel, _ = selection_of(s, a.workflow_id or "")
        if not sel or not sel.get("modelId"):
            raise ValidationFailed("Choose a provider and a model in the agent's Settings: it can't write a reply without one.")
        theme = _theme(s, a)
        system, user = reply_prompt(who.name, theme, await self._email_text(token, a), intent, instruction)
        reply = await self._llm(sel["providerId"], s.keys[sel["providerId"]]).chat(model=sel["modelId"], messages=[Message("system", system), Message("user", user)], tools=[], thinking=sel.get("thinking", "off"))
        subject, body = parse_draft(reply.text)
        if not body:
            raise ValidationFailed("The model didn't write a reply. Try again.")
        a.draft = {"to": a.sender_address, "subject": subject or reply_subject(a.subject), "body": body, "intent": intent, "instruction": instruction}
        await self._alerts.save(a)
        await self._phone.say(channel_id, chat, draft_text(_view(a), a.draft, theme["name"] if theme else ""), draft_buttons(a.id))

    async def _send(self, who: Who, token: str, a: Alert, channel_id: str, chat: int | str) -> None:
        d = a.draft
        if not d:
            await self._phone.say(channel_id, chat, "There is no draft to send. Pick a reply first.")
            return
        if a.sample:
            await self._phone.say(channel_id, chat, "🧪 This was a sample alert, so nothing was sent. A real one would go out from your mailbox now.")
            return
        s = await self._settings.get(who.user_id)
        text, html = render_email(_theme(s, a), d["body"], first_name(a.sender_name))
        message = {"to": [d["to"]], "subject": d["subject"], "text": text, "html": html, "inReplyTo": a.message_id, "references": a.message_id}
        await self._data(token).mail_send(a.account_id, message)
        a.status, a.draft, a.awaiting = "replied", None, None
        await self._alerts.save(a)
        await self._strip(a)
        await self._phone.say(channel_id, chat, f"✅ <b>Sent</b> to {escape(d['to'])}.")

    async def _strip(self, a: Alert) -> None:
        """Takes the buttons off the alert once it is dealt with, so it can't be answered twice."""
        for ref in a.chats:
            try:
                await self._phone.strip(ref["channel"], ref["chat"], ref["message"])
            except Exception:  # noqa: BLE001  a message that is gone or old is not a reason to fail
                log.info("could not take the buttons off an alert message")
