"""Conversations with the team's agents: kept on the server, so a thread picks up where it was left on any device."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import Conversation, ConversationRepository, ProposalRepository
from app.application.runner import AgentRun
from app.application.services import AgentService, Who
from app.domain.agents import WORKSPACES
from app.domain.llm import AiError
from app.domain.orchestrator import (
    ORCHESTRATOR_ID,
    build_preface,
    gist_of,
    is_follow_up,
    jev_question,
    jev_state,
    route_by_rules,
    scope_line,
    update_digest,
    update_focus,
    workspace_of,
)

#: What a thread remembers for the model: the last messages, so a long thread doesn't grow without end.
MAX_STORED_MESSAGES = 40
MAX_TURNS = 300
MAX_TITLE = 80
#: A thread belongs to one workspace, or to none in particular ("all").
SCOPES = (*WORKSPACES, "all")
ASSISTANT = "the assistant"
#: A phone chat that has been quiet this long starts a new thread, so yesterday's talk doesn't colour today's.
CHANNEL_IDLE = timedelta(hours=12)
NEW_THREAD = ("/new", "/reset", "new chat")


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
        if agent_id != ORCHESTRATOR_ID:
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
                elif f.get("kind") == "schedule" and f.get("proposalId"):
                    p = await self._proposals.get(who.user_id, f["proposalId"])
                    f = {**f, "proposalStatus": p.status if p else "dropped"}
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
        if c.agent_id == ORCHESTRATOR_ID:
            turn = await self._orchestrated(who, token, c, message)
        else:
            run = await self._agent.chat(who, token, c.agent_id, message, c.messages, ASSISTANT, trigger="assistant")
            turn = self._turn(message, run)
            c.messages = [m.to_json() for m in run.messages][-MAX_STORED_MESSAGES:]
        c.turns = [*c.turns, turn]
        c.title = c.title or _title_from(message)
        c.updated_at = datetime.now(UTC)
        await self._repo.save(c)
        return {"turn": (await self._full(who, c))["turns"][-1], "conversation": self.summary(c)}

    @staticmethod
    def _turn(message: str, run: AgentRun, **extra: Any) -> dict[str, Any]:
        return {"id": str(uuid.uuid4()), "question": message.strip(), "answer": run.answer, "steps": [s.to_json() for s in run.steps], "found": run.found, "at": _iso(datetime.now(UTC)), **extra}

    async def _route(self, who: Who, candidates: list[dict[str, Any]], message: str, preface: str, last: str | None) -> tuple[str, str, str]:
        """Which agent should answer, why, and how it was decided. A message that goes on from the last answer stays with that agent (no
        model needed); otherwise the decision model (Jev) chooses when it is on, and plain rules when it is not or can't be reached."""
        ids = [c["id"] for c in candidates]
        if len(ids) == 1:
            return ids[0], "the only agent here", "only"
        if last in ids and is_follow_up(message):
            return last, "carrying on from the last answer", "continuity"
        if await self._agent.jev_ready(who):
            try:
                out = await self._agent.decide(who, jev_state(message, preface), jev_question(candidates, last))
                choice = out["answers"]["agent"]["choice"]
                if choice in ids:
                    return choice, "chosen by the decision model", "jev"
            except (ValidationFailed, AiError, KeyError, TypeError):
                pass  # the rules below always have an answer
        agent_id, why = route_by_rules(message, candidates, last)
        return agent_id, why, "rules"

    async def _orchestrated(self, who: Who, token: str, c: Conversation, message: str, *, channel_name: str = ASSISTANT, channel_id: str | None = None, trigger: str = "assistant") -> dict[str, Any]:
        """One message to the assistant: choose the agent, tell it what the conversation has established, and keep what comes back."""
        candidates = await self._agent.visible_agents(who, c.workspace)
        if not candidates:
            raise ValidationFailed("No agent is set up to work here yet. Choose which workspaces an agent is shown in under Developer.")
        last = next((t.get("agentId") for t in reversed(c.turns) if t.get("agentId")), None)
        preface = build_preface(c.turns, c.context)
        agent_id, why, by = await self._route(who, candidates, message, preface, last)
        scope = scope_line(c.workspace)
        if scope:
            preface = f"{scope}\n\n{preface}".strip()
        run = await self._agent.chat(who, token, agent_id, message, [], channel_name, trigger=trigger, channel_id=channel_id, preface=preface, workspace=c.workspace if c.workspace != "all" else None)
        name = next(a["name"] for a in candidates if a["id"] == agent_id)
        turn = self._turn(message, run, agentId=agent_id, agentName=name, routedBy=by, why=why, gist=gist_of(run.found))
        context = c.context or {}
        c.context = {"focus": update_focus(context.get("focus") or {}, message, run.found), "digest": update_digest(context.get("digest") or [], turn)}
        return turn

    async def pick_workspace(self, who: Who, base_key: str, message: str) -> str | None:
        """Which workspace a message to the master assistant is about: what its words say; else the one this chat was last working in (within a
        few hours); else None, and the person is asked. Each workspace has a thread of its own under the chat, so nothing said about one is in
        another's context."""
        said = workspace_of(message)
        if said is not None:
            return said
        now = datetime.now(UTC)
        recent = []
        for ws in ("sb", "fsa", "personal", "all"):
            c = await self._repo.by_channel(who.user_id, f"{base_key}:{ws}")
            if c is not None and now - c.updated_at <= CHANNEL_IDLE:
                recent.append((c.updated_at, ws))
        return max(recent)[1] if recent else None

    async def channel_ask(self, who: Who, token: str, channel_key: str, channel_name: str, message: str, channel_id: str, workspace: str = "all") -> tuple[str, list[dict[str, Any]], str | None]:
        """A message from a phone chat. It goes into that chat's thread (the same thread, on any day within a few hours of each other), which
        also shows in the web app. `workspace`: a bot that belongs to one workspace is that workspace's assistant and answers only about it.
        Returns the answer, the cards, and the name of the agent that answered."""
        now = datetime.now(UTC)
        c = await self._repo.by_channel(who.user_id, channel_key)
        fresh = message.strip().lower() in NEW_THREAD
        if c is not None and (fresh or now - c.updated_at > CHANNEL_IDLE or (c.workspace or "all") != workspace):
            # (a thread begun under another scope is never carried on: what it holds was said to a bot that could see more, or less)
            c.channel_key = None  # the old thread stays in the list, but is no longer the one this chat is in
            await self._repo.save(c)
            c = None
        if fresh:
            return "Started a new conversation. What do you need?", [], None
        if c is None:
            c = Conversation(str(uuid.uuid4()), who.user_id, ORCHESTRATOR_ID, workspace, "", [], [], now, now, {}, channel_key)
        if len(c.turns) >= MAX_TURNS:
            c.channel_key = None
            await self._repo.save(c)
            c = Conversation(str(uuid.uuid4()), who.user_id, ORCHESTRATOR_ID, workspace, "", [], [], now, now, {}, channel_key)
        turn = await self._orchestrated(who, token, c, message, channel_name=channel_name, channel_id=channel_id, trigger=channel_name.lower())
        c.turns = [*c.turns, turn]
        c.title = c.title or _title_from(f"{channel_name}: {message}")
        c.updated_at = datetime.now(UTC)
        await self._repo.save(c)
        return turn["answer"], turn["found"], turn.get("agentName")

    async def rename(self, who: Who, conversation_id: str, title: str) -> dict[str, Any]:
        c = await self._mine(who, conversation_id)
        c.title = _title_from(title) or c.title
        await self._repo.save(c)
        return self.summary(c)

    async def delete(self, who: Who, conversation_id: str) -> None:
        await self._mine(who, conversation_id)
        await self._repo.delete(who.user_id, conversation_id)
