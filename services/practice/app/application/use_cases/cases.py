"""Cases, hearings (including writing one up in a single step), counsel engagements and hearing actions."""

from dataclasses import dataclass, field
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.crud import Crud
from app.application.ports import Env
from app.application.use_cases.directory import normalize_counsel
from app.application.use_cases.plans import PlanUseCases, StepDraft
from app.application.use_cases.work import OutcomeContext, task_crud
from app.domain.cases import (
    Engagement,
    Hearing,
    HearingAction,
    LegalCase,
    hearing_case_ids,
    merge_candidates,
    next_case_number,
)
from app.domain.common import EntityRef, Outcome
from app.domain.directory import Counsel, counsel_label
from app.domain.plans import ActionPlan, plan_steps
from app.domain.rules import blank_to_none, required, unique


def normalize_case(c: LegalCase) -> LegalCase:
    return c.model_copy(update={"title": required(c.title, "Title")})


def normalize_hearing(h: Hearing) -> Hearing:
    actions = [a.model_copy(update={"text": a.text.strip()}) for a in (h.actions or []) if a.text.strip()]
    also = [c for c in unique(h.also_case_ids or []) if c != h.case_id]
    authorities = unique(a.strip() for a in (h.authorities or []) if a.strip())
    return h.model_copy(update={
        "purpose": required(h.purpose, "Purpose"),
        "also_case_ids": also or None,
        "bench": blank_to_none(h.bench), "order": blank_to_none(h.order), "submissions": blank_to_none(h.submissions),
        "evidence": blank_to_none(h.evidence), "reflections": blank_to_none(h.reflections),
        "authorities": authorities or None, "actions": actions or None,
    })


def _step_of(a: HearingAction) -> StepDraft:
    """An action with its own date is due then; one without starts from the plan's date."""
    return StepDraft(a.text, due_date=a.due) if a.due else StepDraft(a.text)


def hearing_crud(env: Env) -> Crud[Hearing]:
    return Crud(env, "hearing", Hearing, env.stores.hearing, normalize=normalize_hearing)


# --- cases ------------------------------------------------------------------------------------------------


class CaseUseCases(Crud[LegalCase]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "case", LegalCase, env.stores.case, normalize=normalize_case)
        self._hearings = hearing_crud(env)

    async def _require_counsel(self, user_id: str, counsel_id: str) -> Counsel:
        counsel = await self.env.stores.counsel.get(user_id, counsel_id)
        if counsel is None:
            raise ValidationFailed("That counsel no longer exists")
        return counsel

    async def save(self, user_id, id, draft):
        data = draft.model_dump(exclude={"id"})
        existing = await self.repo.get(user_id, id) if id else None
        if existing is None:
            return await self.open(user_id, {**data, "id": id})
        if data.get("engagement"):
            await self._require_counsel(user_id, data["engagement"]["counsel_id"])
        data["reference"] = existing.reference  # the number is generated once and never retyped
        return await self.update(self.model.model_validate({
            **data, "id": existing.id, "user_id": user_id,
            "created_at": existing.created_at, "updated_at": existing.updated_at,
        }))

    async def open(self, user_id: str, data: dict[str, Any]) -> LegalCase:
        """Opens a case and gives it the next case number for its organization. The year in the number is the
        year it was filed (or today's, when no filing date is given)."""
        year = int(data["filed_on"][:4]) if data.get("filed_on") else self.env.clock.now().year
        existing = [c.reference for c in await self.repo.list(user_id)]
        reference = next_case_number(data["organization"], year, existing)
        if data.get("engagement"):
            await self._require_counsel(user_id, data["engagement"]["counsel_id"])
        opened = await self.create(user_id, {**data, "reference": reference})
        if opened.engagement:
            await self._log_assignment(user_id, opened, opened.engagement.counsel_id)
        return opened

    async def assign_counsel(self, user_id: str, case_id: str, engagement: Engagement | None) -> LegalCase:
        """Briefs outside counsel on a case (or changes the brief), or takes it back with None so we handle it
        ourselves. The change is written to the history of both the case and the counsel."""
        legal_case = await self.require(user_id, case_id, "Case")
        if engagement:
            await self._require_counsel(user_id, engagement.counsel_id)
        previous = legal_case.engagement.counsel_id if legal_case.engagement else None
        if engagement:
            engagement = engagement.model_copy(update={
                "scope": blank_to_none(engagement.scope), "fee": blank_to_none(engagement.fee),
                "notes": blank_to_none(engagement.notes),
            })
        updated = await self.update(legal_case.model_copy(update={"engagement": engagement}))

        case_ref = EntityRef(kind="case", id=case_id)
        if previous and (engagement is None or previous != engagement.counsel_id):
            old = await self.env.stores.counsel.get(user_id, previous)
            if old:
                await self.log.record(user_id, ref=EntityRef(kind="counsel", id=old.id), title=counsel_label(old),
                                      action="unlinked", related=case_ref,
                                      summary=f"No longer briefed on “{legal_case.title}”")
        if engagement:
            await self._log_assignment(user_id, updated, engagement.counsel_id)
        else:
            await self.log.record(user_id, ref=case_ref, title=f"{legal_case.reference} — {legal_case.title}",
                                  action="updated", summary="Taken back — now handled by us")
        return updated

    async def _log_assignment(self, user_id: str, legal_case: LegalCase, counsel_id: str) -> None:
        counsel = await self.env.stores.counsel.get(user_id, counsel_id)
        if counsel is None or legal_case.engagement is None:
            return
        e = legal_case.engagement
        detail = " (pro bono)" if e.pro_bono else f" — {e.fee}" if e.fee else ""
        await self.log.record(user_id, ref=EntityRef(kind="case", id=legal_case.id),
                              title=f"{legal_case.reference} — {legal_case.title}", action="linked",
                              summary=f"Briefed {counsel_label(counsel)}{detail}",
                              related=EntityRef(kind="counsel", id=counsel.id))
        await self.log.record(user_id, ref=EntityRef(kind="counsel", id=counsel.id), title=counsel_label(counsel),
                              action="linked", summary=f"Briefed on “{legal_case.title}”{detail}",
                              related=EntityRef(kind="case", id=legal_case.id))

    async def remove(self, user_id: str, case_id: str) -> None:
        """Deletes a case. Hearings that were only about it go with it; a hearing shared with other cases stays
        on them (another of its cases becomes the main one). Plans are kept but no longer cover the case."""
        s = self.env.stores
        gone: set[str] = set()
        for h in await s.hearing.list(user_id):
            everyone = hearing_case_ids(h)
            if case_id not in everyone:
                continue
            rest = [c for c in everyone if c != case_id]
            if not rest:
                gone.add(h.id)
                await s.hearing.remove(user_id, h.id)
            else:
                await self._hearings.update(h.model_copy(update={"case_id": rest[0], "also_case_ids": rest[1:] or None}))
        now = self.env.now_iso()
        plans = []
        for p in await s.plan.list(user_id):
            if case_id in (p.case_ids or []):
                cases = [c for c in p.case_ids or [] if c != case_id]
                hearings = [h for h in p.hearing_ids or [] if h not in gone]
                plans.append(p.model_copy(update={"case_ids": cases or None, "hearing_ids": hearings or None, "updated_at": now}))
        await s.plan.save_many(plans)
        await super().remove(user_id, case_id)


# --- counsel ----------------------------------------------------------------------------------------------


class CounselUseCases(Crud[Counsel]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "counsel", Counsel, env.stores.counsel, normalize=normalize_counsel)
        self._cases = Crud(env, "case", LegalCase, env.stores.case, normalize=normalize_case)

    async def remove(self, user_id: str, id: str) -> None:
        """Deletes counsel; cases they were briefed on go back to being handled by us, with a note."""
        counsel = await self.repo.get(user_id, id)
        for c in await self.env.stores.case.list(user_id):
            if c.engagement and c.engagement.counsel_id == id:
                await self._cases.update(c.model_copy(update={"engagement": None}))
                await self.log.record(
                    user_id, ref=EntityRef(kind="case", id=c.id), title=f"{c.reference} — {c.title}", action="updated",
                    summary=f"{counsel.name if counsel else 'Counsel'} was removed from the directory — now handled by us")
        await super().remove(user_id, id)


# --- hearings ---------------------------------------------------------------------------------------------


@dataclass
class ActionNote:
    text: str
    id: str | None = None
    due: str | None = None
    task_id: str | None = None


@dataclass
class HearingNotes:
    """What is written up about a hearing beyond the summary; it lives on the hearing itself."""

    bench: str | None = None
    order: str | None = None
    submissions: str | None = None
    authorities: list[str] | None = None
    evidence: str | None = None
    reflections: str | None = None
    #: Work due before the next date. Actions with no task yet become the plan's steps when no steps were typed.
    actions: list[ActionNote] | None = None


@dataclass
class NextListing:
    date: str
    purpose: str
    time: int | None = None


@dataclass
class PlanRequest:
    title: str
    objective: str = ""
    steps: list[StepDraft | str] = field(default_factory=list)
    due_date: str | None = None


@dataclass
class HearingRecord:
    summary: str
    outcome: Outcome
    next: NextListing | None = None
    plan: PlanRequest | None = None
    #: The rest of the write-up. When given it is the whole of it (anything left out is cleared); when omitted
    #: what the hearing already has is kept.
    notes: HearingNotes | None = None
    #: The other cases this hearing was taken up with. When given it is the whole list; when omitted it is left.
    also_case_ids: list[str] | None = None
    #: Case-level changes resulting from the hearing, applied to the main case.
    case_update: dict[str, str] | None = None


@dataclass
class HearingRecordResult:
    hearing: Hearing
    next_hearing: Hearing | None = None
    plan: ActionPlan | None = None


@dataclass
class HearingSaveResult:
    hearing: Hearing
    #: Other cases' listings for the same day that were folded into this one.
    merged: list[Hearing] = field(default_factory=list)


class HearingUseCases(Crud[Hearing]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "hearing", Hearing, env.stores.hearing, normalize=normalize_hearing)
        self._cases = Crud(env, "case", LegalCase, env.stores.case, normalize=normalize_case)
        self.plans = PlanUseCases(env)

    async def _require_cases(self, user_id: str, h: Hearing | dict) -> None:
        ids = hearing_case_ids(h) if isinstance(h, Hearing) else [h["case_id"], *(h.get("also_case_ids") or [])]
        for cid in unique(ids):
            if await self.env.stores.case.get(user_id, cid) is None:
                raise ValidationFailed("That case no longer exists")

    async def create(self, user_id, data):
        await self._require_cases(user_id, data)
        return await super().create(user_id, data)

    async def update(self, entity):
        await self._require_cases(entity.user_id, entity)
        return await super().update(entity)

    async def save_listing(self, user_id, id, draft) -> "HearingSaveResult":
        """Lists a hearing, or saves changes to one, together with the cases it was taken up with. Reports the
        same-day listings that were folded into it."""
        wanted = draft.also_case_ids or []
        saved = await super().save(user_id, id, draft)
        return await self.join_cases(user_id, saved.id, wanted)

    async def save(self, user_id, id, draft):
        return (await self.save_listing(user_id, id, draft)).hearing

    # --- outcome (used by the generic outcome endpoint) ------------------------------------------------

    async def set_outcome(self, user_id: str, ref: EntityRef, recorded: Outcome) -> OutcomeContext:
        h = await self.require(user_id, ref.id, "Hearing")
        await self.update(h.model_copy(update={"outcome": recorded}))
        return OutcomeContext("sb")

    # --- writing a hearing up --------------------------------------------------------------------------

    async def record_hearing(self, user_id: str, hearing_id: str, record: HearingRecord) -> HearingRecordResult:
        """Writes up a hearing in one step: summary and outcome, order and notes, the next date (listed for the same
        cases), an action plan whose steps are the work due, and any change to the main case's stage or status."""
        if record.also_case_ids is not None:
            await self.join_cases(user_id, hearing_id, record.also_case_ids)
        hearing = await self.require(user_id, hearing_id, "Hearing")
        legal_case = await self.env.stores.case.get(user_id, hearing.case_id)
        if legal_case is None:
            raise ValidationFailed("The case this hearing belongs to no longer exists")

        next_hearing = None
        if record.next:
            next_hearing = await self.create(user_id, {
                "case_id": hearing.case_id, "also_case_ids": hearing.also_case_ids, "date": record.next.date,
                "time": record.next.time, "duration": hearing.duration, "purpose": record.next.purpose,
                "forum": hearing.forum,
            })

        notes = record.notes or HearingNotes(
            hearing.bench, hearing.order, hearing.submissions, hearing.authorities, hearing.evidence,
            hearing.reflections,
            [ActionNote(a.text, a.id, a.due, a.task_id) for a in hearing.actions or []] or None,
        )
        actions = [
            HearingAction(id=a.id or self.env.ids.next(), text=a.text, due=a.due, task_id=a.task_id)
            for a in (notes.actions or []) if a.text.strip()
        ]
        pending = [a for a in actions if not a.task_id]

        plan = None
        if record.plan:
            typed = record.plan.steps
            from_actions = not typed and bool(pending)
            plan = await self.plans.create_with_steps(user_id, {
                "title": record.plan.title, "objective": record.plan.objective, "workspace": "sb", "status": "active",
                "due_date": record.plan.due_date or (next_hearing.date if next_hearing else None),
                "origin": {"kind": "hearing", "id": hearing.id}, "case_ids": hearing_case_ids(hearing),
                # The plan is for getting ready for the next date, when there is one.
                "hearing_ids": [next_hearing.id] if next_hearing else None,
            }, [_step_of(a) for a in pending] if from_actions else typed)
            if from_actions:
                # The steps were made from the actions without a task, in the same order: remember which is which.
                steps = plan_steps(plan.id, await self.plans.pool(user_id))
                for a, step in zip(pending, steps, strict=False):
                    a.task_id = step.id

        updated = await self.update(hearing.model_copy(update={
            "summary": record.summary.strip(),
            "outcome": record.outcome.model_copy(update={"recorded_at": self.env.now_iso()}),
            "next_hearing_id": next_hearing.id if next_hearing else hearing.next_hearing_id,
            "bench": notes.bench, "order": notes.order, "submissions": notes.submissions,
            "authorities": notes.authorities, "evidence": notes.evidence, "reflections": notes.reflections,
            "actions": actions,
        }))

        cu = record.case_update or {}
        if cu.get("stage") is not None or cu.get("status") is not None:
            await self._cases.update(legal_case.model_copy(update={
                "stage": cu.get("stage", legal_case.stage), "status": cu.get("status", legal_case.status),
            }))
        return HearingRecordResult(updated, next_hearing, plan)

    # --- several cases, one hearing --------------------------------------------------------------------

    async def join_cases(self, user_id: str, hearing_id: str, case_ids: list[str]) -> HearingSaveResult:
        """Sets which other cases a hearing is heard together with. A case added to it may already have its own
        listing for the same day: that listing is folded into this one (it was the same sitting), and whatever
        pointed at it is moved across. A listing that has been written up on its own is left alone."""
        hearing = await self.require(user_id, hearing_id, "Hearing")
        chosen = [c for c in unique(case_ids) if c != hearing.case_id]
        for cid in chosen:
            if await self.env.stores.case.get(user_id, cid) is None:
                raise ValidationFailed("One of those cases no longer exists")

        merged = merge_candidates(hearing, chosen, await self.repo.list(user_id))
        together: dict[str, None] = dict.fromkeys(chosen)
        for o in merged:
            for cid in hearing_case_ids(o):
                if cid != hearing.case_id:
                    together[cid] = None
        for o in merged:
            await self._absorb(user_id, o, hearing)
        updated = await self.update(hearing.model_copy(update={"also_case_ids": list(together) or None}))

        if merged:
            folded = {c for o in merged for c in hearing_case_ids(o)}
            refs = [c.reference for c in await self.env.stores.case.list(user_id) if c.id in folded]
            await self.log.record(user_id, ref=EntityRef(kind="hearing", id=hearing.id), title=f"Hearing: {hearing.purpose}",
                                  action="updated", summary=f"Folded in the same-day listing for {', '.join(refs)}")
        return HearingSaveResult(updated, merged)

    async def _absorb(self, user_id: str, src: Hearing, into: Hearing) -> None:
        """Moves everything that pointed at `src` over to `into`, then deletes `src`."""
        s, now = self.env.stores, self.env.now_iso()
        target = EntityRef(kind="hearing", id=into.id)

        plans = []
        for p in await s.plan.list(user_id):
            if src.id in (p.hearing_ids or []):
                swapped = unique(into.id if h == src.id else h for h in p.hearing_ids or [])
                plans.append(p.model_copy(update={
                    "hearing_ids": swapped, "case_ids": unique([*(p.case_ids or []), *hearing_case_ids(into)]),
                    "updated_at": now}))
        await s.plan.save_many(plans)

        await s.hearing.save_many([
            h.model_copy(update={"next_hearing_id": into.id, "updated_at": now})
            for h in await s.hearing.list(user_id) if h.next_hearing_id == src.id and h.id != into.id])
        await s.task.save_many([
            t.model_copy(update={"related": target, "updated_at": now})
            for t in await s.task.list(user_id) if t.related and t.related.kind == "hearing" and t.related.id == src.id])
        await s.follow_up.save_many([
            f.model_copy(update={"subject": target, "updated_at": now})
            for f in await s.follow_up.list(user_id) if f.subject and f.subject.kind == "hearing" and f.subject.id == src.id])
        await self.env.files.transfer(EntityRef(kind="hearing", id=src.id), target)
        await super().remove(user_id, src.id)  # also drops links to it and writes the history line


class HearingActionUseCases:
    """Turns the work noted on a hearing into tasks, or into an action plan. Each action then follows its task."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self.hearings = HearingUseCases(env)
        self.tasks = task_crud(env)
        self.plans = self.hearings.plans

    async def _next_of(self, user_id: str, hearing: Hearing) -> Hearing | None:
        nxt = await self.env.stores.hearing.get(user_id, hearing.next_hearing_id) if hearing.next_hearing_id else None
        return nxt if nxt and nxt.outcome.status == "open" else None

    async def to_tasks(self, user_id: str, hearing_id: str) -> Hearing:
        """Makes a task for every action that has none, due by its date (or the next hearing's), filed on the case."""
        hearing = await self.hearings.require(user_id, hearing_id, "Hearing")
        pending = [a for a in hearing.actions or [] if not a.task_id]
        if not pending:
            raise ValidationFailed("Every action already has a task")
        nxt = await self._next_of(user_id, hearing)
        made: dict[str, str] = {}
        for a in pending:
            task = await self.tasks.create(user_id, {
                "title": a.text, "notes": f"From the hearing of {hearing.date}", "workspace": "sb",
                "due_date": a.due or (nxt.date if nxt else None), "estimate": 45,
                "related": {"kind": "case", "id": hearing.case_id},
            })
            made[a.id] = task.id
        return await self.hearings.update(hearing.model_copy(update={
            "actions": [a.model_copy(update={"task_id": made[a.id]}) if a.id in made else a for a in hearing.actions or []],
        }))

    async def to_plan(self, user_id: str, hearing_id: str, title: str | None = None) -> ActionPlan:
        """Starts an action plan from the actions that have no task yet: each becomes a step due by its own date.
        The plan covers every case the hearing concerned, and the next hearing when it is listed."""
        hearing = await self.hearings.require(user_id, hearing_id, "Hearing")
        pending = [a for a in hearing.actions or [] if not a.task_id]
        if not pending:
            raise ValidationFailed("Every action already has a task")
        nxt = await self._next_of(user_id, hearing)
        plan = await self.plans.create_with_steps(user_id, {
            "title": (title or "").strip() or f"Prepare for {nxt.purpose if nxt else 'the next hearing'}",
            "workspace": "sb", "status": "active", "due_date": nxt.date if nxt else None,
            "origin": {"kind": "hearing", "id": hearing.id}, "case_ids": hearing_case_ids(hearing),
            "hearing_ids": [nxt.id] if nxt else None,
        }, [_step_of(a) for a in pending])
        steps = plan_steps(plan.id, await self.plans.pool(user_id))
        made = {a.id: steps[i].id for i, a in enumerate(pending) if i < len(steps)}
        await self.hearings.update(hearing.model_copy(update={
            "actions": [a.model_copy(update={"task_id": made[a.id]}) if a.id in made else a for a in hearing.actions or []],
        }))
        return plan
