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

    async def replace_contacts(self, account: ContactAccount, contacts: list[Contact]) -> int:
        await self.s.execute(delete(ContactModel).where(ContactModel.account_id == account.id))
        for c in contacts:
            self.s.add(ContactModel(
                id=c.id, user_id=c.user_id, account_id=c.account_id, workspace=c.workspace, google_id=c.google_id, name=c.name, organisation=c.organisation, title=c.title,
                emails=c.emails, phones=c.phones, notes=c.notes, photo=c.photo, search=c.search,
            ))
        await self.s.flush()
        return len(contacts)

    async def contacts(self, user_id: str, workspace: str | None, query: str | None, limit: int) -> list[Contact]:
        q = select(ContactModel).where(ContactModel.user_id == user_id)
        if workspace:
            q = q.where(ContactModel.workspace == workspace)
        if query:
            q = q.where(ContactModel.search.like(f"%{query.replace(chr(37), '').replace(chr(95), '')}%"))
        rows = await self.s.scalars(q.order_by(func.lower(ContactModel.name)).limit(limit))
        return [Contact(r.id, r.user_id, r.account_id, r.workspace, r.google_id, r.name, r.organisation, r.title, list(r.emails or []), list(r.phones or []), r.notes, r.photo, r.search) for r in rows]
