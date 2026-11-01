"""Running an agent every so often.

For sources that can't say when something new arrives, like news feeds: the agent is run once when the schedule is set, then every
`minutes`. A run is a background sweep, so it carries on until what is new has been read, and if one is still going when the time comes
the new one is skipped. Held in memory and started again with the service, from the agents that have a schedule.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Scheduled:
    workflow_id: str
    minutes: int
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    runs: int = 0
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class ScheduleManager:
    def __init__(self, seconds_per_minute: float = 60.0) -> None:
        #: What a minute is: 60 seconds, shorter in a test.
        self.unit = seconds_per_minute
        self._by: dict[str, Scheduled] = {}

    def status(self, workflow_id: str) -> dict[str, Any]:
        s = self._by.get(workflow_id)
        if s is None:
            return {"scheduled": False, "everyMinutes": None, "nextRunAt": None, "lastRunAt": None, "runs": 0, "error": None}
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {"scheduled": True, "everyMinutes": s.minutes, "nextRunAt": iso(s.next_run_at), "lastRunAt": iso(s.last_run_at), "runs": s.runs, "error": s.error}

    def ensure(self, workflow_id: str, minutes: int, trigger: Callable[[], Awaitable[None]]) -> None:
        """Runs it now and then every `minutes` (replacing any earlier schedule for it)."""
        self.stop(workflow_id)
        s = Scheduled(workflow_id, minutes)
        s.task = asyncio.get_running_loop().create_task(self._loop(s, trigger))
        self._by[workflow_id] = s

    def stop(self, workflow_id: str) -> None:
        s = self._by.pop(workflow_id, None)
        if s and s.task:
            s.task.cancel()

    async def close(self) -> None:
        tasks = [s.task for s in self._by.values() if s.task]
        for wid in list(self._by):
            self.stop(wid)
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _loop(self, s: Scheduled, trigger: Callable[[], Awaitable[None]]) -> None:
        while True:
            try:
                await trigger()
                s.runs += 1
                s.last_run_at = datetime.now(UTC)
                s.error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  one failed start must not end the schedule
                s.error = str(exc)[:200] or "The run couldn't be started."
                log.exception("scheduled run of %s failed", s.workflow_id)
            s.next_run_at = datetime.now(UTC) + timedelta(seconds=s.minutes * self.unit)
            await asyncio.sleep(s.minutes * self.unit)
