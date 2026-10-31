import base64
import hashlib
import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from stiamo_common.security import TokenSettings

_DEV_SECRET = "dev-only-secret-change-me-0123456789abcdef"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAIL_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./mail.db"

    # Must match the identity service.
    jwt_algorithm: str = "HS256"
    jwt_secret: str = _DEV_SECRET
    jwt_public_key: str = ""
    jwt_issuer: str = "stiamo-identity"
    jwt_audience: str = "stiamo"

    #: Encrypts the mailbox passwords in the database (a Fernet key). Required in production.
    secret_key: str = ""

    #: Seconds to wait for a mail server before giving up.
    imap_timeout: float = 20.0
    #: IDLE is restarted this often. Servers drop a silent connection after ~30 minutes (RFC 2177 says re-issue before 29).
    idle_seconds: int = 25 * 60
    #: How many messages one page of the list holds.
    page_size: int = 40
    #: Mail servers on private networks (localhost, 10.x, 192.168.x) are refused unless this is set: it stops the service
    #: being used to probe the machines behind it. Only for development against a local server.
    allow_private_hosts: bool = False

    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value):
        """Accepts a JSON list or a plain comma-separated list: https://a.com,https://b.com"""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            try:
                return json.loads(text)
            except ValueError:
                text = text.strip("[]")
        return [part.strip().strip("\"'") for part in text.split(",") if part.strip().strip("\"'")]

    @model_validator(mode="after")
    def _safe_in_production(self) -> "Settings":
        if self.env == "production":
            if self.jwt_algorithm.startswith("HS") and self.jwt_secret == _DEV_SECRET:
                raise ValueError("MAIL_JWT_SECRET must be set in production")
            if not self.secret_key:
                raise ValueError("MAIL_SECRET_KEY must be set in production (it encrypts the stored mailbox passwords)")
            if self.allow_private_hosts:
                raise ValueError("MAIL_ALLOW_PRIVATE_HOSTS is for development only")
        return self

    @property
    def encryption_key(self) -> bytes:
        """The key that seals stored passwords. In development one is derived from the JWT secret."""
        if self.secret_key:
            return self.secret_key.encode()
        return base64.urlsafe_b64encode(hashlib.sha256(("mail:" + self.jwt_secret).encode()).digest())

    def token_settings(self) -> TokenSettings:
        key = self.jwt_public_key if self.jwt_algorithm.startswith(("RS", "ES")) else self.jwt_secret
        return TokenSettings(algorithm=self.jwt_algorithm, verification_key=key, issuer=self.jwt_issuer, audience=self.jwt_audience)


@lru_cache
def get_settings() -> Settings:
    return Settings()
