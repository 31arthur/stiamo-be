"""Workflow agents: create them, test them, run them, and review what they propose.

The pipeline is code (fetch → filter → decide → policy → ledger); the model only judges. See docs/WORKFLOW_AGENTS.md.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import (
    DataGateway,
    DecisionClient,
    LedgerEntry,
    LlmClient,
    RunLog,
    RunRecord,
    SettingsRepository,
    UserSettings,
    Workflow,
    WorkflowRepository,
)
from app.application.services import Memo, Who, selection_of
from app.domain.llm import DECISION_MODELS, AiError, Message
from app.domain.workflows import (
    TEST_ITEMS,
    Item,
    WorkflowConfig,
    apply_filters,
    decide_status,
    item_from_record,
    parse_decision,
    parse_manual,
    system_prompt,
    wrap_item,
)

log = logging.getLogger(__name__)

MAX_WORKFLOWS = 20
#: How many items are decided at once: enough to be quick, few enough not to trip a provider's rate limit.
CONCURRENCY = 3
EXCERPT_CHARS = 300


@dataclass
class Outcome:
    item: Item
    status: str  # proposed, needs_review or failed
    label: str | None = None
    confidence: float = 0.0
    reason: str = ""
    error: str | None = None
    ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.item.id, "title": self.item.title, "excerpt": self.item.text[:EXCERPT_CHARS], "label": self.label,
            "confidence": round(self.confidence, 3), "reason": self.reason, "status": self.status, "ms": self.ms,
        }
        if self.error:
            out["error"] = self.error
        return out


def workflow_json(w: Workflow, counts: dict[str, int] | None = None) -> dict[str, Any]:
    c = counts or {}
    iso = lambda d: d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None  # noqa: E731
    return {
        "id": w.id, "kind": "workflow", "name": w.name, "description": w.description, "version": w.version, "config": w.config,
        "createdAt": iso(w.created_at), "updatedAt": iso(w.updated_at), "lastRunAt": iso(w.last_run_at),
        "ledger": c, "pending": c.get("proposed", 0) + c.get("needs_review", 0),
    }


class WorkflowService:
    def __init__(
        self,
        repo: WorkflowRepository,
        settings: SettingsRepository,
        runs: RunLog,
        llm_factory: Callable[[str, str], LlmClient],
        decision_factory: Callable[[str], DecisionClient],
        data_factory: Callable[[str], DataGateway],
    ) -> None:
        self._repo, self._settings, self._runs = repo, settings, runs
        self._llm, self._decisions, self._data = llm_factory, decision_factory, data_factory

    # --- the agents themselves -----------------------------------------------------------------------------------

    @staticmethod
    def _config(raw: dict[str, Any]) -> WorkflowConfig:
        try:
            return WorkflowConfig(**raw).validated()
        except PydanticValidationError as exc:  # a field of the wrong type or size: say which
            first = exc.errors()[0]
            where = ".".join(str(x) for x in first["loc"]) or "the setup"
            raise ValidationFailed(f"That setup isn't valid: {where} — {first['msg']}.") from exc

    async def all_for(self, who: Who) -> list[dict[str, Any]]:
        return [workflow_json(w, await self._repo.counts(w.id)) for w in await self._repo.all(who.user_id)]

    async def _mine(self, who: Who, workflow_id: str) -> Workflow:
        w = await self._repo.get(who.user_id, workflow_id)
        if w is None:
            raise NotFound("No such agent.")
        return w

    async def get(self, who: Who, workflow_id: str) -> dict[str, Any]:
        w = await self._mine(who, workflow_id)
        return workflow_json(w, await self._repo.counts(w.id))

    async def create(self, who: Who, name: str, description: str, config: dict[str, Any]) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValidationFailed("Give the agent a name.")
        if len(await self._repo.all(who.user_id)) >= MAX_WORKFLOWS:
            raise ValidationFailed(f"That's the most agents you can build ({MAX_WORKFLOWS}). Delete one first.")
        cfg = self._config(config)
        w = Workflow(f"wf-{uuid.uuid4().hex[:12]}", who.user_id, name[:100], description.strip()[:300], cfg.model_dump(by_alias=True), 1, datetime.now(UTC))
        await self._repo.save(w)
        return workflow_json(w)

    async def update(self, who: Who, workflow_id: str, name: str, description: str, config: dict[str, Any]) -> dict[str, Any]:
        w = await self._mine(who, workflow_id)
        name = name.strip()
        if not name:
            raise ValidationFailed("Give the agent a name.")
        cfg = self._config(config).model_dump(by_alias=True)
        if cfg != w.config:
            w.version += 1  # a change to the setup is a new version, kept
        w.name, w.description, w.config = name[:100], description.strip()[:300], cfg
        await self._repo.save(w)
        return workflow_json(w, await self._repo.counts(w.id))

    async def delete(self, who: Who, workflow_id: str) -> None:
        await self._mine(who, workflow_id)
        await self._repo.delete(who.user_id, workflow_id)
        await self._runs.clear(who.user_id, workflow_id)

    # --- deciding ----------------------------------------------------------------------------------------------

    async def _items(self, token: str, cfg: WorkflowConfig, manual: str | None, seen: set[str], limit: int | None) -> tuple[list[Item], int]:
        """The items to decide about (filtered, capped), and how many matched before the cap."""
        if cfg.source.kind == "manual":
            items = parse_manual(manual or "")
            if not items:
                raise ValidationFailed("Paste at least one item to try. Separate several with a line of ---.")
        else:
            try:
                records = await Memo(self._data(token)).records(cfg.source.table or "")
            except AiError as exc:
                raise ValidationFailed(str(exc)) from exc
            items = [item_from_record(r) for r in records]
        matched = apply_filters(items, cfg.filters.model_copy(update={"limit": 10_000}), seen)
        cap = min(cfg.filters.limit, limit) if limit else cfg.filters.limit
        return matched[:cap], len(matched)

    async def _decider(self, who: Who, cfg: WorkflowConfig, s: UserSettings, workflow_id: str | None):
        """A function that decides one item. Raises ValidationFailed when the model it needs isn't set up."""
        system = system_prompt(cfg, who.name)
        names = [lb.name for lb in cfg.labels]
        if cfg.decider == "jev":
            if "openrouter" not in s.keys:
                raise ValidationFailed("The decision model runs through OpenRouter. Add an OpenRouter key first, or switch this agent to its own model.")
            jev = self._decisions(s.keys["openrouter"])
            jev_model = (s.decision or {}).get("modelId") or DECISION_MODELS[0]["id"]
            criteria = {lb.name: lb.description for lb in cfg.labels}

            async def by_jev(item: Item) -> tuple[str | None, float, str, dict[str, int]]:
                out = await jev.decide(model=jev_model, state=f"{item.title}\n{item.text}", questions={"label": {"type": "choice", "instructions": cfg.prompt, "criteria": criteria}})
                a = (out.get("answers") or {}).get("label") or {}
                choice = a.get("choice")
                if choice not in names:
                    return None, 0.0, "The decision model's answer wasn't one of the labels.", {}
                conf = (a.get("probabilities") or {}).get(choice, a.get("confidence", 0.0))
                return choice, float(conf), "", {}

            return by_jev

        sel, _ = selection_of(s, workflow_id or "draft")
        if not sel or not sel.get("modelId"):
            raise ValidationFailed("Choose a provider and a model for this agent first (Settings).")
        client = self._llm(sel["providerId"], s.keys[sel["providerId"]])
        model, thinking = sel["modelId"], sel.get("thinking", "off")

        async def by_model(item: Item) -> tuple[str | None, float, str, dict[str, int]]:
            usage = {"inputTokens": 0, "outputTokens": 0}
            messages = [Message("system", system), Message("user", wrap_item(item))]
            for attempt in (1, 2):
                reply = await client.chat(model=model, messages=messages, tools=[], thinking=thinking)
                for k, v in (reply.usage or {}).items():
                    usage[k] = usage.get(k, 0) + v
                parsed = parse_decision(reply.text, names)
                if parsed:
                    return parsed.label, parsed.confidence, parsed.reason, usage
                if attempt == 1:  # one more try, told what was wrong
                    messages = [*messages, Message("assistant", reply.text), Message("user", "That wasn't valid. Answer again with the JSON only, using one of the label names.")]
            return None, 0.0, "The model's answer couldn't be read.", usage

        return by_model

    async def _decide_all(self, cfg: WorkflowConfig, items: list[Item], decide) -> list[Outcome]:
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(item: Item) -> Outcome:
            async with gate:
                began = time.monotonic()
                try:
                    label, conf, reason, usage = await decide(item)
                except AiError as exc:
                    return Outcome(item, "failed", error=str(exc), ms=int((time.monotonic() - began) * 1000))
                ms = int((time.monotonic() - began) * 1000)
                if label is None:
                    return Outcome(item, "needs_review", None, conf, reason, ms=ms, usage=usage)  # unsure means ask
                return Outcome(item, decide_status(conf, cfg.threshold(label)), label, conf, reason, ms=ms, usage=usage)

        return await asyncio.gather(*(one(i) for i in items))

    async def _execute(self, who: Who, token: str, *, workflow: Workflow | None, cfg: WorkflowConfig, manual: str | None, mode: str) -> dict[str, Any]:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        started, began = datetime.now(UTC), time.monotonic()
        seen = await self._repo.seen(workflow.id) if workflow else set()
        items, matched = await self._items(token, cfg, manual, seen, TEST_ITEMS if mode == "test" else None)
        decide = await self._decider(who, cfg, s, workflow.id if workflow else None)
        outcomes = await self._decide_all(cfg, items, decide) if items else []

        counts = {"items": len(outcomes), "proposed": 0, "needsReview": 0, "failed": 0}
        for o in outcomes:
            counts[{"proposed": "proposed", "needs_review": "needsReview", "failed": "failed"}[o.status]] += 1
        usage = {"inputTokens": sum(o.usage.get("inputTokens", 0) for o in outcomes), "outputTokens": sum(o.usage.get("outputTokens", 0) for o in outcomes)}
        run_id = str(uuid.uuid4())

        if mode == "run" and workflow:
            await self._repo.record([
                LedgerEntry(str(uuid.uuid4()), workflow.id, who.user_id, o.item.id, o.item.title, o.item.text[:EXCERPT_CHARS], o.label, o.confidence, o.reason, o.status, run_id, workflow.version)
                for o in outcomes if o.status != "failed"
            ])
            await self._repo.touch(workflow.id, started)

        summary = f"{counts['items']} item{'s' if counts['items'] != 1 else ''}: {counts['proposed']} proposed, {counts['needsReview']} to review" + (f", {counts['failed']} failed" if counts["failed"] else "")
        if workflow:
            sel, _ = selection_of(s, workflow.id)
            await self._log(RunRecord(
                id=run_id, user_id=who.user_id, agent_id=workflow.id, trigger="test" if mode == "test" else "manual",
                status="error" if outcomes and counts["failed"] == len(outcomes) else "ok", started_at=started, duration_ms=int((time.monotonic() - began) * 1000),
                provider=(sel or {}).get("providerId", "openrouter" if cfg.decider == "jev" else ""), model=(sel or {}).get("modelId", "") if cfg.decider == "model" else (s.decision or {}).get("modelId", "jev"),
                thinking=(sel or {}).get("thinking", "off") if cfg.decider == "model" else "off", input=f"{counts['items']} items from {cfg.source.table or 'pasted text'}",
                user_message=cfg.prompt, system_prompt=system_prompt(cfg, who.name), steps=[self._step(o) for o in outcomes], steps_count=len(outcomes),
                output=summary, found_count=counts["proposed"] + counts["needsReview"], error=next((o.error for o in outcomes if o.error), None) if counts["failed"] == len(outcomes) and outcomes else None,
                usage=usage, version=workflow.version,
            ))
        return {
            "runId": run_id if workflow else None, "mode": mode, "counts": counts, "matched": matched, "usage": usage, "summary": summary,
            "durationMs": int((time.monotonic() - began) * 1000), "items": [o.to_json() for o in outcomes],
        }

    @staticmethod
    def _step(o: Outcome) -> dict[str, Any]:
        label = f"{o.item.title[:60]} → {o.label or 'no label'} ({round(o.confidence * 100)}%)" if o.status != "failed" else f"{o.item.title[:60]} → failed"
        step: dict[str, Any] = {"tool": "decide", "label": label, "args": {"item": o.item.text[:EXCERPT_CHARS]}, "found": 0, "ms": o.ms, "result": json.dumps({"label": o.label, "confidence": round(o.confidence, 3), "reason": o.reason, "status": o.status})}
        if o.error:
            step["error"] = o.error
        return step

    async def _log(self, record: RunRecord) -> None:
        try:
            await self._runs.add(record)
        except Exception:  # noqa: BLE001  logging must never be the reason a run fails
            log.exception("could not keep the run log for %s", record.agent_id)

    async def test_draft(self, who: Who, token: str, config: dict[str, Any], manual: str | None) -> dict[str, Any]:
        """Tries a setup that isn't saved yet. Nothing is written: not the ledger, not the history."""
        return await self._execute(who, token, workflow=None, cfg=self._config(config), manual=manual, mode="test")

    async def test(self, who: Who, token: str, workflow_id: str, manual: str | None) -> dict[str, Any]:
        """The saved setup on a sample, as it would run, with nothing kept but a line in the history."""
        w = await self._mine(who, workflow_id)
        return await self._execute(who, token, workflow=w, cfg=self._config(w.config), manual=manual, mode="test")

    async def run(self, who: Who, token: str, workflow_id: str, manual: str | None) -> dict[str, Any]:
        """Decides the new items and keeps each decision in the ledger for review."""
        w = await self._mine(who, workflow_id)
        return await self._execute(who, token, workflow=w, cfg=self._config(w.config), manual=manual, mode="run")

    # --- review ------------------------------------------------------------------------------------------------

    async def review(self, who: Who, workflow_id: str) -> dict[str, Any]:
        await self._mine(who, workflow_id)
        rows = await self._repo.review(who.user_id, workflow_id, ["proposed", "needs_review"])
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {"items": [{"id": r.item_id, "title": r.title, "excerpt": r.excerpt, "label": r.label, "confidence": round(r.confidence, 3), "reason": r.reason, "status": r.status, "version": r.version, "at": iso(r.created_at)} for r in rows]}

    async def decide_review(self, who: Who, workflow_id: str, item_ids: list[str], action: str, label: str | None) -> dict[str, int]:
        """A person's answer on items the agent proposed or wasn't sure about. Approving may correct the label."""
        w = await self._mine(who, workflow_id)
        if action not in ("approve", "dismiss"):
            raise ValidationFailed("Approve or dismiss.")
        if label is not None and label not in [lb["name"] for lb in w.config.get("labels", [])]:
            raise ValidationFailed("That isn't one of this agent's labels.")
        if label and len(item_ids) != 1:
            raise ValidationFailed("Change a label one item at a time.")
        n = await self._repo.decide(who.user_id, workflow_id, item_ids, "approved" if action == "approve" else "dismissed", label if action == "approve" else None)
        return {"changed": n}
