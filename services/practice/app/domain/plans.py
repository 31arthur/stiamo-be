"""Action plans: an objective with ordered steps, where a step is a task, a follow-up or an appointment."""

from dataclasses import dataclass, field
from typing import Any, Literal

from app.domain.common import Entity, EntityRef, ISODate, Outcome, Workspace, progress_of
from app.domain.work import FollowUp, Meeting, Task

PlanStatus = Literal["draft", "active", "completed", "abandoned"]
PlanScope = Literal["general", "case", "hearing", "task"]
StepKind = Literal["task", "followUp", "appointment"]


class ActionPlan(Entity):
    """A set of steps towards an objective. Plans can come from a hearing, a meeting or stand alone, and can
    contribute to several goals."""

    title: str
    objective: str = ""
    workspace: Workspace = "personal"
    status: PlanStatus = "draft"
    due_date: ISODate | None = None
    #: What prompted the plan: a hearing, meeting, case or goal.
    origin: EntityRef | None = None
    #: The cases this plan belongs to. One plan can serve several cases: one page, one set of steps.
    case_ids: list[str] | None = None
    #: Narrows the plan to particular hearings of those cases. Empty means the cases as a whole.
    hearing_ids: list[str] | None = None
    #: Set when the plan is only about one task.
    task_id: str | None = None
    #: The plan's page: rich blocks from the block editor, stored as-is.
    doc: list[Any] | None = None


def plan_scope(p: ActionPlan) -> PlanScope:
    """What a plan covers: nothing in particular, whole case(s), particular hearings, or a single task."""
    if p.hearing_ids:
        return "hearing"
    if p.task_id:
        return "task"
    return "case" if p.case_ids else "general"


def covers_case(p: ActionPlan, case_id: str) -> bool:
    return case_id in (p.case_ids or [])


def covers_hearing(p: ActionPlan, hearing_id: str) -> bool:
    return hearing_id in (p.hearing_ids or [])


# --- steps ---------------------------------------------------------------------------------------------------


@dataclass
class StepPool:
    """The records a plan's steps can live in."""

    tasks: list[Task] = field(default_factory=list)
    follow_ups: list[FollowUp] = field(default_factory=list)
    meetings: list[Meeting] = field(default_factory=list)


@dataclass
class PlanStep:
    """One step of a plan, whatever it is stored as. `date` is the step's own deadline."""

    kind: StepKind
    id: str
    title: str
    created_at: str
    outcome: Outcome
    ref: EntityRef
    plan_id: str | None = None
    order: int | None = None
    date: str | None = None
    time: int | None = None


def task_step(t: Task) -> PlanStep:
    return PlanStep("task", t.id, t.title, t.created_at, t.outcome, EntityRef(kind="task", id=t.id), t.plan_id, t.order,
                    t.due_date or (t.slot.date if t.slot else None), t.slot.start if t.slot else None)


def follow_up_step(f: FollowUp) -> PlanStep:
    return PlanStep("followUp", f.id, f.title, f.created_at, f.outcome, EntityRef(kind="followUp", id=f.id), f.plan_id,
                    f.order, f.due.date if f.due else None, f.due.time if f.due else None)


def appointment_step(m: Meeting) -> PlanStep:
    return PlanStep("appointment", m.id, m.title, m.created_at, m.outcome, EntityRef(kind="meeting", id=m.id),
                    m.plan_id, m.order, m.slot.date, m.slot.start)


def _by_order(s: PlanStep):
    return (s.order if s.order is not None else float("inf"), s.created_at)


def plan_steps(plan_id: str, pool: StepPool) -> list[PlanStep]:
    """Every step of a plan in its own order, whether a task, a follow-up or an appointment."""
    steps = [
        *(task_step(t) for t in pool.tasks if t.plan_id == plan_id),
        *(follow_up_step(f) for f in pool.follow_ups if f.plan_id == plan_id),
        *(appointment_step(m) for m in pool.meetings if m.plan_id == plan_id),
    ]
    return sorted(steps, key=_by_order)


def step_by_id(step_id: str, pool: StepPool) -> PlanStep | None:
    for t in pool.tasks:
        if t.id == step_id:
            return task_step(t)
    for f in pool.follow_ups:
        if f.id == step_id:
            return follow_up_step(f)
    for m in pool.meetings:
        if m.id == step_id:
            return appointment_step(m)
    return None


@dataclass
class PlanProgress:
    total: int
    closed: int
    done: int
    #: 0..1, or None when the plan has no countable steps.
    progress: float | None


def plan_progress(plan_id: str, pool: StepPool) -> PlanProgress:
    steps = plan_steps(plan_id, pool)
    return PlanProgress(
        total=len(steps),
        closed=sum(1 for s in steps if s.outcome.status != "open"),
        done=sum(1 for s in steps if s.outcome.status == "done"),
        progress=progress_of([s.outcome for s in steps]),
    )
