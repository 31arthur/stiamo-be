"""The practice's data, fetched from the other services with the owner's own access token."""

from typing import Any

import httpx

from app.application.ports import DataGateway
from app.domain.llm import AiError

MAX_RECORDS = 3000


class HttpDataGateway(DataGateway):
    def __init__(self, http: httpx.AsyncClient, token: str, practice_url: str, files_url: str, drive_url: str) -> None:
        self._http, self._headers = http, {"Authorization": f"Bearer {token}"}
        self._practice, self._files, self._drive = practice_url.rstrip("/"), files_url.rstrip("/"), drive_url.rstrip("/")
        self._drive_ok: bool | None = None

    async def _get(self, url: str, **params: Any) -> httpx.Response:
        try:
            return await self._http.get(url, headers=self._headers, params={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as exc:
            raise AiError("Couldn't reach the practice data. Try again in a moment.") from exc

    async def _all(self, url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        after: str | None = None
        while len(items) < MAX_RECORDS:
            r = await self._get(url, limit=500, after=after)
            if r.status_code != 200:
                raise AiError(f"The practice data answered {r.status_code}.")
            body = r.json()
            items += body.get("items", [])
            after = body.get("nextCursor")
            if not after:
                break
        return items

    async def cases(self) -> list[dict[str, Any]]:
        return await self._all(f"{self._practice}/v1/cases")

    async def hearings(self) -> list[dict[str, Any]]:
        return await self._all(f"{self._practice}/v1/hearings")

    async def attachments(self) -> list[dict[str, Any]]:
        return await self._all(f"{self._files}/v1/attachments")

    async def drive_available(self) -> bool:
        if self._drive_ok is None:
            try:
                r = await self._get(f"{self._drive}/v1/drive/status")
                self._drive_ok = r.status_code == 200 and bool(r.json().get("available"))
            except AiError:
                self._drive_ok = False
        return self._drive_ok

    async def drive_search(self, query: str) -> list[dict[str, Any]]:
        r = await self._get(f"{self._drive}/v1/drive/search", q=query, limit=25)
        if r.status_code != 200:
            raise AiError("Google Drive couldn't be searched right now.")
        return r.json().get("items", [])

    async def drive_suggest(self, terms: list[str]) -> list[dict[str, Any]]:
        try:
            r = await self._http.post(f"{self._drive}/v1/drive/suggest", headers=self._headers, json={"terms": terms})
        except httpx.HTTPError as exc:
            raise AiError("Google Drive couldn't be searched right now.") from exc
        if r.status_code != 200:
            raise AiError("Google Drive couldn't be searched right now.")
        return r.json().get("items", [])
