from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import Field

from app.application.use_cases.plans import _UNSET, GoalUseCases, PlanUseCases, StepDraft
from app.domain.common import DomainModel, EntityRef, ISODate, Minutes
from app.domain.goals import Goal
from app.domain.plans import ActionPlan, StepKind
from app.presentation import deps
from app.presentation.crud_router import crud_router, dump

plans = crud_router(prefix="/v1/plans", tag="plans", model=ActionPlan, service_dep=deps.plan_service)
goals = crud_router(prefix="/v1/goals", tag="goals", model=Goal, service_dep=deps.goal_service)

extra = APIRouter(tags=["plans", "goals"])
Plans = Annotated[PlanUseCases, Depends(deps.plan_service)]
Goals = Annotated[GoalUseCases, Depends(deps.goal_service)]


def _camel(d: dict[str, Any]) -> dict[str, Any]:
    from pydantic.alias_generators import to_camel

    return {to_camel(k): v for k, v in d.items()}


def _step(s) -> dict[str, Any]:
    out = {"kind": s.kind, "id": s.id, "title": s.title, "createdAt": s.created_at, "ref": dump(s.ref),
           "outcome": dump(s.outcome)}
    for key, value in (("planId", s.plan_id), ("order", s.order), ("date", s.date), ("time", s.time)):
        if value is not None:
            out[key] = value
    return out


# --- steps ---------------------------------------------------------------------------------------------------


class StepBody(DomainModel):
    title: str
    kind: StepKind = "task"
    #: Absent: starts from the plan's day. Null: deliberately no day.
    due_date: ISODate | None = None
    time: Minutes | None = None
    notes: str = ""

    def to_draft(self) -> StepDraft:
        due = self.due_date if "due_date" in self.model_fields_set else _UNSET
        return StepDraft(self.title, self.kind, due, self.time, self.notes)


class PlanWithSteps(DomainModel):
    plan: dict[str, Any]
    steps: list[StepBody | str] = []
    goal_ids: list[str] = []


@extra.post("/v1/plans/with-steps", status_code=status.HTTP_201_CREATED, summary="Create a plan together with its steps")
async def create_with_steps(body: PlanWithSteps, principal: deps.CurrentPrincipal, svc: Plans):
    from app.domain.common import draft_of

    data = draft_of(ActionPlan).model_validate(body.plan).model_dump()
    steps = [s if isinstance(s, str) else s.to_draft() for s in body.steps]
    return dump(await svc.create_with_steps(principal.user_id, data, steps, body.goal_ids))


@extra.get("/v1/plans/{plan_id}/steps", summary="A plan's steps, in order, whatever they are stored as")
async def steps(plan_id: str, principal: deps.CurrentPrincipal, svc: Plans):
    return {"items": [_step(s) for s in await svc.steps(principal.user_id, plan_id)]}


@extra.get("/v1/plans/{plan_id}/progress", summary="How far along a plan is")
async def plan_progress(plan_id: str, principal: deps.CurrentPrincipal, svc: Plans):
    return _camel(asdict(await svc.progress(principal.user_id, plan_id)))


@extra.post("/v1/plans/{plan_id}/steps", status_code=status.HTTP_201_CREATED, summary="Add a step at the end")
async def add_step(plan_id: str, body: StepBody, principal: deps.CurrentPrincipal, svc: Plans):
    return dump(await svc.add_step(principal.user_id, plan_id, body.to_draft()))


class OrderBody(DomainModel):
    ordered_ids: list[str]


@extra.put("/v1/plans/{plan_id}/steps/order", status_code=status.HTTP_204_NO_CONTENT, summary="Reorder the steps")
async def reorder(plan_id: str, body: OrderBody, principal: deps.CurrentPrincipal, svc: Plans):
    await svc.reorder_steps(principal.user_id, plan_id, body.ordered_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class StepDateBody(DomainModel):
    date: ISODate | None = None


@extra.put("/v1/steps/{kind}/{step_id}/date", status_code=status.HTTP_204_NO_CONTENT, summary="Move a step to another day")
async def set_step_date(kind: Literal["task", "followUp", "meeting"], step_id: str, body: StepDateBody,
                        principal: deps.CurrentPrincipal, svc: Plans):
    await svc.set_step_date(principal.user_id, EntityRef(kind=kind, id=step_id), body.date)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ConvertBody(DomainModel):
    to: StepKind


@extra.post("/v1/steps/{kind}/{step_id}/convert", summary="Turn a step into another kind, keeping its id")
async def convert_step(kind: Literal["task", "followUp", "meeting"], step_id: str, body: ConvertBody,
                       principal: deps.CurrentPrincipal, svc: Plans):
    return dump(await svc.convert_step(principal.user_id, EntityRef(kind=kind, id=step_id), body.to))


# --- plan changes --------------------------------------------------------------------------------------------


class DueDateBody(DomainModel):
    date: ISODate | None = None


@extra.put("/v1/plans/{plan_id}/due-date", summary="Change the plan's day; open steps on the old day move with it")
async def set_due_date(plan_id: str, body: DueDateBody, principal: deps.CurrentPrincipal, svc: Plans):
    plan, moved = await svc.set_due_date(principal.user_id, plan_id, body.date)
    return {"plan": dump(plan), "moved": moved}


@extra.post("/v1/plans/{plan_id}/cover", summary="Make the plan apply to a case, hearing or task")
async def cover(plan_id: str, body: EntityRef, principal: deps.CurrentPrincipal, svc: Plans):
    return dump(await svc.cover(principal.user_id, plan_id, body))


@extra.post("/v1/plans/{plan_id}/uncover", summary="Stop the plan applying to a case, hearing or task")
async def uncover(plan_id: str, body: EntityRef, principal: deps.CurrentPrincipal, svc: Plans):
    return dump(await svc.uncover(principal.user_id, plan_id, body))


@extra.post("/v1/plans/{plan_id}/duplicate", status_code=status.HTTP_201_CREATED, summary="Copy a plan, steps reset to open")
async def duplicate(plan_id: str, principal: deps.CurrentPrincipal, svc: Plans):
    return dump(await svc.duplicate(principal.user_id, plan_id))


class DocBody(DomainModel):
    doc: list[Any]
    first_edit_of_session: bool = False


@extra.put("/v1/plans/{plan_id}/doc", status_code=status.HTTP_204_NO_CONTENT, summary="Save the plan's page")
async def save_doc(plan_id: str, body: DocBody, principal: deps.CurrentPrincipal, svc: Plans):
    await svc.save_doc(principal.user_id, plan_id, body.doc, body.first_edit_of_session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@extra.put("/v1/plans/{plan_id}/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Contribute to a goal")
async def link_goal(plan_id: str, goal_id: str, principal: deps.CurrentPrincipal, svc: Plans):
    await svc.link(principal.user_id, plan_id, goal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@extra.delete("/v1/plans/{plan_id}/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Stop contributing to a goal")
async def unlink_goal(plan_id: str, goal_id: str, principal: deps.CurrentPrincipal, svc: Plans):
    await svc.unlink(principal.user_id, plan_id, goal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- goals ---------------------------------------------------------------------------------------------------


@extra.get("/v1/goals/{goal_id}/progress", summary="Progress and health of a goal")
async def goal_progress_(goal_id: str, principal: deps.CurrentPrincipal, svc: Goals, today: ISODate | None = None):
    return _camel(asdict(await svc.progress(principal.user_id, goal_id, today)))


class CheckInBody(DomainModel):
    date: ISODate
    confidence: Annotated[int, Field(ge=1, le=5)]
    note: str = ""


@extra.post("/v1/goals/{goal_id}/check-ins", status_code=status.HTTP_201_CREATED, summary="Add a check-in")
async def add_check_in(goal_id: str, body: CheckInBody, principal: deps.CurrentPrincipal, svc: Goals):
    return dump(await svc.check_in(principal.user_id, goal_id, body.date, body.confidence, body.note))


class KeyResultBody(DomainModel):
    current: float


@extra.put("/v1/goals/{goal_id}/key-results/{key_result_id}", summary="Update a key result's current value")
async def set_key_result(goal_id: str, key_result_id: str, body: KeyResultBody, principal: deps.CurrentPrincipal,
                         svc: Goals):
    return dump(await svc.set_key_result(principal.user_id, goal_id, key_result_id, body.current))
