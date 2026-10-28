import logging

import httpx

from app.application.ports import FilesClient
from app.domain.common import EntityRef

log = logging.getLogger(__name__)


class HttpFilesClient(FilesClient):
    """Talks to the files service on the caller's behalf, forwarding their own access token."""

    def __init__(self, base_url: str, token: str, timeout: float = 3.0) -> None:
        self._base, self._headers, self._timeout = base_url.rstrip("/"), {"Authorization": f"Bearer {token}"}, timeout

    async def _call(self, method: str, path: str, **kw) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                await http.request(method, f"{self._base}{path}", headers=self._headers, **kw)
        except httpx.HTTPError:
            # The record change already succeeded; stray files are tidied up by the next cleanup, not by failing here.
            log.warning("files service call failed: %s %s", method, path)

    async def delete_owner(self, ref: EntityRef) -> None:
        await self._call("DELETE", "/v1/attachments", params={"kind": ref.kind, "id": ref.id})

    async def transfer(self, source: EntityRef, target: EntityRef) -> None:
        await self._call("POST", "/v1/attachments/transfer", json={"source": source.model_dump(), "target": target.model_dump()})
