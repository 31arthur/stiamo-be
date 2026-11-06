"""What the mail service talks about: a mailbox the person connected, and the messages in it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

WORKSPACES = ("personal", "sb", "fsa", "media")
SECURITY = ("ssl", "starttls")


@dataclass(frozen=True)
class Credentials:
    """Everything needed to open a connection. The password is only ever held in memory, never returned."""

    host: str
    port: int
    security: str
    username: str
    password: str


@dataclass(frozen=True)
class Account:
    id: str
    owner_id: str
    workspace: str
    label: str
    address: str
    credentials: Credentials
    created_at: datetime
    #: Set when the last attempt to reach the server failed, so the screen can say why instead of showing an empty inbox.
    error: str = ""


@dataclass(frozen=True)
class Folder:
    name: str
    #: The name to show: the server's INBOX, "Sent Items" and the like.
    label: str
    role: str  # inbox, sent, drafts, trash, junk, archive, or "" for the rest
    unseen: int = 0
    total: int = 0


@dataclass(frozen=True)
class Person:
    name: str
    address: str


@dataclass(frozen=True)
class Attachment:
    index: int
    filename: str
    content_type: str
    size: int


@dataclass(frozen=True)
class MessageSummary:
    uid: int
    subject: str
    sender: Person
    date: datetime | None
    snippet: str
    seen: bool
    flagged: bool
    has_attachments: bool


@dataclass(frozen=True)
class Message:
    uid: int
    subject: str
    sender: Person
    to: list[Person]
    cc: list[Person]
    date: datetime | None
    seen: bool
    flagged: bool
    text: str
    #: Already made safe (no scripts, no forms). Remote images are still in it: the screen decides whether to load them.
    html: str
    has_remote_content: bool
    attachments: list[Attachment] = field(default_factory=list)
    message_id: str = ""
    in_reply_to: str = ""


@dataclass(frozen=True)
class Outgoing:
    """A message to send: what the person (or an agent on their behalf, after they approved it) wrote."""

    to: list[str]
    subject: str
    text: str
    cc: list[str] = field(default_factory=list)
    #: The same message as a page (a letterhead, a signature). The plain text goes with it for mail programs that don't show pages.
    html: str = ""
    #: The Message-ID of the mail this answers, so the reply sits in the same thread.
    in_reply_to: str = ""
    references: str = ""


@dataclass(frozen=True)
class Page:
    messages: list[MessageSummary]
    total: int
    #: The uid to ask for next to continue further back in time, or None at the start of the mailbox.
    next_before: int | None
    #: Together these name the version of the folder this page came from; a different value means start again.
    uid_validity: int = 0


@dataclass(frozen=True)
class Download:
    filename: str
    content_type: str
    data: bytes
