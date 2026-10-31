"""Live updates. A mailbox is watched with IMAP IDLE only while someone has it open: the first viewer starts one connection to the
mail server, every further viewer shares it, and the last one leaving closes it. The server pushes; nothing here polls.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from app.application.ports import MailGateway
from app.domain.mail import Account


@dataclass
class _Watch:
    stop: Event = field(default_factory=Event)
    queues: set[asyncio.Queue] = field(default_factory=set)
    state: str = "connecting"
    task: asyncio.Task | None = None


class LiveHub:
    def __init__(self, gateway: MailGateway) -> None:
        self.gateway = gateway
        self._watches: dict[tuple[str, str], _Watch] = {}

    def watching(self) -> int:
        return len(self._watches)

    @contextlib.asynccontextmanager
    async def listen(self, account: Account, folder: str) -> AsyncIterator[asyncio.Queue[dict[str, Any] | None]]:
        """A queue of events for this folder: {"type": "state", "state": ...} and {"type": "changed"}. None means the feed ended."""
        key = (account.id, folder)
        watch = self._watches.get(key)
        if watch is None:
            watch = self._watches[key] = _Watch()
            self._start(key, watch, account, folder)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=32)
        queue.put_nowait({"type": "state", "state": watch.state})
        watch.queues.add(queue)
        try:
            yield queue
        finally:
            watch.queues.discard(queue)
            if not watch.queues and self._watches.get(key) is watch:
                del self._watches[key]
                watch.stop.set()  # the worker notices within a few seconds and closes its connection

    def drop(self, account_id: str) -> None:
        """The mailbox was removed or its password changed: end its feeds so the next listener starts from the new settings."""
        for key in [k for k in self._watches if k[0] == account_id]:
            watch = self._watches.pop(key)
            watch.stop.set()
            for queue in list(watch.queues):
                _offer(queue, None)

    def _start(self, key: tuple[str, str], watch: _Watch, account: Account, folder: str) -> None:
        loop = asyncio.get_running_loop()

        def publish(event: dict[str, Any]) -> None:
            if event["type"] == "state":
                watch.state = event["state"]
            for queue in list(watch.queues):
                _offer(queue, event)

        def from_thread(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(publish, event)

        async def run() -> None:
            await asyncio.to_thread(
                self.gateway.idle,
                account.credentials,
                folder,
                watch.stop,
                lambda: from_thread({"type": "changed"}),
                lambda state: from_thread({"type": "state", "state": state}),
            )

        watch.task = loop.create_task(run())

    async def close(self) -> None:
        for watch in self._watches.values():
            watch.stop.set()
        tasks = [w.task for w in self._watches.values() if w.task]
        self._watches.clear()
        for t in tasks:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(t, 8)


def _offer(queue: asyncio.Queue, event: dict[str, Any] | None) -> None:
    """A slow viewer loses old events rather than holding anything up: a "changed" is only a nudge to ask again."""
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    queue.put_nowait(event)
