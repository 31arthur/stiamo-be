"""Workflow agents: create them, test them, run them, and review what they propose.

The pipeline is code (fetch → filter → decide → policy → ledger); the model only judges. See docs/WORKFLOW_AGENTS.md.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
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
from app.domain.agents import clean_workspaces
from app.domain.llm import DECISION_MODELS, AiError, Message
from app.domain.workflows import (
    MAIL_PAGES,
    SAMPLE_ITEMS,
    TEST_ITEMS,
    Item,
    WorkflowConfig,
    account_of,
    apply_filters,
    decide_status,
    item_from_mail,
    item_from_record,
    jev_batch,
    parse_batch,
    parse_decision,
    parse_manual,
    protected_by,
    render_batch,
    system_prompt,
    uid_of,
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
    status: str  # proposed, needs_review, failed, kept (judged fine, nothing to do) or applied (moved to Trash)
    label: str | None = None
    confidence: float = 0.0
    reason: str = ""
    error: str | None = None
    ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    #: What the decision model charged for this item, when it says (USD).
    cost: float = 0.0
    #: What is (or, in a test run, would be) done with the item: `trash`.
    action: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.item.id, "title": self.item.title, "excerpt": self.item.text[:EXCERPT_CHARS], "label": self.label,
            "confidence": round(self.confidence, 3), "reason": self.reason, "status": self.status, "ms": self.ms,
        }
        if self.error:
            out["error"] = self.error
        if self.action:
            out["action"] = self.action
        return out


@dataclass
class Verdict:
    """One item's answer: the label, how sure, why, and what asking cost."""

    label: str | None
    confidence: float = 0.0
    reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    cost: float = 0.0


@dataclass
class Decider:
    """Asks the chosen model about one item, or about several at once (a batch). `many` leaves a hole for an item it got no answer for."""

    one: Callable[[Item], Awaitable[Verdict]]
    many: Callable[[list[Item]], Awaitable[list[Verdict | None]]]
    provider: str
    model: str


@dataclass
class Fetched:
    items: list[Item]
    #: How many matched the filters before the cap.
    matched: int
    #: Set aside by the rules, never judged.
    protected: int
    #: How many the source had to look through.
    scanned: int


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

    async def create(self, who: Who, name: str, description: str, config: dict[str, Any], workspaces: list[str] | None = None) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValidationFailed("Give the agent a name.")
        if len(await self._repo.all(who.user_id)) >= MAX_WORKFLOWS:
            raise ValidationFailed(f"That's the most agents you can build ({MAX_WORKFLOWS}). Delete one first.")
        cfg = self._config(config)
        chosen = clean_workspaces(workspaces or [])
        w = Workflow(f"wf-{uuid.uuid4().hex[:12]}", who.user_id, name[:100], description.strip()[:300], cfg.model_dump(by_alias=True), 1, datetime.now(UTC))
        await self._repo.save(w)
        if chosen:
            s = await self._settings.get(who.user_id) or UserSettings(who.user_id, who.email)
            s.workspaces = {**s.workspaces, w.id: chosen}
            await self._settings.save(s)
        return {**workflow_json(w), "workspaces": chosen}

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
        s = await self._settings.get(who.user_id)
        if s and (workflow_id in s.workspaces or workflow_id in s.selections):
            s.workspaces = {k: v for k, v in s.workspaces.items() if k != workflow_id}
            s.selections = {k: v for k, v in s.selections.items() if k != workflow_id}
            await self._settings.save(s)

    # --- deciding ----------------------------------------------------------------------------------------------

    async def _items(self, token: str, cfg: WorkflowConfig, manual: str | None, seen: set[str], limit: int | None) -> Fetched:
        """The items to decide about (filtered, capped), how many matched before the cap, and what the rules set aside."""
        cap = min(cfg.filters.limit, limit) if limit else cfg.filters.limit
        protected = scanned = 0
        if cfg.source.kind == "manual":
            items = parse_manual(manual or "")
            if not items:
                raise ValidationFailed("Paste at least one item to try. Separate several with a line of ---.")
            scanned = len(items)
        elif cfg.source.kind == "mail":
            items, protected, scanned = await self._mail_items(token, cfg, seen, cap)
        else:
            try:
                records = await Memo(self._data(token)).records(cfg.source.table or "")
            except AiError as exc:
                raise ValidationFailed(str(exc)) from exc
            items = [item_from_record(r) for r in records]
            scanned = len(items)
        matched = apply_filters(items, cfg.filters.model_copy(update={"limit": 10_000}), seen)
        return Fetched(matched[:cap], len(matched), protected, scanned)

    async def _mail_items(self, token: str, cfg: WorkflowConfig, seen: set[str], cap: int) -> tuple[list[Item], int, int]:
        """Reads each mailbox newest first, a page at a time, setting aside what the rules protect, until there is enough to decide
        about (or it has looked far enough). Returns the items, how many were protected, and how many messages were looked at."""
        data = Memo(self._data(token))
        cutoff = datetime.now(UTC).timestamp() - cfg.filters.since_days * 86400 if cfg.filters.since_days else None
        items: list[Item] = []
        protected = scanned = 0
        try:
            for account in cfg.source.account_ids:
                friends = set(await data.mail_correspondents(account)) if cfg.rules.protect_correspondents else set()
                found, before = 0, None
                for _ in range(MAIL_PAGES):
                    page = await data.mail_page(account, cfg.source.folder, before)
                    messages = page.get("messages", [])
                    scanned += len(messages)
                    for m in messages:
                        item = item_from_mail(m, page.get("uidValidity", 0), account)
                        if cfg.filters.new_only and item.id in seen:
                            continue
                        if protected_by(item, cfg.rules, friends):
                            protected += 1
                            continue
                        items.append(item)
                        found += 1
                    before = page.get("nextBefore")
                    oldest = (messages or [{}])[-1].get("date")
                    too_old = bool(cutoff and oldest and datetime.fromisoformat(oldest.replace("Z", "+00:00")).timestamp() < cutoff)
                    if not before or found >= cap or too_old:
                        break
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        return items, protected, scanned

    async def _decider(self, who: Who, cfg: WorkflowConfig, s: UserSettings, workflow_id: str | None) -> Decider:
        """What decides, for one item or a batch. Raises ValidationFailed when the model it needs isn't set up."""
        names = [lb.name for lb in cfg.labels]
        batch_system = system_prompt(cfg, who.name, True)
        single_system = system_prompt(cfg, who.name)

        if cfg.decider == "jev":
            if "openrouter" not in s.keys:
                raise ValidationFailed("The decision model runs through OpenRouter. Add an OpenRouter key first, or switch this agent to its own model.")
            jev = self._decisions(s.keys["openrouter"])
            jev_model = cfg.decision_model or (s.decision or {}).get("modelId") or DECISION_MODELS[0]["id"]
            criteria = {lb.name: lb.description for lb in cfg.labels}

            def verdict(answer: dict[str, Any], cost: float) -> Verdict | None:
                choice = answer.get("choice")
                if choice not in names:
                    return None
                return Verdict(choice, float((answer.get("probabilities") or {}).get(choice, answer.get("confidence", 0.0))), "", {}, cost)

            async def one(item: Item) -> Verdict:
                out = await jev.decide(model=jev_model, state=f"{item.title}\n{item.text}", questions={"label": {"type": "choice", "instructions": cfg.prompt, "criteria": criteria}})
                cost = float(out.get("cost") or 0)
                return verdict((out.get("answers") or {}).get("label") or {}, cost) or Verdict(None, 0.0, "The decision model's answer wasn't one of the labels.", {}, cost)

            async def many(items: list[Item]) -> list[Verdict | None]:
                state, questions = jev_batch(cfg, items)
                out = await jev.decide(model=jev_model, state=state, questions=questions)
                share = float(out.get("cost") or 0) / len(items)
                answers = out.get("answers") or {}
                return [verdict(answers.get(str(i)) or {}, share) for i in range(1, len(items) + 1)]

            return Decider(one, many, "openrouter", jev_model)

        sel, _ = selection_of(s, workflow_id or "draft")
        if not sel or not sel.get("modelId"):
            raise ValidationFailed("Choose a provider and a model for this agent first (Settings).")
        client = self._llm(sel["providerId"], s.keys[sel["providerId"]])
        model, thinking = sel["modelId"], sel.get("thinking", "off")

        async def one(item: Item) -> Verdict:
            usage = {"inputTokens": 0, "outputTokens": 0}
            messages = [Message("system", single_system), Message("user", wrap_item(item))]
            for attempt in (1, 2):
                reply = await client.chat(model=model, messages=messages, tools=[], thinking=thinking)
                for k, v in (reply.usage or {}).items():
                    usage[k] = usage.get(k, 0) + v
                parsed = parse_decision(reply.text, names)
                if parsed:
                    return Verdict(parsed.label, parsed.confidence, parsed.reason, usage)
                if attempt == 1:  # one more try, told what was wrong
                    messages = [*messages, Message("assistant", reply.text), Message("user", "That wasn't valid. Answer again with the JSON only, using one of the label names.")]
            return Verdict(None, 0.0, "The model's answer couldn't be read.", usage)

        async def many(items: list[Item]) -> list[Verdict | None]:
            usage = {"inputTokens": 0, "outputTokens": 0}
            messages = [Message("system", batch_system), Message("user", render_batch(items))]
            parsed: dict[int, Any] = {}
            for attempt in (1, 2):
                reply = await client.chat(model=model, messages=messages, tools=[], thinking=thinking)
                for k, v in (reply.usage or {}).items():
                    usage[k] = usage.get(k, 0) + v
                parsed = parse_batch(reply.text, names, len(items))
                if parsed:
                    break
                if attempt == 1:
                    messages = [*messages, Message("assistant", reply.text), Message("user", "That wasn't valid. Answer again with the JSON array only, one object per item, using one of the label names.")]
            n = len(items)
            # What the one request used is shared out, so the totals add up whatever the batch size.
            share = [{k: v // n + (v % n if i == 0 else 0) for k, v in usage.items()} for i in range(n)]
            return [Verdict(p.label, p.confidence, p.reason, share[i]) if (p := parsed.get(i + 1)) else None for i in range(n)]

        return Decider(one, many, sel["providerId"], model)

    async def _decide_all(self, cfg: WorkflowConfig, items: list[Item], decider: Decider) -> tuple[list[Outcome], int]:
        """Decides every item, in requests of `batch_size` items. An item the model didn't answer in its batch is asked about alone."""
        gate = asyncio.Semaphore(CONCURRENCY)
        size = max(1, cfg.batch_size)
        chunks = [items[i : i + size] for i in range(0, len(items), size)]

        def outcome(item: Item, v: Verdict, ms: int) -> Outcome:
            if v.label is None:
                return Outcome(item, "needs_review", None, v.confidence, v.reason, ms=ms, usage=v.usage, cost=v.cost)  # unsure means ask
            return Outcome(item, decide_status(v.confidence, cfg.threshold(v.label)), v.label, v.confidence, v.reason, ms=ms, usage=v.usage, cost=v.cost)

        async def chunk(group: list[Item]) -> list[Outcome]:
            async with gate:
                began = time.monotonic()
                try:
                    verdicts = [await decider.one(group[0])] if size == 1 else await decider.many(group)
                    for i, v in enumerate(verdicts):
                        if v is None:
                            verdicts[i] = await decider.one(group[i])
                except AiError as exc:
                    ms = int((time.monotonic() - began) * 1000)
                    return [Outcome(item, "failed", error=str(exc), ms=ms) for item in group]
                ms = int((time.monotonic() - began) * 1000 / len(group))
                return [outcome(item, v, ms) for item, v in zip(group, verdicts, strict=True) if v is not None]

        return [o for group in await asyncio.gather(*(chunk(g) for g in chunks)) for o in group], len(chunks)

    async def preview(self, who: Who, config: dict[str, Any]) -> dict[str, Any]:
        """Exactly what the model would be sent for this setup, filled with two example emails. Nothing is asked of any model."""
        cfg = self._config(config)
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        batch = cfg.batch_size > 1
        out: dict[str, Any] = {"decider": cfg.decider, "batchSize": cfg.batch_size, "batched": batch}
        if cfg.decider == "jev":
            model = cfg.decision_model or (s.decision or {}).get("modelId") or DECISION_MODELS[0]["id"]
            if batch:
                state, questions = jev_batch(cfg, SAMPLE_ITEMS)
            else:
                state = f"{SAMPLE_ITEMS[0].title}\n{SAMPLE_ITEMS[0].text}"
                questions = {"label": {"type": "choice", "instructions": cfg.prompt, "criteria": {lb.name: lb.description for lb in cfg.labels}}}
            return {**out, "provider": "openrouter", "model": model, "system": None, "user": state, "questions": questions}
        sel, _ = selection_of(s, "draft")
        user = render_batch(SAMPLE_ITEMS) if batch else wrap_item(SAMPLE_ITEMS[0])
        return {**out, "provider": (sel or {}).get("providerId"), "model": (sel or {}).get("modelId"), "system": system_prompt(cfg, who.name, batch), "user": user, "questions": None}

    async def _execute(self, who: Who, token: str, *, workflow: Workflow | None, cfg: WorkflowConfig, manual: str | None, mode: str) -> dict[str, Any]:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        started, began = datetime.now(UTC), time.monotonic()
        seen = await self._repo.seen(workflow.id) if workflow else set()
        fetched = await self._items(token, cfg, manual, seen, TEST_ITEMS if mode == "test" else None)
        items, matched, protected = fetched.items, fetched.matched, fetched.protected
        decider = await self._decider(who, cfg, s, workflow.id if workflow else None)
        outcomes, batches = await self._decide_all(cfg, items, decider) if items else ([], 0)
        trash_note = await self._act(token, cfg, outcomes, mode)

        counts = {"items": len(outcomes), "proposed": 0, "needsReview": 0, "failed": 0}
        if cfg.source.kind == "mail":  # what a mailbox agent adds: judged fine, moved to Trash, set aside by the rules
            counts.update(kept=0, applied=0, protected=protected)
        for o in outcomes:
            counts[{"proposed": "proposed", "needs_review": "needsReview", "failed": "failed", "kept": "kept", "applied": "applied"}[o.status]] += 1
        usage = {"inputTokens": sum(o.usage.get("inputTokens", 0) for o in outcomes), "outputTokens": sum(o.usage.get("outputTokens", 0) for o in outcomes)}
        cost = sum(o.cost for o in outcomes)
        stats = {
            "scanned": fetched.scanned, "protected": protected, "judged": len(outcomes), "requests": batches, "batchSize": cfg.batch_size,
            "decider": cfg.decider, "provider": decider.provider, "model": decider.model, "mailboxes": len(cfg.source.account_ids), "cost": round(cost, 6) if cost else None,
        }
        run_id = str(uuid.uuid4())

        if mode == "run" and workflow:
            await self._repo.record([
                LedgerEntry(str(uuid.uuid4()), workflow.id, who.user_id, o.item.id, o.item.title, o.item.text[:EXCERPT_CHARS], o.label, o.confidence, o.reason, o.status, run_id, workflow.version)
                for o in outcomes if o.status != "failed"
            ])
            await self._repo.touch(workflow.id, started)

        summary = f"{counts['items']} item{'s' if counts['items'] != 1 else ''}: {counts['proposed']} proposed, {counts['needsReview']} to review" + (f", {counts['failed']} failed" if counts["failed"] else "")
        for key, words in (("applied", "moved to Trash"), ("kept", "kept"), ("protected", "protected")):
            if counts.get(key):
                summary += f", {counts[key]} {words}"
        summary += trash_note
        if workflow:
            sel, _ = selection_of(s, workflow.id)
            await self._log(RunRecord(
                id=run_id, user_id=who.user_id, agent_id=workflow.id, trigger="test" if mode == "test" else "manual",
                status="error" if outcomes and counts["failed"] == len(outcomes) else "ok", started_at=started, duration_ms=int((time.monotonic() - began) * 1000),
                provider=decider.provider, model=decider.model,
                thinking=(sel or {}).get("thinking", "off") if cfg.decider == "model" else "off", input=f"{counts['items']} items from {'the mailbox' if cfg.source.kind == 'mail' else cfg.source.table or 'pasted text'}",
                user_message=cfg.prompt, system_prompt=system_prompt(cfg, who.name, cfg.batch_size > 1), steps=[self._step(o) for o in outcomes], steps_count=len(outcomes),
                output=summary, found_count=counts["proposed"] + counts["needsReview"] + counts.get("applied", 0), error=next((o.error for o in outcomes if o.error), None) if counts["failed"] == len(outcomes) and outcomes else None,
                usage=usage, version=workflow.version,
            ))
        return {
            "runId": run_id if workflow else None, "mode": mode, "counts": counts, "matched": matched, "usage": usage, "stats": stats, "summary": summary,
            "durationMs": int((time.monotonic() - began) * 1000), "items": [o.to_json() for o in outcomes],
        }

    async def _act(self, token: str, cfg: WorkflowConfig, outcomes: list[Outcome], mode: str) -> str:
        """What the labels mean for a mailbox. Confident "trash" items are moved to Trash only in a real run of an agent set to act on
        its own; in a test run, or when approval is required, they are only marked. Everything judged fine is kept. Returns a note when
        the move failed (the items then wait in Review as proposals)."""
        act = cfg.action
        if cfg.source.kind != "mail" or not act.trash_label:
            return ""
        for o in outcomes:
            if o.status == "proposed":
                if o.label == act.trash_label:
                    o.action = "trash"
                else:
                    o.status = "kept"
        targets = [o for o in outcomes if o.action == "trash"]
        if mode != "run" or not act.auto or not targets:
            return ""
        notes = []
        data = Memo(self._data(token))
        for account in dict.fromkeys(account_of(o.item.id) for o in targets):
            mine = [o for o in targets if account_of(o.item.id) == account]
            try:
                await data.mail_trash(account, cfg.source.folder, [uid_of(o.item.id) for o in mine])
            except AiError as exc:
                notes.append(str(exc))
                continue
            for o in mine:
                o.status = "applied"
        return f". Couldn't move some to Trash: {notes[0]}" if notes else ""

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

    async def decide_review(self, who: Who, token: str, workflow_id: str, item_ids: list[str], action: str, label: str | None) -> dict[str, int]:
        """A person's answer on items the agent proposed or wasn't sure about. Approving may correct the label."""
        w = await self._mine(who, workflow_id)
        if action not in ("approve", "dismiss"):
            raise ValidationFailed("Approve or dismiss.")
        if label is not None and label not in [lb["name"] for lb in w.config.get("labels", [])]:
            raise ValidationFailed("That isn't one of this agent's labels.")
        if label and len(item_ids) != 1:
            raise ValidationFailed("Change a label one item at a time.")
        moved = 0
        cfg = self._config(w.config)
        if action == "approve" and cfg.source.kind == "mail" and cfg.action.trash_label:
            pending = {r.item_id: r for r in await self._repo.review(who.user_id, workflow_id, ["proposed", "needs_review"])}
            targets = [i for i in item_ids if i in pending and (label or pending[i].label) == cfg.action.trash_label]
            data = Memo(self._data(token))
            try:
                for account in dict.fromkeys(account_of(i) for i in targets):
                    moved += await data.mail_trash(account, cfg.source.folder, [uid_of(i) for i in targets if account_of(i) == account])
            except AiError as exc:
                raise ValidationFailed(str(exc)) from exc
        n = await self._repo.decide(who.user_id, workflow_id, item_ids, "approved" if action == "approve" else "dismissed", label if action == "approve" else None)
        return {"changed": n, "moved": moved} if cfg.source.kind == "mail" else {"changed": n}
