"""Sealing stored secrets, and signing the short-lived token the agent borrows to read the owner's data."""

import json
import time
import uuid
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from stiamo_common.security import ACCESS, TokenSettings

from app.application.ports import AccessTokens


class SecretBox:
    """Encrypts the AI keys and channel tokens before they reach the database, so a copy of the database holds none in the clear."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def seal(self, value: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(value).encode()).decode()

    def open(self, sealed: str | None) -> dict[str, Any]:
        if not sealed:
            return {}
        try:
            data = json.loads(self._fernet.decrypt(sealed.encode()))
        except (InvalidToken, ValueError):
            return {}  # sealed with another key: treated as not set, so the person is asked again
        return data if isinstance(data, dict) else {}


class JwtAccessTokens(AccessTokens):
    """Mints access tokens with the shared secret, valid for a few minutes. Only possible where services share an HS256 secret."""

    def __init__(self, settings: TokenSettings, ttl_seconds: int = 300) -> None:
        if not settings.algorithm.startswith("HS"):
            raise ValueError("The agent can only act for a person when the services share an HS256 secret.")
        self._settings, self._ttl = settings, ttl_seconds

    def mint(self, user_id: str, email: str) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iss": self._settings.issuer, "aud": self._settings.audience, "sub": user_id, "email": email, "typ": ACCESS, "jti": str(uuid.uuid4()), "iat": now, "exp": now + self._ttl},
            self._settings.verification_key,
            algorithm=self._settings.algorithm,
        )
