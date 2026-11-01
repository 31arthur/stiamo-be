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
    model_config = SettingsConfigDict(env_prefix="CALENDAR_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./calendar.db"

    # Must match the identity service.
    jwt_algorithm: str = "HS256"
    jwt_secret: str = _DEV_SECRET
    jwt_public_key: str = ""
    jwt_issuer: str = "stiamo-identity"
    jwt_audience: str = "stiamo"

    #: Encrypts the Google refresh tokens in the database (a Fernet key). Required in production.
    secret_key: str = ""

    #: The OAuth client made in Google Cloud (docs/CALENDAR_SETUP.md). Without it nothing can be connected.
    google_client_id: str = ""
    google_client_secret: str = ""
    #: Where Google sends the person back after they allow access. Must be listed as an authorised redirect URI in Google Cloud.
    #: This service's public address, e.g. https://stiamo-be.fullstackarthur.com
    public_url: str = "http://localhost:8000"
    #: The web app, where the person lands after connecting.
    app_url: str = "http://localhost:5173"

    #: The practice service, to send meetings and hearings to Google. It is called as the owner, with a token this service mints
    #: (possible where the services share an HS256 secret). Empty: nothing is sent to Google, only read from it.
    practice_url: str = "http://practice:8000"
    #: Calendars are read again this often, for everyone, in the background. 0 turns the background run off.
    sync_minutes: int = 5
    #: How far back and ahead Google's events are read, in days.
    window_back_days: int = 30
    window_ahead_days: int = 400
    #: What the person's wall clock is, until the web app says otherwise.
    default_timezone: str = "Asia/Kolkata"
    #: Only accounts of these Google addresses can be connected (comma separated). Empty: any.
    allowed_google_emails: Annotated[list[str], NoDecode] = []

    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", "allowed_google_emails", mode="before")
    @classmethod
    def _parse_list(cls, value):
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
                raise ValueError("CALENDAR_JWT_SECRET must be set in production")
            if not self.secret_key:
                raise ValueError("CALENDAR_SECRET_KEY must be set in production (it encrypts the stored Google tokens)")
        return self

    @property
    def encryption_key(self) -> bytes:
        """The key that seals stored tokens. In development one is derived from the JWT secret."""
        if self.secret_key:
            return self.secret_key.encode()
        return base64.urlsafe_b64encode(hashlib.sha256(("calendar:" + self.jwt_secret).encode()).digest())

    @property
    def state_secret(self) -> str:
        """Signs the `state` that goes to Google and comes back, so the callback can trust who started the connection."""
        return hashlib.sha256(b"calendar-state:" + self.encryption_key).hexdigest()

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url.rstrip('/')}/v1/calendar/oauth/callback"

    @property
    def configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    def token_settings(self) -> TokenSettings:
        key = self.jwt_public_key if self.jwt_algorithm.startswith(("RS", "ES")) else self.jwt_secret
        return TokenSettings(algorithm=self.jwt_algorithm, verification_key=key, issuer=self.jwt_issuer, audience=self.jwt_audience)


@lru_cache
def get_settings() -> Settings:
    return Settings()
