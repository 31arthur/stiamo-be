"""Listening to a mailbox: running an agent when new mail arrives, instead of when someone presses Run.

The mail service holds each mailbox open with IMAP IDLE and says the moment something changes (a server-sent event). This keeps one
watcher on every mailbox an agent listens to; a change (or a reconnect, to catch what came while it was down) wakes the agent once,
after a short pause so a burst of mail is one run. The run is a background sweep, so it carries on until nothing new is left.

Held in memory and started again when the service starts, from the agents that are set to listen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

#: Opens the mail service's feed for one folder of one mailbox: yields "changed" when mail changes and "live" when the feed is up.
Events = Callable[[str, str, str], AsyncIterator[str]]
#: Gives a token for the owner (the feed checks it once, when it connects).
Mint = Callable[[], str]
#: Starts a run of the agent (a background sweep); returns at once.
Trigger = Callable[[], Awaitable[None]]


@dataclass
class Listening:
    workflow_id: str
    accounts: list[str]
    folder: str
    state: str = "connecting"
    last_event_at: datetime | None = None
    last_run_at: datetime | None = None
    error: str | None = None
    runs: int = 0
    tasks: list[asyncio.Task[None]] = field(default_factory=list)

    def json(self) -> dict[str, Any]:
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {"listening": True, "state": self.state, "mailboxes": len(self.accounts), "lastEventAt": iso(self.last_event_at), "lastRunAt": iso(self.last_run_at), "runs": self.runs, "error": self.error}


class ListenerManager:
    def __init__(self, events: Events, debounce: float = 5.0) -> None:
        self._events = events
        self.debounce = debounce
        self._by: dict[str, Listening] = {}

    def status(self, workflow_id: str) -> dict[str, Any]:
        s = self._by.get(workflow_id)
        return s.json() if s else {"listening": False, "state": "off", "mailboxes": 0, "lastEventAt": None, "lastRunAt": None, "runs": 0, "error": None}

    def ensure(self, workflow_id: str, accounts: list[str], folder: str, mint: Mint, trigger: Trigger) -> None:
        """Listens to these mailboxes for this agent (replacing what it listened to before)."""
        self.stop(workflow_id)
        if not accounts:
            return
        s = Listening(workflow_id, list(dict.fromkeys(accounts)), folder)
        wake = asyncio.Event()
        wake.set()  # once at the start, to catch up on whatever came while nobody was listening
        loop = asyncio.get_running_loop()
        s.tasks = [loop.create_task(self._watch(s, a, mint, wake)) for a in s.accounts] + [loop.create_task(self._runner(s, wake, trigger))]
        self._by[workflow_id] = s

    def stop(self, workflow_id: str) -> None:
        s = self._by.pop(workflow_id, None)
        if s:
            for t in s.tasks:
                t.cancel()

    async def close(self) -> None:
        tasks = [t for s in self._by.values() for t in s.tasks]
        for wid in list(self._by):
            self.stop(wid)
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _watch(self, s: Listening, account: str, mint: Mint, wake: asyncio.Event) -> None:
        delay = 2.0
        while True:
            try:
                async for event in self._events(account, s.folder, mint()):
                    delay = 2.0
                    s.state, s.error = "listening", None
                    if event in ("changed", "live"):
                        s.last_event_at = datetime.now(UTC)
                        wake.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  a dropped feed is expected: say so, wait, reconnect
                s.state, s.error = "reconnecting", str(exc)[:200] or "The mail feed dropped."
                log.info("mail feed for %s lost: %s", account, exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120.0)
            wake.set()  # whatever arrived while it was down

    async def _runner(self, s: Listening, wake: asyncio.Event, trigger: Trigger) -> None:
        while True:
            await wake.wait()
            await asyncio.sleep(self.debounce)  # a burst of mail is one run
            wake.clear()
            try:
                await trigger()
                s.runs += 1
                s.last_run_at = datetime.now(UTC)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                s.error = str(exc)[:200] or "The run couldn't be started."
                log.exception("listener run for %s failed", s.workflow_id)
