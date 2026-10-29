"""Google Drive over its REST API, signing in as a service account (no OAuth screen, no expiring login)."""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import jwt

from app.application.ports import DriveGateway, DriveUnavailable, NotConnected, Page
from app.domain.search import DriveFile

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/drive/v3"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,size)"
_SKEW = 60  # renew the access token a minute early


def load_credentials(file: str = "", inline: str = "") -> dict[str, Any] | None:
    """The service account's key (the JSON Google gives you), from an inline value or a file; None when not set."""
    raw = inline.strip() or (Path(file).read_text(encoding="utf-8") if file and Path(file).is_file() else "")
    if not raw:
        return None
    info = json.loads(raw)
    if not info.get("client_email") or not info.get("private_key"):
        raise ValueError("The Google credentials need a client_email and a private_key.")
    return info


class ServiceAccountTokens:
    """Short-lived access tokens, obtained by signing a request with the service account's private key."""

    def __init__(self, info: dict[str, Any], client: httpx.AsyncClient, scope: str, now=time.time) -> None:
        self._info, self._client, self._scope, self._now = info, client, scope, now
        self._token: str | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:  # one renewal at a time, however many searches arrive together
            if self._token and self._now() < self._expires - _SKEW:
                return self._token
            uri = self._info.get("token_uri") or DEFAULT_TOKEN_URI
            issued = int(self._now())
            assertion = jwt.encode(
                {"iss": self._info["client_email"], "scope": self._scope, "aud": uri, "iat": issued, "exp": issued + 3600},
                self._info["private_key"], algorithm="RS256",
            )
            try:
                r = await self._client.post(uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
            except httpx.HTTPError as exc:
                raise DriveUnavailable(str(exc)) from exc
            if r.status_code != 200:
                log.warning("Google refused the service account sign-in: %s", r.text[:200])
                raise DriveUnavailable("Google refused the service account sign-in")
            body = r.json()
            self._token = body["access_token"]
            self._expires = self._now() + int(body.get("expires_in", 3600))
            return self._token


class GoogleDriveGateway(DriveGateway):
    def __init__(self, tokens: ServiceAccountTokens | None, client: httpx.AsyncClient) -> None:
        self._tokens, self._client = tokens, client

    @property
    def configured(self) -> bool:
        return self._tokens is not None

    async def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        if self._tokens is None:
            raise NotConnected()
        token = await self._tokens.get()
        try:
            return await self._client.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise DriveUnavailable(str(exc)) from exc

    async def search(self, query: str, *, limit: int, page_token: str | None = None, newest_first: bool = False) -> Page:
        params: dict[str, Any] = {
            "q": query, "pageSize": max(1, min(limit, 50)), "fields": _FIELDS,
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        if newest_first:
            params["orderBy"] = "modifiedTime desc"
        r = await self._get("/files", params)
        if r.status_code != 200:
            log.warning("Drive search failed (%s): %s", r.status_code, r.text[:200])
            raise DriveUnavailable(f"Google Drive answered {r.status_code}")
        body = r.json()
        files = [
            DriveFile(
                id=f["id"], name=f.get("name", ""), mime_type=f.get("mimeType", ""), modified_time=f.get("modifiedTime"),
                web_view_link=f.get("webViewLink"), size=int(f["size"]) if f.get("size") else None,
            )
            for f in body.get("files", [])
        ]
        return Page(files, body.get("nextPageToken"))

    async def folder_name(self, folder_id: str) -> str | None:
        try:
            r = await self._get(f"/files/{folder_id}", {"fields": "name", "supportsAllDrives": "true"})
        except (DriveUnavailable, NotConnected):
            return None
        return r.json().get("name") if r.status_code == 200 else None
