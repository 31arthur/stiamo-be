"""Settings, prompt and chat: everything a person does with the agent from the web app."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.ports import DataGateway, DecisionClient, LlmClient, SettingsRepository, UserSettings
from app.application.runner import AgentRun, Tool, run_agent
from app.application.tools import build_tools
from app.domain.llm import DECISION_MODELS, PROVIDER_IDS, THINKING_LEVELS, AiError, Message, ModelInfo, clamp_thinking
from app.domain.prompt import DEFAULT_INPUT, DEFAULT_SYSTEM, VARIABLES, Package, PromptConfig, build_package

MAX_HISTORY = 40


@dataclass(frozen=True)
class Who:
    """The person the agent is acting for."""

    user_id: str
    email: str

    @property
    def name(self) -> str:
        local = self.email.split("@")[0].replace(".", " ").replace("_", " ").strip()
        return local.title() if local else "the owner"


class Memo(DataGateway):
    """Remembers what it fetched, so one question reads each list from the other services once."""

    def __init__(self, inner: DataGateway) -> None:
        self._inner, self._memo = inner, {}

    async def _once(self, key: str, load: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = await load()
        return self._memo[key]

    async def cases(self):
        return await self._once("cases", self._inner.cases)

    async def hearings(self):
        return await self._once("hearings", self._inner.hearings)

    async def attachments(self):
        return await self._once("attachments", self._inner.attachments)

    async def drive_available(self):
        return await self._once("drive", self._inner.drive_available)

    async def drive_search(self, query):
        return await self._inner.drive_search(query)

    async def drive_suggest(self, terms):
        return await self._inner.drive_suggest(terms)


def _prompt_of(s: UserSettings | None) -> PromptConfig:
    try:
        return PromptConfig(**(s.prompt or {})) if s and s.prompt else PromptConfig()
    except ValueError:
        return PromptConfig()  # damaged settings fall back to the defaults rather than breaking the agent


class AgentService:
    def __init__(
        self,
        repo: SettingsRepository,
        llm_factory: Callable[[str, str], LlmClient],
        decision_factory: Callable[[str], DecisionClient],
        data_factory: Callable[[str], DataGateway],
        max_steps: int = 8,
    ) -> None:
        self._repo, self._llm, self._decisions, self._data, self._max_steps = repo, llm_factory, decision_factory, data_factory, max_steps

    async def _load(self, who: Who) -> UserSettings:
        s = await self._repo.get(who.user_id) or UserSettings(who.user_id)
        s.email = who.email or s.email
        return s

    # --- what is set -----------------------------------------------------------------------------------

    async def view(self, who: Who) -> dict[str, Any]:
        s = await self._load(who)
        prompt = _prompt_of(s)
        decision = s.decision or {}
        return {
            "providers": {pid: ({"connected": True, "last4": s.keys[pid][-4:]} if pid in s.keys else {"connected": False}) for pid in PROVIDER_IDS},
            "selection": s.selection,
            "decision": {"enabled": bool(decision.get("enabled")), "modelId": decision.get("modelId") or DECISION_MODELS[0]["id"]},
            "decisionModels": list(DECISION_MODELS),
            "prompt": {"system": prompt.system, "inputTemplate": prompt.input_template, "tools": {k: v.model_dump() for k, v in prompt.tools.items()}},
            "defaults": {"system": DEFAULT_SYSTEM, "inputTemplate": DEFAULT_INPUT},
            "variables": VARIABLES,
            "thinkingLevels": list(THINKING_LEVELS),
        }

    async def put_key(self, who: Who, provider: str, key: str) -> list[ModelInfo]:
        """A key is kept only once the provider has accepted it and offered models."""
        self._provider(provider)
        key = key.strip()
        if not key:
            raise ValidationFailed("Paste the key first.")
        try:
            models = await self._llm(provider, key).list_models()
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        if not models:
            raise ValidationFailed("That key works, but it has no models this agent can use.")
        s = await self._load(who)
        s.keys[provider] = key
        await self._repo.save(s)
        return models

    async def remove_key(self, who: Who, provider: str) -> None:
        self._provider(provider)
        s = await self._load(who)
        s.keys.pop(provider, None)
        if s.selection and s.selection.get("providerId") == provider:
            s.selection = None
        if provider == "openrouter":
            s.decision = {**(s.decision or {}), "enabled": False}
        await self._repo.save(s)

    async def models(self, who: Who, provider: str) -> list[ModelInfo]:
        self._provider(provider)
        s = await self._load(who)
        if provider not in s.keys:
            raise ValidationFailed("Add a key for that provider first.")
        try:
            return await self._llm(provider, s.keys[provider]).list_models()
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc

    async def set_selection(self, who: Who, provider: str, model_id: str, thinking: str) -> None:
        self._provider(provider)
        s = await self._load(who)
        if provider not in s.keys:
            raise ValidationFailed("Add a key for that provider first.")
        if thinking not in THINKING_LEVELS:
            raise ValidationFailed("Unknown thinking level.")
        # The model's own list says which levels it takes; a level it doesn't take is replaced by one it does.
        try:
            known = next((m for m in await self._llm(provider, s.keys[provider]).list_models() if m.id == model_id.strip()), None)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        if known is not None:
            thinking = clamp_thinking(known.thinking, thinking)
        s.selection = {"providerId": provider, "modelId": model_id.strip(), "thinking": thinking}
        await self._repo.save(s)

    async def set_decision(self, who: Who, enabled: bool, model_id: str) -> None:
        if model_id not in {m["id"] for m in DECISION_MODELS}:
            raise ValidationFailed("Unknown decision model.")
        s = await self._load(who)
        if enabled and "openrouter" not in s.keys:
            raise ValidationFailed("Decision models run through OpenRouter. Add an OpenRouter key first.")
        s.decision = {"enabled": enabled, "modelId": model_id}
        await self._repo.save(s)

    async def set_prompt(self, who: Who, prompt: PromptConfig) -> None:
        s = await self._load(who)
        s.prompt = prompt.validated().model_dump()
        await self._repo.save(s)

    async def reset_prompt(self, who: Who) -> None:
        s = await self._load(who)
        s.prompt = None
        await self._repo.save(s)

    @staticmethod
    def _provider(provider: str) -> None:
        if provider not in PROVIDER_IDS:
            raise ValidationFailed("Unknown provider.")

    # --- asking ----------------------------------------------------------------------------------------

    async def _prepare(self, who: Who, token: str, channel: str, prompt: PromptConfig | None = None) -> tuple[UserSettings, PromptConfig, list[Tool], dict[str, Any]]:
        s = await self._load(who)
        data = Memo(self._data(token))
        decision = s.decision or {}
        decisions = self._decisions(s.keys["openrouter"]) if decision.get("enabled") and "openrouter" in s.keys else None
        tools = await build_tools(data=data, today=date.today().isoformat(), decisions=decisions, decision_model=decision.get("modelId") or DECISION_MODELS[0]["id"])
        context = {"today": date.today().isoformat(), "user": who.name, "channel": channel, "cases": len(await data.cases()), "drive": "connected" if await data.drive_available() else "not connected"}
        return s, prompt or _prompt_of(s), tools, context

    @staticmethod
    def _tools_for(package: Package, tools: list[Tool]) -> list[Tool]:
        by_name = {t.name: t for t in package.tools}
        return [Tool(by_name[t.spec.name], t.label, t.run) for t in tools if t.spec.name in by_name]

    async def preview(self, who: Who, token: str, question: str, prompt: PromptConfig | None = None) -> dict[str, Any]:
        """Exactly what would be sent to the model for this question, so the prompt can be judged before it is used."""
        _, cfg, tools, context = await self._prepare(who, token, "the web app", prompt.validated() if prompt else None)
        pkg = build_package(cfg, [t.spec for t in tools], context, question)
        return {"system": pkg.system, "user": pkg.user, "tools": [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in pkg.tools]}

    async def chat(self, who: Who, token: str, message: str, history: list[dict[str, Any]] | None = None, channel: str = "the web app") -> AgentRun:
        message = message.strip()
        if not message:
            raise ValidationFailed("Ask something first.")
        s, cfg, tools, context = await self._prepare(who, token, channel)
        sel = s.selection
        if not sel or sel.get("providerId") not in s.keys or not sel.get("modelId"):
            raise ValidationFailed("Choose a provider and a model first (AI agents → Providers).")
        try:
            past = [Message.from_json(m) for m in (history or [])[-MAX_HISTORY:] if m.get("role") != "system"]
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationFailed("The earlier conversation couldn't be read. Start a new chat.") from exc
        pkg = build_package(cfg, [t.spec for t in tools], context, message)
        client = self._llm(sel["providerId"], s.keys[sel["providerId"]])
        try:
            return await run_agent(client=client, model=sel["modelId"], thinking=sel.get("thinking", "off"), system=pkg.system, user=pkg.user, tools=self._tools_for(pkg, tools), history=past, max_steps=self._max_steps)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc

    async def decide(self, who: Who, state: Any, questions: dict[str, Any], model_id: str | None = None) -> dict[str, Any]:
        s = await self._load(who)
        if "openrouter" not in s.keys:
            raise ValidationFailed("Decision models run through OpenRouter. Add an OpenRouter key first.")
        model = model_id or (s.decision or {}).get("modelId") or DECISION_MODELS[0]["id"]
        if model not in {m["id"] for m in DECISION_MODELS}:
            raise ValidationFailed("Unknown decision model.")
        try:
            return await self._decisions(s.keys["openrouter"]).decide(model=model, state=state, questions=questions)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
