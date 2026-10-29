import time
import uuid

import jwt
import pytest
from app.application.ports import DriveGateway, DriveUnavailable, NotConnected, Page
from app.config import Settings
from app.domain.search import DriveFile
from app.main import create_app
from httpx import ASGITransport, AsyncClient

OWNER = "owner@example.com"


def make_token(email: str = OWNER, user_id: str = "u1", typ: str = "access", ttl: int = 600) -> str:
    s = Settings().token_settings()
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "email": email, "typ": typ, "jti": str(uuid.uuid4()), "iat": now, "exp": now + ttl,
         "iss": s.issuer, "aud": s.audience},
        s.verification_key, algorithm=s.algorithm)


def doc(id_, name, modified="2026-03-01T10:00:00.000Z", mime="application/pdf"):
    return DriveFile(id=id_, name=name, mime_type=mime, modified_time=modified,
                     web_view_link=f"https://drive.google.com/file/d/{id_}/view?usp=drivesdk")


class FakeGateway(DriveGateway):
    """Answers searches from a fixed list by looking for the quoted words in the query."""

    def __init__(self, files=None, configured=True, fail=None):
        self.files, self._configured, self.fail = files or [], configured, fail
        self.queries: list[dict] = []

    @property
    def configured(self):
        return self._configured

    async def search(self, query, *, limit, page_token=None, newest_first=False):
        self.queries.append({"query": query, "limit": limit, "page_token": page_token, "newest_first": newest_first})
        if not self._configured:
            raise NotConnected()
        if self.fail:
            raise DriveUnavailable(self.fail)
        import re

        words = [w.strip('"').lower() for w in re.findall(r"fullText contains '((?:[^'\\]|\\.)*)'", query)]
        hits = [f for f in self.files if all(w in f.name.lower() for w in words)]
        return Page(hits[:limit], "next-page" if len(hits) > limit else None)

    async def folder_name(self, folder_id):
        return "Letters" if self._configured else None


@pytest.fixture
def gateway():
    return FakeGateway([doc("1", "TUMWU-2026-001 letter to labour dept.pdf", "2026-03-02T10:00:00.000Z"),
                        doc("2", "Letter to NHAI.docx", "2026-02-01T10:00:00.000Z"),
                        doc("3", "TUMWU-2026-001 reply from NHAI.pdf", "2026-03-05T10:00:00.000Z"),
                        doc("4", "Unrelated note.pdf")])


def build(gateway, allowed=(OWNER,)):
    return create_app(Settings(allowed_emails=list(allowed), folder_id="folder-1"), gateway=gateway)


@pytest.fixture
async def client(gateway):
    async with AsyncClient(transport=ASGITransport(app=build(gateway)), base_url="http://test") as c:
        yield c


@pytest.fixture
def owner():
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
def stranger():
    return {"Authorization": f"Bearer {make_token('stranger@example.com', 'u2')}"}
