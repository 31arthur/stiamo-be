"""A workspace's view of the person's data: what that workspace's assistant is allowed to see.

The assistant of a workspace answers about that workspace only, so what is pending, what is on today and what it proposes to add are all
that workspace's. The rule is deny by default: a record counts only if it says it belongs to the workspace, so something unlabelled is
visible to no workspace's assistant rather than to all of them. Nothing here changes any data: it only leaves out what belongs to the others.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.application.ports import DataGateway
from app.domain.dayplan import DEFAULT_HEARING, DEFAULT_MEETING
from app.domain.llm import AiError

#: Where a collection that has no workspace field belongs: cases, hearings and their action plans are the S&B practice's; clients and projects are FSA's.
HOME: dict[str, str] = {"cases": "sb", "hearings": "sb", "plans": "sb", "counsel": "sb", "clients": "fsa", "projects": "fsa"}


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
        if table == "characters":
            return await self._characters()
        rows = await self._inner.records(table)
        home = HOME.get(table)
        if home is not None:
            return rows if home == self.workspace else []
        return [r for r in rows if r.get("workspace") == self.workspace]

    async def calendar_events(self, start: str, end: str):
        return [e for e in await self._inner.calendar_events(start, end) if e.get("workspace") == self.workspace]

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

    # The Drive holds the practice's case documents, so it is S&B's alone.
    async def drive_available(self):
        return self.workspace == "sb" and await self._inner.drive_available()

    async def drive_search(self, query):
        return await self._inner.drive_search(query) if self.workspace == "sb" else []

    async def drive_suggest(self, terms):
        return await self._inner.drive_suggest(terms) if self.workspace == "sb" else []

    # A story belongs to its workspace, and its chapters go with it.
    async def _own_story(self, story_id: str) -> bool:
        return any(s.get("id") == story_id for s in await self.records("stories"))

    # The cast: who someone is travels with them into every workspace they have a part in (or were first met in). Their parts and
    # scenes carry their story's workspace, so the rule above keeps another workspace's parts and scenes out.
    async def _characters(self):
        here = {p.get("characterId") for p in await self._inner.records("parts") if p.get("workspace") == self.workspace}
        return [c for c in await self._inner.records("characters") if c.get("home") == self.workspace or c.get("id") in here]

    async def join_cast(self, body):
        story = body.get("storyId")
        if story and not await self._own_story(story):
            raise AiError(f"That story isn't one of {self.workspace}'s, so its cast can't be changed from here.")
        if body.get("characterId") and not any(c.get("id") == body["characterId"] for c in await self._characters()):
            raise AiError(f"That person isn't in {self.workspace}'s cast.")
        return await self._inner.join_cast({**body, "home": self.workspace})

    async def chapters(self, story_id):
        return await self._inner.chapters(story_id) if await self._own_story(story_id) else []

    async def write_chapter(self, story_id, body):
        if not await self._own_story(story_id):
            raise AiError(f"That story isn't one of {self.workspace}'s, so it can't be written from here.")
        return await self._inner.write_chapter(story_id, body)

    async def save_record(self, table, record):
        if HOME.get(table, self.workspace) != self.workspace or record.get("workspace", self.workspace) != self.workspace:
            raise AiError(f"That belongs to another workspace, so it can't be changed from {self.workspace}.")
        return await self._inner.save_record(table, record)

    async def google_links(self):
        return await self._inner.google_links()

    async def contacts(self, query: str = "", limit: int = 50):
        """Only the people of this workspace's contact accounts."""
        return [c for c in await self._inner.contacts(query, 200) if self.workspace in (c.get("workspaces") or [])][:limit]

    async def calendar_sync(self):
        return await self._inner.calendar_sync()


class PlannerData(ScopedData):
    """What one workspace's day plan is made from: that workspace's own tasks and calls to choose from, and the rest of the person's day as
    time already taken. One person has one day, so FSA work is never planned over an S&B hearing, though each workspace plans only its own.

    The rest of the day is shown as bare **busy time**: when, never what. No title, place, link, person or workspace of another workspace's
    item reaches this workspace's assistant or its model, so nothing about it can be shown on its bot or leak into its plans."""

    BUSY = "Busy (another workspace)"
    BUSY_CALLS = "Busy (another workspace, calls are fine)"

    async def calendar_events(self, start: str, end: str):
        return await super().calendar_events(start, end) + await self._busy_elsewhere(start, end)

    async def _busy_elsewhere(self, start: str, end: str) -> list[dict[str, Any]]:
        spans: set[tuple[str, int, int, str]] = set()

        def take(day: str | None, at: Any, length: int, mode: str | None = None) -> None:
            # What the person said about their time inside it carries over: free for anything means it takes no time from anyone's plan.
            if mode == "free" or not day or not (start <= day <= end) or not isinstance(at, (int, float)) or isinstance(at, bool):
                return
            spans.add((day, int(at), int(at) + length, "calls" if mode == "calls" else ""))

        for e in await self._inner.calendar_events(start, end):
            if e.get("workspace") != self.workspace and not e.get("allDay") and e.get("startMinute") is not None:
                take(e.get("date"), e["startMinute"], max(5, int(e.get("endMinute") or e["startMinute"] + DEFAULT_MEETING) - int(e["startMinute"])), e.get("mode"))

        def elsewhere(r: dict[str, Any]) -> bool:
            return r.get("workspace") != self.workspace and (r.get("outcome") or {}).get("status", "open") == "open"

        if self.workspace != "sb":  # hearings are S&B's
            for h in await self._inner.hearings():
                if (h.get("outcome") or {}).get("status", "open") == "open":
                    take(h.get("date"), h.get("time"), int(h.get("duration") or DEFAULT_HEARING))
        for m in await self._inner.records("meetings"):
            if elsewhere(m):
                slot = m.get("slot") or {}
                take(slot.get("date"), slot.get("start"), int(slot.get("duration") or DEFAULT_MEETING), m.get("mode"))
        for t in await self._inner.records("tasks"):
            if elsewhere(t):
                slot = t.get("slot") or {}
                take(slot.get("date"), slot.get("start"), int(slot.get("duration") or t.get("estimate") or 30))
        for f in await self._inner.records("follow-ups"):
            if elsewhere(f):
                due = f.get("due") or {}
                take(due.get("date"), due.get("time"), int(f.get("duration") or 15))
        return [
            {"id": f"busy:{i}", "date": day, "startMinute": a, "endMinute": b, "allDay": False, "title": self.BUSY_CALLS if mode else self.BUSY, "calendar": "Busy", **({"mode": mode} if mode else {})}
            for i, (day, a, b, mode) in enumerate(sorted(spans))
        ]
