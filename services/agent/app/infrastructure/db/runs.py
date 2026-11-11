from collections.abc import Callable
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import RunLog, RunRecord, UsageRow
from app.infrastructure.db.models import RunModel, UsageModel

#: A person's history of one agent is kept to this many runs; older ones go as new ones arrive.
KEEP = 500


def _entity(r: RunModel, *, light: bool = False) -> RunRecord:
    return RunRecord(
        id=r.id, user_id=r.user_id, agent_id=r.agent_id, trigger=r.trigger, status=r.status, started_at=r.started_at, duration_ms=r.duration_ms,
        provider=r.provider, model=r.model, thinking=r.thinking, input=r.input,
        user_message="" if light else r.user_message, system_prompt="" if light else r.system_prompt,
        tools=[] if light else list(r.tools or []), steps=[] if light else list(r.steps or []), steps_count=r.steps_count,
        output=r.output, found_count=r.found_count, error=r.error, usage=dict(r.usage or {}), channel=r.channel, version=r.version, workspace=r.workspace or "", trace=[] if light else list(r.trace or []),
    )


def _usage(m: UsageModel) -> UsageRow:
    return UsageRow(m.id, m.agent_id, m.agent_name, m.kind, m.trigger, m.status, m.at, m.duration_ms, m.provider, m.model, m.input_tokens, m.output_tokens, m.cost, m.items)


class SqlRunLog(RunLog):
    """Each call uses its own session and commits, so a run that failed is kept even though its request was rolled back."""

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._factory = session_factory

    async def add(self, rec: RunRecord) -> None:
        async with self._factory() as s:
            s.add(RunModel(
                id=rec.id, user_id=rec.user_id, agent_id=rec.agent_id, trigger=rec.trigger, status=rec.status, started_at=rec.started_at,
                duration_ms=rec.duration_ms, provider=rec.provider, model=rec.model, thinking=rec.thinking, input=rec.input,
                user_message=rec.user_message, system_prompt=rec.system_prompt, tools=rec.tools, steps=rec.steps, steps_count=len(rec.steps), output=rec.output,
                found_count=rec.found_count, error=rec.error, usage=rec.usage, channel=rec.channel, version=rec.version, workspace=rec.workspace, trace=rec.trace,
            ))
            u = rec.usage or {}
            cost = u.get("cost")
            s.add(UsageModel(
                id=rec.id, user_id=rec.user_id, agent_id=rec.agent_id, agent_name=rec.agent_name[:100], kind="workflow" if rec.agent_id.startswith("wf-") else "chat",
                trigger=rec.trigger, status=rec.status, at=rec.started_at, duration_ms=rec.duration_ms, provider=rec.provider, model=rec.model[:200],
                input_tokens=int(u.get("inputTokens", 0) or 0), output_tokens=int(u.get("outputTokens", 0) or 0), cost=float(cost) if isinstance(cost, (int, float)) else None,
                items=len(rec.steps) if rec.agent_id.startswith("wf-") else 0,
            ))
            await s.flush()
            # Prune: everything beyond the newest KEEP for this person and agent.
            old = (await s.execute(select(RunModel.id).where(RunModel.user_id == rec.user_id, RunModel.agent_id == rec.agent_id).order_by(RunModel.started_at.desc()).offset(KEEP))).scalars().all()
            if old:
                await s.execute(delete(RunModel).where(RunModel.id.in_(old)))
            await s.commit()

    async def recent(self, user_id: str, agent_id: str, *, limit: int, before: datetime | None = None) -> list[RunRecord]:
        async with self._factory() as s:
            q = select(RunModel).where(RunModel.user_id == user_id, RunModel.agent_id == agent_id)
            if before is not None:
                q = q.where(RunModel.started_at < before)
            rows = (await s.execute(q.order_by(RunModel.started_at.desc()).limit(limit))).scalars().all()
            return [_entity(r, light=True) for r in rows]

    async def get(self, user_id: str, run_id: str) -> RunRecord | None:
        async with self._factory() as s:
            row = await s.get(RunModel, run_id)
            return _entity(row) if row and row.user_id == user_id else None

    async def since(self, user_id: str, agent_id: str, since: datetime) -> list[RunRecord]:
        async with self._factory() as s:
            rows = (await s.execute(select(RunModel).where(RunModel.user_id == user_id, RunModel.agent_id == agent_id, RunModel.started_at >= since).order_by(RunModel.started_at.desc()).limit(5000))).scalars().all()
            return [_entity(r, light=True) for r in rows]

    async def count(self, user_id: str, agent_id: str) -> int:
        async with self._factory() as s:
            return (await s.execute(select(func.count()).select_from(RunModel).where(RunModel.user_id == user_id, RunModel.agent_id == agent_id))).scalar_one()

    async def clear(self, user_id: str, agent_id: str) -> int:
        async with self._factory() as s:
            n = (await s.execute(delete(RunModel).where(RunModel.user_id == user_id, RunModel.agent_id == agent_id))).rowcount or 0
            await s.commit()
            return n

    async def usage_rows(self, user_id: str, *, since: datetime | None = None, agent_id: str | None = None, before: datetime | None = None, limit: int = 50_000) -> list[UsageRow]:
        async with self._factory() as s:
            q = select(UsageModel).where(UsageModel.user_id == user_id)
            if since is not None:
                q = q.where(UsageModel.at >= since)
            if before is not None:
                q = q.where(UsageModel.at < before)
            if agent_id:
                q = q.where(UsageModel.agent_id == agent_id)
            return [_usage(m) for m in (await s.execute(q.order_by(UsageModel.at.desc()).limit(limit))).scalars()]
