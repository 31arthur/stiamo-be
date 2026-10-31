"""Working through a whole inbox in the background.

A sweep runs an agent again and again, a batch at a time, until nothing is left in range, someone stops it, or something goes wrong.
It runs inside this process, not in anyone's browser, so closing or refreshing the page doesn't stop it. Each run is an ordinary run of
the agent: it is kept as an execution, and nothing about what it decides or moves is different. Only one sweep per agent at a time.

The state is held in memory: if the service restarts, the sweep ends (what its runs already did is kept) and is started again by hand.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from stiamo_common.errors import AppError, Conflict

log = logging.getLogger(__name__)

#: A safety net, not a target: at 500 emails a run this is far more than any inbox.
MAX_RUNS = 1000
#: Another run of the same agent may be finishing; wait for it this many times, this long.
WAITS, WAIT_SECONDS = 24, 5.0


@dataclass
class Sweep:
    workflow_id: str
    user_id: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    #: running, done, stopped or failed.
    state: str = "running"
    runs: int = 0
    judged: int = 0
    moved: int = 0
    kept: int = 0
    to_review: int = 0
    protected: int = 0
    failed_items: int = 0
    in_range: int | None = None
    remaining: int | None = None
    last_run_id: str | None = None
    error: str | None = None
    stop_requested: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def json(self) -> dict[str, Any]:
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {
            "id": self.id, "state": self.state, "runs": self.runs, "judged": self.judged, "moved": self.moved, "kept": self.kept, "toReview": self.to_review,
            "protected": self.protected, "failedItems": self.failed_items, "inRange": self.in_range, "remaining": self.remaining, "lastRunId": self.last_run_id,
            "error": self.error, "stopRequested": self.stop_requested, "startedAt": iso(self.started_at), "finishedAt": iso(self.finished_at),
        }


RunOnce = Callable[[], Awaitable[dict[str, Any]]]


class SweepManager:
    def __init__(self) -> None:
        self._sweeps: dict[str, Sweep] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def get(self, workflow_id: str) -> Sweep | None:
        return self._sweeps.get(workflow_id)

    def is_active(self, workflow_id: str) -> bool:
        s = self._sweeps.get(workflow_id)
        return bool(s and s.state == "running")

    def start(self, workflow_id: str, user_id: str, run_once: RunOnce) -> Sweep:
        if self.is_active(workflow_id):
            raise Conflict("This agent is already running through the whole inbox.")
        sweep = Sweep(workflow_id, user_id)
        self._sweeps[workflow_id] = sweep
        task = asyncio.get_running_loop().create_task(self._loop(sweep, run_once))
        self._tasks[workflow_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(workflow_id, None))
        return sweep

    def stop(self, workflow_id: str) -> Sweep | None:
        """Asks it to stop after the run in progress: that run is never cut short half way."""
        s = self._sweeps.get(workflow_id)
        if s and s.state == "running":
            s.stop_requested = True
        return s

    async def close(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        for t in list(self._tasks.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self, sweep: Sweep, run_once: RunOnce) -> None:
        try:
            while not sweep.stop_requested and sweep.runs < MAX_RUNS:
                result = await self._one(sweep, run_once)
                counts, stats = result.get("counts", {}), result.get("stats", {})
                sweep.runs += 1
                sweep.last_run_id = result.get("runId")
                sweep.judged += counts.get("items", 0)
                sweep.moved += counts.get("applied", 0)
                sweep.kept += counts.get("kept", 0)
                sweep.to_review += counts.get("needsReview", 0) + counts.get("proposed", 0)
                sweep.protected += counts.get("protected", 0)
                sweep.failed_items += counts.get("failed", 0)
                sweep.in_range = stats.get("inRange", sweep.in_range)
                sweep.remaining = stats.get("remaining", 0)
                items = counts.get("items", 0)
                if not sweep.remaining or items == 0 or counts.get("failed", 0) == items:
                    break
            sweep.state = "stopped" if sweep.stop_requested else "done"
        except asyncio.CancelledError:
            sweep.state, sweep.error = "stopped", "The service restarted. Start it again to carry on."
            raise
        except AppError as exc:
            sweep.state, sweep.error = "failed", exc.message
        except Exception:  # noqa: BLE001
            log.exception("sweep of %s failed", sweep.workflow_id)
            sweep.state, sweep.error = "failed", "Something went wrong inside the agent."
        finally:
            sweep.finished_at = datetime.now(UTC)

    async def _one(self, sweep: Sweep, run_once: RunOnce) -> dict[str, Any]:
        """One run. If the agent is busy with a run someone started by hand, wait for it rather than give up."""
        for attempt in range(WAITS + 1):
            try:
                return await run_once()
            except Conflict:
                if attempt == WAITS or sweep.stop_requested:
                    raise
                await asyncio.sleep(WAIT_SECONDS)
        raise AssertionError("unreachable")
