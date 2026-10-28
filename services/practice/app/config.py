import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from stiamo_common.security import TokenSettings

_DEV_SECRET = "dev-only-secret-change-me-0123456789abcdef"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRACTICE_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./practice.db"

    # Must match the identity service. HS256 uses the shared secret; RS256 only needs the public key.
    jwt_algorithm: str = "HS256"
    jwt_secret: str = _DEV_SECRET
    jwt_public_key: str = ""
    jwt_issuer: str = "stiamo-identity"
    jwt_audience: str = "stiamo"

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
        if self.env == "production" and self.jwt_algorithm.startswith("HS") and self.jwt_secret == _DEV_SECRET:
            raise ValueError("PRACTICE_JWT_SECRET must be set in production")
        return self

    def token_settings(self) -> TokenSettings:
        key = self.jwt_public_key if self.jwt_algorithm.startswith(("RS", "ES")) else self.jwt_secret
        return TokenSettings(
            algorithm=self.jwt_algorithm,
            verification_key=key,
            issuer=self.jwt_issuer,
            audience=self.jwt_audience,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
