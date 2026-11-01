"""Sealing Google refresh tokens so a copy of the database holds none in the clear."""

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def seal(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def open(self, sealed: str | None) -> str:
        if not sealed:
            return ""
        try:
            return self._fernet.decrypt(sealed.encode()).decode()
        except InvalidToken:
            return ""  # sealed with another key: treated as not set, so the person is asked again


class TokenMint:
    """Signs a short-lived access token for a person, so the background run can read their meetings as they would. Only where the services share an HS256 secret."""

    def __init__(self, secret: str, issuer: str, audience: str, ttl_seconds: int = 300) -> None:
        self._secret, self._issuer, self._audience, self._ttl = secret, issuer, audience, ttl_seconds

    def __call__(self, user_id: str, email: str) -> str:
        import time
        import uuid

        import jwt

        now = int(time.time())
        return jwt.encode({"iss": self._issuer, "aud": self._audience, "sub": user_id, "email": email, "typ": "access", "jti": str(uuid.uuid4()), "iat": now, "exp": now + self._ttl}, self._secret, algorithm="HS256")
