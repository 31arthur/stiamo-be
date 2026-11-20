"""The mail pack: the owner's mailboxes, as far as the workspace the bot is for.

Chittu can say how much mail there is, read one message, put the inbox in front of the owner one message at a time (with Keep, Trash and Read buttons on Telegram),
and move a message to Trash in the real mailbox (never deleted: it can be put back from the Trash folder). Mail that an agent judged to need the owner is also an
action item: those are shown with walk_through(kinds=[mail]).
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.cards import card
from app.application.toolpacks.common import limit, text
from app.domain.llm import AiError, ToolSpec

OBJ = "object"
S = {"type": "string"}


def _row(account: dict[str, Any], m: dict[str, Any]) -> dict[str, Any]:
    sender = m.get("sender") or {}
    return {"account": account["id"], "mailbox": account.get("address") or account.get("label"), "workspace": account.get("workspace"), "uid": m.get("uid"), "from": sender.get("name") or sender.get("address"), "fromAddress": sender.get("address"),
            "subject": m.get("subject"), "date": m.get("date"), "snippet": str(m.get("snippet") or "")[:160], "seen": bool(m.get("seen")), "attachments": bool(m.get("hasAttachments"))}


class MailPack(ToolPack):
    name = "mail"

    async def tools(self, ctx: PackContext) -> list[Tool]:
        data = ctx.data

        async def recent(days: int, most: int, unseen_only: bool, folder: str = "INBOX") -> list[dict[str, Any]]:
            since = date.fromisoformat(ctx.today) - timedelta(days=days)
            rows: list[dict[str, Any]] = []
            for acc in await data.mail_accounts():
                got = await data.mail_uids(str(acc["id"]), folder, since, None)
                uids = list(got.get("uids", []))[: max(most, 1) * (3 if unseen_only else 1)]
                for m in await data.mail_summaries(str(acc["id"]), folder, uids) if uids else []:
                    if not unseen_only or not m.get("seen"):
                        rows.append(_row(acc, m))
            rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
            return rows[:most]

        async def recent_mail(a: dict[str, Any]) -> ToolOutput:
            try:
                rows = await recent(limit(a.get("days"), 7, 60), limit(a.get("max"), 10, 30), bool(a.get("unread_only")))
                accounts = await data.mail_accounts()
            except AiError as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            if not accounts:
                return ToolOutput({"error": "No mailbox is connected for this workspace."})
            return ToolOutput({"count": len(rows), "unread": sum(1 for r in rows if not r["seen"]), "mailboxes": [str(x.get("address") or x.get("label")) for x in accounts], "messages": rows})

        async def read_mail(a: dict[str, Any]) -> ToolOutput:
            try:
                m = await data.mail_message(text(a.get("account")), text(a.get("folder")) or "INBOX", int(a.get("uid") or 0))
            except (AiError, ValueError, TypeError) as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            sender = m.get("sender") or {}
            return ToolOutput({"from": f"{sender.get('name', '')} <{sender.get('address', '')}>".strip(), "to": [p.get("address") for p in m.get("to") or []], "subject": m.get("subject"), "date": m.get("date"),
                               "text": str(m.get("text") or "")[:3000], "attachments": [x.get("filename") for x in m.get("attachments") or []]})

        async def trash_mail(a: dict[str, Any]) -> ToolOutput:
            uids = [int(u) for u in (a.get("uids") or [a.get("uid")]) if isinstance(u, (int, float, str)) and str(u).isdigit()][:20]
            if not uids:
                return ToolOutput({"error": "Give the uid of the message (from recent_mail)."})
            try:
                moved = await data.mail_trash(text(a.get("account")), text(a.get("folder")) or "INBOX", uids)
            except AiError as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            return ToolOutput({"moved": moved, "note": "Moved to the Trash folder of the real mailbox. It is not deleted: it can be put back from there."})

        async def walk_mail(a: dict[str, Any]) -> ToolOutput:
            try:
                rows = await recent(limit(a.get("days"), 7, 60), limit(a.get("max"), 10, 30), bool(a.get("unread_only")))
            except AiError as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            if not rows:
                return ToolOutput({"started": False, "count": 0, "next": "There is no mail in that range. Say so in one line."})
            out = await ctx.queue.review_mail(ctx.who, ctx.token, rows, channel_id=ctx.channel_id, title=text(a.get("label"))[:40] or ("Unread mail" if a.get("unread_only") else "Your inbox"))
            return ToolOutput({**out, "next": "The first message has ALREADY been sent to the owner's phone with buttons (Keep, Trash, Read, Next). The app walks the rest by itself. Reply with one short line only; do not list the messages."})

        tools = [
            Tool(
                ToolSpec("recent_mail", card(
                    "Looks at the latest mail in this workspace's mailboxes (the inbox): who it is from, the subject, the date, the start of the text, whether it was read.",
                    use=["The owner asks how much mail there is, whether anything came from someone, what is new or unread, or whether a particular mail arrived: “what are my current emails?”, “anything from the court?”, “how many unread?”."],
                    avoid=["On Telegram never read the messages out as a list: for “show me my emails” use walk_mail, which brings them one at a time. A count or a yes/no is one sentence.", "For mail an agent judged to need the owner: those are action items; use walk_through(kinds=[mail])."],
                    args={"days": "How far back (default 7, at most 60).", "max": "How many (default 10, at most 30).", "unread_only": "Only mail not yet read."},
                    returns="{count, unread, mailboxes, messages: [{account, uid, from, fromAddress, subject, date, snippet, seen, attachments}]} newest first. Each message's account and uid are what read_mail and trash_mail take.",
                    fails=["{error} when no mailbox is connected for this workspace or the mail service is away: say so plainly."],
                    effect="Read only. Changes nothing (it does not mark mail as read).",
                ), {"type": OBJ, "properties": {"days": {"type": "integer"}, "max": {"type": "integer"}, "unread_only": {"type": "boolean"}}}),
                lambda a: "Looked at the inbox",
                recent_mail,
            ),
            Tool(
                ToolSpec("read_mail", card(
                    "Opens one message in full: who, to whom, when, the text (up to 3,000 characters) and the names of attachments.",
                    use=["The owner asks what a mail says, or you need the text to answer a question about it. Take account and uid from recent_mail."],
                    avoid=["Do not open mail one after another to summarise an inbox: ask the owner which one, or use walk_mail."],
                    args={"account": "The mailbox id from recent_mail.", "uid": "The message number from recent_mail.", "folder": "Default INBOX."},
                    returns="{from, to, subject, date, text, attachments}.",
                    fails=["{error} when the message is no longer there: it was moved or deleted; say so."],
                    effect="Read only. It does not mark the mail as read.",
                ), {"type": OBJ, "properties": {"account": S, "uid": {"type": "integer"}, "folder": S}, "required": ["account", "uid"]}),
                lambda a: "Opened a message",
                read_mail,
            ),
            Tool(
                ToolSpec("trash_mail", card(
                    "Moves one or more messages to the Trash folder of the real mailbox. They are never deleted: they can be put back from Trash.",
                    use=["The owner says to trash, bin or get rid of a particular mail or mails: “trash the one from the shop”, “bin that newsletter”. Take account and uid from recent_mail."],
                    avoid=["Never trash on your own judgement, and never a whole inbox. Not when the owner only asked about the mail.", "If two messages could be meant, ask which."],
                    args={"account": "The mailbox id from recent_mail.", "uid": "One message number.", "uids": "Several message numbers of the same mailbox (at most 20).", "folder": "Default INBOX."},
                    returns="{moved, note}.",
                    fails=["{error} when the mailbox has no Trash folder or the server refused: say so; nothing was moved."],
                    effect="Changes the real mailbox: the message(s) leave the inbox and are in Trash.",
                ), {"type": OBJ, "properties": {"account": S, "uid": {"type": "integer"}, "uids": {"type": "array", "items": {"type": "integer"}}, "folder": S}, "required": ["account"]}),
                lambda a: "Moved mail to Trash",
                trash_mail,
            ),
        ]
        if ctx.queue is not None and ctx.channel == "Telegram" and ctx.who is not None:
            tools.append(Tool(
                ToolSpec("walk_mail", card(
                    "Shows the latest mail one message at a time on Telegram, numbered (1/10): who it is from, the subject and the start of the text, with buttons Keep, Trash, Read and Next. Trash moves it to the Trash folder of the real mailbox.",
                    use=["On Telegram, the owner asks to see their emails, their inbox, their new or unread mail: “what are my current emails?”, “show me my unread mail”."],
                    avoid=["Never list messages in a message yourself: that is always this tool.", "For a count or a yes/no: answer in one sentence from recent_mail.", "For mail an agent judged to need the owner: walk_through(kinds=[mail])."],
                    args={"days": "How far back (default 7).", "max": "How many (default 10, at most 30).", "unread_only": "Only mail not yet read.", "label": "A few words for the opening line."},
                    returns="{started, count, next}. started=true means the first message has already been sent; reply with one short line only.",
                    fails=["{error} when no mailbox is connected: say so plainly."],
                    effect="Sends the opening line and the first message to the owner's Telegram. Changes no mail until the owner presses Trash.",
                ), {"type": OBJ, "properties": {"days": {"type": "integer"}, "max": {"type": "integer"}, "unread_only": {"type": "boolean"}, "label": S}}),
                lambda a: "Started the inbox walk",
                walk_mail,
            ))
        return tools
