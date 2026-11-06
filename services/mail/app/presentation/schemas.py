from datetime import datetime
from typing import Annotated

from pydantic import Field
from stiamo_common.models import DomainModel, to_iso

from app.domain import mail as d


class PersonOut(DomainModel):
    name: str
    address: str

    @classmethod
    def of(cls, p: d.Person) -> "PersonOut":
        return cls(name=p.name, address=p.address)


class AccountOut(DomainModel):
    id: str
    workspace: str
    label: str
    address: str
    host: str
    port: int
    security: str
    username: str
    error: str
    created_at: str

    @classmethod
    def of(cls, a: d.Account) -> "AccountOut":
        c = a.credentials
        return cls(id=a.id, workspace=a.workspace, label=a.label, address=a.address, host=c.host, port=c.port, security=c.security, username=c.username, error=a.error, created_at=to_iso(a.created_at))


class ConnectIn(DomainModel):
    workspace: str
    label: str = Field("", max_length=80)
    address: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    host: str = Field(max_length=255)
    port: int = 993
    security: str = "ssl"
    username: str = Field("", max_length=320)
    password: str = Field(max_length=500)


class SendIn(DomainModel):
    to: list[Annotated[str, Field(max_length=320, pattern=r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")]] = Field(min_length=1, max_length=10)
    cc: list[Annotated[str, Field(max_length=320, pattern=r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")]] = Field(default_factory=list, max_length=10)
    subject: str = Field(max_length=300, pattern=r"^[^\r\n]*$")
    text: str = Field(max_length=30000)
    html: str = Field("", max_length=200000)
    #: The Message-ID of the mail being answered, so the reply stays in its thread.
    in_reply_to: str = Field("", max_length=998, pattern=r"^[^\r\n]*$")
    references: str = Field("", max_length=4000, pattern=r"^[^\r\n]*$")


class EditIn(DomainModel):
    workspace: str | None = None
    label: str | None = Field(None, max_length=80)
    host: str | None = Field(None, max_length=255)
    port: int | None = None
    security: str | None = None
    username: str | None = Field(None, max_length=320)
    password: str | None = Field(None, max_length=500)


class FolderOut(DomainModel):
    name: str
    label: str
    role: str
    unseen: int
    total: int

    @classmethod
    def of(cls, f: d.Folder) -> "FolderOut":
        return cls(name=f.name, label=f.label, role=f.role, unseen=f.unseen, total=f.total)


def _when(dt: datetime | None) -> str | None:
    return to_iso(dt) if dt else None


class SummaryOut(DomainModel):
    uid: int
    subject: str
    sender: PersonOut
    date: str | None
    snippet: str
    seen: bool
    flagged: bool
    has_attachments: bool

    @classmethod
    def of(cls, m: d.MessageSummary) -> "SummaryOut":
        return cls(uid=m.uid, subject=m.subject, sender=PersonOut.of(m.sender), date=_when(m.date), snippet=m.snippet, seen=m.seen, flagged=m.flagged, has_attachments=m.has_attachments)


class PageOut(DomainModel):
    messages: list[SummaryOut]
    total: int
    next_before: int | None
    uid_validity: int

    @classmethod
    def of(cls, p: d.Page) -> "PageOut":
        return cls(messages=[SummaryOut.of(m) for m in p.messages], total=p.total, next_before=p.next_before, uid_validity=p.uid_validity)


class AttachmentOut(DomainModel):
    index: int
    filename: str
    content_type: str
    size: int


class MessageOut(DomainModel):
    uid: int
    subject: str
    sender: PersonOut
    to: list[PersonOut]
    cc: list[PersonOut]
    date: str | None
    seen: bool
    flagged: bool
    text: str
    html: str
    has_remote_content: bool
    attachments: list[AttachmentOut]
    message_id: str

    @classmethod
    def of(cls, m: d.Message) -> "MessageOut":
        return cls(
            uid=m.uid, subject=m.subject, sender=PersonOut.of(m.sender), to=[PersonOut.of(p) for p in m.to], cc=[PersonOut.of(p) for p in m.cc],
            date=_when(m.date), seen=m.seen, flagged=m.flagged, text=m.text, html=m.html, has_remote_content=m.has_remote_content,
            attachments=[AttachmentOut(index=a.index, filename=a.filename, content_type=a.content_type, size=a.size) for a in m.attachments], message_id=m.message_id,
        )  # fmt: skip


class TrashIn(DomainModel):
    folder: str = Field("INBOX", max_length=300)
    uids: list[int] = Field(min_length=1, max_length=200)


class SummariesIn(DomainModel):
    folder: str = Field("INBOX", max_length=300)
    uids: list[int] = Field(min_length=1, max_length=100)


class MarkIn(DomainModel):
    seen: bool | None = None
    flagged: bool | None = None
