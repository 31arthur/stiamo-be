import json
import re
from typing import Any

import httpx

from app.application.ports import LlmClient
from app.domain.llm import AiError, ChatResult, Message, ModelInfo, ToolCall, ToolSpec

BASE = "https://generativelanguage.googleapis.com/v1beta"
_BUDGET = {"low": 1024, "medium": 8192, "high": 24576}


def gemini_thinking(model_id: str, flagged: bool = False) -> tuple[str, ...]:
    """Which levels a Gemini model accepts: older ones take a budget, newer ones a level, and some can't be switched off."""
    if "gemini-3" in model_id:
        return ("low", "high")
    if "gemini-2.5-pro" in model_id:
        return ("low", "medium", "high")
    if "gemini-2.5" in model_id or flagged:
        return ("off", "low", "medium", "high")
    return ()


def gemini_models(models: list[dict[str, Any]]) -> list[ModelInfo]:
    out = []
    for m in models:
        name = m.get("name", "")
        if "generateContent" not in (m.get("supportedGenerationMethods") or []) or not name.startswith("models/gemini-") or re.search(r"(embedding|image|tts|live|aqa|robotics|computer-use)", name):
            continue
        mid = name.removeprefix("models/")
        out.append(ModelInfo(mid, m.get("displayName") or mid, gemini_thinking(mid, bool(m.get("thinking")))))
    return sorted(out, key=lambda m: m.id, reverse=True)


def thinking_config(model: str, level: str) -> dict[str, Any] | None:
    allowed = gemini_thinking(model)
    # A model that doesn't think, or can't be switched off, is left to its default.
    if not allowed or (level == "off" and "off" not in allowed):
        return None
    if "gemini-3" in model:
        return {"thinkingLevel": "low" if level == "low" else "high"}
    return {"thinkingBudget": 0 if level == "off" else _BUDGET[level]}


def _json_or_text(s: str) -> Any:
    try:
        return json.loads(s)
    except ValueError:
        return s


def to_contents(messages: list[Message]) -> list[dict[str, Any]]:
    """An assistant turn is sent back as it arrived (`raw`) when we have it, because newer models want their thought
    signatures returned with the calls they made. Consecutive tool results share one turn, as the API expects."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            part = {"functionResponse": {"name": m.name or "", "response": {"result": _json_or_text(m.content)}}}
            if out and out[-1]["role"] == "user" and all("functionResponse" in p for p in out[-1]["parts"]):
                out[-1]["parts"].append(part)
            else:
                out.append({"role": "user", "parts": [part]})
        elif m.role == "assistant":
            raw = m.raw if isinstance(m.raw, dict) else None
            if raw and raw.get("parts"):
                out.append({"role": "model", "parts": raw["parts"]})
            else:
                parts: list[dict[str, Any]] = [{"text": m.content}] if m.content else []
                parts += [{"functionCall": {"name": c.name, "args": c.args}} for c in m.tool_calls]
                out.append({"role": "model", "parts": parts or [{"text": ""}]})
        else:
            out.append({"role": "user", "parts": [{"text": m.content}]})
    return out


def _clean(v: Any) -> Any:
    """Gemini's schema dialect has no `additionalProperties`."""
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items() if k != "additionalProperties"}
    return v


def _failure(r: httpx.Response) -> AiError:
    detail = ""
    try:
        detail = (r.json().get("error") or {}).get("message", "")
    except ValueError:
        pass
    if r.status_code in (401, 403) or (r.status_code == 400 and "api key" in detail.lower()):
        return AiError("Gemini didn't accept the key. Check it on the Providers page.", r.status_code)
    if r.status_code == 429:
        return AiError("Gemini says there have been too many requests, or the quota is used up.", r.status_code)
    return AiError(f"Gemini couldn't answer ({r.status_code}). {detail}".strip(), r.status_code)


class GeminiClient(LlmClient):
    def __init__(self, api_key: str, http: httpx.AsyncClient) -> None:
        self._key, self._http = api_key, http

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return await self._http.request(method, BASE + path, headers={"x-goog-api-key": self._key}, **kw)
        except httpx.HTTPError as exc:
            raise AiError("Couldn't reach Gemini. Try again in a moment.") from exc

    async def list_models(self) -> list[ModelInfo]:
        models: list[dict[str, Any]] = []
        token = ""
        for _ in range(5):
            r = await self._call("GET", "/models", params={"pageSize": 100, **({"pageToken": token} if token else {})})
            if r.status_code != 200:
                raise _failure(r)
            body = r.json()
            models += body.get("models", [])
            token = body.get("nextPageToken", "")
            if not token:
                break
        return gemini_models(models)

    async def chat(self, *, model: str, messages: list[Message], tools: list[ToolSpec], thinking: str) -> ChatResult:
        system = next((m.content for m in messages if m.role == "system"), "")
        body: dict[str, Any] = {"contents": to_contents(messages)}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [{"name": t.name, "description": t.description, "parameters": _clean(t.parameters)} for t in tools]}]
        cfg = thinking_config(model, thinking)
        if cfg:
            body["generationConfig"] = {"thinkingConfig": cfg}
        r = await self._call("POST", f"/models/{model}:generateContent", json=body)
        if r.status_code != 200:
            raise _failure(r)
        out = r.json()
        content = ((out.get("candidates") or [{}])[0]).get("content") or {}
        parts = content.get("parts")
        if not parts:
            block = (out.get("promptFeedback") or {}).get("blockReason")
            raise AiError(f"Gemini declined to answer ({block})." if block else "Gemini sent back an empty answer.")
        text, calls = "", []
        for p in parts:
            if p.get("thought") is True:
                continue
            text += p.get("text", "") if isinstance(p.get("text"), str) else ""
            fc = p.get("functionCall")
            if fc and fc.get("name"):
                calls.append(ToolCall(f"call_{len(calls)}", fc["name"], fc.get("args") or {}))
        return ChatResult(text, calls, {"role": "model", "parts": parts})
