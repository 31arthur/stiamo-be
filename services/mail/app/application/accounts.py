"""Connecting mailboxes and reading them. Every call is for one person and only ever sees that person's mailboxes."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from stiamo_common.errors import AppError, NotFound, ValidationFailed

from app.application.live import LiveHub
from app.application.ports import AccountRepository, MailError, MailGateway
from app.domain.mail import SECURITY, WORKSPACES, Account, Credentials, Download, Folder, Message, Page

MAX_ACCOUNTS = 12
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class MailUnavailable(AppError):
    """The mail server could not be reached or refused the request."""

    status_code = 502
    code = "mail_unavailable"


class MailAuth(AppError):
    """The mail server did not accept the username and password."""

    status_code = 422
    code = "mail_auth_failed"


def _wrap(error: MailError) -> AppError:
    return MailAuth(str(error)) if error.auth else MailUnavailable(str(error))


class MailService:
    def __init__(self, accounts: AccountRepository, gateway: MailGateway, live: LiveHub) -> None:
        self.accounts = accounts
        self.gateway = gateway
        self.live = live

    async def _call(self, fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except MailError as e:
            raise _wrap(e) from None

    async def _account(self, owner: str, account_id: str) -> Account:
        account = await self.accounts.get(owner, account_id)
        if account is None:
            raise NotFound("That mailbox isn’t connected.")
        return account

    # -- mailboxes -------------------------------------------------------------------------------------------------------------

    async def all_for(self, owner: str) -> list[Account]:
        return await self.accounts.all_for(owner)

    async def connect(self, owner: str, *, workspace: str, label: str, address: str, host: str, port: int, security: str, username: str, password: str) -> Account:
        existing = await self.accounts.all_for(owner)
        if len(existing) >= MAX_ACCOUNTS:
            raise ValidationFailed(f"You can connect up to {MAX_ACCOUNTS} mailboxes.")
        creds = self._credentials(host, port, security, username or address, password)
        self._check_workspace(workspace)
        if any(a.workspace == workspace and a.address.lower() == address.lower() for a in existing):
            raise ValidationFailed("That mailbox is already connected to this workspace.")
        await self._call(self.gateway.check, creds)  # fails here, with the server's own reason, rather than later on an empty inbox
        account = Account(str(uuid.uuid4()), owner, workspace, label.strip() or address, address.strip(), creds, datetime.now(UTC))
        await self.accounts.add(account)
        return account

    async def edit(self, owner: str, account_id: str, **changes: Any) -> Account:
        account = await self._account(owner, account_id)
        c = account.credentials
        creds = self._credentials(changes.get("host") or c.host, changes.get("port") or c.port, changes.get("security") or c.security, changes.get("username") or c.username, changes.get("password") or c.password)
        workspace = changes.get("workspace") or account.workspace
        self._check_workspace(workspace)
        if creds != c:
            await self._call(self.gateway.check, creds)
        updated = replace(account, workspace=workspace, label=(changes.get("label") or account.label).strip(), credentials=creds, error="")
        await self.accounts.update(updated)
        if creds != c:
            self.live.drop(account_id)
        return updated

    async def disconnect(self, owner: str, account_id: str) -> None:
        if not await self.accounts.remove(owner, account_id):
            raise NotFound("That mailbox isn’t connected.")
        self.live.drop(account_id)

    async def test(self, owner: str, account_id: str) -> None:
        account = await self._account(owner, account_id)
        try:
            await self._call(self.gateway.check, account.credentials)
        except AppError as e:
            await self.accounts.update(replace(account, error=e.message[:300]))
            raise
        if account.error:
            await self.accounts.update(replace(account, error=""))

    # -- reading ---------------------------------------------------------------------------------------------------------------

    async def folders(self, owner: str, account_id: str) -> list[Folder]:
        return await self._reading(owner, account_id, lambda a: self.gateway.folders(a.credentials))

    async def page(self, owner: str, account_id: str, folder: str, *, before: int | None, limit: int, query: str) -> Page:
        return await self._reading(owner, account_id, lambda a: self.gateway.page(a.credentials, folder, before=before, limit=limit, query=query.strip()))

    async def message(self, owner: str, account_id: str, folder: str, uid: int) -> Message:
        return await self._reading(owner, account_id, lambda a: self.gateway.message(a.credentials, folder, uid))

    async def attachment(self, owner: str, account_id: str, folder: str, uid: int, index: int) -> Download:
        return await self._reading(owner, account_id, lambda a: self.gateway.attachment(a.credentials, folder, uid, index))

    async def uids(self, owner: str, account_id: str, folder: str, since, before):
        return await self._reading(owner, account_id, lambda a: self.gateway.uids(a.credentials, folder, since=since, before=before))

    async def summaries(self, owner: str, account_id: str, folder: str, uids: list[int]):
        return await self._reading(owner, account_id, lambda a: self.gateway.summaries(a.credentials, folder, uids))

    async def correspondents(self, owner: str, account_id: str) -> list[str]:
        return await self._reading(owner, account_id, lambda a: self.gateway.correspondents(a.credentials))

    async def trash(self, owner: str, account_id: str, folder: str, uids: list[int]) -> int:
        return await self._reading(owner, account_id, lambda a: self.gateway.move_to_trash(a.credentials, folder, uids))

    async def mark(self, owner: str, account_id: str, folder: str, uid: int, *, seen: bool | None, flagged: bool | None) -> None:
        await self._reading(owner, account_id, lambda a: self.gateway.set_flags(a.credentials, folder, uid, seen=seen, flagged=flagged))

    async def _reading(self, owner: str, account_id: str, work):
        account = await self._account(owner, account_id)
        try:
            result = await asyncio.to_thread(work, account)
        except MailError as e:
            raise _wrap(e) from None
        if account.error:  # reachable again
            await self.accounts.update(replace(account, error=""))
        return result

    # -- checks ----------------------------------------------------------------------------------------------------------------

    @staticmethod
    def _check_workspace(workspace: str) -> None:
        if workspace not in WORKSPACES:
            raise ValidationFailed("Choose which workspace this mailbox belongs to.")

    @staticmethod
    def _credentials(host: str, port: int, security: str, username: str, password: str) -> Credentials:
        host = host.strip().lower()
        if not _HOST.match(host):
            raise ValidationFailed("That doesn’t look like a mail server address (for example imap.gmail.com).")
        if security not in SECURITY:
            raise ValidationFailed("Security must be ssl or starttls.")
        if not 1 <= port <= 65535:
            raise ValidationFailed("The port must be between 1 and 65535.")
        if not username.strip() or not password:
            raise ValidationFailed("Enter the username and password for this mailbox.")
        return Credentials(host, port, security, username.strip(), password)
