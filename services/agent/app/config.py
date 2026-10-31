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
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./agent.db"

    # Must match the identity service. HS256 uses the shared secret. The service also signs short-lived tokens with it, to read
    # the owner's cases when a Telegram or WhatsApp message arrives and there is no signed-in browser to borrow one from.
    jwt_algorithm: str = "HS256"
    jwt_secret: str = _DEV_SECRET
    jwt_public_key: str = ""
    jwt_issuer: str = "stiamo-identity"
    jwt_audience: str = "stiamo"

    #: Encrypts the AI keys and channel tokens in the database (a Fernet key). Required in production.
    secret_key: str = ""

    #: Where Telegram and Twilio can reach this API from the internet (https), without a trailing slash.
    public_url: str = ""

    practice_url: str = "http://practice:8000"
    files_url: str = "http://files:8000"
    drive_url: str = "http://drive:8000"

    timeout_seconds: float = 60.0
    max_steps: int = 8
    #: How long a Telegram pairing code works.
    pairing_minutes: int = 30

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
                raise ValueError("AGENT_JWT_SECRET must be set in production")
            if not self.secret_key:
                raise ValueError("AGENT_SECRET_KEY must be set in production (it encrypts the stored keys)")
        return self

    @property
    def encryption_key(self) -> bytes:
        """The key that seals stored secrets. In development one is derived from the JWT secret."""
        if self.secret_key:
            return self.secret_key.encode()
        return base64.urlsafe_b64encode(hashlib.sha256(("agent:" + self.jwt_secret).encode()).digest())

    def token_settings(self) -> TokenSettings:
        key = self.jwt_public_key if self.jwt_algorithm.startswith(("RS", "ES")) else self.jwt_secret
        return TokenSettings(algorithm=self.jwt_algorithm, verification_key=key, issuer=self.jwt_issuer, audience=self.jwt_audience)


@lru_cache
def get_settings() -> Settings:
    return Settings()
