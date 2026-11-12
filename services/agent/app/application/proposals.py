"""Approving what an agent proposed: the only place an agent's suggestion becomes a saved record, and only on the person's say-so."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import Conflict, NotFound, ValidationFailed

from app.application.ports import DataGateway, Proposal, ProposalRepository
from app.application.services import Who
from app.domain.llm import AiError

log = logging.getLogger(__name__)

#: A proposal that has waited this long is stale: the day has moved on, so the agent is asked again.
STALE = timedelta(hours=24)
#: On a phone, "yes" means what was proposed in the last few minutes, not yesterday's.
RECENT = timedelta(minutes=30)
YES = {"yes", "y", "yep", "yeah", "ok", "okay", "approve", "approved", "confirm", "do it", "go ahead", "save", "save it", "sure"}
NO = {"no", "n", "nope", "cancel", "drop", "dismiss", "discard", "don't", "dont", "never mind", "nevermind"}
#: Only these collections can be changed by approving; each is a record the practice service already accepts from the owner.
TABLES = ("tasks", "follow-ups", "cases", "hearings", "plans", "stories")
UPDATABLE = ("tasks", "follow-ups")


def verdict_of(message: str) -> bool | None:
    """True for a yes, False for a no, None for anything else (which goes to the agent as a question)."""
    word = " ".join(message.lower().strip(" .!?\n").split())
    return True if word in YES else False if word in NO else None


class ProposalService:
    def __init__(self, repo: ProposalRepository, data_factory: Callable[[str], DataGateway], clock_now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._repo, self._data, self._now = repo, data_factory, clock_now

    @staticmethod
    def view(p: Proposal) -> dict[str, Any]:
        return {"id": p.id, "action": p.action, "title": p.title, "lines": p.lines, "status": p.status}

    async def waiting(self, who: Who) -> list[dict[str, Any]]:
        """What agents proposed that is still waiting for the person (not older than a day: after that it is stale and can't be approved), newest first."""
        return [{**self.view(p), "agentId": p.agent_id, "createdAt": p.created_at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")} for p in await self._repo.pending(who.user_id, self._now() - STALE)]

    async def _mine(self, who: Who, proposal_id: str) -> Proposal:
        p = await self._repo.get(who.user_id, proposal_id)
        if p is None:
            raise NotFound("No such proposal.")
        return p

    async def apply(self, who: Who, token: str, proposal_id: str) -> dict[str, Any]:
        """Saves what was proposed. Each record is written by id, so trying again after a failure never doubles anything."""
        p = await self._mine(who, proposal_id)
        if p.status == "applied":
            return self.view(p)  # approved already (a second tap, a chat reopened): it is saved, and saying so is not an error
        if p.status != "pending":
            raise Conflict("That was dropped, so it wasn't saved. Ask for it again.")
        if self._now() - p.created_at > STALE:
            raise Conflict("That proposal is more than a day old, and things may have changed. Ask for it again.")
        data = self._data(token)
        try:
            for op in p.ops:
                if "trash" in op:  # mail a workflow wants moved to Trash (never deleted): approving it is what moves it
                    t = op["trash"]
                    await data.mail_trash(str(t["account"]), str(t.get("folder") or "INBOX"), [int(u) for u in t["uids"]])
                    continue
                if "meeting" in op:  # an event found in an email: approving it puts it in the calendar
                    await data.create_meeting(dict(op["meeting"]))
                    continue
                if "chapter" in op:  # a chapter of a story, with what was read from it: approving writes it all in one step (by id, so a retry never doubles)
                    await data.write_chapter(str(op["chapter"]["story"]), dict(op["chapter"]["body"]))
                    continue
                if "mail" in op:  # an email drafted by a workflow: approving it is what sends it
                    await data.mail_send(str(op["mail"]["account"]), dict(op["mail"]["message"]))
                    continue
                table = op.get("table")
                if table not in TABLES:
                    raise ValidationFailed("That kind of change isn't allowed.")
                if op.get("create"):
                    record = {**op["patch"], "id": op["id"]}
                else:
                    if table not in UPDATABLE:
                        raise ValidationFailed("That kind of change isn't allowed.")
                    current = next((r for r in await data.records(table) if r.get("id") == op["id"]), None)
                    if current is None:
                        raise Conflict("What you wanted to change has been deleted since.")
                    record = {**current, **{k: v for k, v in op["patch"].items() if v is not None}}
                    for k, v in op["patch"].items():
                        if v is None:
                            record.pop(k, None)  # a field cleared, like a task taken off the planner
                await data.save_record(table, record)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        p.status, p.decided_at = "applied", self._now()
        await self._repo.save(p)
        if any(op.get("table") in ("tasks", "follow-ups", "hearings") or (op.get("chapter") or {}).get("body", {}).get("steps") for op in p.ops):
            await data.calendar_sync()  # what was just saved reaches Google now, not at the next background sync (a failure is not this one's)
        return self.view(p)

    async def drop(self, who: Who, proposal_id: str) -> dict[str, Any]:
        p = await self._mine(who, proposal_id)
        if p.status == "dropped":
            return self.view(p)
        if p.status != "pending":
            raise Conflict("That was already saved, so it can't be dropped.")
        p.status, p.decided_at = "dropped", self._now()
        await self._repo.save(p)
        return self.view(p)

    async def settle_channel(self, who: Who, token: str, channel_id: str, approve: bool) -> str | None:
        """A yes or a no on a phone: applies or drops what the agent proposed in that chat in the last half hour.
        Returns what to say back, or None if nothing was waiting (the word then goes to the agent as an ordinary message)."""
        waiting = await self._repo.pending_in(who.user_id, channel_id, self._now() - RECENT)
        if not waiting:
            return None
        done, failed = [], []
        for p in waiting:
            try:
                await (self.apply(who, token, p.id) if approve else self.drop(who, p.id))
                done.append(p.title)
            except (ValidationFailed, Conflict, NotFound) as exc:
                failed.append(f"{p.title}: {getattr(exc, 'message', None) or exc}")
                log.info("could not settle proposal %s", p.id)
        out = []
        if done:
            out.append(("Saved:\n" if approve else "Dropped:\n") + "\n".join(f"• {t}" for t in done))
        if failed:
            out.append("Couldn't save:\n" + "\n".join(f"• {t}" for t in failed))
        return "\n\n".join(out)
