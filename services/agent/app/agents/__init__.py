"""Where agents are registered. To add an agent: write its definition in a module here and add it to `AGENTS`."""

from app.agents.cases import CASE_AGENT
from app.agents.stories import STORY_AGENT
from app.agents.team import TEAM
from app.application.registry import AgentRegistry
from app.application.toolpacks import PackRegistry
from app.application.toolpacks.agenda import AgendaPack
from app.application.toolpacks.attention import AttentionPack
from app.application.toolpacks.cases import CasesPack
from app.application.toolpacks.changes import ChangesPack
from app.application.toolpacks.decisions import DecisionsPack
from app.application.toolpacks.drive import DrivePack
from app.application.toolpacks.insight import InsightPack
from app.application.toolpacks.stories import StoriesPack

AGENTS = [CASE_AGENT, *TEAM, STORY_AGENT]


def default_registry() -> AgentRegistry:
    return AgentRegistry(AGENTS, PackRegistry([CasesPack(), DrivePack(), DecisionsPack(), AgendaPack(), AttentionPack(), ChangesPack(), StoriesPack(), InsightPack()]))
