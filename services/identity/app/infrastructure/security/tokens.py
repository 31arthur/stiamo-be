from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta

import jwt
from stiamo_common.errors import Unauthorized
from stiamo_common.security import ACCESS, REFRESH, TokenSettings, decode_token

from app.application.ports import IssuedAccess, IssuedRefresh, RefreshClaims, TokenService
from app.domain.entities import User


class JwtTokenService(TokenService):
    def __init__(self, settings: TokenSettings, signing_key: str, access_ttl: timedelta,
                 refresh_ttl: timedelta) -> None:
        self._settings, self._key = settings, signing_key
        self._access_ttl, self._refresh_ttl = access_ttl, refresh_ttl

    def _encode(self, claims: dict) -> str:
        return jwt.encode(
            {"iss": self._settings.issuer, "aud": self._settings.audience, **claims},
            self._key,
            algorithm=self._settings.algorithm,
        )

    def issue_access(self, user: User, now: datetime) -> IssuedAccess:
        token = self._encode({
            "sub": user.id, "email": user.email, "typ": ACCESS, "jti": str(uuid.uuid4()),
            "iat": now, "exp": now + self._access_ttl,
        })
        return IssuedAccess(token, int(self._access_ttl.total_seconds()))

    def issue_refresh(self, user_id: str, now: datetime, family_id: str | None = None) -> IssuedRefresh:
        jti, family = str(uuid.uuid4()), family_id or str(uuid.uuid4())
        expires = now + self._refresh_ttl
        token = self._encode({"sub": user_id, "typ": REFRESH, "jti": jti, "fam": family,
                              "iat": now, "exp": expires})
        return IssuedRefresh(token, jti, family, expires)

    def decode_refresh(self, token: str) -> RefreshClaims:
        claims = decode_token(token, self._settings, REFRESH)
        if "fam" not in claims:
            raise Unauthorized("Token is not valid.")
        return RefreshClaims(claims["jti"], claims["sub"], claims["fam"])

    def fingerprint(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
