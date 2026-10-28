import pytest
from app.config import Settings
from app.infrastructure.db import models  # noqa: F401  (register tables)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from stiamo_common.db import Base


@pytest.fixture
async def app():
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings, hasher_rounds=4)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield app


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


ALICE = {"name": "Alice", "email": "Alice@Example.com", "password": "correct horse"}


@pytest.fixture
async def alice(client):
    r = await client.post("/v1/auth/register", json=ALICE)
    assert r.status_code == 201, r.text
    return r.json()
