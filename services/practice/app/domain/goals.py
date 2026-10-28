"""Goals: the long view. Progress blends key results and linked plans; health compares progress with time."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import Field

from app.domain.common import DomainModel, Entity, ISODate, Workspace
from app.domain.plans import StepPool, plan_progress

GoalHorizon = Literal["quarter", "half", "year", "multi_year"]
GoalStatus = Literal["active", "achieved", "dropped"]
GoalHealth = Literal["on_track", "at_risk", "off_track", "not_started", "achieved", "dropped"]


class KeyResult(DomainModel):
    """A measurable result that evidences progress on the goal."""

    id: str
    label: str
    start: float
    target: float
    current: float
    unit: str | None = None


class CheckIn(DomainModel):
    """A periodic reflection on the goal. Confidence runs from 1 (won't make it) to 5 (certain)."""

    id: str
    date: ISODate
    confidence: int = Field(ge=1, le=5)
    note: str = ""


class Goal(Entity):
    title: str
    description: str = ""
    workspace: Workspace | None = None
    horizon: GoalHorizon = "quarter"
    start_date: ISODate
    target_date: ISODate
    #: Action plans that move this goal forward. A plan may serve several goals.
    plan_ids: list[str] = Field(default_factory=list)
    key_results: list[KeyResult] = Field(default_factory=list)
    check_ins: list[CheckIn] = Field(default_factory=list)
    status: GoalStatus = "active"


def _clamp01(n: float) -> float:
    return min(1.0, max(0.0, n))


def key_result_progress(kr: KeyResult) -> float:
    if kr.target == kr.start:
        return 1.0 if kr.current >= kr.target else 0.0
    return _clamp01((kr.current - kr.start) / (kr.target - kr.start))


@dataclass
class GoalProgress:
    #: Blended progress 0..1.
    progress: float
    #: Average of linked plans' progress, or None without countable plans.
    plans: float | None
    #: Average key-result progress, or None without key results.
    key_results: float | None
    #: Share of the goal's time window that has passed, 0..1.
    elapsed: float
    health: GoalHealth
    days_left: int


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def goal_progress(goal: Goal, pool: StepPool, today: str) -> GoalProgress:
    """Progress blends key results (outcomes) and linked plans (execution) equally when both exist. Health
    compares progress with time elapsed: you should be roughly as far along as the calendar is."""
    plan_values = [p for p in (plan_progress(pid, pool).progress for pid in goal.plan_ids) if p is not None]
    plans = _avg(plan_values) if plan_values else None
    krs = _avg([key_result_progress(k) for k in goal.key_results]) if goal.key_results else None
    parts = [p for p in (plans, krs) if p is not None]
    progress = _avg(parts) if parts else 0.0

    span = max(1, _days(goal.target_date, goal.start_date))
    elapsed = _clamp01(_days(today, goal.start_date) / span)
    return GoalProgress(progress, plans, krs, elapsed, _health(goal, progress, elapsed), _days(goal.target_date, today))


def _health(goal: Goal, progress: float, elapsed: float) -> GoalHealth:
    if goal.status == "achieved":
        return "achieved"
    if goal.status == "dropped":
        return "dropped"
    if progress == 0 and elapsed < 0.1:
        return "not_started"
    lag = elapsed - progress
    if lag <= 0.1:
        return "on_track"
    if lag <= 0.25:
        return "at_risk"
    return "off_track"
