import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from app.application.ports import GoogleError, GoogleGateway, PracticeGateway, SyncExpired, Tokens
from app.config import Settings
from app.infrastructure.db import models as _tables  # noqa: F401  (register tables)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from stiamo_common.db import Base

APP = "https://app.example.com"
PUBLIC = "https://api.example.com"


def headers(user_id: str = "u1", email: str = "owner@example.com") -> dict[str, str]:
    s = Settings().token_settings()
    now = int(time.time())
    token = jwt.encode({"sub": user_id, "email": email, "typ": "access", "jti": str(uuid.uuid4()), "iat": now, "exp": now + 600, "iss": s.issuer, "aud": s.audience}, s.verification_key, algorithm=s.algorithm)
    return {"Authorization": f"Bearer {token}"}


def event(gid: str, title: str, start: str, end: str, **over: Any) -> dict[str, Any]:
    timed = "T" in start
    base = {"id": gid, "status": "confirmed", "summary": title, "htmlLink": f"https://calendar.google.com/{gid}", "updated": "2026-11-01T00:00:00Z", "start": {"dateTime": start} if timed else {"date": start}, "end": {"dateTime": end} if timed else {"date": end}}
    return {**base, **over}


class FakeGoogle(GoogleGateway):
    """Google in memory: accounts by authorisation code, calendars and events by account, and a record of what was written."""

    def __init__(self) -> None:
        #: code -> (email, refresh token or None)
        self.codes: dict[str, tuple[str, str | None]] = {}
        #: refresh token -> email; one that isn't here is refused (access withdrawn)
        self.live: dict[str, str] = {}
        self.calendar_list: dict[str, list[dict[str, Any]]] = {}
        #: calendar id -> events a full read returns
        self.items: dict[str, list[dict[str, Any]]] = {}
        #: calendar id -> changes the next incremental read returns
        self.changes: dict[str, list[dict[str, Any]]] = {}
        self.expired: set[str] = set()
        self.reads: list[tuple[str, str | None]] = []
        self.written: list[tuple[str, str, str, dict[str, Any]]] = []  # (verb, calendar, event id, body)
        self.revoked: list[str] = []
        self.counter = 0

    def auth_url(self, state: str, login_hint: str | None) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&login_hint={login_hint or ''}"

    async def exchange(self, code: str) -> Tokens:
        if code not in self.codes:
            raise GoogleError("Google didn't accept the sign-in: bad code")
        email, refresh = self.codes[code]
        if refresh:
            self.live[refresh] = email
        return Tokens(f"acc-{email}", refresh, 3600)

    async def refresh(self, refresh_token: str) -> str:
        if refresh_token not in self.live:
            raise GoogleError("Google no longer allows Stiamo to read this calendar.", auth=True)
        return f"acc-{self.live[refresh_token]}"

    async def email(self, access: str) -> str:
        return access.removeprefix("acc-")

    async def calendars(self, access: str) -> list[dict[str, Any]]:
        return self.calendar_list.get(access.removeprefix("acc-"), [])

    async def events(self, access, calendar_id, *, sync_token, time_min: datetime, time_max: datetime):
        self.reads.append((calendar_id, sync_token))
        if sync_token and calendar_id in self.expired:
            self.expired.discard(calendar_id)
            raise SyncExpired
        if sync_token:
            out, self.changes[calendar_id] = self.changes.get(calendar_id, []), []
            return out, f"token-{len(self.reads)}"
        return list(self.items.get(calendar_id, [])), f"token-{len(self.reads)}"

    async def insert(self, access, calendar_id, body):
        self.counter += 1
        gid = f"g{self.counter}"
        self.written.append(("insert", calendar_id, gid, body))
        return gid

    async def update(self, access, calendar_id, event_id, body):
        self.written.append(("update", calendar_id, event_id, body))

    async def delete(self, access, calendar_id, event_id):
        self.written.append(("delete", calendar_id, event_id, {}))

    async def revoke(self, token: str) -> None:
        self.revoked.append(token)
        self.live.pop(token, None)

    def connect(self, email: str, calendars: list[dict[str, Any]], code: str | None = None, refresh: str | None = "r") -> str:
        code = code or f"code-{email}"
        self.codes[code] = (email, f"{refresh}-{email}" if refresh else None)
        self.calendar_list[email] = calendars
        return code


class FakePractice(PracticeGateway):
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {"meetings": [], "hearings": [], "cases": []}
        self.tokens: list[str] = []

    async def records(self, token: str, table: str) -> list[dict[str, Any]]:
        self.tokens.append(token)
        return list(self.tables[table])


class Rig:
    def __init__(self, app, client, google, practice) -> None:
        self.app, self.client, self.google, self.practice = app, client, google, practice

    async def start(self, workspace: str = "sb", who: dict[str, str] | None = None) -> str:
        r = await self.client.post("/v1/calendar/oauth/start", json={"workspace": workspace}, headers=who or headers())
        assert r.status_code == 200, r.text
        return parse_qs(urlparse(r.json()["url"]).query)["state"][0]

    async def connect(self, email: str, workspace: str = "sb", calendars: list[dict[str, Any]] | None = None, who: dict[str, str] | None = None):
        """Runs the whole connection as the browser would: start, Google, and back to the callback."""
        code = self.google.connect(email, calendars if calendars is not None else [{"id": email, "summary": email, "primary": True, "accessRole": "owner", "backgroundColor": "#0b8043", "timeZone": "Asia/Kolkata"}])
        state = await self.start(workspace, who)
        return await self.client.get("/v1/calendar/oauth/callback", params={"code": code, "state": state}, follow_redirects=False)

    async def status(self, who: dict[str, str] | None = None) -> dict[str, Any]:
        return (await self.client.get("/v1/calendar/status", headers=who or headers())).json()

    async def sync(self, who: dict[str, str] | None = None, **body: Any) -> dict[str, Any]:
        r = await self.client.post("/v1/calendar/sync", json=body, headers=who or headers())
        assert r.status_code == 200, r.text
        return r.json()

    async def events(self, start: str = "2026-11-01", to: str = "2026-11-30", workspace: str | None = None, who: dict[str, str] | None = None) -> list[dict[str, Any]]:
        q = {"from": start, "to": to, **({"workspace": workspace} if workspace else {})}
        return (await self.client.get("/v1/calendar/events", params=q, headers=who or headers())).json()["items"]


@pytest.fixture
async def rig():
    google, practice = FakeGoogle(), FakePractice()
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", google_client_id="client-id", google_client_secret="client-secret", public_url=PUBLIC, app_url=APP, sync_minutes=0, practice_url="http://practice")
    app = create_app(settings, google=google, practice=practice)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Rig(app, client, google, practice)
