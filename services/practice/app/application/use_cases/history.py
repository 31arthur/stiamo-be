"""Hand-made connections between records, and free-form notes on a record's thread."""

from stiamo_common.errors import ValidationFailed

from app.application.activity_log import ActivityLog
from app.application.ports import Env
from app.domain.common import EntityRef
from app.domain.history import KIND_LABEL, Link
from app.domain.rules import blank_to_none, required


class HistoryUseCases:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.log = ActivityLog(env)

    async def link(self, user_id: str, a: EntityRef, b: EntityRef, titles: tuple[str, str], note: str | None = None) -> Link:
        if a.same_as(b):
            raise ValidationFailed("A record can’t be linked to itself")
        for existing in await self.env.stores.link.touching(user_id, a):
            if existing.touches(b):
                return existing
        now = self.env.now_iso()
        link = Link(id=self.env.ids.next(), user_id=user_id, a=a, b=b, note=blank_to_none(note),
                    created_at=now, updated_at=now)
        await self.env.stores.link.save(link)
        await self.log.record(user_id, ref=a, title=titles[0], action="linked", related=b,
                              summary=f"Linked to {KIND_LABEL[b.kind].lower()} “{titles[1]}”")
        await self.log.record(user_id, ref=b, title=titles[1], action="linked", related=a,
                              summary=f"Linked to {KIND_LABEL[a.kind].lower()} “{titles[0]}”")
        return link

    async def unlink(self, user_id: str, link_id: str, titles: tuple[str, str] | None = None) -> None:
        link = await self.env.stores.link.get(user_id, link_id)
        if link is None:
            return
        ta, tb = titles or (KIND_LABEL[link.a.kind], KIND_LABEL[link.b.kind])
        await self.env.stores.link.remove(user_id, link_id)
        await self.log.record(user_id, ref=link.a, title=ta, action="unlinked", related=link.b,
                              summary=f"Unlinked from “{tb}”")
        await self.log.record(user_id, ref=link.b, title=tb, action="unlinked", related=link.a,
                              summary=f"Unlinked from “{ta}”")

    async def add_note(self, user_id: str, ref: EntityRef, title: str, text: str) -> None:
        await self.log.record(user_id, ref=ref, title=title, action="note", summary=required(text, "Note"))

    async def links_of(self, user_id: str, ref: EntityRef) -> list[Link]:
        return await self.env.stores.link.touching(user_id, ref)

    async def history_of(self, user_id: str, ref: EntityRef, limit: int = 200):
        return await self.env.stores.activity.for_refs(user_id, [ref], limit)
