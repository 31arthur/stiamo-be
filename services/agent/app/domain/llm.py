"""What the agent needs to know about language models, whichever provider they come from. Pure: no network, no framework."""

from dataclasses import dataclass, field
from typing import Any, Literal

ProviderId = Literal["openai", "gemini", "openrouter"]
PROVIDER_IDS: tuple[str, ...] = ("openai", "gemini", "openrouter")

#: How hard a model is asked to think before answering. `off` only exists where the model can be told not to.
THINKING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high")

#: The decision models (Jev) answer typed questions with probabilities, not text. They are reached through OpenRouter.
DECISION_MODELS: tuple[dict[str, str], ...] = (
    {"id": "~typesafe/jev-latest", "name": "Jev (latest)"},
    {"id": "typesafe/jev-1.14", "name": "Jev 1.14"},
    {"id": "typesafe/jev-1.13", "name": "Jev 1.13"},
)


class AiError(Exception):
    """Something went wrong asking a provider, in words for people."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    #: The levels this model accepts; empty when it doesn't think (or doesn't say).
    thinking: tuple[str, ...] = ()


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    #: The provider's own form of an assistant turn, sent back untouched (some models need their thought signatures returned).
    raw: Any = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["toolCalls"] = [{"id": c.id, "name": c.name, "args": c.args} for c in self.tool_calls]
        if self.tool_call_id is not None:
            out["toolCallId"] = self.tool_call_id
        if self.name is not None:
            out["name"] = self.name
        if self.raw is not None:
            out["raw"] = self.raw
        return out

    @staticmethod
    def from_json(data: dict[str, Any]) -> "Message":
        calls = [ToolCall(str(c.get("id", "")), str(c.get("name", "")), dict(c.get("args") or {})) for c in data.get("toolCalls") or []]
        role = data.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            raise ValueError("Unknown message role")
        return Message(role, str(data.get("content", "")), calls, data.get("toolCallId"), data.get("name"), data.get("raw"))


@dataclass(frozen=True)
class ToolSpec:
    """What the model is told about a tool: its name, what it is for, and the arguments it takes (JSON schema)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


def clamp_thinking(levels: tuple[str, ...], wanted: str) -> str:
    """The level to use: what was asked for if the model allows it, otherwise a middling one, otherwise none."""
    if not levels:
        return "off"
    if wanted in levels:
        return wanted
    return "medium" if "medium" in levels else levels[0]
