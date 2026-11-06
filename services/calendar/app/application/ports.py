from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.domain.calendar import Account, Event, Link, Prefs, Source
from app.domain.contacts import Contact, ContactAccount


class GoogleError(Exception):
    """Google refused or couldn't be reached. The message is fit to show."""

    def __init__(self, message: str, *, auth: bool = False) -> None:
        super().__init__(message)
        self.message = message
        #: True when the refresh token is no longer good, so the person has to connect again.
        self.auth = auth


class SyncExpired(Exception):
    """Google no longer has the changes feed this token pointed at: read everything again."""


@dataclass
class Tokens:
    access: str
    refresh: str | None
    expires_in: int


class GoogleGateway(ABC):
    @abstractmethod
    def auth_url(self, state: str, login_hint: str | None) -> str: ...

    @abstractmethod
    async def exchange(self, code: str) -> Tokens: ...

    @abstractmethod
    async def refresh(self, refresh_token: str) -> str:
        """A fresh access token. Raises GoogleError(auth=True) when the refresh token is no longer accepted."""

    @abstractmethod
    async def email(self, access: str) -> str: ...

    @abstractmethod
    async def calendars(self, access: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def events(self, access: str, calendar_id: str, *, sync_token: str | None, time_min: datetime, time_max: datetime) -> tuple[list[dict[str, Any]], str | None]:
        """Events (each recurring one already unrolled) and the token for what changes next. With a sync token, only what changed."""

    @abstractmethod
    async def insert(self, access: str, calendar_id: str, body: dict[str, Any]) -> str: ...

    @abstractmethod
    async def update(self, access: str, calendar_id: str, event_id: str, body: dict[str, Any]) -> None: ...

    @abstractmethod
    async def delete(self, access: str, calendar_id: str, event_id: str) -> None: ...

    @abstractmethod
    async def revoke(self, token: str) -> None: ...


class PeopleGateway(Protocol):
    """Google's OAuth, and the person's contacts (the People API). The same sign-in as the calendar, with its own permission: read contacts."""

    def auth_url(self, state: str, login_hint: str | None) -> str: ...

    async def exchange(self, code: str) -> Tokens: ...

    async def refresh(self, refresh_token: str) -> str: ...

    async def email(self, access: str) -> str: ...

    async def revoke(self, token: str) -> None: ...

    async def connections(self, access: str) -> list[dict[str, Any]]:
        """Every contact in the account, as Google's people (names, emailAddresses, phoneNumbers, organizations, biographies, photos)."""
        ...


class ContactRepository(ABC):
    @abstractmethod
    async def accounts(self, user_id: str) -> list[ContactAccount]: ...

    @abstractmethod
    async def all_accounts(self) -> list[ContactAccount]: ...

    @abstractmethod
    async def account(self, user_id: str, account_id: str) -> ContactAccount | None: ...

    @abstractmethod
    async def save_account(self, a: ContactAccount) -> None: ...

    @abstractmethod
    async def delete_account(self, user_id: str, account_id: str) -> None:
        """The link and the contacts kept for it."""

    @abstractmethod
    async def replace_contacts(self, account: ContactAccount, contacts: list[Contact]) -> int:
        """Makes the contacts kept for this link match what Google has now. Returns how many there are."""

    @abstractmethod
    async def contacts(self, user_id: str, workspace: str | None, query: str | None, limit: int) -> list[Contact]:
        """Sorted by name. `query` (lower case) is looked for in the name, organisation, addresses, numbers and notes."""


class PracticeGateway(ABC):
    """Stiamo's own meetings and hearings, read as the owner."""

    @abstractmethod
    async def records(self, token: str, table: str) -> list[dict[str, Any]]:
        """`meetings`, `hearings` or `cases`."""


class CalendarRepository(ABC):
    @abstractmethod
    async def accounts(self, user_id: str) -> list[Account]: ...

    @abstractmethod
    async def all_accounts(self) -> list[Account]: ...

    @abstractmethod
    async def account(self, user_id: str, account_id: str) -> Account | None: ...

    @abstractmethod
    async def save_account(self, a: Account) -> None: ...

    @abstractmethod
    async def delete_account(self, user_id: str, account_id: str) -> None:
        """The account, its calendars, the events kept for them and the links to what was sent."""

    @abstractmethod
    async def sources(self, user_id: str) -> list[Source]: ...

    @abstractmethod
    async def save_source(self, s: Source) -> None: ...

    @abstractmethod
    async def delete_source(self, user_id: str, source_id: str) -> None: ...

    @abstractmethod
    async def events_between(self, user_id: str, source_ids: list[str], start: datetime, end: datetime) -> list[Event]: ...

    @abstractmethod
    async def upsert_events(self, events: list[Event]) -> None: ...

    @abstractmethod
    async def remove_events(self, source_id: str, google_ids: list[str]) -> None: ...

    @abstractmethod
    async def clear_events(self, source_id: str) -> None: ...

    @abstractmethod
    async def links(self, user_id: str) -> dict[str, Link]: ...

    @abstractmethod
    async def save_link(self, link: Link) -> None: ...

    @abstractmethod
    async def delete_link(self, user_id: str, ref: str) -> None: ...

    @abstractmethod
    async def prefs(self, user_id: str) -> Prefs | None: ...

    @abstractmethod
    async def save_prefs(self, p: Prefs) -> None: ...
