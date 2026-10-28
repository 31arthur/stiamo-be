"""Attach supporting documents to any record, and remove them. Both are written to the record's history."""

import uuid
from datetime import UTC, datetime

from stiamo_common.errors import NotFound, ValidationFailed
from stiamo_common.models import to_iso

from app.application.ports import AttachmentRepository, FileStorage, HistoryNotifier
from app.domain.attachment import (
    LINK_MIME,
    Attachment,
    EntityRef,
    default_link_name,
    describe_link,
    format_bytes,
)


class AttachmentUseCases:
    def __init__(self, repo: AttachmentRepository, storage: FileStorage, history: HistoryNotifier, max_bytes: int) -> None:
        self._repo, self._storage, self._history, self._max = repo, storage, history, max_bytes

    @staticmethod
    def _now() -> str:
        return to_iso(datetime.now(UTC))

    async def attach(self, user_id: str, token: str, owner: EntityRef, owner_title: str, filename: str, mime: str,
                     data: bytes) -> Attachment:
        name = filename.strip() or "file"
        if not data:
            raise ValidationFailed(f"“{name}” is empty")
        if len(data) > self._max:
            raise ValidationFailed(f"“{name}” is larger than the limit of {format_bytes(self._max)}")
        attachment_id = str(uuid.uuid4())
        path = f"{user_id}/{attachment_id}"
        await self._storage.put(path, data)
        now = self._now()
        attachment = Attachment(
            id=attachment_id, user_id=user_id, owner=owner, name=name, mime=mime or "application/octet-stream",
            size=len(data), backend="storage", path=path, created_at=now, updated_at=now)
        try:
            await self._repo.save(attachment)
        except Exception:
            await self._storage.delete(path)  # no orphaned bytes if the row couldn't be written
            raise
        await self._history.record(token, owner, owner_title, "attached", f"Attached “{name}” ({format_bytes(len(data))})")
        return attachment

    async def attach_link(self, user_id: str, token: str, owner: EntityRef, owner_title: str, address: str,
                          name: str | None = None) -> Attachment:
        """Attaches a document that lives elsewhere (a Google Doc, a shared PDF…): nothing is stored, the link is."""
        link = describe_link(address)
        if link is None:
            raise ValidationFailed("Paste a full web address, starting with https://")
        title = (name or "").strip() or default_link_name(link)
        now = self._now()
        attachment = Attachment(
            id=str(uuid.uuid4()), user_id=user_id, owner=owner, name=title, mime=LINK_MIME, size=0, backend="link",
            link=link, created_at=now, updated_at=now)
        await self._repo.save(attachment)
        await self._history.record(token, owner, owner_title, "attached", f"Linked “{title}”")
        return attachment

    async def require(self, user_id: str, id: str) -> Attachment:
        found = await self._repo.get(user_id, id)
        if found is None:
            raise NotFound("Attachment not found.")
        return found

    async def list(self, user_id: str, owner: EntityRef | None, after: str | None, limit: int) -> list[Attachment]:
        return await self._repo.list(user_id, owner=owner, after=after, limit=limit)

    async def rename(self, user_id: str, id: str, caption: str) -> Attachment:
        a = await self.require(user_id, id)
        return await self._repo.save(a.model_copy(update={"caption": caption.strip() or None, "updated_at": self._now()}))

    async def remove(self, user_id: str, token: str, id: str, owner_title: str) -> None:
        a = await self._repo.get(user_id, id)
        if a is None:
            return
        await self._repo.remove(user_id, id)
        if a.path:
            await self._storage.delete(a.path)
        await self._history.record(token, a.owner, owner_title, "detached", f"Removed “{a.name}”")

    async def content(self, user_id: str, id: str) -> tuple[Attachment, bytes]:
        a = await self.require(user_id, id)
        if not a.path:
            raise ValidationFailed("This attachment is a link; it has no file")
        return a, await self._storage.read(a.path)

    # --- used by the practice service when records change ----------------------------------------------

    async def remove_owner(self, user_id: str, owner: EntityRef) -> int:
        """A record was deleted: its attachments and their bytes go with it (no history, the record is gone)."""
        items = await self._repo.of_owner(user_id, owner)
        for a in items:
            await self._repo.remove(user_id, a.id)
            if a.path:
                await self._storage.delete(a.path)
        return len(items)

    async def transfer(self, user_id: str, src: EntityRef, dst: EntityRef) -> int:
        """Two records were merged: everything attached to `src` now belongs to `dst`."""
        if src.same_as(dst):
            return 0
        return await self._repo.transfer(user_id, src, dst, self._now())
