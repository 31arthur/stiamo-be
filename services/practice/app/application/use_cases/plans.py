"""Action plans (steps, coverage, conversion, duplication) and goals (progress, check-ins)."""

from dataclasses import dataclass
from typing import Any

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.crud import Crud
from app.application.ports import Env
from app.application.use_cases.work import FollowUpUseCases, MeetingUseCases, task_crud
from app.domain.common import EntityRef, Outcome
from app.domain.goals import CheckIn, Goal, GoalProgress, goal_progress
from app.domain.plans import ActionPlan, PlanProgress, PlanStep, StepKind, StepPool, plan_progress, plan_steps
from app.domain.rules import required, unique
from app.domain.work import default_confirmations

_UNSET: Any = object()
_DEFAULT_START = 10 * 60  # an appointment with no time given is booked for the morning
_APPOINTMENT_LENGTH = 30


@dataclass
class StepDraft:
    """A step to create: what it is (a task unless said otherwise), when it is due, and optionally at what time."""

    title: str
    kind: StepKind = "task"
    #: Its own day. Left out, it starts from the plan's; None is deliberately no day.
    due_date: Any = _UNSET
    time: int | None = None
    notes: str = ""


@dataclass
class _Snapshot:
    """A step in a form every kind can be built from, so one can be turned into another."""

    kind: StepKind
    title: str
    notes: str
    workspace: str
    plan_id: str | None
    order: int | None
    date: str | None
    time: int | None
    duration: int
    contact: str | None
    about: EntityRef | None
    outcome: Outcome


def _ref(ref: EntityRef | None) -> dict | None:
    return ref.model_dump() if ref else None


# --- goals ------------------------------------------------------------------------------------------------


def normalize_goal(g: Goal) -> Goal:
    if g.target_date <= g.start_date:
        raise ValidationFailed("Target date must be after the start date")
    for kr in g.key_results:
        if not kr.label.strip():
            raise ValidationFailed("Each key result needs a label")
        if kr.target == kr.start:
            raise ValidationFailed(f"“{kr.label}” needs a target different from its starting value")
    return g.model_copy(update={"title": required(g.title, "Title"), "plan_ids": unique(g.plan_ids)})


class GoalUseCases(Crud[Goal]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "goal", Goal, env.stores.goal, normalize=normalize_goal)

    async def _require_plans(self, user_id: str, plan_ids: list[str]) -> None:
        for pid in plan_ids:
            if await self.env.stores.plan.get(user_id, pid) is None:
                raise ValidationFailed("One of the linked action plans no longer exists")

    async def create(self, user_id, data):
        await self._require_plans(user_id, data.get("plan_ids") or [])
        return await super().create(user_id, data)

    async def update(self, entity):
        await self._require_plans(entity.user_id, entity.plan_ids)
        return await super().update(entity)

    async def pool(self, user_id: str) -> StepPool:
        s = self.env.stores
        return StepPool(await s.task.list(user_id), await s.follow_up.list(user_id), await s.meeting.list(user_id))

    async def progress(self, user_id: str, id: str, today: str | None = None) -> GoalProgress:
        goal = await self.require(user_id, id, "Goal")
        return goal_progress(goal, await self.pool(user_id), today or self.env.clock.now().date().isoformat())

    async def check_in(self, user_id: str, id: str, date: str, confidence: int, note: str) -> Goal:
        goal = await self.require(user_id, id, "Goal")
        entry = CheckIn(id=self.env.ids.next(), date=date, confidence=confidence, note=note.strip())
        return await self.update(goal.model_copy(update={"check_ins": [*goal.check_ins, entry]}))

    async def set_key_result(self, user_id: str, id: str, key_result_id: str, current: float) -> Goal:
        goal = await self.require(user_id, id, "Goal")
        if not any(k.id == key_result_id for k in goal.key_results):
            raise NotFound("Key result not found.")
        results = [k.model_copy(update={"current": current}) if k.id == key_result_id else k for k in goal.key_results]
        return await self.update(goal.model_copy(update={"key_results": results}))


# --- plans ------------------------------------------------------------------------------------------------


def normalize_plan(p: ActionPlan) -> ActionPlan:
    return p.model_copy(update={
        "title": required(p.title, "Title"),
        "objective": (p.objective or "").strip(),
        "case_ids": unique(p.case_ids or []) or None,
        "hearing_ids": unique(p.hearing_ids or []) or None,
    })


class PlanUseCases(Crud[ActionPlan]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "plan", ActionPlan, env.stores.plan, normalize=normalize_plan)
        self.tasks = task_crud(env)
        self.follow_ups = FollowUpUseCases(env)
        self.meetings = MeetingUseCases(env)
        self.goals = GoalUseCases(env)

    async def _require_coverage(self, user_id: str, case_ids, hearing_ids) -> None:
        for cid in case_ids or []:
            if await self.env.stores.case.get(user_id, cid) is None:
                raise ValidationFailed("One of the cases this plan covers no longer exists")
        for hid in hearing_ids or []:
            if await self.env.stores.hearing.get(user_id, hid) is None:
                raise ValidationFailed("One of the hearings this plan covers no longer exists")

    async def create(self, user_id, data):
        await self._require_coverage(user_id, data.get("case_ids"), data.get("hearing_ids"))
        return await super().create(user_id, data)

    async def update(self, entity):
        await self._require_coverage(entity.user_id, entity.case_ids, entity.hearing_ids)
        return await super().update(entity)

    # --- reads ---------------------------------------------------------------------------------------------

    async def pool(self, user_id: str) -> StepPool:
        return await self.goals.pool(user_id)

    async def steps(self, user_id: str, plan_id: str) -> list[PlanStep]:
        await self.require(user_id, plan_id, "Action plan")
        return plan_steps(plan_id, await self.pool(user_id))

    async def progress(self, user_id: str, plan_id: str) -> PlanProgress:
        await self.require(user_id, plan_id, "Action plan")
        return plan_progress(plan_id, await self.pool(user_id))

    # --- creating ------------------------------------------------------------------------------------------

    async def create_with_steps(self, user_id: str, data: dict, steps: list[StepDraft | str],
                                goal_ids: list[str] | None = None) -> ActionPlan:
        plan = await self.create(user_id, data)
        order = 0
        for step in steps:
            draft = StepDraft(title=step) if isinstance(step, str) else step
            title = draft.title.strip()
            if title:
                await self._create_step(user_id, plan, StepDraft(title, draft.kind, draft.due_date, draft.time, draft.notes), order)
                order += 1
        for goal_id in goal_ids or []:
            await self.link(user_id, plan.id, goal_id)
        return plan

    async def add_step(self, user_id: str, plan_id: str, step: StepDraft | str) -> EntityRef:
        """Adds a step at the end. It has its own due date: the one given, else the plan's to start from."""
        plan = await self.require(user_id, plan_id, "Action plan")
        draft = StepDraft(title=step) if isinstance(step, str) else step
        title = draft.title.strip()
        if not title:
            raise ValidationFailed("A step needs a title")
        existing = plan_steps(plan_id, await self.pool(user_id))
        next_order = max((s.order or 0 for s in existing), default=-1) + 1 if existing else 0
        return await self._create_step(user_id, plan, StepDraft(title, draft.kind, draft.due_date, draft.time, draft.notes), next_order)

    async def duplicate(self, user_id: str, plan_id: str) -> ActionPlan:
        """An independent copy: the same page and objective, every step reset to open, covering nothing."""
        plan = await self.require(user_id, plan_id, "Action plan")
        copy = await self.create(user_id, {
            "title": f"{plan.title} (copy)", "objective": plan.objective, "workspace": plan.workspace,
            "status": "draft", "doc": plan.doc,
        })
        pool = await self.pool(user_id)
        order = 0
        for step in plan_steps(plan_id, pool):
            task = next((t for t in pool.tasks if t.id == step.id), None) if step.kind == "task" else None
            if task:
                await self.tasks.create(user_id, {
                    "title": task.title, "notes": task.notes, "workspace": task.workspace, "priority": task.priority,
                    "estimate": task.estimate, "plan_id": copy.id, "order": order,
                })
            else:
                await self._create_step(user_id, copy, StepDraft(step.title, step.kind), order)
            order += 1
        return copy

    # --- changing ------------------------------------------------------------------------------------------

    async def set_due_date(self, user_id: str, plan_id: str, date: str | None) -> tuple[ActionPlan, int]:
        """Changes the plan's due date. Open steps that were still on the old date — the ones that simply took
        the plan's — move with it; steps with a day of their own stay where they are."""
        plan = await self.require(user_id, plan_id, "Action plan")
        before = plan.due_date
        saved = await self.update(plan.model_copy(update={"due_date": date}))
        moved = 0
        if before and date and before != date:
            for step in plan_steps(plan_id, await self.pool(user_id)):
                if step.outcome.status == "open" and step.date == before:
                    await self.set_step_date(user_id, step.ref, date)
                    moved += 1
        return saved, moved

    async def set_step_date(self, user_id: str, ref: EntityRef, date: str | None) -> None:
        """Moves a step to another day. A task's planned slot moves with it; an appointment keeps its time."""
        if ref.kind == "task":
            t = await self.tasks.require(user_id, ref.id, "Task")
            slot = t.slot.model_copy(update={"date": date}) if t.slot and date else t.slot
            await self.tasks.update(t.model_copy(update={"due_date": date, "slot": slot}))
        elif ref.kind == "followUp":
            f = await self.follow_ups.require(user_id, ref.id, "Follow-up")
            due = {"date": date, "time": f.due.time if f.due else None} if date else None
            await self.follow_ups.update(type(f).model_validate({**f.model_dump(), "due": due}))
        elif ref.kind == "meeting":
            m = await self.meetings.require(user_id, ref.id, "Appointment")
            if not date:
                raise ValidationFailed("An appointment needs a day")
            await self.meetings.update(m.model_copy(update={"slot": m.slot.model_copy(update={"date": date})}))
        else:
            raise ValidationFailed("Only tasks, follow-ups and appointments are steps")

    async def convert_step(self, user_id: str, from_ref: EntityRef, to: StepKind) -> EntityRef:
        """Turns a step into another kind. The record keeps its id, plan, place, due day, notes and outcome, so
        whatever points at it (a hearing's action, a hand-made link) still does."""
        snap = await self._snapshot(user_id, from_ref)
        if snap.kind == to:
            return from_ref
        if from_ref.kind == "task" and any(p.task_id == from_ref.id for p in await self.repo.list(user_id)):
            raise ValidationFailed("An action plan is about this task — change that first")
        made = await self._build(user_id, to, snap, from_ref.id)
        for link in await self.env.stores.link.touching(user_id, from_ref):
            swap = lambda r: made if r.same_as(from_ref) else r  # noqa: E731
            await self.env.stores.link.save(link.model_copy(update={"a": swap(link.a), "b": swap(link.b)}))
        if from_ref.kind == "task":
            await self.tasks.remove(user_id, from_ref.id)
        elif from_ref.kind == "followUp":
            await self.follow_ups.remove(user_id, from_ref.id)
        else:
            await self.meetings.remove(user_id, from_ref.id)
        return made

    async def reorder_steps(self, user_id: str, plan_id: str, ordered_ids: list[str]) -> None:
        """Persists a new step order, across tasks, follow-ups and appointments alike."""
        await self.require(user_id, plan_id, "Action plan")
        position = {id_: i for i, id_ in enumerate(ordered_ids)}
        pool, now, s = await self.pool(user_id), self.env.now_iso(), self.env.stores

        def place(items):
            return [x.model_copy(update={"order": position[x.id], "updated_at": now})
                    for x in items if x.plan_id == plan_id and x.id in position]

        await s.task.save_many(place(pool.tasks))
        await s.follow_up.save_many(place(pool.follow_ups))
        await s.meeting.save_many(place(pool.meetings))

    async def save_doc(self, user_id: str, plan_id: str, doc: list[Any], first_edit_of_session: bool) -> None:
        """Saves the plan's page. The first edit of a session writes one "page edited" line to the history;
        autosaves after that stay quiet so the thread isn't flooded."""
        plan = await self.require(user_id, plan_id, "Action plan")
        await self.repo.save(plan.model_copy(update={"doc": doc, "updated_at": self.env.now_iso()}))
        if first_edit_of_session:
            await self.log.record(user_id, ref=EntityRef(kind="plan", id=plan_id), title=plan.title, action="updated",
                                  summary="Edited the plan page")

    # --- coverage ------------------------------------------------------------------------------------------

    async def cover(self, user_id: str, plan_id: str, target: EntityRef) -> ActionPlan:
        """Makes the plan apply to one more case, hearing or task. A plan can cover several cases and hearings at
        once and stays one page with one set of steps. A hearing brings its case along; a task is exclusive."""
        plan = await self.require(user_id, plan_id, "Action plan")
        case_ids, hearing_ids = plan.case_ids or [], plan.hearing_ids or []
        if target.kind == "case":
            if not await self._exists(user_id, "case", target.id):
                raise ValidationFailed("That case no longer exists")
            return await self._cover(plan, case_ids=[*case_ids, target.id], hearing_ids=hearing_ids, task_id=None, workspace="sb")
        if target.kind == "hearing":
            hearing = await self._load(user_id, "hearing", target.id)
            if hearing is None:
                raise ValidationFailed("That hearing no longer exists")
            return await self._cover(plan, case_ids=[*case_ids, *_hearing_cases(hearing)],
                                     hearing_ids=[*hearing_ids, hearing.id], task_id=None, workspace="sb")
        if target.kind == "task":
            task = await self.env.stores.task.get(user_id, target.id)
            if task is None:
                raise ValidationFailed("That task no longer exists")
            if task.plan_id == plan_id:
                raise ValidationFailed("A plan can’t be about one of its own steps")
            case_id = task.related.id if task.related and task.related.kind == "case" else None
            return await self._cover(plan, case_ids=[case_id] if case_id else [], hearing_ids=[], task_id=task.id,
                                     workspace=task.workspace)
        raise ValidationFailed("A plan can cover cases, hearings or a task")

    async def uncover(self, user_id: str, plan_id: str, target: EntityRef) -> ActionPlan:
        """Stops the plan applying to a case, hearing or its task. Dropping a case also drops that case's hearings;
        dropping a hearing leaves the plan on the case."""
        plan = await self.require(user_id, plan_id, "Action plan")
        case_ids, hearing_ids = plan.case_ids or [], plan.hearing_ids or []
        if target.kind == "task":
            return await self._cover(plan, case_ids=case_ids, hearing_ids=hearing_ids, task_id=None)
        if target.kind == "hearing":
            return await self._cover(plan, case_ids=case_ids, hearing_ids=[h for h in hearing_ids if h != target.id])
        if target.kind == "case":
            remaining = [c for c in case_ids if c != target.id]
            hearings = getattr(self.env.stores, "hearing", None)
            dropped: set[str] = set()
            if hearings is not None:
                # A hearing shared with another case the plan still covers stays on the plan.
                for h in await hearings.list(user_id):
                    cases = _hearing_cases(h)
                    if target.id in cases and not any(c in remaining for c in cases):
                        dropped.add(h.id)
            return await self._cover(plan, case_ids=remaining, hearing_ids=[h for h in hearing_ids if h not in dropped])
        return plan

    async def _cover(self, plan: ActionPlan, *, case_ids, hearing_ids, task_id=_UNSET, workspace=None) -> ActionPlan:
        change: dict[str, Any] = {"case_ids": unique(case_ids) or None, "hearing_ids": unique(hearing_ids) or None}
        if task_id is not _UNSET:
            change["task_id"] = task_id
        if workspace:
            change["workspace"] = workspace
        return await self.update(plan.model_copy(update=change))

    async def _exists(self, user_id: str, kind: str, id: str) -> bool:
        return await self._load(user_id, kind, id) is not None

    async def _load(self, user_id: str, kind: str, id: str):
        store = getattr(self.env.stores, kind, None)
        return await store.get(user_id, id) if store is not None else None

    # --- goals ---------------------------------------------------------------------------------------------

    async def link(self, user_id: str, plan_id: str, goal_id: str) -> None:
        goal = await self.goals.require(user_id, goal_id, "Goal")
        await self.require(user_id, plan_id, "Action plan")
        if plan_id not in goal.plan_ids:
            await self.goals.update(goal.model_copy(update={"plan_ids": [*goal.plan_ids, plan_id]}))

    async def unlink(self, user_id: str, plan_id: str, goal_id: str) -> None:
        goal = await self.goals.get(user_id, goal_id)
        if goal is not None:
            await self.goals.update(goal.model_copy(update={"plan_ids": [p for p in goal.plan_ids if p != plan_id]}))

    # --- deleting ------------------------------------------------------------------------------------------

    async def remove(self, user_id: str, plan_id: str) -> None:
        """Deletes a plan; its steps stay as standalone records, and goal and meeting links are removed."""
        s, now = self.env.stores, self.env.now_iso()
        pool = await self.pool(user_id)

        def free(items):
            return [x.model_copy(update={"plan_id": None, "order": None, "updated_at": now})
                    for x in items if x.plan_id == plan_id]

        await s.task.save_many(free(pool.tasks))
        await s.follow_up.save_many(free(pool.follow_ups))
        meetings = []
        for m in pool.meetings:
            change = {}
            if m.plan_id == plan_id:
                change.update(plan_id=None, order=None)
            if m.prep_plan_id == plan_id:
                change["prep_plan_id"] = None
            if change:
                meetings.append(m.model_copy(update={**change, "updated_at": now}))
        await s.meeting.save_many(meetings)
        await super().remove(user_id, plan_id)  # goal links go with the plan (cascade)

    # --- building steps ------------------------------------------------------------------------------------

    async def _create_step(self, user_id: str, plan: ActionPlan, d: StepDraft, order: int) -> EntityRef:
        date = plan.due_date if d.due_date is _UNSET else d.due_date
        return await self._build(user_id, d.kind, _Snapshot(
            kind=d.kind, title=d.title, notes=d.notes, workspace=plan.workspace, plan_id=plan.id, order=order,
            date=date, time=d.time, duration=0, contact=None, about=plan.origin, outcome=Outcome(),
        ))

    async def _build(self, user_id: str, kind: StepKind, s: _Snapshot, id: str | None = None) -> EntityRef:
        if kind == "task":
            slot = {"date": s.date, "start": s.time, "duration": s.duration or 45} if s.date and s.time is not None else None
            t = await self.tasks.create(user_id, {
                "id": id, "title": s.title, "notes": s.notes, "workspace": s.workspace, "priority": "normal",
                "due_date": s.date, "slot": slot, "estimate": s.duration or 45, "plan_id": s.plan_id,
                "order": s.order, "related": _ref(s.about), "outcome": s.outcome.model_dump(),
            })
            return EntityRef(kind="task", id=t.id)
        if kind == "followUp":
            f = await self.follow_ups.create(user_id, {
                "id": id, "title": s.title, "channel": "call", "workspace": s.workspace, "contact": s.contact,
                "due": {"date": s.date, "time": s.time} if s.date else None, "duration": s.duration or 15,
                "subject": _ref(s.about), "notes": s.notes or None, "plan_id": s.plan_id, "order": s.order,
                "outcome": s.outcome.model_dump(),
            })
            return EntityRef(kind="followUp", id=f.id)
        m = await self.meetings.create(user_id, {
            "id": id, "kind": "appointment", "title": s.title, "workspace": s.workspace,
            "slot": {"date": s.date or self.env.clock.now().date().isoformat(),
                     "start": s.time if s.time is not None else _DEFAULT_START,
                     "duration": s.duration or _APPOINTMENT_LENGTH},
            "mode": "in_person", "counterpart": s.contact, "contact": s.contact, "attendees": [],
            "purpose": s.notes or None, "agenda": [], "confirmations": default_confirmations("appointment"),
            "plan_id": s.plan_id, "order": s.order, "related": _ref(s.about), "outcome": s.outcome.model_dump(),
        })
        return EntityRef(kind="meeting", id=m.id)

    async def _snapshot(self, user_id: str, ref: EntityRef) -> _Snapshot:
        if ref.kind == "task":
            t = await self.tasks.require(user_id, ref.id, "Task")
            return _Snapshot("task", t.title, t.notes, t.workspace, t.plan_id, t.order,
                             t.due_date or (t.slot.date if t.slot else None), t.slot.start if t.slot else None,
                             t.slot.duration if t.slot else t.estimate, None, t.related, t.outcome)
        if ref.kind == "followUp":
            f = await self.follow_ups.require(user_id, ref.id, "Follow-up")
            return _Snapshot("followUp", f.title, f.notes or "", f.workspace, f.plan_id, f.order,
                             f.due.date if f.due else None, f.due.time if f.due else None, f.duration, f.contact,
                             f.subject, f.outcome)
        if ref.kind == "meeting":
            m = await self.meetings.require(user_id, ref.id, "Appointment")
            return _Snapshot("appointment", m.title, m.purpose or "", m.workspace, m.plan_id, m.order, m.slot.date,
                             m.slot.start, m.slot.duration, m.contact or m.counterpart, m.related, m.outcome)
        raise ValidationFailed("Only tasks, follow-ups and appointments are steps")


def _hearing_cases(h) -> list[str]:
    """Every case a hearing concerns, the main one first."""
    return [h.case_id, *[c for c in (getattr(h, "also_case_ids", None) or []) if c != h.case_id]]
