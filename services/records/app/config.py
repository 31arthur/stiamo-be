from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from stiamo_common.security import TokenSettings

_DEV_SECRET = "dev-only-secret-change-me-0123456789abcdef"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECORDS_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./records.db"

    # Must match the identity service. HS256 uses the shared secret; RS256 only needs the public key.
    jwt_algorithm: str = "HS256"
    jwt_secret: str = _DEV_SECRET
    jwt_public_key: str = ""
    jwt_issuer: str = "stiamo-identity"
    jwt_audience: str = "stiamo"

    cors_origins: list[str] = ["http://localhost:5173"]

    max_document_bytes: int = 1_000_000
    max_batch: int = 500

    @model_validator(mode="after")
    def _safe_in_production(self) -> "Settings":
        if self.env == "production" and self.jwt_algorithm.startswith("HS") and self.jwt_secret == _DEV_SECRET:
            raise ValueError("RECORDS_JWT_SECRET must be set in production")
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
