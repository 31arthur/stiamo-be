"""Ports: what use cases need from the outside world. Infrastructure implements them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domain.entities import RefreshTokenRecord, User


class UserRepository(ABC):
    @abstractmethod
    async def get(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def add(self, user: User) -> None: ...


class RefreshTokenRepository(ABC):
    @abstractmethod
    async def add(self, record: RefreshTokenRecord) -> None: ...

    @abstractmethod
    async def get(self, jti: str) -> RefreshTokenRecord | None: ...

    @abstractmethod
    async def revoke(self, jti: str, now: datetime, replaced_by: str | None = None) -> None: ...

    @abstractmethod
    async def revoke_family(self, family_id: str, now: datetime) -> None: ...


class UnitOfWork(ABC):
    @abstractmethod
    async def commit(self) -> None:
        """Persist now. Needed when a use case must keep its writes even though it then raises."""


class PasswordHasher(ABC):
    @abstractmethod
    def hash(self, password: str) -> str: ...

    @abstractmethod
    def verify(self, password: str, password_hash: str) -> bool: ...

    @abstractmethod
    def verify_dummy(self, password: str) -> None:
        """Spend the same time as a real check, so unknown emails look like bad passwords."""


@dataclass(frozen=True)
class IssuedRefresh:
    token: str
    jti: str
    family_id: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedAccess:
    token: str
    expires_in: int  # seconds


@dataclass(frozen=True)
class RefreshClaims:
    jti: str
    user_id: str
    family_id: str


class TokenService(ABC):
    @abstractmethod
    def issue_access(self, user: User, now: datetime) -> IssuedAccess: ...

    @abstractmethod
    def issue_refresh(self, user_id: str, now: datetime, family_id: str | None = None) -> IssuedRefresh: ...

    @abstractmethod
    def decode_refresh(self, token: str) -> RefreshClaims:
        """Raises Unauthorized when the token is malformed, forged, expired or not a refresh token."""

    @abstractmethod
    def fingerprint(self, token: str) -> str:
        """Stable hash stored instead of the token itself."""


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...


class IdGenerator(ABC):
    @abstractmethod
    def new(self) -> str: ...
