"""Google's OAuth and Calendar APIs, over HTTPS."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.application.ports import GoogleError, GoogleGateway, PracticeGateway, SyncExpired, Tokens

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
API = "https://www.googleapis.com/calendar/v3"
#: Read the list of calendars, and read and write events. Nothing wider: no access to settings, sharing or other Google data.
SCOPES = "openid email https://www.googleapis.com/auth/calendar.calendarlist.readonly https://www.googleapis.com/auth/calendar.events"
PAGE = 250
MAX_PAGES = 20


def _message(r: httpx.Response) -> str:
    try:
        body = r.json()
        err = body.get("error")
        return (err.get("message") if isinstance(err, dict) else body.get("error_description") or err) or f"Google answered {r.status_code}."
    except ValueError:
        return f"Google answered {r.status_code}."


class HttpGoogle(GoogleGateway):
    def __init__(self, http: httpx.AsyncClient, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._http, self._id, self._secret, self._redirect = http, client_id, client_secret, redirect_uri

    async def _send(self, method: str, url: str, **kw: Any) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise GoogleError("Couldn't reach Google. Try again in a moment.") from exc

    def auth_url(self, state: str, login_hint: str | None) -> str:
        q = {"client_id": self._id, "redirect_uri": self._redirect, "response_type": "code", "scope": SCOPES, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state}
        if login_hint:
            q["login_hint"] = login_hint
        return f"{AUTH}?{urlencode(q)}"

    async def exchange(self, code: str) -> Tokens:
        r = await self._send("POST", TOKEN, data={"code": code, "client_id": self._id, "client_secret": self._secret, "redirect_uri": self._redirect, "grant_type": "authorization_code"})
        if r.status_code != 200:
            raise GoogleError(f"Google didn't accept the sign-in: {_message(r)}")
        b = r.json()
        return Tokens(b["access_token"], b.get("refresh_token"), int(b.get("expires_in", 3600)))

    async def refresh(self, refresh_token: str) -> str:
        r = await self._send("POST", TOKEN, data={"refresh_token": refresh_token, "client_id": self._id, "client_secret": self._secret, "grant_type": "refresh_token"})
        if r.status_code == 200:
            return r.json()["access_token"]
        bad = r.status_code in (400, 401) and (r.json().get("error") == "invalid_grant" if r.headers.get("content-type", "").startswith("application/json") else False)
        raise GoogleError("Google no longer allows Stiamo to read this calendar." if bad else _message(r), auth=bad)

    async def email(self, access: str) -> str:
        r = await self._send("GET", USERINFO, headers={"Authorization": f"Bearer {access}"})
        if r.status_code != 200 or not r.json().get("email"):
            raise GoogleError("Couldn't tell which Google account that was.")
        return r.json()["email"]

    async def calendars(self, access: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            r = await self._send("GET", f"{API}/users/me/calendarList", headers={"Authorization": f"Bearer {access}"}, params={"maxResults": 250, **({"pageToken": token} if token else {})})
            if r.status_code != 200:
                raise GoogleError(f"Couldn't list the calendars: {_message(r)}", auth=r.status_code == 401)
            body = r.json()
            items += body.get("items", [])
            token = body.get("nextPageToken")
            if not token:
                break
        return items

    async def events(self, access: str, calendar_id: str, *, sync_token: str | None, time_min: datetime, time_max: datetime) -> tuple[list[dict[str, Any]], str | None]:
        items: list[dict[str, Any]] = []
        page: str | None = None
        next_sync: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"singleEvents": "true", "maxResults": PAGE, "showDeleted": "true" if sync_token else "false"}
            if sync_token:
                params["syncToken"] = sync_token
            else:
                params |= {"timeMin": time_min.isoformat().replace("+00:00", "Z"), "timeMax": time_max.isoformat().replace("+00:00", "Z")}
            if page:
                params["pageToken"] = page
            r = await self._send("GET", f"{API}/calendars/{quote(calendar_id, safe='')}/events", headers={"Authorization": f"Bearer {access}"}, params=params)
            if r.status_code == 410:
                raise SyncExpired
            if r.status_code != 200:
                raise GoogleError(f"Couldn't read a calendar: {_message(r)}", auth=r.status_code == 401)
            body = r.json()
            items += body.get("items", [])
            page, next_sync = body.get("nextPageToken"), body.get("nextSyncToken") or next_sync
            if not page:
                break
        return items, next_sync

    async def insert(self, access: str, calendar_id: str, body: dict[str, Any]) -> str:
        r = await self._send("POST", f"{API}/calendars/{quote(calendar_id, safe='')}/events", headers={"Authorization": f"Bearer {access}"}, json=body)
        if r.status_code not in (200, 201):
            raise GoogleError(f"Couldn't add an event to Google: {_message(r)}", auth=r.status_code == 401)
        return r.json()["id"]

    async def update(self, access: str, calendar_id: str, event_id: str, body: dict[str, Any]) -> None:
        r = await self._send("PATCH", f"{API}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}", headers={"Authorization": f"Bearer {access}"}, json=body)
        if r.status_code not in (200, 204):
            raise GoogleError(f"Couldn't change an event in Google: {_message(r)}", auth=r.status_code == 401)

    async def delete(self, access: str, calendar_id: str, event_id: str) -> None:
        r = await self._send("DELETE", f"{API}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}", headers={"Authorization": f"Bearer {access}"})
        if r.status_code not in (200, 204, 404, 410):  # already gone is as good as deleted
            raise GoogleError(f"Couldn't remove an event from Google: {_message(r)}", auth=r.status_code == 401)

    async def revoke(self, token: str) -> None:
        r = await self._send("POST", REVOKE, data={"token": token})
        if r.status_code not in (200, 400):  # 400: already revoked
            raise GoogleError(_message(r))


class HttpPractice(PracticeGateway):
    def __init__(self, http: httpx.AsyncClient, practice_url: str) -> None:
        self._http, self._url = http, practice_url.rstrip("/")

    async def records(self, token: str, table: str) -> list[dict[str, Any]]:
        if table not in ("meetings", "hearings", "cases", "tasks", "follow-ups"):
            raise GoogleError("That can't be read.")
        out: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(20):
            try:
                r = await self._http.get(f"{self._url}/v1/{table}", headers={"Authorization": f"Bearer {token}"}, params={"limit": 500, **({"after": after} if after else {})})
            except httpx.HTTPError as exc:
                raise GoogleError("Couldn't reach your meetings and hearings.") from exc
            if r.status_code != 200:
                raise GoogleError(f"Your {table} couldn't be read ({r.status_code}).")
            body = r.json()
            out += body.get("items", [])
            after = body.get("nextCursor")
            if not after:
                break
        return out
