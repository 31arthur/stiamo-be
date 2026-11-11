"""Running something once a day at a set time of day: the daily planner's evening plan.

One loop per person, held in memory and started again with the service from everyone whose plan is switched on. A day that fails is
logged and the next one still runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.application.dayplans import seconds_until

log = logging.getLogger(__name__)


class DailyManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._status: dict[str, dict[str, Any]] = {}
        #: How long to wait for the next time of day: replaced by a test, which can't wait until nine.
        self.delay_for: Callable[[str, str], float] = lambda at, zone: seconds_until(datetime.now(UTC), at, zone)

    def status(self, user_id: str) -> dict[str, Any]:
        return self._status.get(user_id) or {"scheduled": False, "at": None, "timezone": None, "nextRunAt": None, "lastRunAt": None, "runs": 0, "error": None}

    def ensure(self, user_id: str, at: str, zone: str, trigger: Callable[[], Awaitable[None]]) -> None:
        """Runs `trigger` every day at `at` in `zone` (replacing any earlier time for this person)."""
        self.stop(user_id)
        self._status[user_id] = {"scheduled": True, "at": at, "timezone": zone, "nextRunAt": None, "lastRunAt": None, "runs": 0, "error": None}
        self._tasks[user_id] = asyncio.get_running_loop().create_task(self._loop(user_id, at, zone, trigger))

    def stop_prefix(self, prefix: str) -> None:
        """Stops every schedule whose key starts with this (all of one person's workflow schedules)."""
        for key in [k for k in self._tasks if k.startswith(prefix)]:
            self.stop(key)

    def keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._tasks if k.startswith(prefix)]

    def stop(self, user_id: str) -> None:
        t = self._tasks.pop(user_id, None)
        if t:
            t.cancel()
        self._status.pop(user_id, None)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for uid in list(self._tasks):
            self.stop(uid)
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _loop(self, user_id: str, at: str, zone: str, trigger: Callable[[], Awaitable[None]]) -> None:
        st = self._status[user_id]
        while True:
            wait = self.delay_for(at, zone)
            st["nextRunAt"] = datetime.fromtimestamp(datetime.now(UTC).timestamp() + wait, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            await asyncio.sleep(wait)
            try:
                await trigger()
                st["runs"] += 1
                st["lastRunAt"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                st["error"] = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  one bad evening must not end the schedule
                st["error"] = str(getattr(exc, "message", None) or exc)[:200] or "The plan couldn't be made."
                log.exception("the evening plan for %s failed", user_id)
