from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends

from app.application.use_cases.work import (
    _UNSET,
    AttemptInput,
    FollowUpUseCases,
    RecordOutcome,
    SpawnRequest,
)
from app.domain.chase import Moment
from app.domain.common import DomainModel, EntityRef, ISODate, Minutes, Outcome
from app.domain.work import AttemptResult, FollowUp, FollowUpChannel, Meeting, Task
from app.presentation import deps
from app.presentation.crud_router import crud_router, dump

tasks = crud_router(prefix="/v1/tasks", tag="tasks", model=Task, service_dep=deps.task_service)
meetings = crud_router(prefix="/v1/meetings", tag="meetings", model=Meeting, service_dep=deps.meeting_service)

follow_ups = crud_router(prefix="/v1/follow-ups", tag="follow-ups", model=FollowUp, service_dep=deps.follow_up_service)

extra = APIRouter(tags=["follow-ups", "outcomes"])
FollowUps = Annotated[FollowUpUseCases, Depends(deps.follow_up_service)]


class Local(DomainModel):
    """The caller's local date and minute (the server clock is UTC)."""

    date: ISODate
    minute: Minutes


class NextTry(DomainModel):
    date: ISODate
    time: Minutes | None = None


class AttemptBody(DomainModel):
    result: AttemptResult
    note: str | None = None
    call_back_at: Minutes | None = None
    promised_for: ISODate | None = None
    #: Present with a date to choose the next try; present as null to leave the follow-up undated;
    #: absent to take the suggestion.
    next: NextTry | None = None
    switch_to: FollowUpChannel | None = None
    at: Local | None = None

    def to_input(self) -> AttemptInput:
        nxt: Any = _UNSET
        if "next" in self.model_fields_set:
            nxt = self.next.model_dump() if self.next else None
        return AttemptInput(
            result=self.result, note=self.note, call_back_at=self.call_back_at, promised_for=self.promised_for,
            next=nxt, switch_to=self.switch_to, at=Moment(self.at.date, self.at.minute) if self.at else None,
        )


@extra.post("/v1/follow-ups/{follow_up_id}/attempts", summary="Log a try at reaching someone")
async def log_attempt(follow_up_id: str, body: AttemptBody, principal: deps.CurrentPrincipal, svc: FollowUps):
    r = await svc.log_attempt(principal.user_id, follow_up_id, body.to_input())
    return {
        "followUp": dump(r.follow_up), "before": dump(r.before),
        "suggestion": _suggestion(r.suggestion),
    }


@extra.post("/v1/follow-ups/{follow_up_id}/suggestion", summary="When the next try would be, for a given result")
async def suggest(follow_up_id: str, body: AttemptBody, principal: deps.CurrentPrincipal, svc: FollowUps):
    return {"suggestion": _suggestion(await svc.suggest(principal.user_id, follow_up_id, body.to_input()))}


def _suggestion(s) -> dict[str, Any] | None:
    if s is None:
        return None
    out = asdict(s)
    return {"date": out["date"], "time": out["time"], "reason": out["reason"], "switchChannel": out["switch_channel"]}


class SpawnBody(DomainModel):
    kind: Literal["task", "followUp"]
    title: str
    date: ISODate | None = None
    time: Minutes | None = None


class OutcomeBody(DomainModel):
    ref: EntityRef
    outcome: Outcome
    spawn: SpawnBody | None = None


@extra.post("/v1/outcomes", summary="Record how a task, follow-up, meeting or hearing turned out")
async def record_outcome(body: OutcomeBody, principal: deps.CurrentPrincipal,
                         uc: Annotated[RecordOutcome, Depends(deps.record_outcome)]):
    spawn = SpawnRequest(body.spawn.kind, body.spawn.title, body.spawn.date, body.spawn.time) if body.spawn else None
    result = await uc.execute(principal.user_id, body.ref, body.outcome, spawn)
    return {"spawned": dump(result.spawned) if result.spawned else None}
