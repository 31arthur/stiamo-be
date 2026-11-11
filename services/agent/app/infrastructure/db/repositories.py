from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import (
    Alert,
    AlertRepository,
    Channel,
    ChannelRepository,
    Conversation,
    ConversationRepository,
    DayPlan,
    DayPlanRepository,
    Proposal,
    ProposalRepository,
    SettingsRepository,
    UserSettings,
)
from app.infrastructure.db.models import (
    AlertModel,
    ChannelModel,
    ConversationModel,
    DayPlanModel,
    ProposalModel,
    SettingsModel,
)
from app.infrastructure.security import SecretBox


class SqlSettingsRepository(SettingsRepository):
    def __init__(self, session: AsyncSession, box: SecretBox) -> None:
        self._s, self._box = session, box

    async def get(self, user_id: str) -> UserSettings | None:
        row = await self._s.get(SettingsModel, user_id)
        if row is None:
            return None
        keys = {k: v for k, v in self._box.open(row.keys_sealed).items() if isinstance(v, str)}
        return UserSettings(row.user_id, row.email, keys, dict(row.selection or {}), row.decision, dict(row.prompt or {}), dict(row.workspaces or {}), row.updated_at, dict(row.planner or {}), dict(row.reminders or {}), list(row.themes or []), dict(row.mailwatch or {}), dict(row.availability or {}), dict(row.graphs or {}), dict(row.flows or {}), dict(row.custom_agents or {}), dict(row.flow_waits or {}))

    async def save(self, s: UserSettings) -> None:
        row = await self._s.get(SettingsModel, s.user_id) or SettingsModel(user_id=s.user_id)
        row.email = s.email
        row.keys_sealed = self._box.seal(s.keys) if s.keys else None
        # Reassigned (not mutated) so the change is noticed.
        row.selection, row.decision, row.prompt, row.workspaces, row.planner = dict(s.selections), s.decision, dict(s.prompts), dict(s.workspaces), dict(s.planner)
        row.reminders = dict(s.reminders)
        row.themes = list(s.themes)
        row.mailwatch = dict(s.mailwatch)
        row.availability = dict(s.availability)
        row.graphs = dict(s.graphs)
        row.flows = dict(s.flows)
        row.custom_agents = dict(s.custom_agents)
        row.flow_waits = dict(s.flow_waits)
        row.updated_at = datetime.now(UTC)
        self._s.add(row)
        await self._s.flush()

    async def planners(self) -> list[UserSettings]:
        rows = (await self._s.execute(select(SettingsModel))).scalars()
        out = []
        for r in rows:
            if (r.planner or {}).get("enabled"):
                found = await self.get(r.user_id)
                if found:
                    out.append(found)
        return out

    async def everyone(self) -> list[UserSettings]:
        out = []
        for r in (await self._s.execute(select(SettingsModel))).scalars():
            found = await self.get(r.user_id)
            if found:
                out.append(found)
        return out

    async def with_reminders(self) -> list[UserSettings]:
        rows = (await self._s.execute(select(SettingsModel))).scalars()
        out = []
        for r in rows:
            if (r.reminders or {}).get("enabled"):
                found = await self.get(r.user_id)
                if found:
                    out.append(found)
        return out


def _alert(r: AlertModel) -> Alert:
    return Alert(
        r.id, r.user_id, r.item_id, r.workflow_id, r.workspace, r.account_id, r.folder, r.uid, r.subject, r.sender_name, r.sender_address, r.message_id, r.summary, r.action, r.due, r.priority,
        list(r.replies or []), r.theme_id, r.status, dict(r.draft) if r.draft else None, dict(r.awaiting) if r.awaiting else None, list(r.chats or []), bool(r.sample), r.created_at, r.updated_at,
    )


class SqlAlertRepository(AlertRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, alert_id: str) -> Alert | None:
        row = await self._s.get(AlertModel, alert_id)
        return _alert(row) if row else None

    async def by_item(self, user_id: str, item_id: str) -> Alert | None:
        row = (await self._s.execute(select(AlertModel).where(AlertModel.user_id == user_id, AlertModel.item_id == item_id))).scalar_one_or_none()
        return _alert(row) if row else None

    async def waiting_for(self, user_id: str, chat: int | str) -> Alert | None:
        rows = (await self._s.execute(select(AlertModel).where(AlertModel.user_id == user_id, AlertModel.status == "open").order_by(AlertModel.updated_at.desc()).limit(20))).scalars()
        for r in rows:
            if r.awaiting and str(r.awaiting.get("chat")) == str(chat):
                return _alert(r)
        return None

    async def recent(self, user_id: str, limit: int = 30) -> list[Alert]:
        rows = (await self._s.execute(select(AlertModel).where(AlertModel.user_id == user_id).order_by(AlertModel.created_at.desc()).limit(limit))).scalars()
        return [_alert(r) for r in rows]

    async def save(self, a: Alert) -> None:
        now = datetime.now(UTC)
        row = await self._s.get(AlertModel, a.id) or AlertModel(id=a.id, user_id=a.user_id, item_id=a.item_id, created_at=a.created_at or now)
        row.workflow_id, row.workspace, row.account_id, row.folder, row.uid = a.workflow_id, a.workspace, a.account_id, a.folder, a.uid
        row.subject, row.sender_name, row.sender_address, row.message_id = a.subject[:400], a.sender_name[:200], a.sender_address[:320], a.message_id[:998]
        row.summary, row.action, row.due, row.priority = a.summary, a.action[:300], a.due, a.priority
        row.replies, row.theme_id, row.status, row.draft, row.awaiting, row.chats, row.sample = list(a.replies), a.theme_id, a.status, a.draft, a.awaiting, list(a.chats), a.sample
        row.updated_at = now
        self._s.add(row)
        await self._s.flush()


class SqlChannelRepository(ChannelRepository):
    def __init__(self, session: AsyncSession, box: SecretBox) -> None:
        self._s, self._box = session, box

    def _entity(self, row: ChannelModel) -> Channel:
        secrets = {k: v for k, v in self._box.open(row.secrets_sealed).items() if isinstance(v, str)}
        return Channel(row.id, row.user_id, row.kind, secrets, dict(row.public or {}), dict(row.state or {}), row.created_at)

    async def get(self, channel_id: str) -> Channel | None:
        row = await self._s.get(ChannelModel, channel_id)
        return self._entity(row) if row else None

    async def for_user(self, user_id: str) -> list[Channel]:
        rows = (await self._s.execute(select(ChannelModel).where(ChannelModel.user_id == user_id).order_by(ChannelModel.created_at))).scalars()
        return [self._entity(r) for r in rows]

    async def save(self, c: Channel) -> None:
        row = await self._s.get(ChannelModel, c.id) or ChannelModel(id=c.id, user_id=c.user_id, kind=c.kind, created_at=c.created_at or datetime.now(UTC))
        row.secrets_sealed = self._box.seal(c.secrets)
        # Reassigned (not mutated) so the change is noticed.
        row.public, row.state = dict(c.public), dict(c.state)
        self._s.add(row)
        await self._s.flush()

    async def all_telegram(self) -> list[Channel]:
        rows = (await self._s.execute(select(ChannelModel).where(ChannelModel.kind == "telegram"))).scalars()
        return [self._entity(r) for r in rows]

    async def delete(self, channel_id: str) -> None:
        await self._s.execute(delete(ChannelModel).where(ChannelModel.id == channel_id))


def _plan(r: DayPlanModel) -> DayPlan:
    return DayPlan(r.id, r.user_id, r.day, r.status, r.briefing, list(r.blocks or []), r.run_id, r.trigger, r.created_at, r.decided_at, r.card, r.workspace or "")


class SqlDayPlanRepository(DayPlanRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def for_day(self, user_id: str, day: str, workspace: str = "") -> DayPlan | None:
        row = (await self._s.execute(select(DayPlanModel).where(DayPlanModel.user_id == user_id, DayPlanModel.day == day, DayPlanModel.workspace == workspace))).scalar_one_or_none()
        return _plan(row) if row else None

    async def recent(self, user_id: str, limit: int = 10, workspace: str | None = None) -> list[DayPlan]:
        q = select(DayPlanModel).where(DayPlanModel.user_id == user_id)
        if workspace is not None:
            q = q.where(DayPlanModel.workspace == workspace)
        rows = (await self._s.execute(q.order_by(DayPlanModel.day.desc(), DayPlanModel.created_at.desc()).limit(limit))).scalars()
        return [_plan(r) for r in rows]

    async def get(self, user_id: str, plan_id: str) -> DayPlan | None:
        row = await self._s.get(DayPlanModel, plan_id)
        return _plan(row) if row and row.user_id == user_id else None

    async def save(self, p: DayPlan) -> None:
        row = await self._s.get(DayPlanModel, p.id) or DayPlanModel(id=p.id, user_id=p.user_id, day=p.day, workspace=p.workspace, created_at=p.created_at)
        row.status, row.briefing, row.blocks, row.run_id, row.trigger, row.decided_at, row.card = p.status, p.briefing, list(p.blocks), p.run_id, p.trigger, p.decided_at, p.card
        self._s.add(row)
        await self._s.flush()


def _proposal(r: ProposalModel) -> Proposal:
    return Proposal(r.id, r.user_id, r.agent_id, r.action, r.title, list(r.lines or []), list(r.ops or []), r.status, r.channel_id, r.created_at, r.decided_at)


class SqlProposalRepository(ProposalRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, p: Proposal) -> None:
        self._s.add(ProposalModel(id=p.id, user_id=p.user_id, agent_id=p.agent_id, action=p.action, title=p.title[:300], lines=list(p.lines), ops=list(p.ops), status=p.status, channel_id=p.channel_id, created_at=p.created_at))
        await self._s.flush()

    async def get(self, user_id: str, proposal_id: str) -> Proposal | None:
        row = await self._s.get(ProposalModel, proposal_id)
        return _proposal(row) if row and row.user_id == user_id else None

    async def pending_in(self, user_id: str, channel_id: str, since: datetime) -> list[Proposal]:
        rows = (await self._s.execute(select(ProposalModel).where(ProposalModel.user_id == user_id, ProposalModel.channel_id == channel_id, ProposalModel.status == "pending", ProposalModel.created_at >= since).order_by(ProposalModel.created_at))).scalars()
        return [_proposal(r) for r in rows]

    async def pending(self, user_id: str, since: datetime, limit: int = 50) -> list[Proposal]:
        rows = (await self._s.execute(select(ProposalModel).where(ProposalModel.user_id == user_id, ProposalModel.status == "pending", ProposalModel.created_at >= since).order_by(ProposalModel.created_at.desc()).limit(limit))).scalars()
        return [_proposal(r) for r in rows]

    async def save(self, p: Proposal) -> None:
        row = await self._s.get(ProposalModel, p.id)
        if row is not None:
            row.status, row.decided_at = p.status, p.decided_at
            await self._s.flush()


def _conversation(r: ConversationModel) -> Conversation:
    return Conversation(r.id, r.user_id, r.agent_id, r.workspace, r.title, list(r.turns or []), list(r.messages or []), r.created_at, r.updated_at, dict(r.context or {}), r.channel_key)


class SqlConversationRepository(ConversationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def recent(self, user_id: str, workspace: str | None, limit: int = 200) -> list[Conversation]:
        q = select(ConversationModel).where(ConversationModel.user_id == user_id)
        if workspace:
            q = q.where(ConversationModel.workspace == workspace)
        rows = (await self._s.execute(q.order_by(ConversationModel.updated_at.desc()).limit(limit))).scalars()
        return [_conversation(r) for r in rows]

    async def get(self, user_id: str, conversation_id: str) -> Conversation | None:
        row = await self._s.get(ConversationModel, conversation_id)
        return _conversation(row) if row and row.user_id == user_id else None

    async def by_channel(self, user_id: str, channel_key: str) -> Conversation | None:
        row = (await self._s.execute(select(ConversationModel).where(ConversationModel.user_id == user_id, ConversationModel.channel_key == channel_key).order_by(ConversationModel.updated_at.desc()).limit(1))).scalar_one_or_none()
        return _conversation(row) if row else None

    async def save(self, c: Conversation) -> None:
        row = await self._s.get(ConversationModel, c.id) or ConversationModel(id=c.id, user_id=c.user_id, created_at=c.created_at)
        row.agent_id, row.workspace, row.title, row.turns, row.messages, row.updated_at = c.agent_id, c.workspace, c.title[:120], list(c.turns), list(c.messages), c.updated_at
        row.context, row.channel_key = dict(c.context), c.channel_key
        self._s.add(row)
        await self._s.flush()

    async def delete(self, user_id: str, conversation_id: str) -> None:
        await self._s.execute(delete(ConversationModel).where(ConversationModel.id == conversation_id, ConversationModel.user_id == user_id))
