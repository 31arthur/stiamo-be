from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import Channel, ChannelRepository, SettingsRepository, UserSettings
from app.infrastructure.db.models import ChannelModel, SettingsModel
from app.infrastructure.security import SecretBox


class SqlSettingsRepository(SettingsRepository):
    def __init__(self, session: AsyncSession, box: SecretBox) -> None:
        self._s, self._box = session, box

    async def get(self, user_id: str) -> UserSettings | None:
        row = await self._s.get(SettingsModel, user_id)
        if row is None:
            return None
        keys = {k: v for k, v in self._box.open(row.keys_sealed).items() if isinstance(v, str)}
        return UserSettings(row.user_id, row.email, keys, dict(row.selection or {}), row.decision, dict(row.prompt or {}), row.updated_at)

    async def save(self, s: UserSettings) -> None:
        row = await self._s.get(SettingsModel, s.user_id) or SettingsModel(user_id=s.user_id)
        row.email = s.email
        row.keys_sealed = self._box.seal(s.keys) if s.keys else None
        # Reassigned (not mutated) so the change is noticed.
        row.selection, row.decision, row.prompt = dict(s.selections), s.decision, dict(s.prompts)
        row.updated_at = datetime.now(UTC)
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

    async def delete(self, channel_id: str) -> None:
        await self._s.execute(delete(ChannelModel).where(ChannelModel.id == channel_id))
