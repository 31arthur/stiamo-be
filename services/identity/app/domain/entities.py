"""Domain entities: plain data and rules, no framework imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class User:
    id: str
    email: str
    name: str
    password_hash: str
    created_at: datetime
    is_active: bool = True


@dataclass
class RefreshTokenRecord:
    """Server-side state of one issued refresh token. Tokens of one login chain share a `family_id`."""

    jti: str
    user_id: str
    family_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None
    replaced_by: str | None = None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at <= now
