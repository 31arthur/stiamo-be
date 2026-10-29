import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from stiamo_common.security import TokenSettings

_DEV_SECRET = "dev-only-secret-change-me-0123456789abcdef"


def _list(value):
    """Accepts a JSON list or a plain comma-separated list."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except ValueError:
            text = text.strip("[]")
    return [part.strip().strip("\"'") for part in text.split(",") if part.strip().strip("\"'")]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DRIVE_", env_file=".env", extra="ignore")

    env: str = "development"

    # Must match the identity service. HS256 uses the shared secret; RS256 only needs the public key.
    jwt_algorithm: str = "HS256"
    jwt_secret: str = _DEV_SECRET
    jwt_public_key: str = ""
    jwt_issuer: str = "stiamo-identity"
    jwt_audience: str = "stiamo"

    #: The Google service account's key (the JSON file Google gives you): a path, or the JSON itself.
    credentials_file: str = ""
    credentials_json: str = ""
    #: The folder shared with that service account (only used to show its name; sharing is what limits access).
    folder_id: str = ""
    scope: str = "https://www.googleapis.com/auth/drive.readonly"
    timeout_seconds: float = 15.0

    #: Who may search the Drive. Empty means nobody: set this to the owner's sign-in email.
    allowed_emails: Annotated[list[str], NoDecode] = []

    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", "allowed_emails", mode="before")
    @classmethod
    def _parse_lists(cls, value):
        return _list(value)

    @model_validator(mode="after")
    def _safe_in_production(self) -> "Settings":
        if self.env == "production" and self.jwt_algorithm.startswith("HS") and self.jwt_secret == _DEV_SECRET:
            raise ValueError("DRIVE_JWT_SECRET must be set in production")
        return self

    @property
    def allowed(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails}

    def token_settings(self) -> TokenSettings:
        key = self.jwt_public_key if self.jwt_algorithm.startswith(("RS", "ES")) else self.jwt_secret
        return TokenSettings(
            algorithm=self.jwt_algorithm, verification_key=key, issuer=self.jwt_issuer, audience=self.jwt_audience
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
