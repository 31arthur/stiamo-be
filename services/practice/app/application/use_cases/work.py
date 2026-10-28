"""Tasks, follow-ups, meetings, and recording how any of them turned out."""

from dataclasses import dataclass
from typing import Any

from stiamo_common.errors import NotFound

from app.application.crud import Crud
from app.application.ports import Env
from app.domain.chase import Hints, Moment, RetrySuggestion, best_minute, suggest_retry
from app.domain.common import EntityRef, Outcome
from app.domain.confirmations import sync_confirmations
from app.domain.rules import blank_to_none, required
from app.domain.work import Attempt, AttemptResult, FollowUp, FollowUpChannel, Meeting, Task

_UNSET: Any = object()

# --- normalisers ------------------------------------------------------------------------------------------


def normalize_task(t: Task) -> Task:
    return t.model_copy(update={"title": required(t.title, "Title"), "notes": (t.notes or "").strip()})


def normalize_follow_up(f: FollowUp) -> FollowUp:
    return f.model_copy(update={"title": required(f.title, "Title")})


def normalize_meeting(m: Meeting) -> Meeting:
    return m.model_copy(update={
        "title": required(m.title, "Title"),
        "attendees": [a.strip() for a in m.attendees if a.strip()],
        "agenda": [a for a in m.agenda if a.topic.strip()],
    })


# --- tasks ------------------------------------------------------------------------------------------------


def task_crud(env: Env) -> Crud[Task]:
    return Crud(env, "task", Task, env.stores.task, normalize=normalize_task)


# --- follow-ups -------------------------------------------------------------------------------------------


@dataclass
class AttemptInput:
    result: AttemptResult
    note: str | None = None
    #: For "said call later": the time they asked for.
    call_back_at: int | None = None
    #: For "not done yet": the day they promised it for.
    promised_for: str | None = None
    #: Overrides the suggested next try; None leaves the follow-up without a date. Left out = use the suggestion.
    next: Any = _UNSET
    #: Carry on by another channel from here.
    switch_to: FollowUpChannel | None = None
    #: The caller's local date and minute; the server's clock is UTC and doesn't know the user's time zone.
    at: Moment | None = None


@dataclass
class AttemptLogged:
    follow_up: FollowUp
    before: FollowUp
    suggestion: RetrySuggestion | None = None


class FollowUpUseCases(Crud[FollowUp]):
    """A follow-up is a chase: it stays open through every try, each try is written down, and the next one
    is set from how the last went — until someone is reached and it is done."""

    def __init__(self, env: Env) -> None:
        super().__init__(env, "followUp", FollowUp, env.stores.follow_up, normalize=normalize_follow_up)

    def _moment(self, at: Moment | None) -> Moment:
        if at:
            return at
        now = self.env.clock.now()
        return Moment(now.date().isoformat(), now.hour * 60 + now.minute)

    async def _suggestion(self, user_id: str, f: FollowUp, result: AttemptResult, call_back_at, promised_for, at):
        mine = await self.repo.list(user_id)
        hints = Hints(call_back_at, promised_for, best_minute(f.contact, mine))
        return suggest_retry(f.attempts or [], result, self._moment(at), hints)

    async def suggest(self, user_id: str, id: str, inp: AttemptInput) -> RetrySuggestion | None:
        f = await self.require(user_id, id, "Follow-up")
        return await self._suggestion(user_id, f, inp.result, inp.call_back_at, inp.promised_for, inp.at)

    async def log_attempt(self, user_id: str, id: str, inp: AttemptInput) -> AttemptLogged:
        before = await self.require(user_id, id, "Follow-up")
        moment = self._moment(inp.at)
        attempt = Attempt(
            date=moment.date, minute=moment.minute, result=inp.result, channel=before.channel,
            note=blank_to_none(inp.note), promised_for=inp.promised_for if inp.result == "not_ready" else None,
        )
        suggestion = await self._suggestion(user_id, before, inp.result, inp.call_back_at, inp.promised_for, inp.at)

        due = before.due.model_dump() if before.due else None
        if inp.result == "unreachable":
            due = None
        elif inp.result != "reached":
            if inp.next is not _UNSET:
                due = inp.next
            elif suggestion:
                due = {"date": suggestion.date, "time": suggestion.time}
        outcome = before.outcome
        if inp.result == "reached":
            outcome = Outcome(status="done", comment=attempt.note or before.outcome.comment, recorded_at=self.env.now_iso())

        data = before.model_dump()
        data.update(
            attempts=[a.model_dump() for a in [*(before.attempts or []), attempt]],
            channel=inp.switch_to or before.channel, due=due, outcome=outcome.model_dump(),
        )
        saved = await self.update(FollowUp.model_validate(data))
        return AttemptLogged(saved, before, suggestion)


# --- meetings ---------------------------------------------------------------------------------------------


class MeetingUseCases(Crud[Meeting]):
    """Saving or deleting a meeting keeps its confirmation follow-ups in step."""

    def __init__(self, env: Env) -> None:
        super().__init__(env, "meeting", Meeting, env.stores.meeting, normalize=normalize_meeting)
        self._follow_ups = FollowUpUseCases(env)

    async def create(self, user_id: str, data: dict[str, Any]) -> Meeting:
        saved = await super().create(user_id, data)
        await self._sync(user_id, saved)
        return saved

    async def update(self, entity: Meeting) -> Meeting:
        saved = await super().update(entity)
        await self._sync(entity.user_id, saved)
        return saved

    async def remove(self, user_id: str, id: str) -> None:
        for f in await self.env.stores.follow_up.list(user_id):
            if f.trigger and f.subject and f.subject.kind == "meeting" and f.subject.id == id and f.outcome.status == "open":
                await self._follow_ups.remove(user_id, f.id)
        await super().remove(user_id, id)

    async def _sync(self, user_id: str, meeting: Meeting) -> None:
        sync = sync_confirmations(meeting, await self.env.stores.follow_up.list(user_id))
        for draft in sync.create:
            await self._follow_ups.create(user_id, draft)
        for f in sync.update:
            await self._follow_ups.update(f)
        for f in sync.remove:
            await self._follow_ups.remove(user_id, f.id)


# --- outcomes ---------------------------------------------------------------------------------------------


@dataclass
class SpawnRequest:
    """A further action to create while recording an outcome."""

    kind: str  # "task" | "followUp"
    title: str
    date: str | None = None
    time: int | None = None


@dataclass
class OutcomeResult:
    spawned: EntityRef | None = None


@dataclass
class OutcomeContext:
    workspace: str = "personal"
    plan_id: str | None = None
    contact: str | None = None


class RecordOutcome:
    """Records how something turned out — done, partly done, not done or cancelled — with a comment and an
    optional further action, which can be created right away as a linked task or follow-up."""

    def __init__(self, env: Env, hearing_outcome=None) -> None:
        self.env = env
        self.tasks = task_crud(env)
        self.follow_ups = FollowUpUseCases(env)
        self.meetings = MeetingUseCases(env)
        #: Hearings live in the legal-cases module, which registers its own handler.
        self._hearing_outcome = hearing_outcome

    async def execute(self, user_id: str, ref: EntityRef, outcome: Outcome, spawn: SpawnRequest | None = None) -> OutcomeResult:
        recorded = Outcome(
            status=outcome.status,
            comment=blank_to_none(outcome.comment),
            next_action=blank_to_none(outcome.next_action),
            recorded_at=None if outcome.status == "open" else self.env.now_iso(),
        )
        ctx = await self._apply(user_id, ref, recorded)
        if spawn is None:
            return OutcomeResult()

        title = required(spawn.title, "Further action")
        if spawn.kind == "task":
            task = await self.tasks.create(user_id, {
                "title": title, "notes": recorded.comment or "", "workspace": ctx.workspace, "priority": "normal",
                "due_date": spawn.date, "estimate": 30, "plan_id": ctx.plan_id, "related": ref.model_dump(),
            })
            return OutcomeResult(EntityRef(kind="task", id=task.id))
        follow_up = await self.follow_ups.create(user_id, {
            "title": title, "channel": "call", "workspace": ctx.workspace, "contact": ctx.contact,
            "due": {"date": spawn.date, "time": spawn.time} if spawn.date else None, "duration": 15,
            "subject": ref.model_dump(), "notes": recorded.comment,
        })
        return OutcomeResult(EntityRef(kind="followUp", id=follow_up.id))

    async def _apply(self, user_id: str, ref: EntityRef, recorded: Outcome) -> OutcomeContext:
        if ref.kind == "task":
            t = await self.tasks.require(user_id, ref.id, "Task")
            await self.tasks.update(t.model_copy(update={"outcome": recorded}))
            return OutcomeContext(t.workspace, t.plan_id)
        if ref.kind == "followUp":
            f = await self.follow_ups.require(user_id, ref.id, "Follow-up")
            await self.follow_ups.update(f.model_copy(update={"outcome": recorded}))
            return OutcomeContext(f.workspace, f.plan_id, f.contact)
        if ref.kind == "meeting":
            m = await self.meetings.require(user_id, ref.id, "Meeting")
            await self.meetings.update(m.model_copy(update={"outcome": recorded}))
            return OutcomeContext(m.workspace, m.prep_plan_id, m.contact or m.counterpart)
        if ref.kind == "hearing" and self._hearing_outcome:
            return await self._hearing_outcome(user_id, ref, recorded)
        raise NotFound(f"Nothing to record an outcome on for {ref.kind}.")
