import logging

import httpx

from app.application.ports import HistoryNotifier
from app.domain.attachment import EntityRef

log = logging.getLogger(__name__)


class PracticeHistoryNotifier(HistoryNotifier):
    """Posts a history line to the practice service, forwarding the caller's own access token."""

    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        self._base, self._timeout = base_url.rstrip("/"), timeout

    async def record(self, token: str, ref: EntityRef, title: str, action: str, summary: str) -> None:
        if not self._base:
            return
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                await http.post(
                    f"{self._base}/v1/activity/events",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"ref": ref.model_dump(), "title": title, "action": action, "summary": summary},
                )
        except httpx.HTTPError:
            log.warning("could not write history for %s %s", ref.kind, ref.id)
