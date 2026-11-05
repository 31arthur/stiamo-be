"""Conversations with the team's agents: kept on the server, so a thread picks up where it was left on any device."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import Conversation, ConversationRepository, ProposalRepository
from app.application.services import AgentService, Who
from app.domain.agents import WORKSPACES

#: What a thread remembers for the model: the last messages, so a long thread doesn't grow without end.
MAX_STORED_MESSAGES = 40
MAX_TURNS = 300
MAX_TITLE = 80
#: A thread belongs to one workspace, or to none in particular ("all").
SCOPES = (*WORKSPACES, "all")
ASSISTANT = "the assistant"


def _title_from(message: str) -> str:
    one_line = " ".join(message.split())
    return one_line if len(one_line) <= MAX_TITLE else one_line[: MAX_TITLE - 1].rstrip() + "…"


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class ConversationService:
    def __init__(self, repo: ConversationRepository, agent: AgentService, proposals: ProposalRepository) -> None:
        self._repo, self._agent, self._proposals = repo, agent, proposals

    @staticmethod
    def summary(c: Conversation) -> dict[str, Any]:
        last = c.turns[-1] if c.turns else None
        return {"id": c.id, "agentId": c.agent_id, "workspace": c.workspace, "title": c.title or "New chat", "updatedAt": _iso(c.updated_at), "createdAt": _iso(c.created_at), "turns": len(c.turns), "preview": ((last or {}).get("answer") or "")[:140]}

    async def list(self, who: Who, workspace: str | None) -> dict[str, Any]:
        """The person's threads, newest first. In a workspace: that workspace's. Under "all" (or none given): every one."""
        items = await self._repo.recent(who.user_id, None if workspace in (None, "all") else workspace)
        return {"items": [self.summary(c) for c in items]}

    async def create(self, who: Who, agent_id: str, workspace: str) -> dict[str, Any]:
        if workspace not in SCOPES:
            raise ValidationFailed("Unknown workspace.")
        self._agent.registry.get(agent_id)  # raises for an agent that doesn't exist
        now = datetime.now(UTC)
        c = Conversation(str(uuid.uuid4()), who.user_id, agent_id, workspace, "", [], [], now, now)
        await self._repo.save(c)
        return await self._full(who, c)

    async def _mine(self, who: Who, conversation_id: str) -> Conversation:
        c = await self._repo.get(who.user_id, conversation_id)
        if c is None:
            raise NotFound("No such conversation.")
        return c

    async def _full(self, who: Who, c: Conversation) -> dict[str, Any]:
        """The thread with what it proposed shown as it stands now: a card approved yesterday still says it was saved."""
        turns = []
        for t in c.turns:
            found = []
            for f in t.get("found", []):
                if f.get("kind") == "proposal":
                    p = await self._proposals.get(who.user_id, f["id"])
                    f = {**f, "status": p.status if p else "dropped"}
                found.append(f)
            turns.append({**t, "found": found})
        return {**self.summary(c), "turns": turns}

    async def get(self, who: Who, conversation_id: str) -> dict[str, Any]:
        return await self._full(who, await self._mine(who, conversation_id))

    async def send(self, who: Who, token: str, conversation_id: str, message: str) -> dict[str, Any]:
        """Asks the thread's agent, with what the thread already said, and keeps the exchange."""
        c = await self._mine(who, conversation_id)
        if len(c.turns) >= MAX_TURNS:
            raise ValidationFailed("This chat is very long. Start a new one.")
        run = await self._agent.chat(who, token, c.agent_id, message, c.messages, ASSISTANT, trigger="assistant")
        turn = {"id": str(uuid.uuid4()), "question": message.strip(), "answer": run.answer, "steps": [s.to_json() for s in run.steps], "found": run.found, "at": _iso(datetime.now(UTC))}
        c.turns = [*c.turns, turn]
        c.messages = [m.to_json() for m in run.messages][-MAX_STORED_MESSAGES:]
        c.title = c.title or _title_from(message)
        c.updated_at = datetime.now(UTC)
        await self._repo.save(c)
        return {"turn": (await self._full(who, c))["turns"][-1], "conversation": self.summary(c)}

    async def rename(self, who: Who, conversation_id: str, title: str) -> dict[str, Any]:
        c = await self._mine(who, conversation_id)
        c.title = _title_from(title) or c.title
        await self._repo.save(c)
        return self.summary(c)

    async def delete(self, who: Who, conversation_id: str) -> None:
        await self._mine(who, conversation_id)
        await self._repo.delete(who.user_id, conversation_id)
