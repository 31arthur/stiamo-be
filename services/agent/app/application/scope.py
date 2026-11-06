"""A workspace's view of the person's data: what that workspace's assistant is allowed to see.

The assistant of a workspace answers about that workspace only, so what is pending, what is on today and what it proposes to add are all
that workspace's. Nothing here changes any data: it only leaves out what belongs to the others.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.application.ports import DataGateway
from app.domain.llm import AiError

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
