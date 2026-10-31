"""The prompt as a person can edit it: a template with variables, a wrapper for what is typed, and per-tool overrides."""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from stiamo_common.errors import ValidationFailed

from app.domain.llm import ToolSpec

MAX_TEMPLATE_CHARS = 8000

DEFAULT_INPUT = "{{input}}"

#: Variables every agent has. A tool pack adds its own (the cases pack adds `cases`, the drive pack `drive`).
BASE_VARIABLES: dict[str, str] = {
    "input": "What the person typed (the whole message).",
    "today": "Today's date.",
    "user": "Who is asking (their name, from their email).",
    "channel": "Where the message came from: the web app, Telegram or WhatsApp.",
    "tools": "The tools switched on, one per line with what each does.",
}

_VAR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class ToolOverride(BaseModel):
    enabled: bool = True
    #: Replaces the description the model is shown for this tool.
    description: str | None = Field(default=None, max_length=1000)


class PromptConfig(BaseModel):
    """What a person may change about one agent. Stored per person and per agent; the agent's own defaults are in code."""

    system: str
    #: Wraps what is typed before it reaches the model. Must contain {{input}}.
    input_template: str = DEFAULT_INPUT
    tools: dict[str, ToolOverride] = Field(default_factory=dict)

    def validated(self, variables: dict[str, str]) -> "PromptConfig":
        """Checks the templates against the variables this agent really has."""
        for label, text in (("The prompt", self.system), ("The input template", self.input_template)):
            if not text.strip():
                raise ValidationFailed(f"{label} can't be empty.")
            if len(text) > MAX_TEMPLATE_CHARS:
                raise ValidationFailed(f"{label} is too long (at most {MAX_TEMPLATE_CHARS} characters).")
            unknown = sorted({m for m in _VAR.findall(text) if m not in variables})
            if unknown:
                raise ValidationFailed(f"{label} uses unknown variables: {', '.join('{{' + u + '}}' for u in unknown)}.")
        if "input" not in _VAR.findall(self.input_template):
            raise ValidationFailed("The input template must contain {{input}}, or what is typed never reaches the model.")
        return self


def render(template: str, values: dict[str, Any]) -> str:
    """Fills in {{variables}}. A value is inserted as it is, never read for more variables."""
    return _VAR.sub(lambda m: str(values.get(m.group(1), "")), template)


@dataclass(frozen=True)
class Package:
    """Everything that goes to the model with one question."""

    system: str
    user: str
    tools: list[ToolSpec]


def tool_lines(tools: list[ToolSpec]) -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in tools) or "(none)"


def build_package(config: PromptConfig, tools: list[ToolSpec], context: dict[str, Any], question: str) -> Package:
    """Applies the person's overrides to the tools, then fills in the prompt and the wrapper around the question."""
    active: list[ToolSpec] = []
    for t in tools:
        o = config.tools.get(t.name)
        if o is not None and not o.enabled:
            continue
        active.append(ToolSpec(t.name, (o.description or t.description) if o else t.description, t.parameters))
    values = {**context, "tools": tool_lines(active)}
    return Package(
        system=render(config.system, {**values, "input": question}),
        user=render(config.input_template, {**values, "input": question}),
        tools=active,
    )
