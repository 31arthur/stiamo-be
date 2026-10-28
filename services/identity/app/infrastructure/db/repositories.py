from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.errors import Conflict

from app.application.ports import RefreshTokenRepository, UnitOfWork, UserRepository
from app.domain.entities import RefreshTokenRecord, User
from app.infrastructure.db.models import RefreshTokenModel, UserModel


class SqlUnitOfWork(UnitOfWork):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()


class SqlUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _entity(m: UserModel) -> User:
        return User(m.id, m.email, m.name, m.password_hash, m.created_at, m.is_active)

    async def get(self, user_id: str) -> User | None:
        m = await self._s.get(UserModel, user_id)
        return self._entity(m) if m else None

    async def get_by_email(self, email: str) -> User | None:
        m = (await self._s.execute(select(UserModel).where(UserModel.email == email))).scalar_one_or_none()
        return self._entity(m) if m else None

    async def add(self, user: User) -> None:
        self._s.add(UserModel(id=user.id, email=user.email, name=user.name, password_hash=user.password_hash,
                              is_active=user.is_active, created_at=user.created_at))
        try:
            await self._s.flush()
        except IntegrityError as exc:  # lost a race with a concurrent registration
            raise Conflict("An account with this email already exists.") from exc


class SqlRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, record: RefreshTokenRecord) -> None:
        self._s.add(RefreshTokenModel(**record.__dict__))
        await self._s.flush()

    async def get(self, jti: str) -> RefreshTokenRecord | None:
        m = await self._s.get(RefreshTokenModel, jti)
        if m is None:
            return None
        return RefreshTokenRecord(m.jti, m.user_id, m.family_id, m.token_hash, m.expires_at, m.created_at,
                                  m.revoked_at, m.replaced_by)

    async def revoke(self, jti: str, now: datetime, replaced_by: str | None = None) -> None:
        await self._s.execute(update(RefreshTokenModel).where(RefreshTokenModel.jti == jti)
                              .values(revoked_at=now, replaced_by=replaced_by))

    async def revoke_family(self, family_id: str, now: datetime) -> None:
        await self._s.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.family_id == family_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=now)
        )
