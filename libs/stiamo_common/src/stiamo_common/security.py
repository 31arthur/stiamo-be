"""Access-token verification used by every service (issuing lives in the identity service only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from stiamo_common.errors import Unauthorized

ACCESS = "access"
REFRESH = "refresh"


class TokenSettings(BaseModel):
    """What a service needs to verify tokens. Identity additionally needs the signing key."""

    algorithm: str = "HS256"
    verification_key: str
    issuer: str = "stiamo-identity"
    audience: str = "stiamo"
    leeway_seconds: int = 10


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str


def decode_token(token: str, settings: TokenSettings, expected_type: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            settings.verification_key,
            algorithms=[settings.algorithm],
            issuer=settings.issuer,
            audience=settings.audience,
            leeway=settings.leeway_seconds,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Token is not valid.") from exc
    if claims.get("typ") != expected_type:
        raise Unauthorized("Wrong token type.")
    return claims


_bearer = HTTPBearer(auto_error=False)


def make_principal_dependency(get_settings):
    """Builds a FastAPI dependency that turns `Authorization: Bearer <access token>` into a Principal.

    `get_settings` is a zero-arg callable returning TokenSettings, so each service wires its own config.
    """

    async def current_principal(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Principal:
        if creds is None or creds.scheme.lower() != "bearer":
            raise Unauthorized("Missing bearer token.")
        claims = decode_token(creds.credentials, get_settings(), ACCESS)
        return Principal(user_id=claims["sub"], email=claims.get("email", ""))

    return current_principal
