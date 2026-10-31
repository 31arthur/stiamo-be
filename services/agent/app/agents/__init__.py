"""Where agents are registered. To add an agent: write its definition in a module here and add it to `AGENTS`."""

from app.agents.cases import CASE_AGENT
from app.application.registry import AgentRegistry
from app.application.toolpacks import PackRegistry
from app.application.toolpacks.cases import CasesPack
from app.application.toolpacks.decisions import DecisionsPack
from app.application.toolpacks.drive import DrivePack

AGENTS = [CASE_AGENT]


def default_registry() -> AgentRegistry:
    return AgentRegistry(AGENTS, PackRegistry([CasesPack(), DrivePack(), DecisionsPack()]))
