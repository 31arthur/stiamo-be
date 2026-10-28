"""Legal cases and hearings.

A hearing carries its own write-up, so the case diary is a view over recorded hearings, not a second entity.
One hearing can serve several cases heard together: `case_id` is the main one and `also_case_ids` the others.
"""

from typing import Literal

from pydantic import Field

from app.domain.common import CaseType, DomainModel, Duration, Entity, ISODate, Minutes, Outcome
from app.domain.plans import StepPool, step_by_id

OrganizationCode = Literal["NUMW", "TUMWU", "PMLU", "MRC", "TYP", "SA"]
CaseStatus = Literal["active", "on_hold", "disposed", "closed"]

#: The organizations on whose behalf cases are filed. The code prefixes each case number.
ORGANIZATIONS: dict[str, str] = {
    "NUMW": "National Union for Migrant Workers",
    "TUMWU": "Telangana Unorganized and Migrant Workers Union",
    "PMLU": "Palamooru Migrant Labour Union",
    "MRC": "Migrants Rights Council",
    "TYP": "The Youth Project",
    "SA": "Samrat & Associates",
}


class Engagement(DomainModel):
    """Set when a case is briefed to outside counsel. A case with no engagement is handled by us."""

    counsel_id: str
    engaged_on: ISODate | None = None
    #: What they're briefed to do: "Appear at all hearings", "Draft and file only"…
    scope: str | None = None
    pro_bono: bool = False
    #: The agreed fee in words, e.g. "₹25,000 fixed + ₹3,000 per hearing".
    fee: str | None = None
    notes: str | None = None


class LegalCase(Entity):
    #: Our case number, generated when the case is opened: `{ORG}-{YYYY}-{NNN}`. Never typed.
    reference: str = ""
    organization: OrganizationCode
    type: CaseType = "other"
    title: str
    #: The person or group the case is brought for, if more specific than the organization.
    client: str | None = None
    #: The court's own number for the matter, once it has one (e.g. "WP 1290/2026").
    court_number: str | None = None
    court: str | None = None
    engagement: Engagement | None = None
    opposing_party: str | None = None
    practice_area: str | None = None
    #: Procedural stage, e.g. "Evidence", "Arguments".
    stage: str | None = None
    status: CaseStatus = "active"
    #: Running case summary, written and revised over time.
    summary: str = ""
    filed_on: ISODate | None = None


class HearingAction(DomainModel):
    """Something to do as a result of a hearing. It can be turned into a task, which it then follows."""

    id: str
    text: str
    due: ISODate | None = None
    task_id: str | None = None


class Hearing(Entity):
    """A listing before the court — and, once recorded, the case diary entry for that date."""

    case_id: str
    #: Other cases heard together with this one (connected or clubbed matters).
    also_case_ids: list[str] | None = None
    date: ISODate
    time: Minutes | None = None
    duration: Duration = 30
    #: What the hearing is listed for.
    purpose: str
    #: Courtroom / bench.
    forum: str | None = None
    #: What happened, written after the hearing.
    summary: str | None = None
    bench: str | None = None
    #: The order passed and directions given, in a few lines.
    order: str | None = None
    submissions: str | None = None
    #: Judgments and provisions cited.
    authorities: list[str] | None = None
    evidence: str | None = None
    #: Your own read: the bench's leaning, what to do differently, what to watch.
    reflections: str | None = None
    #: Work due before the next date.
    actions: list[HearingAction] | None = None
    #: Done = proceeded as planned; not done = adjourned/not taken up; etc.
    outcome: Outcome = Field(default_factory=Outcome)
    #: The hearing scheduled as a result of this one.
    next_hearing_id: str | None = None


# --- case numbers -----------------------------------------------------------------------------------------


def next_case_number(organization: str, year: int, existing: list[str]) -> str:
    """The next case number for an organization in a year: `{ORG}-{YYYY}-{NNN}`, counting on from the highest
    number already used (so deleting a case never makes a number get reused out of order)."""
    prefix = f"{organization}-{year}-"
    highest = 0
    for ref in existing:
        if ref.startswith(prefix) and ref[len(prefix):].isdigit():
            highest = max(highest, int(ref[len(prefix):]))
    return f"{prefix}{highest + 1:03d}"


# --- hearings ---------------------------------------------------------------------------------------------


def hearing_case_ids(h: Hearing) -> list[str]:
    """Every case a hearing concerns, the main one first."""
    return [h.case_id, *[c for c in (h.also_case_ids or []) if c != h.case_id]]


def _key(h: Hearing) -> str:
    return f"{h.date}T{(h.time or 0):04d}"


def hearings_of(case_id: str, hearings: list[Hearing]) -> list[Hearing]:
    return sorted((h for h in hearings if case_id in hearing_case_ids(h)), key=_key)


def next_hearing(case_id: str, hearings: list[Hearing], today: str) -> Hearing | None:
    """The next hearing on or after `today` that hasn't been recorded yet."""
    return next((h for h in hearings_of(case_id, hearings) if h.date >= today and h.outcome.status == "open"), None)


def unrecorded_hearings(hearings: list[Hearing], today: str) -> list[Hearing]:
    """Past hearings whose result hasn't been written up — these need attention."""
    return [h for h in hearings if h.date < today and h.outcome.status == "open"]


def is_diary_entry(h: Hearing) -> bool:
    """A hearing that has been written up (or has an order or work noted on it) is a diary entry."""
    return h.outcome.status != "open" or bool(h.summary or h.order or h.actions)


def diary_of(hearings: list[Hearing]) -> list[Hearing]:
    """Recorded hearings, newest first."""
    return sorted((h for h in hearings if is_diary_entry(h)), key=_key, reverse=True)


def merge_candidates(hearing: Hearing, case_ids: list[str], hearings: list[Hearing]) -> list[Hearing]:
    """Listings that would be folded into `hearing` if `case_ids` were added to it: another case's listing on
    the same day that hasn't been written up. (One that has been is left alone — it has its own record.)"""
    return [
        o for o in hearings
        if o.id != hearing.id and o.date == hearing.date and o.outcome.status == "open" and not is_diary_entry(o)
        and any(c != hearing.case_id and c in case_ids for c in hearing_case_ids(o))
    ]


def action_closed(a: HearingAction, pool: StepPool) -> bool:
    """True when an action's step has been closed: done, or deliberately dropped."""
    step = step_by_id(a.task_id, pool) if a.task_id else None
    return step is not None and step.outcome.status != "open"


def pending_actions(h: Hearing, pool: StepPool) -> list[HearingAction]:
    """Actions that still need doing: with nothing made for them yet, or whose step is open."""
    return [a for a in (h.actions or []) if not action_closed(a, pool)]


def search_diary(hearings: list[Hearing], cases: list[LegalCase], query: str) -> list[Hearing]:
    """Every word of the query must appear somewhere in the entry (or in the cases it concerns)."""
    words = query.lower().split()
    if not words:
        return hearings
    by_id = {c.id: c for c in cases}

    def text(h: Hearing) -> str:
        parts = [h.purpose, h.bench, h.summary, h.order, h.submissions, h.evidence, h.reflections, h.outcome.comment,
                 *(h.authorities or []), *(a.text for a in h.actions or [])]
        for cid in hearing_case_ids(h):
            if c := by_id.get(cid):
                parts += [c.reference, c.title, c.client]
        return " ".join(p for p in parts if p).lower()

    return [h for h in hearings if all(w in text(h) for w in words)]
