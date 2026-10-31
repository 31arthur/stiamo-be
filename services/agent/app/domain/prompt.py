"""The agent's prompt as a person can edit it: a template with variables, a wrapper for what is typed, and per-tool overrides."""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from stiamo_common.errors import ValidationFailed

from app.domain.llm import ToolSpec

MAX_TEMPLATE_CHARS = 8000

DEFAULT_SYSTEM = """You are the case assistant inside Stiamo, the practice-management app of a small legal practice (S&B Consultants).
You are talking to {{user}} through {{channel}}.

Answer questions about their cases and the documents that go with them, using the tools. Never guess: if a tool finds nothing, say so.
Find cases with search_cases (try the parties, the case number or the court number), read one with get_case, and find its documents with case_documents. Use find_documents to search the Drive directly.
Whatever the tools find is shown to the person as a list beneath your answer, so do not repeat long lists: answer briefly, name cases by their reference number, and point out what matters (the next hearing, a missing document).

The tools you have:
{{tools}}

Today is {{today}}. The practice has {{cases}} cases and the Drive is {{drive}}. Reply in plain language, in a few short sentences."""

DEFAULT_INPUT = "{{input}}"

#: What can be written between double braces, and what each stands for.
VARIABLES: dict[str, str] = {
    "input": "What the person typed (the whole message).",
    "today": "Today's date.",
    "user": "Who is asking (their name, from their email).",
    "channel": "Where the message came from: the web app, Telegram or WhatsApp.",
    "tools": "The tools switched on, one per line with what each does.",
    "cases": "How many cases the practice has.",
    "drive": "Whether the Google Drive is connected: \"connected\" or \"not connected\".",
}

_VAR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class ToolOverride(BaseModel):
    enabled: bool = True
    #: Replaces the description the model is shown for this tool.
    description: str | None = Field(default=None, max_length=1000)


class PromptConfig(BaseModel):
    system: str = DEFAULT_SYSTEM
    #: Wraps what is typed before it reaches the model. Must contain {{input}}.
    input_template: str = DEFAULT_INPUT
    tools: dict[str, ToolOverride] = Field(default_factory=dict)

    def validated(self) -> "PromptConfig":
        for label, text in (("The prompt", self.system), ("The input template", self.input_template)):
            if not text.strip():
                raise ValidationFailed(f"{label} can't be empty.")
            if len(text) > MAX_TEMPLATE_CHARS:
                raise ValidationFailed(f"{label} is too long (at most {MAX_TEMPLATE_CHARS} characters).")
            unknown = sorted({m for m in _VAR.findall(text) if m not in VARIABLES})
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
