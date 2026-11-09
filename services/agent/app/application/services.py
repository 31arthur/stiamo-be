"""Settings, prompts and chat: everything a person does with the agents from the web app. One service, many agents."""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import (
    DataGateway,
    DecisionClient,
    LlmClient,
    Proposal,
    ProposalRepository,
    RunLog,
    RunRecord,
    SettingsRepository,
    UserSettings,
    Workflow,
    WorkflowRepository,
)
from app.application.registry import AgentRegistry
from app.application.runner import AgentRun, Tool, run_agent
from app.application.scope import PlannerData, ScopedData
from app.application.toolpacks import PackContext
from app.application.usage_totals import Bucket
from app.domain.agents import WORKSPACES, AgentDefinition, UnknownAgent, clean_workspaces
from app.domain.llm import DECISION_MODELS, PROVIDER_IDS, THINKING_LEVELS, AiError, Message, ModelInfo, clamp_thinking
from app.domain.prompt import Package, PromptConfig, build_package

log = logging.getLogger(__name__)

MAX_HISTORY = 40
#: What a run keeps of what was typed.
MAX_INPUT_CHARS = 4000
STATS_DAYS = 30
DAILY_DAYS = 14
#: The selection an agent with none of its own starts from: the choice made last, for any agent.
ANY = "*"


DEFAULT_ZONE = "Asia/Kolkata"


def local_today(s: UserSettings | None, now: datetime | None = None) -> date:
    """Today on the person's own clock (the time zone set for their evening plan, India's by default), not the server's: an hour
    after midnight there is already tomorrow's date, and "tomorrow" must mean theirs."""
    name = ((s.planner if s else None) or {}).get("timezone") or DEFAULT_ZONE
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        zone = UTC
    return (now or datetime.now(UTC)).astimezone(zone).date()


def selection_of(s: UserSettings, agent_id: str) -> tuple[dict[str, Any] | None, bool]:
    """The provider and model an agent uses, and whether that is its own choice (False: it follows the one made last)."""
    own, shared = s.selections.get(agent_id), s.selections.get(ANY)
    pick = own or shared
    return (pick if pick and pick.get("providerId") in s.keys else None), own is None


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
    """Remembers what it fetched, so one question reads each list from the other services once, however many packs ask."""

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

    async def records(self, table):
        return await self._once(f"records:{table}", lambda: self._inner.records(table))

    async def mail_uids(self, account_id, folder, since, before):
        return await self._inner.mail_uids(account_id, folder, since, before)

    async def mail_summaries(self, account_id, folder, uids):
        return await self._inner.mail_summaries(account_id, folder, uids)

    async def feed_items(self, feeds, since_days, known_ids, limit):
        return await self._inner.feed_items(feeds, since_days, known_ids, limit)

    async def mail_message(self, account_id, folder, uid):
        return await self._inner.mail_message(account_id, folder, uid)

    async def create_meeting(self, record):
        return await self._inner.create_meeting(record)

    async def mail_correspondents(self, account_id):
        return await self._once(f"correspondents:{account_id}", lambda: self._inner.mail_correspondents(account_id))

    async def mail_trash(self, account_id, folder, uids):
        return await self._inner.mail_trash(account_id, folder, uids)

    async def mail_accounts(self):
        return await self._once("mail_accounts", self._inner.mail_accounts)

    async def mail_send(self, account_id, message):
        return await self._inner.mail_send(account_id, message)

    async def mail_test(self, account_id):
        return await self._inner.mail_test(account_id)

    async def drive_available(self):
        return await self._once("drive", self._inner.drive_available)

    async def drive_search(self, query):
        return await self._inner.drive_search(query)

    async def drive_suggest(self, terms):
        return await self._inner.drive_suggest(terms)

    async def calendar_events(self, start, end):
        return await self._once(f"calendar:{start}:{end}", lambda: self._inner.calendar_events(start, end))

    async def save_record(self, table, record):
        await self._inner.save_record(table, record)
        self._memo.pop(f"records:{table}", None)


@dataclass
class Prepared:
    settings: UserSettings
    config: PromptConfig
    tools: list[Tool]
    context: dict[str, Any]


class AgentService:
    def __init__(
        self,
        repo: SettingsRepository,
        registry: AgentRegistry,
        runs: RunLog,
        workflows: WorkflowRepository,
        llm_factory: Callable[[str, str], LlmClient],
        decision_factory: Callable[[str], DecisionClient],
        data_factory: Callable[[str], DataGateway],
        max_steps: int = 8,
        proposals: ProposalRepository | None = None,
    ) -> None:
        self._repo, self._registry, self._runs, self._workflows = repo, registry, runs, workflows
        self._llm, self._decisions, self._data, self._max_steps = llm_factory, decision_factory, data_factory, max_steps
        self._proposals = proposals

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    async def _load(self, who: Who) -> UserSettings:
        s = await self._repo.get(who.user_id) or UserSettings(who.user_id)
        s.email = who.email or s.email
        return s

    # --- what is set, for everyone -----------------------------------------------------------------------

    async def view(self, who: Who) -> dict[str, Any]:
        """The providers and the decision model (shared by every agent), and a line on each agent."""
        s = await self._load(who)
        decision = s.decision or {}
        return {
            "providers": {pid: ({"connected": True, "last4": s.keys[pid][-4:]} if pid in s.keys else {"connected": False}) for pid in PROVIDER_IDS},
            "decision": {"enabled": bool(decision.get("enabled")), "modelId": decision.get("modelId") or DECISION_MODELS[0]["id"]},
            "decisionModels": list(DECISION_MODELS),
            "thinkingLevels": list(THINKING_LEVELS),
            "agents": [self._summary(a, s) for a in self._registry.all()] + [self._workflow_summary(w, s) for w in await self._workflows.all(who.user_id)],
            "defaultAgent": self._registry.default_id,
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
        s.selections = {k: v for k, v in s.selections.items() if v.get("providerId") != provider}
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

    async def set_decision(self, who: Who, enabled: bool, model_id: str) -> None:
        if model_id not in {m["id"] for m in DECISION_MODELS}:
            raise ValidationFailed("Unknown decision model.")
        s = await self._load(who)
        if enabled and "openrouter" not in s.keys:
            raise ValidationFailed("Decision models run through OpenRouter. Add an OpenRouter key first.")
        s.decision = {"enabled": enabled, "modelId": model_id}
        await self._repo.save(s)

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

    @staticmethod
    def _provider(provider: str) -> None:
        if provider not in PROVIDER_IDS:
            raise ValidationFailed("Unknown provider.")

    # --- one agent -----------------------------------------------------------------------------------------

    def _selection(self, s: UserSettings, agent_id: str) -> tuple[dict[str, Any] | None, bool]:
        return selection_of(s, agent_id)

    async def _known(self, who: Who, agent_id: str) -> None:
        """Agents come in two kinds: the ones in code, and the ones this person built. Either may have a model and a history."""
        try:
            self._registry.get(agent_id)
        except UnknownAgent:
            if await self._workflows.get(who.user_id, agent_id) is None:
                raise

    def _prompt(self, agent: AgentDefinition, s: UserSettings) -> PromptConfig:
        stored = s.prompts.get(agent.id)
        try:
            return PromptConfig(**stored) if stored else PromptConfig(system=agent.system, input_template=agent.input_template)
        except ValueError:
            return PromptConfig(system=agent.system, input_template=agent.input_template)  # damaged settings fall back to the defaults

    def _summary(self, agent: AgentDefinition, s: UserSettings) -> dict[str, Any]:
        selection, inherited = self._selection(s, agent.id)
        return {"id": agent.id, "kind": "chat", "name": agent.name, "description": agent.description, "suggestions": list(agent.suggestions), "selection": selection, "selectionInherited": inherited and selection is not None, "customPrompt": agent.id in s.prompts, "workspaces": s.workspaces.get(agent.id, list(agent.workspaces))}

    def _workflow_summary(self, w: Workflow, s: UserSettings) -> dict[str, Any]:
        selection, inherited = self._selection(s, w.id)
        return {"id": w.id, "kind": "workflow", "name": w.name, "description": w.description, "suggestions": [], "selection": selection, "selectionInherited": inherited and selection is not None, "customPrompt": False, "workspaces": s.workspaces.get(w.id, [])}

    async def agent(self, who: Who, agent_id: str) -> dict[str, Any]:
        """Everything the prompt editor needs for one agent."""
        agent = self._registry.get(agent_id)
        s = await self._load(who)
        prompt = self._prompt(agent, s)
        return {
            **self._summary(agent, s),
            "prompt": {"system": prompt.system, "inputTemplate": prompt.input_template, "tools": {k: v.model_dump() for k, v in prompt.tools.items()}},
            "defaults": {"system": agent.system, "inputTemplate": agent.input_template},
            "variables": self._registry.variables(agent),
            "packs": list(agent.packs),
        }

    async def visible_agents(self, who: Who, workspace: str) -> list[dict[str, Any]]:
        """The team's agents that can answer in a workspace ("all": every one), as the person has assigned them."""
        s = await self._load(who)
        out = []
        for a in self._registry.all():
            where = s.workspaces.get(a.id, list(a.workspaces))
            if workspace == "all" or workspace in where:
                out.append({"id": a.id, "name": a.name, "description": a.description, "suggestions": list(a.suggestions)})
        return out

    async def jev_ready(self, who: Who) -> bool:
        """Whether the decision model is on and has what it needs to run."""
        s = await self._load(who)
        return bool((s.decision or {}).get("enabled")) and "openrouter" in s.keys

    async def set_selection(self, who: Who, agent_id: str, provider: str, model_id: str, thinking: str) -> None:
        await self._known(who, agent_id)
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
        choice = {"providerId": provider, "modelId": model_id.strip(), "thinking": thinking}
        s.selections = {**s.selections, agent_id: choice, ANY: choice}
        await self._repo.save(s)

    async def set_workspaces(self, who: Who, agent_id: str, workspaces: list[str]) -> None:
        """Which workspaces an agent is shown in. It can belong to several; "all workspaces" always shows every agent."""
        await self._known(who, agent_id)
        s = await self._load(who)
        s.workspaces = {**s.workspaces, agent_id: clean_workspaces(workspaces)}
        await self._repo.save(s)

    async def set_prompt(self, who: Who, agent_id: str, prompt: PromptConfig) -> None:
        agent = self._registry.get(agent_id)
        s = await self._load(who)
        s.prompts = {**s.prompts, agent_id: prompt.validated(self._registry.variables(agent)).model_dump()}
        await self._repo.save(s)

    async def reset_prompt(self, who: Who, agent_id: str) -> None:
        self._registry.get(agent_id)
        s = await self._load(who)
        s.prompts = {k: v for k, v in s.prompts.items() if k != agent_id}
        await self._repo.save(s)

    # --- asking ----------------------------------------------------------------------------------------------

    async def _prepare(self, who: Who, token: str, agent: AgentDefinition, channel: str, prompt: PromptConfig | None = None, channel_id: str | None = None, workspace: str | None = None, plan_for: str | None = None) -> Prepared:
        """Builds the agent for this person: their tools (from the agent's packs, bound to their data) and the prompt's variables."""
        s = await self._load(who)
        today = local_today(s).isoformat()
        decision = s.decision or {}
        decisions = self._decisions(s.keys["openrouter"]) if decision.get("enabled") and "openrouter" in s.keys else None
        scope = plan_for if plan_for in WORKSPACES else workspace if workspace in WORKSPACES else None
        data: DataGateway = Memo(self._data(token))
        if scope and (scope == plan_for or "agenda" in agent.packs):
            data = PlannerData(data, scope)  # an agent that plans a workspace's day chooses from its own work, around everything else on the day
        elif scope:
            data = ScopedData(data, scope)  # a workspace's assistant sees that workspace's data only
        ctx = PackContext(data, today, decisions, decision.get("modelId") or DECISION_MODELS[0]["id"], lambda: self._waiting(who, scope), self._proposer(who, agent.id, channel_id), channel != "the evening plan", scope)
        tools: list[Tool] = []
        context: dict[str, Any] = {"today": today, "user": who.name, "channel": channel}
        for name in agent.packs:
            pack = self._registry.packs.get(name)
            tools += await pack.tools(ctx)
            context.update(await pack.context(ctx))
        names = [t.spec.name for t in tools]
        if len(names) != len(set(names)):
            raise RuntimeError(f"Agent {agent.id!r} gets the same tool name from two packs: {sorted({n for n in names if names.count(n) > 1})}")
        return Prepared(s, prompt or self._prompt(agent, s), tools, context)

    def _proposer(self, who: Who, agent_id: str, channel_id: str | None):
        """What an agent's propose tools call to keep a proposal for the person to approve. None when proposals aren't kept here."""
        repo = self._proposals
        if repo is None:
            return None

        async def propose(action: str, title: str, lines: list[str], ops: list[dict[str, Any]]) -> str:
            pid = str(uuid.uuid4())
            await repo.add(Proposal(pid, who.user_id, agent_id, action, title, lines, ops, "pending", channel_id, datetime.now(UTC)))
            return pid

        return propose

    async def _waiting(self, who: Who, workspace: str | None = None) -> list[dict[str, Any]]:
        """What is waiting in the Attention queue, for the agents that read it. For a workspace: what its agents put there."""
        agents = {w.id: w for w in await self._workflows.all(who.user_id)}
        mine = (await self._load(who)).workspaces if workspace else {}
        out = []
        for r in await self._workflows.queue(who.user_id, ["queued", "event_proposed"]):
            if workspace and workspace not in (mine.get(r.agent_id) or ["sb"]):  # an agent nobody placed is the S&B desk's
                continue
            d = r.data or {}
            out.append({"from": agents[r.agent_id].name if r.agent_id in agents else "", "title": r.title, "summary": d.get("summary") or r.title, "priority": d.get("priority") or "normal", "action": d.get("action"), "due": d.get("due"), "event": d.get("event"), "status": r.status})
        return out

    @staticmethod
    def _tools_for(package: Package, tools: list[Tool]) -> list[Tool]:
        """The tools the person left on, with the descriptions they gave them."""
        by_name = {t.name: t for t in package.tools}
        return [Tool(by_name[t.spec.name], t.label, t.run) for t in tools if t.spec.name in by_name]

    async def preview(self, who: Who, token: str, agent_id: str, question: str, prompt: PromptConfig | None = None) -> dict[str, Any]:
        """Exactly what would be sent to the model for this question, so the prompt can be judged before it is used."""
        agent = self._registry.get(agent_id)
        p = await self._prepare(who, token, agent, "the web app", prompt.validated(self._registry.variables(agent)) if prompt else None)
        pkg = build_package(p.config, [t.spec for t in p.tools], p.context, question)
        return {"system": pkg.system, "user": pkg.user, "tools": [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in pkg.tools]}

    async def chat(
        self, who: Who, token: str, agent_id: str, message: str, history: list[dict[str, Any]] | None = None, channel: str = "the web app",
        *, trigger: str = "chat", channel_id: str | None = None, preface: str = "", workspace: str | None = None, plan_for: str | None = None,
    ) -> AgentRun:
        """Asks an agent. Every question that reaches a model is kept in the run log, whether it worked or not."""
        agent = self._registry.get(agent_id)
        message = message.strip()
        if not message:
            raise ValidationFailed("Ask something first.")
        try:
            p = await self._prepare(who, token, agent, channel, channel_id=channel_id, workspace=workspace, plan_for=plan_for)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        sel, _ = self._selection(p.settings, agent.id)
        if not sel or not sel.get("modelId"):
            raise ValidationFailed(f"Choose a provider and a model for the {agent.name} first (AI agents → {agent.name}).")
        try:
            past = [Message.from_json(m) for m in (history or [])[-MAX_HISTORY:] if m.get("role") != "system"]
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationFailed("The earlier conversation couldn't be read. Start a new chat.") from exc
        # What the conversation has established so far (when another agent may have answered before), ahead of the message itself.
        asked = f"{preface}\n\nThe person's latest message:\n{message}" if preface else message
        pkg = build_package(p.config, [t.spec for t in p.tools], p.context, asked)
        client = self._llm(sel["providerId"], p.settings.keys[sel["providerId"]])

        record = RunRecord(
            id=str(uuid.uuid4()), user_id=who.user_id, agent_id=agent.id, trigger=trigger, status="ok", started_at=datetime.now(UTC),
            provider=sel["providerId"], model=sel["modelId"], thinking=sel.get("thinking", "off"), input=message[:MAX_INPUT_CHARS],
            user_message=pkg.user[:MAX_INPUT_CHARS * 2], system_prompt=pkg.system, tools=[t.name for t in pkg.tools], channel=channel_id, agent_name=agent.name,
        )
        began = time.monotonic()
        try:
            run = await run_agent(client=client, model=sel["modelId"], thinking=record.thinking, system=pkg.system, user=pkg.user, tools=self._tools_for(pkg, p.tools), history=past, max_steps=self._max_steps)
        except AiError as exc:
            record.status, record.error = "error", str(exc)
            raise ValidationFailed(str(exc)) from exc
        except Exception as exc:
            record.status, record.error = "error", "Something went wrong inside the agent."
            log.exception("agent %s failed", agent.id)
            raise exc
        else:
            record.steps, record.output, record.found_count, record.usage = [s.to_log() for s in run.steps], run.answer, len(run.found), run.usage
            return run
        finally:
            record.duration_ms = int((time.monotonic() - began) * 1000)
            await self._keep(record)

    async def _keep(self, record: RunRecord) -> None:
        """Logging is for people to look back on: it must never be the reason a question fails."""
        try:
            await self._runs.add(record)
        except Exception:  # noqa: BLE001
            log.exception("could not keep the run log for %s", record.agent_id)

    # --- what an agent has done ---------------------------------------------------------------------------------

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _run_summary(self, r: RunRecord) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": r.id, "agentId": r.agent_id, "trigger": r.trigger, "status": r.status, "startedAt": self._iso(r.started_at), "durationMs": r.duration_ms,
            "provider": r.provider, "model": r.model, "input": r.input[:200], "output": r.output[:200], "found": r.found_count, "steps": r.steps_count,
        }
        if r.error:
            out["error"] = r.error
        return out

    async def runs(self, who: Who, agent_id: str, limit: int = 30, before: str | None = None) -> dict[str, Any]:
        await self._known(who, agent_id)
        cut = datetime.fromisoformat(before.replace("Z", "+00:00")) if before else None
        rows = await self._runs.recent(who.user_id, agent_id, limit=limit + 1, before=cut)
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [self._run_summary(r) for r in rows], "next": self._iso(rows[-1].started_at) if more and rows else None}

    async def run(self, who: Who, run_id: str) -> dict[str, Any]:
        r = await self._runs.get(who.user_id, run_id)
        if r is None:
            raise NotFound("No such run.")
        return {
            **self._run_summary(r), "input": r.input, "output": r.output, "thinking": r.thinking, "userMessage": r.user_message,
            "systemPrompt": r.system_prompt, "tools": r.tools, "steps": r.steps, "usage": r.usage, "channel": r.channel,
        }

    async def clear_runs(self, who: Who, agent_id: str) -> int:
        await self._known(who, agent_id)
        return await self._runs.clear(who.user_id, agent_id)

    async def stats(self, who: Who, agent_id: str) -> dict[str, Any]:
        """How an agent has been doing: totals, the last 30 days, and a day-by-day count for the last 14."""
        await self._known(who, agent_id)
        now = datetime.now(UTC)
        rows = await self._runs.since(who.user_id, agent_id, now - timedelta(days=STATS_DAYS))
        ok = [r for r in rows if r.status == "ok"]
        by_trigger: dict[str, int] = {}
        for r in rows:
            by_trigger[r.trigger] = by_trigger.get(r.trigger, 0) + 1
        daily = {(now - timedelta(days=i)).date().isoformat(): {"runs": 0, "errors": 0} for i in range(DAILY_DAYS - 1, -1, -1)}
        for r in rows:
            d = daily.get(r.started_at.astimezone(UTC).date().isoformat())
            if d:
                d["runs"] += 1
                d["errors"] += r.status != "ok"
        last = (await self._runs.recent(who.user_id, agent_id, limit=1)) or []
        everything = Bucket()
        for u in await self._runs.usage_rows(who.user_id, agent_id=agent_id):
            everything.add(u)
        return {
            "allTime": everything.json(),
            "totalRuns": await self._runs.count(who.user_id, agent_id),
            "windowDays": STATS_DAYS,
            "runs": len(rows),
            "ok": len(ok),
            "errors": len(rows) - len(ok),
            "avgDurationMs": int(sum(r.duration_ms for r in ok) / len(ok)) if ok else 0,
            "inputTokens": sum(r.usage.get("inputTokens", 0) for r in rows),
            "outputTokens": sum(r.usage.get("outputTokens", 0) for r in rows),
            "cost": round(sum(float(r.usage.get("cost", 0) or 0) for r in rows), 6) if any(r.usage.get("cost") is not None for r in rows) else None,
            "byTrigger": by_trigger,
            "daily": [{"date": k, **v} for k, v in daily.items()],
            "lastRun": self._run_summary(last[0]) if last else None,
        }

