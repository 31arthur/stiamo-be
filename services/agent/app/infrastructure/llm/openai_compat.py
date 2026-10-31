"""OpenAI and OpenRouter speak the same chat API, so one client serves both."""

import json
import re
from typing import Any

import httpx

from app.application.ports import LlmClient
from app.domain.llm import AiError, ChatResult, Message, ModelInfo, ToolCall, ToolSpec

BASE = {"openai": "https://api.openai.com/v1", "openrouter": "https://openrouter.ai/api/v1"}
WHO = {"openai": "OpenAI", "openrouter": "OpenRouter"}
LEVELS = ("low", "medium", "high")

#: OpenAI's catalogue also holds speech, images and embeddings; only models that chat are offered.
_NOT_CHAT = re.compile(r"(audio|realtime|transcribe|tts|whisper|embedding|moderation|image|dall-e|davinci|babbage|instruct|search|codex|computer-use|^o1-(mini|preview|pro))")
_OPENAI_CHAT = re.compile(r"^(gpt-|chatgpt-|o\d)")
_OPENAI_THINKS = re.compile(r"^(o\d|gpt-5)")


def openai_models(body: dict[str, Any]) -> list[ModelInfo]:
    ids = sorted((m["id"] for m in body.get("data", []) if "id" in m), reverse=True)
    return [ModelInfo(i, i, LEVELS if _OPENAI_THINKS.match(i) else ()) for i in ids if _OPENAI_CHAT.match(i) and not _NOT_CHAT.search(i)]


def openrouter_models(body: dict[str, Any]) -> list[ModelInfo]:
    """Only models that can call tools can run an agent; those that accept `reasoning` can be told how hard to think."""
    out = []
    for m in body.get("data", []):
        params = m.get("supported_parameters") or []
        if "tools" in params:
            out.append(ModelInfo(m["id"], m.get("name") or m["id"], ("off", *LEVELS) if "reasoning" in params else ()))
    return sorted(out, key=lambda m: m.name.lower())


def to_wire(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append({"role": "assistant", "content": m.content or None, "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}} for c in m.tool_calls]})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _args(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        v = json.loads(raw)
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def failure(response: httpx.Response, who: str) -> AiError:
    detail = ""
    try:
        err = response.json().get("error")
        detail = err if isinstance(err, str) else (err or {}).get("message", "")
    except ValueError:
        pass
    if response.status_code in (401, 403):
        reason = f"{who} didn't accept the key. Check it on the Providers page."
    elif response.status_code == 429:
        reason = f"{who} says there have been too many requests, or the account is out of credit."
    else:
        reason = f"{who} couldn't answer ({response.status_code})."
    return AiError(f"{reason} {detail}".strip(), response.status_code)


class OpenAiCompatibleClient(LlmClient):
    def __init__(self, flavour: str, api_key: str, http: httpx.AsyncClient) -> None:
        self._flavour, self._key, self._http, self._who = flavour, api_key, http, WHO[flavour]

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return await self._http.request(method, BASE[self._flavour] + path, headers={"Authorization": f"Bearer {self._key}"}, **kw)
        except httpx.HTTPError as exc:
            raise AiError(f"Couldn't reach {self._who}. Try again in a moment.") from exc

    async def list_models(self) -> list[ModelInfo]:
        r = await self._call("GET", "/models")
        if r.status_code != 200:
            raise failure(r, self._who)
        body = r.json()
        return openai_models(body) if self._flavour == "openai" else openrouter_models(body)

    async def chat(self, *, model: str, messages: list[Message], tools: list[ToolSpec], thinking: str) -> ChatResult:
        body: dict[str, Any] = {"model": model, "messages": to_wire(messages)}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
            body["tool_choice"] = "auto"
        if thinking != "off":
            if self._flavour == "openai":
                body["reasoning_effort"] = thinking
            else:
                body["reasoning"] = {"effort": thinking}
        elif self._flavour == "openrouter":
            body["reasoning"] = {"enabled": False}
        r = await self._call("POST", "/chat/completions", json=body)
        if r.status_code != 200:
            raise failure(r, self._who)
        choices = r.json().get("choices") or []
        message = (choices[0] if choices else {}).get("message")
        if not message:
            raise AiError(f"{self._who} sent back an empty answer.")
        calls = [ToolCall(c.get("id") or f"call_{i}", (c.get("function") or {}).get("name", ""), _args((c.get("function") or {}).get("arguments"))) for i, c in enumerate(message.get("tool_calls") or [])]
        return ChatResult(message.get("content") or "", calls)
