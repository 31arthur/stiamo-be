"""Shared helper: start a token pair for a user (used by register, login and refresh)."""

from __future__ import annotations

from datetime import datetime

from app.application.dto import TokenPair
from app.application.ports import RefreshTokenRepository, TokenService
from app.domain.entities import RefreshTokenRecord, User


async def start_session(
    user: User,
    *,
    tokens: TokenService,
    refresh_tokens: RefreshTokenRepository,
    now: datetime,
    family_id: str | None = None,
) -> tuple[TokenPair, str]:
    access = tokens.issue_access(user, now)
    refresh = tokens.issue_refresh(user.id, now, family_id)
    await refresh_tokens.add(
        RefreshTokenRecord(
            jti=refresh.jti,
            user_id=user.id,
            family_id=refresh.family_id,
            token_hash=tokens.fingerprint(refresh.token),
            expires_at=refresh.expires_at,
            created_at=now,
        )
    )
    return TokenPair(access.token, refresh.token, access.expires_in), refresh.jti
