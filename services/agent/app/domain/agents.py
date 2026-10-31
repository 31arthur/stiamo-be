"""What an agent *is*: a name, a default prompt, and the tool packs it draws on. Pure data: no network, no framework."""

from dataclasses import dataclass

from stiamo_common.errors import NotFound, ValidationFailed

from app.domain.prompt import DEFAULT_INPUT

#: The parts of a person's life and practice that work belongs to (the same ids as the app's workspaces).
WORKSPACES: tuple[str, ...] = ("personal", "sb", "fsa", "media")

#: Identifies an agent in addresses, settings and channels. Lower case, so it is safe in a URL.
AGENT_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_-"


@dataclass(frozen=True)
class AgentDefinition:
    """One agent, written once in code and never stored. What a person changes (the prompt, the model) is stored per person."""

    id: str
    name: str
    #: One sentence for the agent picker: what it is for.
    description: str
    #: The instructions it starts with. May use {{variables}}: the base ones, and those of its packs.
    system: str
    #: The tool packs it may use, by name. Each pack contributes tools and, optionally, prompt variables.
    packs: tuple[str, ...]
    #: Example questions shown to a person with an empty chat.
    suggestions: tuple[str, ...] = ()
    #: How what is typed is wrapped before it reaches the model.
    input_template: str = DEFAULT_INPUT
    #: The workspaces it is shown in until a person assigns it differently. Empty: only in "all workspaces".
    workspaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or any(c not in AGENT_ID_CHARS for c in self.id):
            raise ValueError(f"Agent id {self.id!r} must be lower case letters, digits, - or _.")
        if not self.packs:
            raise ValueError(f"Agent {self.id!r} needs at least one tool pack.")


def clean_workspaces(workspaces: list[str]) -> list[str]:
    """A person's choice of workspaces for an agent: known ids, each once, in the order the app lists them."""
    unknown = [w for w in workspaces if w not in WORKSPACES]
    if unknown:
        raise ValidationFailed(f"Unknown workspace: {', '.join(unknown)}.")
    return [w for w in WORKSPACES if w in set(workspaces)]


class UnknownAgent(NotFound):
    """No agent has that id."""
