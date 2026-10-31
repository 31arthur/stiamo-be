"""The agents this service offers, and the tool packs they draw on. Built once at start-up; checked then, so a typo fails the deploy, not a user."""

from app.application.toolpacks import PackRegistry
from app.domain.agents import AgentDefinition, UnknownAgent
from app.domain.prompt import BASE_VARIABLES


class AgentRegistry:
    def __init__(self, agents: list[AgentDefinition], packs: PackRegistry) -> None:
        if not agents:
            raise ValueError("At least one agent is needed.")
        self._agents: dict[str, AgentDefinition] = {}
        for a in agents:
            if a.id in self._agents:
                raise ValueError(f"Agent {a.id!r} is registered twice.")
            missing = [p for p in a.packs if p not in packs]
            if missing:
                raise ValueError(f"Agent {a.id!r} uses tool packs that don't exist: {', '.join(missing)}.")
            self._agents[a.id] = a
        self.packs = packs

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise UnknownAgent("No such agent.") from exc

    def all(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    @property
    def default_id(self) -> str:
        """The agent a channel starts with: the first one registered."""
        return next(iter(self._agents))

    def variables(self, agent: AgentDefinition) -> dict[str, str]:
        """What may go between {{braces}} in this agent's prompt: the base variables and those of its packs."""
        out = dict(BASE_VARIABLES)
        for name in agent.packs:
            out.update(self.packs.get(name).variables)
        return out
