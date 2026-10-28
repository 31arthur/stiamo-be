from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities import User


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"


@dataclass(frozen=True)
class AuthResult:
    user: User
    tokens: TokenPair
