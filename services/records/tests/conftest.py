import time
import uuid

import jwt
import pytest
from app.config import Settings
from app.infrastructure.db import models  # noqa: F401
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from stiamo_common.db import Base


def make_token(user_id: str, typ: str = "access", ttl: int = 600, settings: Settings | None = None) -> str:
    s = (settings or Settings()).token_settings()
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "email": "u@example.com", "typ": typ, "jti": str(uuid.uuid4()), "iat": now,
         "exp": now + ttl, "iss": s.issuer, "aud": s.audience},
        s.verification_key, algorithm=s.algorithm)


@pytest.fixture
async def app():
    app = create_app(Settings(database_url="sqlite+aiosqlite:///:memory:"))
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield app


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def alice():
    return {"Authorization": f"Bearer {make_token('alice-id')}"}


@pytest.fixture
def bob():
    return {"Authorization": f"Bearer {make_token('bob-id')}"}
