import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event

import jwt
import pytest
from app.application.ports import MailError, MailGateway
from app.config import Settings
from app.domain.mail import Attachment, Credentials, Download, Folder, Message, MessageSummary, Page, Person
from app.infrastructure.db import models as _tables  # noqa: F401  (register tables)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from stiamo_common.db import Base


def headers(user_id: str = "u1", email: str = "owner@example.com") -> dict[str, str]:
    s = Settings().token_settings()
    now = int(time.time())
    token = jwt.encode({"sub": user_id, "email": email, "typ": "access", "jti": str(uuid.uuid4()), "iat": now, "exp": now + 600, "iss": s.issuer, "aud": s.audience}, s.verification_key, algorithm=s.algorithm)
    return {"Authorization": f"Bearer {token}"}


class FakeMail(MailGateway):
    """A mail server in memory. A password of "wrong" is refused; a host of "down.example.com" can't be reached."""

    def __init__(self) -> None:
        self.flags: dict[int, dict[str, bool]] = {}
        self.pokes: list[Callable[[], None]] = []
        self.states: list[Callable[[str], None]] = []
        self.idling = 0
        self.checked: list[Credentials] = []
        self.trashed: list[tuple[str, int]] = []

    def check(self, creds: Credentials) -> None:
        self.checked.append(creds)
        if creds.host == "down.example.com":
            raise MailError("Couldn't reach the mail server.")
        if creds.password == "wrong":
            raise MailError("The server didn't accept that username and password.", auth=True)

    def folders(self, creds):
        return [Folder("INBOX", "Inbox", "inbox", unseen=2, total=3), Folder("Sent", "Sent", "sent", total=1)]

    def _summary(self, uid: int) -> MessageSummary:
        f = self.flags.get(uid, {})
        return MessageSummary(uid, f"Subject {uid}", Person("Ada", "ada@example.com"), datetime(2026, 1, uid, tzinfo=UTC), "Hello there", f.get("seen", False), f.get("flagged", False), uid == 3)

    def page(self, creds, folder, *, before, limit, query):
        if folder == "Nope":
            raise MailError("That folder isn't on the server.")
        uids = [u for u in (3, 2, 1) if before is None or u < before]
        if query:
            uids = [u for u in uids if query in f"Subject {u}"]
        chosen = uids[:limit]
        more = len(uids) > len(chosen)
        return Page([self._summary(u) for u in chosen], total=len(uids), next_before=chosen[-1] if more else None, uid_validity=7)

    def message(self, creds, folder, uid):
        if uid == 99:
            raise MailError("That message isn't in the mailbox any more.")
        s = self._summary(uid)
        files = [Attachment(0, "brief.pdf", "application/pdf", 4)] if uid == 3 else []
        return Message(uid, s.subject, s.sender, [Person("Me", "me@example.com")], [], s.date, s.seen, s.flagged, "Hello there", "<p>Hello <b>there</b></p>", False, files)

    def set_flags(self, creds, folder, uid, *, seen, flagged):
        current = self.flags.setdefault(uid, {})
        if seen is not None:
            current["seen"] = seen
        if flagged is not None:
            current["flagged"] = flagged

    def attachment(self, creds, folder, uid, index):
        return Download("brief ü.pdf", "application/pdf", b"%PDF")

    def correspondents(self, creds):
        return ["partner@fsa.example.com", "ada@example.com"]

    def move_to_trash(self, creds, folder, uids):
        if folder == "Nope":
            raise MailError("That folder isn't on the server.")
        self.trashed.extend((folder, u) for u in uids)
        return len(uids)

    def idle(self, creds, folder, stop: Event, on_change, on_state):
        self.idling += 1
        self.pokes.append(on_change)
        self.states.append(on_state)
        on_state("live")
        stop.wait(10)
        self.idling -= 1


@pytest.fixture
def mail() -> FakeMail:
    return FakeMail()


@pytest.fixture
async def client(mail):
    app = create_app(Settings(database_url="sqlite+aiosqlite:///:memory:"), gateway=mail)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


NEW = {"workspace": "sb", "label": "Chambers", "address": "arjun@sb.example.com", "host": "imap.example.com", "port": 993, "security": "ssl", "username": "arjun@sb.example.com", "password": "s3cret"}
