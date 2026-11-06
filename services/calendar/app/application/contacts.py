"""Google contacts, per workspace: linking Google accounts, reading their contacts, and listing them.

Any number of Google accounts can be linked to a workspace, and one Google account can be linked to several workspaces. Each link is its own
row with its own copy of the contacts, so a workspace shows its contacts without depending on another.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import jwt
from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import ContactRepository, GoogleError, PeopleGateway
from app.application.service import AccessCache
from app.config import Settings
from app.domain.contacts import WORKSPACES, ContactAccount, contact_from_person, merge_across, view

log = logging.getLogger(__name__)

STATE_TYPE = "contacts-oauth"
STATE_MINUTES = 15
MAX_LISTED = 5000


def _account_json(a: ContactAccount) -> dict[str, Any]:
    iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None  # noqa: E731
    return {"id": a.id, "workspace": a.workspace, "email": a.email, "status": a.status, "error": a.error, "contacts": a.count, "lastSyncAt": iso(a.last_sync_at)}


class ContactService:
    def __init__(self, repo: ContactRepository, people: PeopleGateway, settings: Settings, access: AccessCache) -> None:
        self._repo, self._people, self._cfg, self._access = repo, people, settings, access

    # --- linking ---------------------------------------------------------------------------------------------------

    def start(self, user_id: str, workspace: str, email_hint: str | None) -> dict[str, str]:
        if not self._cfg.configured:
            raise ValidationFailed("Google isn't set up on the server yet. Add the OAuth client first (docs/CONTACTS_SETUP.md).")
        if workspace not in WORKSPACES:
            raise ValidationFailed("Choose a workspace for these contacts.")
        now = int(time.time())
        state = jwt.encode({"typ": STATE_TYPE, "sub": user_id, "ws": workspace, "nonce": uuid.uuid4().hex, "iat": now, "exp": now + STATE_MINUTES * 60}, self._cfg.state_secret, algorithm="HS256")
        return {"url": self._people.auth_url(state, (email_hint or "").strip() or None)}

    async def complete(self, code: str, state: str) -> tuple[str, str]:
        """Google sent the person back with a code. Returns the workspace and the account's email, or raises with what to tell them."""
        try:
            claims = jwt.decode(state, self._cfg.state_secret, algorithms=["HS256"])
            if claims.get("typ") != STATE_TYPE:
                raise jwt.InvalidTokenError("wrong kind")
        except jwt.PyJWTError as exc:
            raise ValidationFailed("That connection link has expired. Start again from Stiamo.") from exc
        user_id, workspace = claims["sub"], claims["ws"]
        try:
            tokens = await self._people.exchange(code)
            email = (await self._people.email(tokens.access)).lower()
        except GoogleError as exc:
            raise ValidationFailed(exc.message) from exc
        allowed = [e.lower() for e in self._cfg.allowed_google_emails]
        if allowed and email not in allowed:
            raise ValidationFailed(f"{email} isn't one of the Google accounts allowed here.")
        existing = next((a for a in await self._repo.accounts(user_id) if a.workspace == workspace and a.email == email), None)
        refresh = tokens.refresh or (existing.refresh_token if existing else None)
        if not refresh:
            raise ValidationFailed("Google didn't give lasting access. Remove Stiamo at myaccount.google.com/permissions, then connect again.")
        account = existing or ContactAccount(str(uuid.uuid4()), user_id, workspace, email, refresh, created_at=datetime.now(UTC))
        account.refresh_token, account.status, account.error = refresh, "ok", ""
        await self._repo.save_account(account)
        self._access.put(account.id, tokens.access)
        try:
            await self._read(account, tokens.access)
        except GoogleError as exc:
            account.error = exc.message[:300]
            await self._repo.save_account(account)
        return workspace, email

    async def link(self, user_id: str, account_id: str, workspace: str) -> dict[str, Any]:
        """The same Google account, in another workspace too: no new sign-in, the permission it already gave is used."""
        if workspace not in WORKSPACES:
            raise ValidationFailed("Choose a workspace.")
        source = await self._account(user_id, account_id)
        if any(a.workspace == workspace and a.email == source.email for a in await self._repo.accounts(user_id)):
            raise ValidationFailed(f"{source.email} is already linked to that workspace.")
        a = ContactAccount(str(uuid.uuid4()), user_id, workspace, source.email, source.refresh_token, created_at=datetime.now(UTC))
        await self._repo.save_account(a)
        await self._sync_one(a)
        return _account_json(a)

    async def disconnect(self, user_id: str, account_id: str) -> None:
        a = await self._account(user_id, account_id)
        await self._repo.delete_account(user_id, a.id)
        self._access.forget(a.id)
        # Google's permission is revoked only when no other link (another workspace) still uses it.
        if not any(o.refresh_token == a.refresh_token for o in await self._repo.accounts(user_id)):
            try:
                await self._people.revoke(a.refresh_token)
            except GoogleError:
                log.info("could not revoke a contacts token at Google")

    # --- reading ---------------------------------------------------------------------------------------------------

    async def _account(self, user_id: str, account_id: str) -> ContactAccount:
        a = await self._repo.account(user_id, account_id)
        if a is None:
            raise NotFound("That account isn't linked.")
        return a

    async def _access_for(self, a: ContactAccount) -> str:
        cached = self._access.get(a.id)
        if cached:
            return cached
        try:
            token = await self._people.refresh(a.refresh_token)
        except GoogleError as exc:
            if exc.auth:
                a.status, a.error = "reconnect", "Google no longer allows Stiamo to read these contacts. Link the account again."
                await self._repo.save_account(a)
            raise
        self._access.put(a.id, token)
        return token

    async def _read(self, a: ContactAccount, access: str) -> int:
        people = await self._people.connections(access)
        contacts = [c for c in (contact_from_person(a, p) for p in people) if c]
        a.count = await self._repo.replace_contacts(a, contacts)
        a.last_sync_at, a.status, a.error = datetime.now(UTC), "ok", ""
        await self._repo.save_account(a)
        return a.count

    async def _sync_one(self, a: ContactAccount) -> int:
        try:
            return await self._read(a, await self._access_for(a))
        except GoogleError as exc:
            if not exc.auth:
                a.error = exc.message[:300]
                await self._repo.save_account(a)
            raise

    async def sync(self, user_id: str, account_id: str | None = None) -> dict[str, Any]:
        """Reads the contacts again, for one account link or all of the person's. Returns what each found, and what went wrong."""
        accounts = [await self._account(user_id, account_id)] if account_id else await self._repo.accounts(user_id)
        found, problems = 0, []
        for a in accounts:
            try:
                found += await self._sync_one(a)
            except GoogleError as exc:
                problems.append(f"{a.email}: {exc.message}")
        return {"contacts": found, "problems": problems}

    async def sync_everyone(self) -> None:
        for a in await self._repo.all_accounts():
            if a.status != "ok":
                continue
            try:
                await self._sync_one(a)
            except GoogleError:
                log.info("could not read the contacts of an account")

    # --- showing -----------------------------------------------------------------------------------------------

    async def status(self, user_id: str) -> dict[str, Any]:
        return {"configured": self._cfg.configured, "accounts": [_account_json(a) for a in await self._repo.accounts(user_id)]}

    async def contacts(self, user_id: str, workspace: str | None, q: str, limit: int, offset: int) -> dict[str, Any]:
        """One workspace's contacts, or everyone's together (a contact in several workspaces shown once). Sorted by name."""
        if workspace is not None and workspace not in WORKSPACES:
            raise ValidationFailed("That isn't one of your workspaces.")
        found = await self._repo.contacts(user_id, workspace, q.strip().lower() or None, MAX_LISTED)
        if workspace is None:
            by_account = {a.id: a.email for a in await self._repo.accounts(user_id)}
            items = merge_across(found, by_account)
        else:
            emails = {a.id: a.email for a in await self._repo.accounts(user_id)}
            items = [view(c, [c.workspace], [emails.get(c.account_id, "")]) for c in found]
        return {"items": items[offset : offset + limit], "total": len(items)}
