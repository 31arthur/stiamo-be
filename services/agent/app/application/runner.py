"""The agent loop: ask the model, run the tools it asks for, hand back the results, until it answers in words."""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.application.ports import LlmClient
from app.domain.llm import AiError, Message, ToolCall, ToolSpec

#: What goes back to a model is capped, so one wide search can't fill its whole window.
MAX_RESULT_CHARS = 12_000


@dataclass
class ToolOutput:
    #: What the model gets back.
    result: Any
    #: What the person is shown: cases and documents, each a dict with a `kind`.
    found: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Tool:
    spec: ToolSpec
    #: A line for the trace, e.g. `Searched cases for "NHAI"`.
    label: Callable[[dict[str, Any]], str]
    run: Callable[[dict[str, Any]], Awaitable[ToolOutput]]


#: How much of a tool's result a run log keeps. The model got all of it (up to MAX_RESULT_CHARS); a person reads this.
LOG_RESULT_CHARS = 1500


@dataclass
class Step:
    call: ToolCall
    label: str
    found: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    #: What the tool gave back, shortened, and how long it took.
    result: str = ""
    ms: int = 0

    def to_log(self) -> dict[str, Any]:
        """The step as the run log keeps it: enough to see what was asked of which tool, and what came back."""
        out: dict[str, Any] = {"tool": self.call.name, "label": self.label, "args": self.call.args, "found": len(self.found), "ms": self.ms, "result": self.result}
        if self.error:
            out["error"] = self.error
        return out

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"call": {"id": self.call.id, "name": self.call.name, "args": self.call.args}, "label": self.label, "found": len(self.found)}
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class AgentRun:
    answer: str
    steps: list[Step]
    found: list[dict[str, Any]]
    #: The conversation so far, without the system prompt: send it back to carry on.
    messages: list[Message]
    #: Tokens used across every call of the run.
    usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0})


def _json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "…(shortened)"


async def run_agent(
    *, client: LlmClient, model: str, thinking: str, system: str, user: str, tools: list[Tool], history: list[Message] | None = None, max_steps: int = 8
) -> AgentRun:
    specs = [t.spec for t in tools]
    past = list(history or [])
    turn: list[Message] = [Message("user", user)]
    steps: list[Step] = []
    found: dict[str, dict[str, Any]] = {}
    usage = {"inputTokens": 0, "outputTokens": 0}

    i = 0
    while True:
        # After the last round of tools the model is asked once more, with none, so it has to answer.
        last = i >= max_steps
        reply = await client.chat(model=model, messages=[Message("system", system), *past, *turn], tools=[] if last else specs, thinking=thinking)
        for k, v in (reply.usage or {}).items():
            usage[k] = usage.get(k, 0) + v
        if last or not reply.tool_calls:
            turn.append(Message("assistant", reply.text, raw=reply.raw))
            return AgentRun(reply.text.strip(), steps, list(found.values()), [*past, *turn], usage)
        turn.append(Message("assistant", reply.text, list(reply.tool_calls), raw=reply.raw))
        for call in reply.tool_calls:
            tool = next((t for t in tools if t.spec.name == call.name), None)
            step = Step(call, tool.label(call.args) if tool else f"Asked for {call.name}")
            began = time.monotonic()
            try:
                if tool is None:
                    raise ValueError(f"There is no tool called {call.name}.")
                out = await tool.run(call.args)
                step.found = out.found
                for f in out.found:
                    found[f"{f['kind']}:{f['id']}"] = f
                content = _json(out.result)
                step.result = content[:LOG_RESULT_CHARS] + ("…" if len(content) > LOG_RESULT_CHARS else "")
            except AiError:
                raise
            except Exception as exc:  # a tool that fails is something the model can hear about and work around
                step.error = str(exc) or "That tool failed."
                content = _json({"error": step.error})
            step.ms = int((time.monotonic() - began) * 1000)
            steps.append(step)
            turn.append(Message("tool", content, tool_call_id=call.id, name=call.name))
        i += 1
