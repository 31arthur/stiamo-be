from datetime import UTC, datetime, timedelta

import jwt
from app.config import Settings
from stiamo_common.security import TokenSettings


async def test_expired_access_token_rejected(client, alice):
    s = Settings().token_settings()
    now = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(
        {"sub": alice["user"]["id"], "typ": "access", "jti": "x", "iat": now, "exp": now + timedelta(minutes=15),
         "iss": s.issuer, "aud": s.audience},
        s.verification_key, algorithm="HS256")
    r = await client.get("/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"].lower()


def test_production_requires_real_secret():
    import pytest

    with pytest.raises(ValueError):
        Settings(env="production")


def test_token_settings_type():
    assert isinstance(Settings().token_settings(), TokenSettings)
