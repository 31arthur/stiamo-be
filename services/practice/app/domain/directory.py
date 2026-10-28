"""People and organisations we deal with: outside counsel, clients and their projects."""

from typing import Literal

from pydantic import Field

from app.domain.common import CaseType, Entity, ISODate

# --- counsel ------------------------------------------------------------------------------------------

CounselKind = Literal["advocate", "firm"]
CounselStatus = Literal["active", "inactive"]


class Counsel(Entity):
    """An outside advocate or law firm we may brief on a case we don't handle ourselves."""

    kind: CounselKind
    name: str
    firm: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    courts: str | None = None
    expertise: list[CaseType] = Field(default_factory=list)
    pro_bono: bool = False
    status: CounselStatus = "active"
    notes: str = ""


def counsel_label(c: Counsel) -> str:
    return f"{c.name} ({c.firm})" if c.kind == "advocate" and c.firm else c.name


# --- clients and projects (FSA) ---------------------------------------------------------------------

ClientKind = Literal["company", "individual"]
ClientStatus = Literal["prospect", "active", "past"]
ProjectStatus = Literal["planned", "active", "on_hold", "completed", "cancelled"]


class Client(Entity):
    kind: ClientKind
    name: str
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    status: ClientStatus = "active"
    notes: str = ""


class Project(Entity):
    title: str
    client_id: str
    status: ProjectStatus = "planned"
    description: str = ""
    start_date: ISODate | None = None
    due_date: ISODate | None = None
