"""Connecting Google accounts, reading their calendars into one place, and sending meetings and hearings back."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock
from typing import Any

import jwt
from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import CalendarRepository, GoogleError, GoogleGateway, PracticeGateway, SyncExpired
from app.config import Settings
from app.domain.calendar import (
    PALETTE,
    WORKSPACES,
    Account,
    Link,
    Prefs,
    Source,
    desired_events,
    digest,
    event_from_google,
    plan_export,
    view,
    zone,
)

log = logging.getLogger(__name__)

STATE_MINUTES = 15
#: A read that began with a changes token only sees what changed; the window itself is read afresh this often so it keeps moving.
FULL_READ_EVERY = timedelta(hours=24)
#: A Google access token is good for an hour: kept this long, in memory only.
ACCESS_SECONDS = 50 * 60
MAX_ERRORS = 5
#: Most events written to Google in one run.
MAX_WRITES = 150


def _within(budget: int, *lists: list) -> tuple[list, ...]:
    """Each list cut to what is left of the budget, in order: removals first, then changes, then additions."""
    out = []
    for items in lists:
        out.append(items[: max(0, budget)])
        budget -= len(out[-1])
    return tuple(out)


class AccessCache:
    """Short-lived Google access tokens, so a page of work refreshes each account once, not once per call."""

    def __init__(self) -> None:
        self._by: dict[str, tuple[str, float]] = {}

    def get(self, account_id: str) -> str | None:
        hit = self._by.get(account_id)
        return hit[0] if hit and hit[1] > time.monotonic() else None

    def put(self, account_id: str, token: str) -> None:
        self._by[account_id] = (token, time.monotonic() + ACCESS_SECONDS)

    def forget(self, account_id: str) -> None:
        self._by.pop(account_id, None)


class CalendarService:
    def __init__(self, repo: CalendarRepository, google: GoogleGateway, practice: PracticeGateway, settings: Settings, access: AccessCache, mint: Callable[[str, str], str] | None = None) -> None:
        self._repo, self._google, self._practice, self._cfg, self._access, self._mint = repo, google, practice, settings, access, mint

    # --- connecting ----------------------------------------------------------------------------------------------

    def start(self, user_id: str, workspace: str, email_hint: str | None) -> dict[str, str]:
        """Where to send the person to allow access. They come back to `complete` with a code, and the same state."""
        if not self._cfg.configured:
            raise ValidationFailed("Google isn't set up on the server yet. Add the OAuth client first (docs/CALENDAR_SETUP.md).")
        if workspace not in WORKSPACES:
            raise ValidationFailed("Choose a workspace for this calendar.")
        now = int(time.time())
        state = jwt.encode({"typ": "calendar-oauth", "sub": user_id, "ws": workspace, "nonce": uuid.uuid4().hex, "iat": now, "exp": now + STATE_MINUTES * 60}, self._cfg.state_secret, algorithm="HS256")
        return {"url": self._google.auth_url(state, (email_hint or "").strip() or None)}

    async def complete(self, code: str, state: str) -> tuple[str, str]:
        """Google sent the person back with a code. Returns the workspace and the account's email, or raises with what to tell them."""
        try:
            claims = jwt.decode(state, self._cfg.state_secret, algorithms=["HS256"])
            if claims.get("typ") != "calendar-oauth":
                raise jwt.InvalidTokenError("wrong kind")
        except jwt.PyJWTError as exc:
            raise ValidationFailed("That connection link has expired. Start again from Stiamo.") from exc
        user_id, workspace = claims["sub"], claims["ws"]
        try:
            tokens = await self._google.exchange(code)
            email = (await self._google.email(tokens.access)).lower()
        except GoogleError as exc:
            raise ValidationFailed(exc.message) from exc
        allowed = [e.lower() for e in self._cfg.allowed_google_emails]
        if allowed and email not in allowed:
            raise ValidationFailed(f"{email} isn't one of the Google accounts allowed here.")
        existing = next((a for a in await self._repo.accounts(user_id) if a.workspace == workspace and a.email == email), None)
        refresh = tokens.refresh or (existing.refresh_token if existing else None)
        if not refresh:
            raise ValidationFailed("Google didn't give lasting access. Remove Stiamo at myaccount.google.com/permissions, then connect again.")
        account = existing or Account(str(uuid.uuid4()), user_id, workspace, email, refresh, "ok", "", datetime.now(UTC))
        account.refresh_token, account.status, account.error = refresh, "ok", ""
        await self._repo.save_account(account)
        self._access.put(account.id, tokens.access)
        await self._find_calendars(account, tokens.access)
        return workspace, email

    async def _access_for(self, a: Account) -> str:
        cached = self._access.get(a.id)
        if cached:
            return cached
        try:
            token = await self._google.refresh(a.refresh_token)
        except GoogleError as exc:
            if exc.auth:
                a.status, a.error = "reconnect", "Google no longer allows Stiamo to read this calendar. Connect it again."
                await self._repo.save_account(a)
            raise
        self._access.put(a.id, token)
        return token

    async def _find_calendars(self, a: Account, access: str) -> list[Source]:
        """The calendars in the account. New ones start shown; ones that are gone from Google are forgotten."""
        have = {s.google_id: s for s in await self._repo.sources(a.user_id) if s.account_id == a.id}
        rows = await self._google.calendars(access)
        seen: set[str] = set()
        out: list[Source] = []
        for i, c in enumerate(rows):
            gid = c.get("id")
            if not gid:
                continue
            seen.add(gid)
            s = have.get(gid) or Source(str(uuid.uuid4()), a.id, a.user_id, gid, "", PALETTE[(len(have) + i) % len(PALETTE)], "reader", self._cfg.default_timezone, enabled=bool(c.get("primary") or c.get("selected", True)))
            s.name = (c.get("summaryOverride") or c.get("summary") or gid)[:300]
            s.role = c.get("accessRole") or s.role
            s.timezone = c.get("timeZone") or s.timezone
            if gid not in have and c.get("backgroundColor"):
                s.color = c["backgroundColor"][:9]
            await self._repo.save_source(s)
            out.append(s)
        for gid, s in have.items():
            if gid not in seen:
                await self._repo.delete_source(a.user_id, s.id)
        return out

    async def disconnect(self, user_id: str, account_id: str) -> None:
        """Forgets the account and what was read from it. Events Stiamo sent to Google stay there, and so does everything in Google."""
        a = await self._mine(user_id, account_id)
        try:
            await self._google.revoke(a.refresh_token)
        except GoogleError:
            log.info("could not revoke the token of %s", a.id)
        self._access.forget(a.id)
        await self._repo.delete_account(user_id, a.id)

    async def _mine(self, user_id: str, account_id: str) -> Account:
        a = await self._repo.account(user_id, account_id)
        if a is None:
            raise NotFound("No such account.")
        return a

    # --- what is connected -----------------------------------------------------------------------------------------

    async def timezone_of(self, user_id: str) -> str:
        p = await self._repo.prefs(user_id)
        return p.timezone if p else self._cfg.default_timezone

    async def set_timezone(self, user_id: str, tz: str) -> str:
        if zone(tz, default="UTC").key != tz:
            raise ValidationFailed("That isn't a time zone. Use a name like Asia/Kolkata.")
        await self._repo.save_prefs(Prefs(user_id, tz))
        return tz

    async def status(self, user_id: str) -> dict[str, Any]:
        accounts, sources = await self._repo.accounts(user_id), await self._repo.sources(user_id)
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {
            "configured": self._cfg.configured,
            "sends": bool(self._mint and self._cfg.practice_url),
            "timezone": await self.timezone_of(user_id),
            "accounts": [
                {
                    "id": a.id, "workspace": a.workspace, "email": a.email, "status": a.status, "error": a.error, "lastSyncAt": iso(a.last_sync_at),
                    "calendars": [{"id": s.id, "name": s.name, "color": s.color, "role": s.role, "writable": s.writable, "enabled": s.enabled, "export": s.export} for s in sources if s.account_id == a.id],
                }
                for a in accounts
            ],
        }

    async def set_source(self, user_id: str, source_id: str, *, enabled: bool | None, export: bool | None, color: str | None) -> None:
        sources = await self._repo.sources(user_id)
        s = next((x for x in sources if x.id == source_id), None)
        if s is None:
            raise NotFound("No such calendar.")
        if enabled is not None:
            s.enabled = enabled
        if color is not None:
            if not (color.startswith("#") and len(color) in (4, 7) and all(c in "0123456789abcdefABCDEF" for c in color[1:])):
                raise ValidationFailed("Give the colour as #RRGGBB.")
            s.color = color
        if export is not None:
            if export:
                if not s.writable:
                    raise ValidationFailed("You can only read this calendar, so Stiamo can't add to it.")
                accounts = {a.id: a for a in await self._repo.accounts(user_id)}
                workspace = accounts[s.account_id].workspace
                for other in sources:
                    if other.id != s.id and other.export and accounts[other.account_id].workspace == workspace:
                        other.export = False  # one place per workspace, so nothing is sent twice
                        await self._repo.save_source(other)
            s.export = export
        await self._repo.save_source(s)

    # --- reading -------------------------------------------------------------------------------------------------------

    async def events(self, user_id: str, start: date, end: date, workspace: str | None) -> dict[str, Any]:
        """The events from every calendar that is shown (or those of one workspace), on the person's own clock."""
        if end < start or (end - start).days > 400:
            raise ValidationFailed("Ask for at most about a year at a time.")
        tz = await self.timezone_of(user_id)
        z = zone(tz)
        accounts = {a.id: a for a in await self._repo.accounts(user_id)}
        chosen = [s for s in await self._repo.sources(user_id) if s.enabled and s.account_id in accounts and workspace in (None, accounts[s.account_id].workspace)]
        by_source = {s.id: s for s in chosen}
        lo = datetime.combine(start, clock.min, z).astimezone(UTC)
        hi = datetime.combine(end + timedelta(days=1), clock.min, z).astimezone(UTC)
        items = []
        for e in await self._repo.events_between(user_id, list(by_source), lo, hi):
            s = by_source[e.source_id]
            items.append({**view(e, tz), "workspace": accounts[s.account_id].workspace, "color": s.color, "calendar": s.name, "account": accounts[s.account_id].email})
        items.sort(key=lambda i: (i["date"], not i["allDay"], i.get("startMinute", 0)))
        return {"timezone": tz, "items": items}

    # --- syncing -------------------------------------------------------------------------------------------------------

    async def sync(self, user_id: str, *, token: str | None = None, account_id: str | None = None, timezone: str | None = None) -> dict[str, Any]:
        """Reads what changed in Google, and (when it can act as the owner) sends Stiamo's meetings and hearings there."""
        if timezone:
            await self.set_timezone(user_id, timezone)
        accounts = [a for a in await self._repo.accounts(user_id) if account_id in (None, a.id)]
        if account_id and not accounts:
            raise NotFound("No such account.")
        errors: list[str] = []
        pulled = 0
        for a in accounts:
            if a.status != "ok":
                continue
            try:
                access = await self._access_for(a)
                sources = [s for s in await self._find_calendars(a, access) if s.enabled or s.export]
                for s in sources:
                    pulled += await self._pull(access, s)
                a.last_sync_at, a.error = datetime.now(UTC), ""
                await self._repo.save_account(a)
            except GoogleError as exc:
                errors.append(f"{a.email}: {exc.message}")
                if not exc.auth:
                    a.error = exc.message
                    await self._repo.save_account(a)
        sent = {"added": 0, "changed": 0, "removed": 0}
        if token and self._cfg.practice_url:
            try:
                sent = await self._export(user_id, token, errors)
            except GoogleError as exc:
                errors.append(exc.message)
        return {"pulled": pulled, "sent": sent, "errors": errors[:MAX_ERRORS]}

    async def _pull(self, access: str, s: Source) -> int:
        now = datetime.now(UTC)
        token = s.sync_token
        if token and (not s.synced_through or now - s.synced_through > FULL_READ_EVERY):
            token = None
        window = dict(time_min=now - timedelta(days=self._cfg.window_back_days), time_max=now + timedelta(days=self._cfg.window_ahead_days))
        try:
            rows, next_token = await self._google.events(access, s.google_id, sync_token=token, **window)
        except SyncExpired:
            token = None
            rows, next_token = await self._google.events(access, s.google_id, sync_token=None, **window)
        if token is None:
            await self._repo.clear_events(s.id)
            s.synced_through = now
        keep, gone = [], []
        for raw in rows:
            e = event_from_google(raw, s)
            (keep if e else gone).append(e or raw.get("id"))
        await self._repo.upsert_events(keep)
        await self._repo.remove_events(s.id, [g for g in gone if g])
        s.sync_token = next_token
        await self._repo.save_source(s)
        return len(keep)

    async def _export(self, user_id: str, token: str, errors: list[str]) -> dict[str, int]:
        """Makes Google hold the same meetings and hearings Stiamo does, in the calendar chosen for each workspace."""
        accounts = {a.id: a for a in await self._repo.accounts(user_id)}
        sources = {s.id: s for s in await self._repo.sources(user_id)}
        target = {accounts[s.account_id].workspace: s.id for s in sources.values() if s.export and s.writable and s.account_id in accounts and accounts[s.account_id].status == "ok"}
        links = await self._repo.links(user_id)
        if not target and not links:
            return {"added": 0, "changed": 0, "removed": 0}
        tz = await self.timezone_of(user_id)
        records = {t: await self._practice.records(token, t) for t in ("meetings", "hearings", "cases", "tasks", "follow-ups")}
        desired = desired_events(records["meetings"], records["hearings"], records["cases"], tz, datetime.now(UTC).date(), tasks=records["tasks"], follow_ups=records["follow-ups"])
        plan = plan_export(desired, links, target)
        # Google limits how fast events can be written, so a first run with a lot to send does it over a few runs.
        plan.delete, plan.update, plan.insert = _within(MAX_WRITES, plan.delete, plan.update, plan.insert)
        counts = {"added": 0, "changed": 0, "removed": 0}

        async def at(source_id: str) -> tuple[str, str]:
            s = sources[source_id]
            return await self._access_for(accounts[s.account_id]), s.google_id

        for link in plan.delete:
            try:
                if link.source_id in sources:
                    access, cal = await at(link.source_id)
                    await self._google.delete(access, cal, link.google_id)
                await self._repo.delete_link(user_id, link.ref)
                counts["removed"] += 1
            except GoogleError as exc:
                errors.append(exc.message)
        for link, body in plan.update:
            try:
                access, cal = await at(link.source_id)
                await self._google.update(access, cal, link.google_id, body)
                await self._repo.save_link(Link(user_id, link.ref, link.source_id, link.google_id, digest(body)))
                counts["changed"] += 1
            except GoogleError as exc:
                errors.append(exc.message)
        for ref, source_id, body in plan.insert:
            try:
                access, cal = await at(source_id)
                gid = await self._google.insert(access, cal, body)
                await self._repo.save_link(Link(user_id, ref, source_id, gid, digest(body)))
                counts["added"] += 1
            except GoogleError as exc:
                errors.append(exc.message)
        return counts

    async def sync_everyone(self) -> None:
        """The background run: every connected account, as its owner."""
        by_user: dict[str, str] = {}
        for a in await self._repo.all_accounts():
            if a.status == "ok":
                by_user.setdefault(a.user_id, "")
        for user_id in by_user:
            try:
                await self.sync(user_id, token=self._mint(user_id, "") if self._mint else None)
            except Exception:  # noqa: BLE001  one person's trouble must not stop the others' calendars
                log.exception("could not sync the calendars of %s", user_id)
