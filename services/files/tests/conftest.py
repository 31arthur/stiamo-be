import time
import uuid

import jwt
import pytest
from app.application.ports import FileStorage, HistoryNotifier
from app.config import Settings
from app.infrastructure.db import models  # noqa: F401  (register tables)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from stiamo_common.db import Base


def make_token(user_id: str, typ: str = "access", ttl: int = 600) -> str:
    s = Settings().token_settings()
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "email": "u@example.com", "typ": typ, "jti": str(uuid.uuid4()), "iat": now,
         "exp": now + ttl, "iss": s.issuer, "aud": s.audience},
        s.verification_key, algorithm=s.algorithm)


class MemoryStorage(FileStorage):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def put(self, path, data):
        self.files[path] = data

    async def read(self, path):
        return self.files[path]

    async def delete(self, path):
        self.files.pop(path, None)


class RecordingHistory(HistoryNotifier):
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def record(self, token, ref, title, action, summary):
        self.calls.append((token, ref.kind, ref.id, title, action, summary))


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def history():
    return RecordingHistory()


@pytest.fixture
async def app(storage, history):
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", max_bytes=1024)
    app = create_app(settings, storage=storage, history=history)
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
