"""A workspace's view of the person's data: what that workspace's assistant is allowed to see.

The assistant of a workspace answers about that workspace only, so what is pending, what is on today and what it proposes to add are all
that workspace's. Nothing here changes any data: it only leaves out what belongs to the others.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.application.ports import DataGateway
from app.domain.dayplan import DEFAULT_HEARING, DEFAULT_MEETING
from app.domain.llm import AiError
from app.domain.mailwatch import NAMES

#: Where a collection that doesn't say its workspace belongs. (Cases and hearings are the S&B practice's; clients and projects are FSA's.)
HOME: dict[str, str] = {"cases": "sb", "counsel": "sb", "clients": "fsa", "projects": "fsa"}


class ScopedData(DataGateway):
    def __init__(self, inner: DataGateway, workspace: str) -> None:
        self._inner, self.workspace = inner, workspace
        self._boxes: set[str] | None = None

    # --- what belongs to a workspace -------------------------------------------------------------------------------

    async def cases(self):
        return await self._inner.cases() if self.workspace == "sb" else []

    async def hearings(self):
        return await self._inner.hearings() if self.workspace == "sb" else []

    async def attachments(self):
        return await self._inner.attachments() if self.workspace == "sb" else []

    async def records(self, table: str):
        rows = await self._inner.records(table)
        home = HOME.get(table)
        if home is not None:
            return rows if home == self.workspace else []
        return [r for r in rows if r.get("workspace", self.workspace) == self.workspace]

    async def calendar_events(self, start: str, end: str):
        return [e for e in await self._inner.calendar_events(start, end) if e.get("workspace", self.workspace) == self.workspace]

    # --- mail: only this workspace's mailboxes ---------------------------------------------------------------------------

    async def mail_accounts(self):
        return [a for a in await self._inner.mail_accounts() if a.get("workspace") == self.workspace]

    async def _mine(self, account_id: str) -> None:
        if self._boxes is None:
            self._boxes = {str(a.get("id")) for a in await self.mail_accounts()}
        if account_id not in self._boxes:
            raise AiError("That mailbox isn't one of this workspace's.")

    async def mail_uids(self, account_id: str, folder: str, since: date | None, before: date | None):
        await self._mine(account_id)
        return await self._inner.mail_uids(account_id, folder, since, before)

    async def mail_summaries(self, account_id: str, folder: str, uids: list[int]):
        await self._mine(account_id)
        return await self._inner.mail_summaries(account_id, folder, uids)

    async def mail_message(self, account_id: str, folder: str, uid: int):
        await self._mine(account_id)
        return await self._inner.mail_message(account_id, folder, uid)

    async def mail_correspondents(self, account_id: str):
        await self._mine(account_id)
        return await self._inner.mail_correspondents(account_id)

    async def mail_trash(self, account_id: str, folder: str, uids: list[int]):
        await self._mine(account_id)
        return await self._inner.mail_trash(account_id, folder, uids)

    async def mail_test(self, account_id: str):
        await self._mine(account_id)
        return await self._inner.mail_test(account_id)

    async def mail_send(self, account_id: str, message: dict[str, Any]):
        await self._mine(account_id)
        return await self._inner.mail_send(account_id, message)

    # --- the rest is not per workspace -------------------------------------------------------------------------------------

    async def feed_items(self, feeds, since_days, known_ids, limit):
        return await self._inner.feed_items(feeds, since_days, known_ids, limit)

    async def create_meeting(self, record):
        return await self._inner.create_meeting(record)

    async def drive_available(self):
        return await self._inner.drive_available()

    async def drive_search(self, query):
        return await self._inner.drive_search(query)

    async def drive_suggest(self, terms):
        return await self._inner.drive_suggest(terms)

    async def save_record(self, table, record):
        return await self._inner.save_record(table, record)

    async def google_links(self):
        return await self._inner.google_links()

    async def calendar_sync(self):
        return await self._inner.calendar_sync()


class PlannerData(ScopedData):
    """What one workspace's day plan is made from: that workspace's own tasks and calls to choose from, with the rest of the person's day
    (the other workspaces' hearings, meetings, timed work and every calendar event) shown as time already taken. One person has one day, so
    FSA work is never planned over an S&B hearing, though each workspace plans only its own."""

    async def calendar_events(self, start: str, end: str):
        events = list(await self._inner.calendar_events(start, end))
        return events + await self._elsewhere(start, end)

    async def _elsewhere(self, start: str, end: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        def add(kind: str, record: dict[str, Any], workspace: str, day: str | None, at: int | None, length: int, title: str) -> None:
            if day and start <= day <= end and at is not None:
                out.append({"id": f"elsewhere:{kind}:{record.get('id')}", "date": day, "startMinute": int(at), "endMinute": int(at) + length, "allDay": False, "title": f"{title or kind} ({NAMES.get(workspace, workspace)})", "workspace": workspace, "calendar": "Another workspace"})

        def other(record: dict[str, Any]) -> str | None:
            ws = record.get("workspace")
            return ws if ws and ws != self.workspace and (record.get("outcome") or {}).get("status", "open") == "open" else None

        if self.workspace != "sb":
            for h in await self._inner.hearings():
                if (h.get("outcome") or {}).get("status", "open") == "open":
                    add("hearing", h, "sb", h.get("date"), h.get("time"), int(h.get("duration") or DEFAULT_HEARING), h.get("purpose") or "Hearing")
        for m in await self._inner.records("meetings"):
            if ws := other(m):
                slot = m.get("slot") or {}
                add("meeting", m, ws, slot.get("date"), slot.get("start"), int(slot.get("duration") or DEFAULT_MEETING), m.get("title") or "")
        for t in await self._inner.records("tasks"):
            if ws := other(t):
                slot = t.get("slot") or {}
                add("task", t, ws, slot.get("date"), slot.get("start"), int(slot.get("duration") or t.get("estimate") or 30), t.get("title") or "")
        for f in await self._inner.records("follow-ups"):
            if ws := other(f):
                due = f.get("due") or {}
                add("followUp", f, ws, due.get("date"), due.get("time"), int(f.get("duration") or 15), f.get("title") or "")
        return out
