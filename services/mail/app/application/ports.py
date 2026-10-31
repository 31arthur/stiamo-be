"""What the application needs from the outside. The IMAP adapter and the test fakes both implement these."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from threading import Event
from typing import Protocol

from app.domain.mail import Account, Credentials, Download, Folder, Message, MessageSummary, Page


class MailError(Exception):
    """The mail server refused, or could not be reached. The text is safe to show the person."""

    def __init__(self, message: str, *, auth: bool = False) -> None:
        super().__init__(message)
        #: The server said the username or password is wrong (as opposed to being unreachable).
        self.auth = auth


class MailGateway(Protocol):
    """Blocking calls to a mail server; the service runs them on worker threads."""

    def check(self, creds: Credentials) -> None: ...

    def folders(self, creds: Credentials) -> list[Folder]: ...

    def page(self, creds: Credentials, folder: str, *, before: int | None, limit: int, query: str) -> Page: ...

    def message(self, creds: Credentials, folder: str, uid: int) -> Message: ...

    def set_flags(self, creds: Credentials, folder: str, uid: int, *, seen: bool | None, flagged: bool | None) -> None: ...

    def attachment(self, creds: Credentials, folder: str, uid: int, index: int) -> Download: ...

    def uids(self, creds: Credentials, folder: str, *, since: date | None, before: date | None) -> tuple[int, list[int]]:
        """UIDVALIDITY and the uids of the messages in a date range (inclusive start, exclusive end), newest first. Cheap: no mail is read."""
        ...

    def summaries(self, creds: Credentials, folder: str, uids: list[int]) -> list[MessageSummary]:
        """List rows for the given messages, newest first."""
        ...

    def correspondents(self, creds: Credentials) -> list[str]:
        """Addresses the person has written to (From their Sent folder), lower case."""
        ...

    def move_to_trash(self, creds: Credentials, folder: str, uids: list[int]) -> int:
        """Moves messages to the Trash folder (never deletes them). Returns how many were moved."""
        ...

    def idle(self, creds: Credentials, folder: str, stop: Event, on_change: Callable[[], None], on_state: Callable[[str], None]) -> None:
        """Holds the folder open with IDLE and calls on_change whenever the server says something changed, until stop is set."""
        ...


class AccountRepository(Protocol):
    async def all_for(self, owner_id: str) -> list[Account]: ...

    async def get(self, owner_id: str, account_id: str) -> Account | None: ...

    async def add(self, account: Account) -> None: ...

    async def update(self, account: Account) -> None: ...

    async def remove(self, owner_id: str, account_id: str) -> bool: ...
