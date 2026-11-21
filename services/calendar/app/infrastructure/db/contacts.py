from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import ContactRepository
from app.domain.contacts import Contact, ContactAccount
from app.infrastructure.db.models import ContactAccountModel, ContactModel
from app.infrastructure.security import SecretBox


class SqlContactRepository(ContactRepository):
    def __init__(self, session: AsyncSession, box: SecretBox) -> None:
        self.s, self.box = session, box

    def _account(self, r: ContactAccountModel) -> ContactAccount:
        return ContactAccount(r.id, r.user_id, r.workspace, r.email, self.box.open(r.refresh_sealed), r.status, r.error, r.created_at, r.last_sync_at, r.count)

    async def accounts(self, user_id: str) -> list[ContactAccount]:
        rows = await self.s.scalars(select(ContactAccountModel).where(ContactAccountModel.user_id == user_id).order_by(ContactAccountModel.created_at))
        return [self._account(r) for r in rows]

    async def all_accounts(self) -> list[ContactAccount]:
        return [self._account(r) for r in await self.s.scalars(select(ContactAccountModel))]

    async def account(self, user_id: str, account_id: str) -> ContactAccount | None:
        r = await self.s.get(ContactAccountModel, account_id)
        return self._account(r) if r and r.user_id == user_id else None

    async def save_account(self, a: ContactAccount) -> None:
        r = await self.s.get(ContactAccountModel, a.id) or ContactAccountModel(id=a.id, user_id=a.user_id, created_at=a.created_at)
        r.workspace, r.email, r.refresh_sealed, r.status, r.error, r.count, r.last_sync_at = a.workspace, a.email, self.box.seal(a.refresh_token), a.status, a.error[:300], a.count, a.last_sync_at
        self.s.add(r)
        await self.s.flush()

    async def delete_account(self, user_id: str, account_id: str) -> None:
        await self.s.execute(delete(ContactModel).where(ContactModel.account_id == account_id, ContactModel.user_id == user_id))
        await self.s.execute(delete(ContactAccountModel).where(ContactAccountModel.id == account_id, ContactAccountModel.user_id == user_id))

    @staticmethod
    def _contact(r: ContactModel) -> Contact:
        return Contact(r.id, r.user_id, r.account_id, r.workspace, r.google_id, r.name, r.organisation, r.title, list(r.emails or []), list(r.phones or []), r.notes, r.photo, r.search,
                       r.first_seen_at, bool(r.baseline), r.google_updated or "", list(r.tags or []), dict(r.profile or {}), r.timezone or "", bool(r.rapport_excluded))

    async def replace_contacts(self, account: ContactAccount, contacts: list[Contact]) -> int:
        """Makes the contacts kept for this link match what Google has now. What Google says is replaced; what the owner added (tags, profile, zone, "no rapport plan") and
        when a contact first appeared are kept. The very first read of a link is the baseline: those contacts are not "new"."""
        first_read = account.last_sync_at is None
        now = datetime.now(UTC)
        old = {r.id: r for r in await self.s.scalars(select(ContactModel).where(ContactModel.account_id == account.id))}
        keep = {c.id for c in contacts}
        for gone in [r for i, r in old.items() if i not in keep]:
            await self.s.delete(gone)
        for c in contacts:
            r = old.get(c.id)
            fields = dict(workspace=c.workspace, google_id=c.google_id, name=c.name, organisation=c.organisation, title=c.title, emails=c.emails, phones=c.phones, notes=c.notes, photo=c.photo, search=c.search, google_updated=c.google_updated)
            if r is None:
                self.s.add(ContactModel(id=c.id, user_id=c.user_id, account_id=c.account_id, first_seen_at=now, baseline=first_read, tags=[], profile={}, timezone="", rapport_excluded=False, **fields))
            else:
                for k, v in fields.items():
                    setattr(r, k, v)
        await self.s.flush()
        return len(contacts)

    async def update_local(self, user_id: str, contact_id: str, fields: dict) -> list[Contact]:
        """Changes what the owner knows about a contact. It applies to the same Google contact in every workspace it is in."""
        row = await self.s.get(ContactModel, contact_id)
        if row is None or row.user_id != user_id:
            return []
        siblings = list(await self.s.scalars(select(ContactModel).where(ContactModel.user_id == user_id, ContactModel.google_id == row.google_id)))
        for r in siblings:
            for k, v in fields.items():
                setattr(r, k, v)
        await self.s.flush()
        return [self._contact(r) for r in siblings]

    async def contacts(self, user_id: str, workspace: str | None, query: str | None, limit: int) -> list[Contact]:
        q = select(ContactModel).where(ContactModel.user_id == user_id)
        if workspace:
            q = q.where(ContactModel.workspace == workspace)
        if query:
            q = q.where(ContactModel.search.like(f"%{query.replace(chr(37), '').replace(chr(95), '')}%"))
        rows = await self.s.scalars(q.order_by(func.lower(ContactModel.name)).limit(limit))
        return [self._contact(r) for r in rows]
