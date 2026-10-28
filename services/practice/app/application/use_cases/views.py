"""Read models over everything a user has: the thread around a record, and the agenda."""

from dataclasses import dataclass
from typing import Any

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import Env
from app.application.use_cases.work import FollowUpUseCases, MeetingUseCases, task_crud
from app.domain.agenda import AgendaEntry, AgendaSources, build_agenda, clamp_block
from app.domain.common import EntityRef
from app.domain.history import Activity
from app.domain.relations import GraphSources, RelationGraph, Thread
from app.domain.work import FollowUp


class RelationUseCases:
    def __init__(self, env: Env) -> None:
        self.env = env

    async def graph(self, user_id: str) -> RelationGraph:
        s = self.env.stores
        return RelationGraph(GraphSources(
            tasks=await s.task.list(user_id), meetings=await s.meeting.list(user_id),
            follow_ups=await s.follow_up.list(user_id), cases=await s.case.list(user_id),
            hearings=await s.hearing.list(user_id), plans=await s.plan.list(user_id), goals=await s.goal.list(user_id),
            counsel=await s.counsel.list(user_id), clients=await s.client.list(user_id),
            projects=await s.project.list(user_id), links=await s.link.list(user_id),
        ))

    async def thread(self, user_id: str, ref: EntityRef) -> Thread:
        graph = await self.graph(user_id)
        if not graph.has(ref):
            raise NotFound("Record not found.")
        return graph.thread(ref)

    async def thread_history(self, user_id: str, ref: EntityRef, limit: int = 300) -> list[Activity]:
        """A record's history is the merged activity of its whole thread."""
        thread = await self.thread(user_id, ref)
        return await self.env.stores.activity.for_refs(user_id, thread.members, limit)


@dataclass
class Block:
    start: int
    duration: int


class AgendaUseCases:
    """Reads the unified agenda and moves things around on it. Every change returns the record as it was."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self.tasks = task_crud(env)
        self.meetings = MeetingUseCases(env)
        self.follow_ups = FollowUpUseCases(env)

    async def range(self, user_id: str, start: str, end: str) -> list[AgendaEntry]:
        s = self.env.stores
        return build_agenda(
            AgendaSources(await s.task.list(user_id), await s.meeting.list(user_id), await s.follow_up.list(user_id),
                          await s.hearing.list(user_id), await s.case.list(user_id)),
            start, end,
        )

    async def schedule(self, user_id: str, ref: EntityRef, date: str, block: Block | None) -> tuple[str, Any]:
        """Places an entry at a time on `date`, or clears its time (None). Returns the prior state."""
        start, duration = clamp_block(block.start, block.duration) if block else (None, None)
        if ref.kind == "task":
            t = await self.tasks.require(user_id, ref.id, "Task")
            slot = {"date": date, "start": start, "duration": duration} if block else None
            # Keep it on this day when it's taken off the timeline.
            due = t.due_date if block else (t.due_date or date)
            await self.tasks.update(type(t).model_validate({**t.model_dump(), "slot": slot, "due_date": due}))
            return "task", t
        if ref.kind == "followUp":
            f = await self.follow_ups.require(user_id, ref.id, "Follow-up")
            data = {**f.model_dump(), "due": {"date": date, "time": start}, "duration": duration or f.duration}
            await self.follow_ups.update(FollowUp.model_validate(data))
            return "followUp", f
        if ref.kind == "meeting":
            m = await self.meetings.require(user_id, ref.id, "Meeting")
            if not block:
                raise ValidationFailed("Meetings always have a time")
            await self.meetings.update(type(m).model_validate({
                **m.model_dump(), "slot": {"date": date, "start": start, "duration": duration}}))
            return "meeting", m
        raise ValidationFailed("Hearings are set by the court and can’t be moved here")
