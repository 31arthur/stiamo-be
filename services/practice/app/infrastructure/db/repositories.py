"""SQLAlchemy implementations of the repository ports."""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import ActivityRepository, LinkRepository, Stores
from app.domain.common import EntityRef
from app.domain.directory import Client, Counsel, Project
from app.domain.history import Activity, Link
from app.infrastructure.db import models
from app.infrastructure.db.joined import Join, SqlJoinedRepository
from app.infrastructure.db.mappers import (
    ACTIVITY,
    FOLLOW_UP,
    GOAL,
    LINK,
    MEETING,
    PLAN,
    TASK,
    flat_mapper,
)
from app.infrastructure.db.sql_repository import SqlRepository


class SqlLinkRepository(SqlRepository, LinkRepository):
    async def touching(self, user_id: str, ref: EntityRef) -> list[Link]:
        M = models.LinkModel
        q = select(M).where(
            M.user_id == user_id,
            or_(and_(M.a_kind == ref.kind, M.a_id == ref.id), and_(M.b_kind == ref.kind, M.b_id == ref.id)),
        ).order_by(M.id)
        return [self._m.from_row(r) for r in (await self._s.execute(q)).scalars()]


class SqlActivityRepository(SqlRepository, ActivityRepository):
    async def for_refs(self, user_id: str, refs: list[EntityRef], limit: int) -> list[Activity]:
        if not refs:
            return []
        M = models.ActivityModel
        q = (
            select(M)
            .where(M.user_id == user_id, or_(*[and_(M.ref_kind == r.kind, M.ref_id == r.id) for r in refs]))
            .order_by(M.at.desc(), M.id.desc())
            .limit(limit)
        )
        return [self._m.from_row(r) for r in (await self._s.execute(q)).scalars()]


def build_stores(s: AsyncSession) -> Stores:
    return Stores(
        counsel=SqlRepository(s, flat_mapper(models.CounselModel, Counsel)),
        client=SqlRepository(s, flat_mapper(models.ClientModel, Client)),
        project=SqlRepository(s, flat_mapper(models.ProjectModel, Project)),
        task=SqlRepository(s, TASK),
        follow_up=SqlRepository(s, FOLLOW_UP),
        meeting=SqlRepository(s, MEETING),
        plan=SqlJoinedRepository(s, PLAN, [
            Join(models.PlanCaseModel, "plan_id", "case_id", "case_ids"),
            Join(models.PlanHearingModel, "plan_id", "hearing_id", "hearing_ids"),
        ]),
        goal=SqlJoinedRepository(s, GOAL, [Join(models.GoalPlanModel, "goal_id", "plan_id", "plan_ids", [])]),
        link=SqlLinkRepository(s, LINK),
        activity=SqlActivityRepository(s, ACTIVITY),
    )
