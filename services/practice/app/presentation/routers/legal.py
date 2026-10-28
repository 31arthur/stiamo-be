from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.application.use_cases.cases import (
    ActionNote,
    CaseUseCases,
    HearingActionUseCases,
    HearingNotes,
    HearingRecord,
    HearingUseCases,
    NextListing,
    PlanRequest,
)
from app.domain.cases import (
    CaseStatus,
    Engagement,
    Hearing,
    LegalCase,
    diary_of,
    hearings_of,
    next_hearing,
    pending_actions,
    search_diary,
    unrecorded_hearings,
)
from app.domain.common import DomainModel, ISODate, Minutes, Outcome
from app.domain.plans import StepPool
from app.presentation import deps
from app.presentation.crud_router import crud_router, dump
from app.presentation.routers.plans import StepBody

cases = crud_router(prefix="/v1/cases", tag="cases", model=LegalCase, service_dep=deps.case_service)
hearings = crud_router(prefix="/v1/hearings", tag="hearings", model=Hearing, service_dep=deps.hearing_service)

#: Registered before the CRUD routers so `/v1/hearings/diary` isn't read as a hearing id.
views = APIRouter(tags=["hearings", "cases"])
extra = APIRouter(tags=["hearings", "cases"])

Cases = Annotated[CaseUseCases, Depends(deps.case_service)]
Hearings = Annotated[HearingUseCases, Depends(deps.hearing_service)]
Actions = Annotated[HearingActionUseCases, Depends(deps.hearing_action_service)]


# --- read views ----------------------------------------------------------------------------------------------


@views.get("/v1/hearings/diary", summary="The case diary: recorded hearings, newest first")
async def diary(principal: deps.CurrentPrincipal, svc: Hearings, search: str | None = None,
                case_id: Annotated[str | None, Query(alias="caseId")] = None) -> dict[str, Any]:
    items = await svc.repo.list(principal.user_id)
    if case_id:
        items = hearings_of(case_id, items)
    entries = diary_of(items)
    if search:
        entries = search_diary(entries, await svc.env.stores.case.list(principal.user_id), search)
    return {"items": [dump(h) for h in entries]}


@views.get("/v1/hearings/unrecorded", summary="Past hearings nobody has written up yet")
async def unrecorded(principal: deps.CurrentPrincipal, svc: Hearings, today: ISODate | None = None) -> dict[str, Any]:
    day = today or svc.env.clock.now().date().isoformat()
    return {"items": [dump(h) for h in unrecorded_hearings(await svc.repo.list(principal.user_id), day)]}


@views.get("/v1/hearings/{hearing_id}/pending-actions", summary="Actions of a hearing that still need doing")
async def pending(hearing_id: str, principal: deps.CurrentPrincipal, svc: Hearings) -> dict[str, Any]:
    hearing = await svc.require(principal.user_id, hearing_id, "Hearing")
    s = svc.env.stores
    pool = StepPool(await s.task.list(principal.user_id), await s.follow_up.list(principal.user_id),
                    await s.meeting.list(principal.user_id))
    return {"items": [dump(a) for a in pending_actions(hearing, pool)]}


@views.get("/v1/cases/{case_id}/hearings", summary="Every hearing of a case, including shared ones, in date order")
async def case_hearings(case_id: str, principal: deps.CurrentPrincipal, svc: Hearings) -> dict[str, Any]:
    await svc.env.stores.case.get(principal.user_id, case_id)
    return {"items": [dump(h) for h in hearings_of(case_id, await svc.repo.list(principal.user_id))]}


@views.get("/v1/cases/{case_id}/next-hearing", summary="The next unrecorded hearing on or after today")
async def case_next_hearing(case_id: str, principal: deps.CurrentPrincipal, svc: Hearings,
                            today: ISODate | None = None) -> dict[str, Any]:
    day = today or svc.env.clock.now().date().isoformat()
    found = next_hearing(case_id, await svc.repo.list(principal.user_id), day)
    return {"hearing": dump(found) if found else None}


# --- cases ---------------------------------------------------------------------------------------------------


class EngagementBody(DomainModel):
    engagement: Engagement | None = None


@extra.put("/v1/cases/{case_id}/engagement", summary="Brief outside counsel, change the brief, or take it back")
async def set_engagement(case_id: str, body: EngagementBody, principal: deps.CurrentPrincipal, svc: Cases):
    return dump(await svc.assign_counsel(principal.user_id, case_id, body.engagement))


# --- writing a hearing up ---------------------------------------------------------------------------------------


class ActionNoteBody(DomainModel):
    text: str
    id: str | None = None
    due: ISODate | None = None
    task_id: str | None = None


class NotesBody(DomainModel):
    bench: str | None = None
    order: str | None = None
    submissions: str | None = None
    authorities: list[str] | None = None
    evidence: str | None = None
    reflections: str | None = None
    actions: list[ActionNoteBody] | None = None


class NextBody(DomainModel):
    date: ISODate
    purpose: str
    time: Minutes | None = None


class PlanBody(DomainModel):
    title: str
    objective: str = ""
    steps: list[StepBody | str] = []
    due_date: ISODate | None = None


class CaseUpdateBody(DomainModel):
    stage: str | None = None
    status: CaseStatus | None = None


class RecordBody(DomainModel):
    summary: str
    outcome: Outcome
    next: NextBody | None = None
    plan: PlanBody | None = None
    #: Absent: keep what the hearing already has. Present: this is the whole write-up.
    notes: NotesBody | None = None
    #: Absent: leave the other cases as they are. Present: this is the whole list.
    also_case_ids: list[str] | None = None
    case_update: CaseUpdateBody | None = None

    def to_record(self) -> HearingRecord:
        notes = None
        if "notes" in self.model_fields_set and self.notes is not None:
            n = self.notes
            notes = HearingNotes(
                n.bench, n.order, n.submissions, n.authorities, n.evidence, n.reflections,
                [ActionNote(a.text, a.id, a.due, a.task_id) for a in n.actions] if n.actions is not None else None,
            )
        plan = None
        if self.plan:
            plan = PlanRequest(self.plan.title, self.plan.objective,
                               [s if isinstance(s, str) else s.to_draft() for s in self.plan.steps], self.plan.due_date)
        update = self.case_update.model_dump(exclude_none=True) if self.case_update else None
        return HearingRecord(
            summary=self.summary, outcome=self.outcome,
            next=NextListing(self.next.date, self.next.purpose, self.next.time) if self.next else None,
            plan=plan, notes=notes, also_case_ids=self.also_case_ids, case_update=update,
        )


@extra.post("/v1/hearings/{hearing_id}/record", summary="Write a hearing up in one step")
async def record_hearing(hearing_id: str, body: RecordBody, principal: deps.CurrentPrincipal, svc: Hearings):
    result = await svc.record_hearing(principal.user_id, hearing_id, body.to_record())
    return {
        "hearing": dump(result.hearing),
        "nextHearing": dump(result.next_hearing) if result.next_hearing else None,
        "plan": dump(result.plan) if result.plan else None,
    }


class CasesBody(DomainModel):
    case_ids: list[str]


@extra.put("/v1/hearings/{hearing_id}/cases", summary="Set the other cases a hearing is heard together with")
async def join_cases(hearing_id: str, body: CasesBody, principal: deps.CurrentPrincipal, svc: Hearings):
    result = await svc.join_cases(principal.user_id, hearing_id, body.case_ids)
    return {"hearing": dump(result.hearing), "merged": [dump(h) for h in result.merged]}


@extra.post("/v1/hearings/{hearing_id}/actions/tasks", summary="Make a task for every action that has none")
async def actions_to_tasks(hearing_id: str, principal: deps.CurrentPrincipal, svc: Actions):
    return dump(await svc.to_tasks(principal.user_id, hearing_id))


class ActionPlanBody(DomainModel):
    title: str | None = None


@extra.post("/v1/hearings/{hearing_id}/actions/plan", status_code=status.HTTP_201_CREATED,
            summary="Start an action plan from the actions that have no task yet")
async def actions_to_plan(hearing_id: str, principal: deps.CurrentPrincipal, svc: Actions,
                          body: ActionPlanBody | None = None):
    return dump(await svc.to_plan(principal.user_id, hearing_id, body.title if body else None))

