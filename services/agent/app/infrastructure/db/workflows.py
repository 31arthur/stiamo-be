from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import Execution, LedgerEntry, Workflow, WorkflowRepository
from app.infrastructure.db.models import ExecutionModel, LedgerModel, WorkflowModel, WorkflowVersionModel


def _workflow(r: WorkflowModel) -> Workflow:
    return Workflow(r.id, r.user_id, r.name, r.description, dict(r.config or {}), r.version, r.created_at, r.updated_at, r.last_run_at)


def _entry(r: LedgerModel) -> LedgerEntry:
    return LedgerEntry(r.id, r.agent_id, r.user_id, r.item_id, r.title, r.excerpt, r.label, r.confidence, r.reason, r.status, r.run_id, r.version, r.created_at, r.decided_at, dict(r.data or {}))


def _execution(r: ExecutionModel) -> Execution:
    return Execution(r.id, r.agent_id, r.user_id, r.started_at, r.duration_ms, r.status, r.summary, dict(r.counts or {}), dict(r.stats or {}), r.error, r.version)


class SqlWorkflowRepository(WorkflowRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def all(self, user_id: str) -> list[Workflow]:
        rows = (await self._s.execute(select(WorkflowModel).where(WorkflowModel.user_id == user_id).order_by(WorkflowModel.created_at))).scalars()
        return [_workflow(r) for r in rows]

    async def listening(self) -> list[Workflow]:
        rows = (await self._s.execute(select(WorkflowModel))).scalars()
        return [_workflow(r) for r in rows if (r.config or {}).get("listen") or (r.config or {}).get("scheduleMinutes")]

    async def get(self, user_id: str, workflow_id: str) -> Workflow | None:
        row = await self._s.get(WorkflowModel, workflow_id)
        return _workflow(row) if row and row.user_id == user_id else None

    async def save(self, w: Workflow) -> None:
        now = datetime.now(UTC)
        row = await self._s.get(WorkflowModel, w.id)
        if row is None:
            row = WorkflowModel(id=w.id, user_id=w.user_id, created_at=w.created_at or now)
        row.name, row.description, row.version, row.updated_at = w.name, w.description, w.version, now
        row.config = dict(w.config)  # reassigned, so the change is noticed
        self._s.add(row)
        existing = await self._s.get(WorkflowVersionModel, (w.id, w.version))
        if existing is None:
            self._s.add(WorkflowVersionModel(agent_id=w.id, version=w.version, config=dict(w.config), created_at=now))
        await self._s.flush()

    async def delete(self, user_id: str, workflow_id: str) -> None:
        if await self.get(user_id, workflow_id) is None:
            return
        await self._s.execute(delete(LedgerModel).where(LedgerModel.agent_id == workflow_id))
        await self._s.execute(delete(ExecutionModel).where(ExecutionModel.agent_id == workflow_id))
        await self._s.execute(delete(WorkflowVersionModel).where(WorkflowVersionModel.agent_id == workflow_id))
        await self._s.execute(delete(WorkflowModel).where(WorkflowModel.id == workflow_id))

    async def seen(self, workflow_id: str) -> set[str]:
        return set((await self._s.execute(select(LedgerModel.item_id).where(LedgerModel.agent_id == workflow_id))).scalars())

    async def record(self, entries: list[LedgerEntry]) -> None:
        now = datetime.now(UTC)
        for e in entries:
            row = (await self._s.execute(select(LedgerModel).where(LedgerModel.agent_id == e.agent_id, LedgerModel.item_id == e.item_id))).scalar_one_or_none()
            if row is None:
                row = LedgerModel(id=e.id, agent_id=e.agent_id, user_id=e.user_id, item_id=e.item_id, created_at=now)
            row.title, row.excerpt, row.label, row.confidence, row.reason = e.title[:200], e.excerpt, e.label, e.confidence, e.reason
            row.status, row.run_id, row.version, row.decided_at = e.status, e.run_id, e.version, None
            row.data = dict(e.data) if e.data else None
            self._s.add(row)
        await self._s.flush()

    async def review(self, user_id: str, workflow_id: str, statuses: list[str], limit: int = 200) -> list[LedgerEntry]:
        q = select(LedgerModel).where(LedgerModel.user_id == user_id, LedgerModel.agent_id == workflow_id, LedgerModel.status.in_(statuses)).order_by(LedgerModel.created_at.desc()).limit(limit)
        return [_entry(r) for r in (await self._s.execute(q)).scalars()]

    async def decide(self, user_id: str, workflow_id: str, item_ids: list[str], status: str, label: str | None = None, note: str | None = None) -> int:
        values: dict = {"status": status, "decided_at": datetime.now(UTC)}
        if label:
            values["label"] = label
        if note:
            values["reason"] = note[:500]
        q = update(LedgerModel).where(LedgerModel.user_id == user_id, LedgerModel.agent_id == workflow_id, LedgerModel.item_id.in_(item_ids), LedgerModel.status.in_(["proposed", "needs_review"]))
        if status == "approved" and not label:
            q = q.where(LedgerModel.label.is_not(None))  # an item the agent couldn't label needs one chosen before it is approved
        result = await self._s.execute(q.values(**values))
        return result.rowcount or 0

    async def entry(self, user_id: str, workflow_id: str, item_id: str) -> LedgerEntry | None:
        row = (await self._s.execute(select(LedgerModel).where(LedgerModel.user_id == user_id, LedgerModel.agent_id == workflow_id, LedgerModel.item_id == item_id))).scalar_one_or_none()
        return _entry(row) if row else None

    async def counts(self, workflow_id: str) -> dict[str, int]:
        rows = (await self._s.execute(select(LedgerModel.status, func.count()).where(LedgerModel.agent_id == workflow_id).group_by(LedgerModel.status))).all()
        return {status: n for status, n in rows}

    async def touch(self, workflow_id: str, when: datetime) -> None:
        await self._s.execute(update(WorkflowModel).where(WorkflowModel.id == workflow_id).values(last_run_at=when))

    async def add_execution(self, e: Execution) -> None:
        self._s.add(ExecutionModel(id=e.id, agent_id=e.agent_id, user_id=e.user_id, started_at=e.started_at, duration_ms=e.duration_ms, status=e.status, summary=e.summary, counts=dict(e.counts), stats=dict(e.stats), error=e.error, version=e.version))
        await self._s.flush()

    async def executions(self, user_id: str, workflow_id: str, limit: int = 50) -> list[Execution]:
        q = select(ExecutionModel).where(ExecutionModel.user_id == user_id, ExecutionModel.agent_id == workflow_id).order_by(ExecutionModel.started_at.desc()).limit(limit)
        return [_execution(r) for r in (await self._s.execute(q)).scalars()]

    async def execution(self, user_id: str, workflow_id: str, run_id: str) -> Execution | None:
        row = await self._s.get(ExecutionModel, run_id)
        return _execution(row) if row and row.user_id == user_id and row.agent_id == workflow_id else None

    async def run_items(self, user_id: str, workflow_id: str, run_id: str, limit: int = 2000) -> list[LedgerEntry]:
        q = select(LedgerModel).where(LedgerModel.user_id == user_id, LedgerModel.agent_id == workflow_id, LedgerModel.run_id == run_id).order_by(LedgerModel.confidence.asc()).limit(limit)
        return [_entry(r) for r in (await self._s.execute(q)).scalars()]

    async def run_status_counts(self, workflow_id: str) -> dict[str, dict[str, int]]:
        rows = (await self._s.execute(select(LedgerModel.run_id, LedgerModel.status, func.count()).where(LedgerModel.agent_id == workflow_id).group_by(LedgerModel.run_id, LedgerModel.status))).all()
        out: dict[str, dict[str, int]] = {}
        for run_id, status, n in rows:
            out.setdefault(run_id, {})[status] = n
        return out

    async def upstream_entries(self, user_id: str, workflow_id: str, statuses: list[str], limit: int = 2000) -> list[LedgerEntry]:
        q = select(LedgerModel).where(LedgerModel.user_id == user_id, LedgerModel.agent_id == workflow_id, LedgerModel.status.in_(statuses)).order_by(LedgerModel.created_at.desc()).limit(limit)
        return [_entry(r) for r in (await self._s.execute(q)).scalars()]

    async def since(self, user_id: str, since: datetime, limit: int = 20000) -> list[LedgerEntry]:
        q = select(LedgerModel).where(LedgerModel.user_id == user_id, LedgerModel.created_at >= since).order_by(LedgerModel.created_at.desc()).limit(limit)
        return [_entry(r) for r in (await self._s.execute(q)).scalars()]

    async def queue(self, user_id: str, statuses: list[str], limit: int = 500) -> list[LedgerEntry]:
        q = select(LedgerModel).where(LedgerModel.user_id == user_id, LedgerModel.status.in_(statuses)).order_by(LedgerModel.created_at.desc()).limit(limit)
        return [_entry(r) for r in (await self._s.execute(q)).scalars()]

    async def settle(self, user_id: str, workflow_id: str, item_id: str, status: str, data: dict | None = None) -> bool:
        row = (await self._s.execute(select(LedgerModel).where(LedgerModel.user_id == user_id, LedgerModel.agent_id == workflow_id, LedgerModel.item_id == item_id))).scalar_one_or_none()
        if row is None:
            return False
        row.status, row.decided_at = status, datetime.now(UTC)
        if data is not None:
            row.data = dict(data)
        self._s.add(row)
        await self._s.flush()
        return True
