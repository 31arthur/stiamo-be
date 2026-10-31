from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import AccountRepository
from app.domain.mail import Account, Credentials
from app.infrastructure.db.models import AccountModel
from app.infrastructure.security import SecretBox


class SqlAccountRepository(AccountRepository):
    def __init__(self, session: AsyncSession, box: SecretBox) -> None:
        self.session = session
        self.box = box

    def _to_domain(self, row: AccountModel) -> Account:
        creds = Credentials(row.host, row.port, row.security, row.username, self.box.open(row.password_sealed))
        return Account(row.id, row.owner_id, row.workspace, row.label, row.address, creds, row.created_at, row.error)

    def _fill(self, row: AccountModel, a: Account) -> None:
        row.workspace, row.label, row.address = a.workspace, a.label, a.address
        c = a.credentials
        row.host, row.port, row.security, row.username = c.host, c.port, c.security, c.username
        row.password_sealed = self.box.seal(c.password)
        row.error = a.error

    async def all_for(self, owner_id: str) -> list[Account]:
        rows = await self.session.scalars(select(AccountModel).where(AccountModel.owner_id == owner_id).order_by(AccountModel.created_at))
        return [self._to_domain(r) for r in rows]

    async def get(self, owner_id: str, account_id: str) -> Account | None:
        row = await self.session.get(AccountModel, account_id)
        return self._to_domain(row) if row and row.owner_id == owner_id else None

    async def add(self, account: Account) -> None:
        row = AccountModel(id=account.id, owner_id=account.owner_id, created_at=account.created_at)
        self._fill(row, account)
        self.session.add(row)

    async def update(self, account: Account) -> None:
        row = await self.session.get(AccountModel, account.id)
        if row and row.owner_id == account.owner_id:
            self._fill(row, account)

    async def remove(self, owner_id: str, account_id: str) -> bool:
        result = await self.session.execute(delete(AccountModel).where(AccountModel.id == account_id, AccountModel.owner_id == owner_id))
        return result.rowcount > 0
