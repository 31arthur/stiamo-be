from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import CalendarRepository
from app.domain.calendar import Account, Event, Link, Prefs, Source
from app.infrastructure.db.models import AccountModel, EventModel, LinkModel, PrefsModel, SourceModel
from app.infrastructure.security import SecretBox


class SqlCalendarRepository(CalendarRepository):
    def __init__(self, session: AsyncSession, box: SecretBox) -> None:
        self.s, self.box = session, box

    # --- accounts -----------------------------------------------------------------------------------------

    def _account(self, r: AccountModel) -> Account:
        return Account(r.id, r.user_id, r.workspace, r.email, self.box.open(r.refresh_sealed), r.status, r.error, r.created_at, r.last_sync_at)

    async def accounts(self, user_id: str) -> list[Account]:
        rows = await self.s.scalars(select(AccountModel).where(AccountModel.user_id == user_id).order_by(AccountModel.created_at))
        return [self._account(r) for r in rows]

    async def all_accounts(self) -> list[Account]:
        return [self._account(r) for r in await self.s.scalars(select(AccountModel))]

    async def account(self, user_id: str, account_id: str) -> Account | None:
        r = await self.s.get(AccountModel, account_id)
        return self._account(r) if r and r.user_id == user_id else None

    async def save_account(self, a: Account) -> None:
        r = await self.s.get(AccountModel, a.id) or AccountModel(id=a.id, user_id=a.user_id, created_at=a.created_at)
        r.workspace, r.email, r.refresh_sealed, r.status, r.error, r.last_sync_at = a.workspace, a.email, self.box.seal(a.refresh_token), a.status, a.error[:300], a.last_sync_at
        self.s.add(r)
        await self.s.flush()

    async def delete_account(self, user_id: str, account_id: str) -> None:
        ids = [s.id for s in await self.sources(user_id) if s.account_id == account_id]
        for sid in ids:
            await self.delete_source(user_id, sid)
        await self.s.execute(delete(AccountModel).where(AccountModel.id == account_id, AccountModel.user_id == user_id))

    # --- calendars --------------------------------------------------------------------------------------------

    def _source(self, r: SourceModel) -> Source:
        return Source(r.id, r.account_id, r.user_id, r.google_id, r.name, r.color, r.role, r.timezone, r.enabled, r.export, r.sync_token, r.synced_through)

    async def sources(self, user_id: str) -> list[Source]:
        rows = await self.s.scalars(select(SourceModel).where(SourceModel.user_id == user_id).order_by(SourceModel.name))
        return [self._source(r) for r in rows]

    async def save_source(self, x: Source) -> None:
        r = await self.s.get(SourceModel, x.id) or SourceModel(id=x.id, account_id=x.account_id, user_id=x.user_id, google_id=x.google_id)
        r.name, r.color, r.role, r.timezone, r.enabled, r.export, r.sync_token, r.synced_through = x.name, x.color, x.role, x.timezone, x.enabled, x.export, x.sync_token, x.synced_through
        self.s.add(r)
        await self.s.flush()

    async def delete_source(self, user_id: str, source_id: str) -> None:
        await self.s.execute(delete(EventModel).where(EventModel.source_id == source_id))
        await self.s.execute(delete(LinkModel).where(LinkModel.user_id == user_id, LinkModel.source_id == source_id))
        await self.s.execute(delete(SourceModel).where(SourceModel.id == source_id, SourceModel.user_id == user_id))

    # --- events -----------------------------------------------------------------------------------------------

    @staticmethod
    def _event(r: EventModel) -> Event:
        return Event(r.id, r.user_id, r.source_id, r.google_id, r.title, r.description, r.location, r.all_day, r.start, r.end, r.link, r.meet, r.attendees, r.updated)

    async def events_between(self, user_id: str, source_ids: list[str], start: datetime, end: datetime) -> list[Event]:
        if not source_ids:
            return []
        q = select(EventModel).where(EventModel.user_id == user_id, EventModel.source_id.in_(source_ids), EventModel.end > start, EventModel.start < end).order_by(EventModel.start)
        return [self._event(r) for r in await self.s.scalars(q)]

    async def upsert_events(self, events: list[Event]) -> None:
        for e in events:
            r = await self.s.get(EventModel, e.id) or EventModel(id=e.id, user_id=e.user_id, source_id=e.source_id, google_id=e.google_id)
            r.title, r.description, r.location, r.all_day, r.start, r.end, r.link, r.meet, r.attendees, r.updated = e.title, e.description, e.location, e.all_day, e.start, e.end, e.link, e.meet, e.attendees, e.updated
            self.s.add(r)
        await self.s.flush()

    async def remove_events(self, source_id: str, google_ids: list[str]) -> None:
        if google_ids:
            await self.s.execute(delete(EventModel).where(EventModel.source_id == source_id, EventModel.google_id.in_(google_ids)))

    async def clear_events(self, source_id: str) -> None:
        await self.s.execute(delete(EventModel).where(EventModel.source_id == source_id))

    # --- what was sent to Google -------------------------------------------------------------------------------

    async def links(self, user_id: str) -> dict[str, Link]:
        rows = await self.s.scalars(select(LinkModel).where(LinkModel.user_id == user_id))
        return {r.ref: Link(r.user_id, r.ref, r.source_id, r.google_id, r.digest) for r in rows}

    async def save_link(self, link: Link) -> None:
        r = await self.s.get(LinkModel, (link.user_id, link.ref)) or LinkModel(user_id=link.user_id, ref=link.ref)
        r.source_id, r.google_id, r.digest = link.source_id, link.google_id, link.digest
        self.s.add(r)
        await self.s.flush()

    async def delete_link(self, user_id: str, ref: str) -> None:
        await self.s.execute(delete(LinkModel).where(LinkModel.user_id == user_id, LinkModel.ref == ref))

    async def prefs(self, user_id: str) -> Prefs | None:
        r = await self.s.get(PrefsModel, user_id)
        return Prefs(r.user_id, r.timezone) if r else None

    async def save_prefs(self, p: Prefs) -> None:
        r = await self.s.get(PrefsModel, p.user_id) or PrefsModel(user_id=p.user_id)
        r.timezone = p.timezone
        self.s.add(r)
        await self.s.flush()
