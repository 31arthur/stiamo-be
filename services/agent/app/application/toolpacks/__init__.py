"""Tool packs: the unit of reuse between agents.

A pack is a small group of tools about one thing (cases, the Drive, decisions), with the prompt variables that go with
them. An agent names the packs it draws on; it never defines tools itself. Add a tool to a pack and every agent that uses
the pack has it; add a pack and any agent can opt in.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.application.ports import DataGateway, DecisionClient
from app.application.runner import Tool


@dataclass(frozen=True)
class PackContext:
    """What a pack may use while building its tools: this person's data, today's date, and the decision model if it is on."""

    #: Remembers what it fetched, so several packs read each list from the other services once.
    data: DataGateway
    today: str
    decisions: DecisionClient | None = None
    decision_model: str | None = None
    #: What is waiting in the owner's Attention queue (mail that matters, events to approve), when the agent may read it.
    attention: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None
    #: Keeps something the agent proposes (action, title, the lines to show, what approving does) and returns its id. None: the agent may not propose.
    propose: Callable[[str, str, list[str], list[dict[str, Any]]], Awaitable[str]] | None = None
    #: Someone is there to approve what is proposed (a chat), as opposed to a run by itself (the evening plan, which keeps its own plan).
    interactive: bool = True
    #: The one workspace this is for (a workspace's assistant): what it proposes to add belongs to it. None: any.
    workspace: str | None = None
    #: A runner for an AI job by its name (on that job's own model, else the one chosen last), or None when no model is usable.
    jobs: Callable[[str], Any] | None = None


class ToolPack(ABC):
    #: How agents refer to the pack.
    name: str
    #: Prompt variables this pack adds ({{name}} → what it stands for), so the prompt editor can offer and check them.
    variables: dict[str, str] = {}

    @abstractmethod
    async def tools(self, ctx: PackContext) -> list[Tool]:
        """The tools, bound to this person's data. May be empty (the decisions pack is empty until a decision model is on)."""

    async def context(self, ctx: PackContext) -> dict[str, Any]:
        """Values for the variables above. Called only for agents that use the pack."""
        return {}


class PackRegistry:
    def __init__(self, packs: list[ToolPack]) -> None:
        self._packs: dict[str, ToolPack] = {}
        for p in packs:
            if p.name in self._packs:
                raise ValueError(f"Tool pack {p.name!r} is registered twice.")
            self._packs[p.name] = p

    def get(self, name: str) -> ToolPack:
        return self._packs[name]

    def names(self) -> list[str]:
        return list(self._packs)

    def __contains__(self, name: str) -> bool:
        return name in self._packs
