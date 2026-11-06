"""The priority mail watch: makes sure every workspace with a mailbox has an agent that listens to it, all the time.

The agent is an ordinary one (it shows in Developer, has its history and can be changed), but nobody has to build it: this creates it when a
mailbox is connected, keeps it pointed at the workspace's mailboxes, and starts it listening. It is checked when the service starts, every
so often, and whenever the owner opens the app. Switching a workspace's watch off stops it listening and keeps it off until switched on.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.ports import DataGateway, SettingsRepository, UserSettings, Workflow, WorkflowRepository
from app.application.services import Who, selection_of
from app.domain.agents import WORKSPACES
from app.domain.llm import AiError
from app.domain.mailwatch import NAMES, watch_config, watch_description, watch_id, watch_name, workspace_of

log = logging.getLogger(__name__)


class MailWatchService:
    def __init__(
        self,
        workflows: WorkflowRepository,
        settings: SettingsRepository,
        data_factory: Callable[[str], DataGateway],
        listen: Callable[[Who, Workflow], Awaitable[None]],
        listener_status: Callable[[str], dict[str, Any]],
        phones: Callable[[Who, str], Awaitable[str]],
    ) -> None:
        self._workflows, self._settings, self._data, self._listen, self._status, self._phones = workflows, settings, data_factory, listen, listener_status, phones

    async def _load(self, who: Who) -> UserSettings:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        s.email = who.email or s.email
        return s

    @staticmethod
    def _off(s: UserSettings) -> set[str]:
        return {w for w in (s.mailwatch.get("off") or []) if w in WORKSPACES}

    # --- keeping them there -------------------------------------------------------------------------------------

    async def ensure(self, who: Who, token: str) -> list[str]:
        """A watch for each workspace that has a mailbox, pointed at that workspace's mailboxes and listening, unless switched off.
        Returns the workspaces whose watch was made or changed. What the person changed in a watch (its prompt, labels) is left as it is."""
        try:
            accounts = await self._data(token).mail_accounts()
        except AiError:
            return []  # the mail service is away: nothing to do until it is back
        by_ws: dict[str, list[str]] = {}
        for a in accounts:
            if a.get("workspace") in WORKSPACES and a.get("id"):
                by_ws.setdefault(a["workspace"], []).append(str(a["id"]))
        s = await self._load(who)
        off = self._off(s)
        have = {w.id: w for w in await self._workflows.all(who.user_id)}
        changed: list[str] = []
        for ws, ids in by_ws.items():
            wid = watch_id(ws, who.user_id)
            w = have.get(wid)
            if w is None:
                if ws in off:
                    continue
                cfg = watch_config(ws, ids)
                w = Workflow(wid, who.user_id, watch_name(ws), watch_description(ws), cfg, 1, datetime.now(UTC))
                await self._workflows.save(w)
                s.workspaces = {**s.workspaces, wid: [ws]}
                await self._settings.save(s)
                changed.append(ws)
            elif sorted(w.config.get("source", {}).get("accountIds") or []) != sorted(ids):
                w.config = {**w.config, "source": {**w.config.get("source", {}), "accountIds": sorted(ids)}}
                w.version += 1
                await self._workflows.save(w)
                changed.append(ws)
            if ws not in off:
                await self._listen(who, w)
        return changed

    async def set_enabled(self, who: Who, workspace: str, enabled: bool) -> None:
        """Pauses or resumes a workspace's watch. Paused it doesn't listen, and isn't made again; its history is kept."""
        if workspace not in WORKSPACES:
            raise ValidationFailed("That isn't one of your workspaces.")
        s = await self._load(who)
        off = self._off(s)
        (off.discard if enabled else off.add)(workspace)
        s.mailwatch = {**s.mailwatch, "off": sorted(off)}
        await self._settings.save(s)
        w = await self._workflows.get(who.user_id, watch_id(workspace, who.user_id))
        if w is not None:
            w.config = {**w.config, "listen": enabled}
            await self._workflows.save(w)
            await self._listen(who, w)

    async def forget(self, who: Who, workflow_id: str) -> None:
        """A watch the person deleted is not made again: it counts as switched off."""
        ws = workspace_of(workflow_id)
        if ws is None:
            return
        s = await self._load(who)
        s.mailwatch = {**s.mailwatch, "off": sorted(self._off(s) | {ws})}
        await self._settings.save(s)

    # --- showing them ----------------------------------------------------------------------------------------------

    async def view(self, who: Who, token: str) -> dict[str, Any]:
        """Each workspace with a mailbox: its watch, whether it is listening, and what stands between it and the phone."""
        await self.ensure(who, token)
        try:
            accounts = await self._data(token).mail_accounts()
        except AiError:
            accounts = []
        s = await self._load(who)
        off = self._off(s)
        out = []
        for ws in WORKSPACES:
            boxes = [a for a in accounts if a.get("workspace") == ws]
            if not boxes:
                continue
            wid = watch_id(ws, who.user_id)
            w = await self._workflows.get(who.user_id, wid)
            st = self._status(wid)
            out.append({
                "workspace": ws, "name": NAMES.get(ws, ws), "agentId": wid if w else None, "enabled": ws not in off,
                "mailboxes": [str(a.get("address") or a.get("label") or a.get("id")) for a in boxes],
                "listening": bool(st.get("listening")) and st.get("state") != "off", "state": st.get("state", "off"), "error": st.get("error"),
                "lastRunAt": st.get("lastRunAt"), "model": bool(selection_of(s, wid)[0]),
                "phone": await self._phones(who, ws),
            })
        return {"workspaces": out}

