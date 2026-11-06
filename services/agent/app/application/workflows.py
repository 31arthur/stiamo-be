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
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from stiamo_common.errors import Conflict, NotFound, ValidationFailed

from app.application.ports import (
    DataGateway,
    DecisionClient,
    Execution,
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
from app.domain.mailwatch import workspace_of
from app.domain.workflows import (
    MAIL_FETCH,
    MAX_ITEM_CHARS,
    MAX_REFINEMENTS,
    SAMPLE_ITEMS,
    TEST_ITEMS,
    Item,
    WorkflowConfig,
    account_of,
    analysis_prompt,
    apply_filters,
    decide_status,
    extra_guidance,
    feedback_prompt,
    importance_config,
    item_from_article,
    item_from_entry,
    item_from_mail,
    item_from_record,
    jev_batch,
    parse_analysis,
    parse_batch,
    parse_decision,
    parse_feedback,
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
    #: What the agent worked out about it: summary, priority, suggested action, a proposed event.
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.item.id, "title": self.item.title, "excerpt": self.item.text[:EXCERPT_CHARS], "label": self.label,
            "confidence": round(self.confidence, 3), "reason": self.reason, "status": self.status, "ms": self.ms,
        }
        if self.error:
            out["error"] = self.error
        if self.action:
            out["action"] = self.action
        if self.data:
            out["data"] = self.data
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
    #: Set aside by the rules, never judged: each with the rule that kept it.
    protected: list[tuple[Item, str]]
    #: How many the source had to read.
    scanned: int
    #: How many are still waiting for a later run (the folder is worked through, a limited number at a time).
    remaining: int = 0
    #: How many messages are in the range at all.
    total: int = 0


def parse_sender_domain(excerpt: str) -> str | None:
    """The domain of the From address at the top of a kept email."""
    import re

    m = re.search(r"^From:.*?<?([\w.+'-]+@([\w-]+(?:\.[\w-]+)+))>?\s*$", excerpt, re.MULTILINE)
    return m.group(2).lower() if m else None


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
        running: set[str] | None = None,
        sweeping: Callable[[str], bool] | None = None,
        alerts: Any = None,
    ) -> None:
        self._repo, self._settings, self._runs = repo, settings, runs
        #: Agents being run right now, shared by every request of this process. One run of an agent at a time.
        self._running = running if running is not None else set()
        #: Whether a run through the whole inbox is going for an agent (see sweeps.py).
        self._sweeping = sweeping or (lambda _id: False)
        self._llm, self._decisions, self._data = llm_factory, decision_factory, data_factory
        #: Raises an urgent email on the phone (see alerts.py); None where there is no phone to tell.
        self._alerts = alerts

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
        w = Workflow(f"wf-{uuid.uuid4().hex[:12]}", who.user_id, name[:100], description.strip()[:300], cfg.model_dump(by_alias=True, mode="json"), 1, datetime.now(UTC))
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
        cfg = self._config(config).model_dump(by_alias=True, mode="json")
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
        if s and workspace_of(workflow_id):  # a deleted mail watch counts as switched off, so it is not made again
            s.mailwatch = {**s.mailwatch, "off": sorted({*(s.mailwatch.get("off") or []), workspace_of(workflow_id)})}
            await self._settings.save(s)
        if s and (workflow_id in s.workspaces or workflow_id in s.selections):
            s.workspaces = {k: v for k, v in s.workspaces.items() if k != workflow_id}
            s.selections = {k: v for k, v in s.selections.items() if k != workflow_id}
            await self._settings.save(s)

    # --- deciding ----------------------------------------------------------------------------------------------

    async def _items(self, token: str, cfg: WorkflowConfig, manual: str | None, seen: set[str], limit: int | None, who: Who | None = None, run_upstream: bool = False) -> Fetched:
        """The items to decide about (filtered, capped), how many matched before the cap, and what the rules set aside."""
        cap = min(cfg.filters.limit, limit) if limit else cfg.filters.limit
        protected: list[tuple[Item, str]] = []
        scanned = total = remaining = 0
        if cfg.source.kind == "manual":
            items = parse_manual(manual or "")
            if not items:
                raise ValidationFailed("Paste at least one item to try. Separate several with a line of ---.")
            scanned = total = len(items)
        elif cfg.source.kind == "mail":
            items, protected, scanned, total, remaining = await self._mail_items(token, cfg, seen, cap)
        elif cfg.source.kind == "agent":
            items = await self._agent_items(who, token, cfg, seen, run_upstream)
            scanned = total = len(items)
        elif cfg.source.kind == "feed":
            items, total, remaining = await self._feed_items(token, cfg, seen, cap)
            scanned = len(items)
        else:
            try:
                records = await Memo(self._data(token)).records(cfg.source.table or "")
            except AiError as exc:
                raise ValidationFailed(str(exc)) from exc
            items = [item_from_record(r) for r in records]
            scanned = total = len(items)
        matched = apply_filters(items, cfg.filters.model_copy(update={"limit": 10_000}), seen)
        # Whatever was read but not decided this time (the cap) waits for the next run.
        return Fetched(matched[:cap], len(matched), protected, scanned, remaining + max(0, len(matched) - cap) if cfg.source.kind in ("mail", "agent", "feed") else 0, total)

    async def _mail_items(self, token: str, cfg: WorkflowConfig, seen: set[str], cap: int) -> tuple[list[Item], list[tuple[Item, str]], int, int, int]:
        """Works through the mailboxes newest first, in the date range asked for, skipping what an earlier run already judged. It asks
        the mail service only for the list of messages first, then reads them a hundred at a time until there is enough to decide
        about. Returns the items, what the rules protected, how many were read, how many were in range, and how many are left."""
        data = Memo(self._data(token))
        f = cfg.filters
        since = f.after or (datetime.now(UTC).date() - timedelta(days=f.since_days) if f.since_days else None)
        items: list[Item] = []
        protected: list[tuple[Item, str]] = []
        scanned = total = remaining = 0
        try:
            for account in cfg.source.account_ids:
                friends = set(await data.mail_correspondents(account)) if cfg.rules.protect_correspondents else set()
                listing = await data.mail_uids(account, cfg.source.folder, since, f.before)
                validity, everything = listing.get("uidValidity", 0), listing.get("uids", [])
                total += len(everything)
                todo = [u for u in everything if not (f.new_only and f"{account}:{validity}:{u}" in seen)]
                i = 0
                while i < len(todo) and len(items) < cap:
                    chunk = todo[i : i + MAIL_FETCH]
                    i += len(chunk)
                    for m in await data.mail_summaries(account, cfg.source.folder, chunk):
                        scanned += 1
                        item = item_from_mail(m, validity, account)
                        why = protected_by(item, cfg.rules, friends)
                        if why:
                            protected.append((item, why))
                        else:
                            items.append(item)
                remaining += len(todo) - i
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        return items, protected, scanned, total, remaining

    async def _feed_items(self, token: str, cfg: WorkflowConfig, seen: set[str], cap: int) -> tuple[list[Item], int, int]:
        """The newest articles from the feeds that haven't been judged yet. Only those are read from their publishers (that is the slow
        part), and only up to the cap; how many more there are is what a run through everything carries on with."""
        feeds = [f.model_dump() for f in cfg.source.feeds]
        try:
            found, new_total = await Memo(self._data(token)).feed_items(feeds, cfg.filters.since_days, seen if cfg.filters.new_only else set(), cap)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        return [item_from_article(a) for a in found], new_total, max(0, new_total - len(found))

    async def _agent_items(self, who: Who | None, token: str, cfg: WorkflowConfig, seen: set[str], run_upstream: bool) -> list[Item]:
        """The mail another agent has already looked at and kept (or you said wasn't spam). It is run first, so new mail has been through
        it: what that agent trashes never gets here, and its safe senders and rules apply. A test run doesn't run it (that would move mail)."""
        assert who is not None
        up_id = cfg.source.upstream_id or ""
        upstream = await self._repo.get(who.user_id, up_id)
        if upstream is None:
            raise ValidationFailed("The agent this one reads from doesn't exist any more. Choose another in Settings.")
        if run_upstream and cfg.source.run_upstream:
            try:
                await self.run(who, token, up_id, None)
            except Conflict:
                pass  # it is busy (a run or a sweep): carry on with what it has judged so far
        trash = self._config(upstream.config).action.trash_label
        rows = await self._repo.upstream_entries(who.user_id, up_id, ["kept", "protected", "approved"])
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return [
            item_from_entry(r.item_id, r.title, r.excerpt, iso(r.created_at))
            for r in rows
            if not (r.status == "approved" and trash and r.label == trash) and not (cfg.filters.new_only and r.item_id in seen)
        ]

    async def safe_senders(self, token: str, account_ids: list[str]) -> list[dict[str, Any]]:
        """Who the safe-senders shield would protect in each mailbox: the addresses found in its Sent folder."""
        data = Memo(self._data(token))
        out = []
        for account in dict.fromkeys(account_ids):
            try:
                found = sorted(await data.mail_correspondents(account))
            except AiError as exc:
                out.append({"accountId": account, "count": 0, "sample": [], "error": str(exc)})
                continue
            out.append({"accountId": account, "count": len(found), "sample": found[:30], "error": None})
        return out

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
                out = await jev.decide(model=jev_model, state=f"{item.title}\n{item.text}", questions={"label": {"type": "choice", "instructions": f"{cfg.prompt}\n\n{extra_guidance(cfg)}".strip(), "criteria": criteria}})
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
                    return Verdict(parsed.label, parsed.confidence, parsed.reason, usage, float(usage.pop("cost", 0)))
                if attempt == 1:  # one more try, told what was wrong
                    messages = [*messages, Message("assistant", reply.text), Message("user", "That wasn't valid. Answer again with the JSON only, using one of the label names.")]
            return Verdict(None, 0.0, "The model's answer couldn't be read.", usage, float(usage.pop("cost", 0)))

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
            cost = float(usage.pop("cost", 0)) / n
            # What the one request used is shared out, so the totals add up whatever the batch size.
            share = [{k: v // n + (v % n if i == 0 else 0) for k, v in usage.items()} for i in range(n)]
            return [Verdict(p.label, p.confidence, p.reason, share[i], cost) if (p := parsed.get(i + 1)) else None for i in range(n)]

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
                questions = {"label": {"type": "choice", "instructions": f"{cfg.prompt}\n\n{extra_guidance(cfg)}".strip(), "criteria": {lb.name: lb.description for lb in cfg.labels}}}
            return {**out, "provider": "openrouter", "model": model, "system": None, "user": state, "questions": questions}
        sel, _ = selection_of(s, "draft")
        user = render_batch(SAMPLE_ITEMS) if batch else wrap_item(SAMPLE_ITEMS[0])
        return {**out, "provider": (sel or {}).get("providerId"), "model": (sel or {}).get("modelId"), "system": system_prompt(cfg, who.name, batch), "user": user, "questions": None}

    async def _execute(self, who: Who, token: str, *, workflow: Workflow | None, cfg: WorkflowConfig, manual: str | None, mode: str) -> dict[str, Any]:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        started, began = datetime.now(UTC), time.monotonic()
        seen = await self._repo.seen(workflow.id) if workflow else set()
        fetched = await self._items(token, cfg, manual, seen, TEST_ITEMS if mode == "test" else None, who, mode == "run")
        items, matched, protected = fetched.items, fetched.matched, len(fetched.protected)
        decider = await self._decider(who, cfg, s, workflow.id if workflow else None)
        outcomes, batches = await self._decide_all(cfg, items, decider) if items else ([], 0)
        trash_note = await self._act(token, cfg, outcomes, mode)
        await self._route(who, token, cfg, s, workflow.id if workflow else None, outcomes, mode)

        counts = {"items": len(outcomes), "proposed": 0, "needsReview": 0, "failed": 0}
        if cfg.source.kind == "mail":  # what a mailbox agent adds: judged fine, moved to Trash, set aside by the rules
            counts.update(kept=0, applied=0, protected=protected)
        if any(lb.route != "none" for lb in cfg.labels):  # what an agent that reads for what matters adds
            counts.update(kept=counts.get("kept", 0), queued=0, events=0)
            if cfg.source.kind == "feed":
                counts.update(published=0, irrelevant=0)
        for o in outcomes:
            key = {"proposed": "proposed", "needs_review": "needsReview", "failed": "failed", "kept": "kept", "applied": "applied", "queued": "queued", "event_proposed": "events", "published": "published", "irrelevant": "irrelevant"}[o.status]
            counts[key] = counts.get(key, 0) + 1
        usage: dict[str, Any] = {"inputTokens": sum(o.usage.get("inputTokens", 0) for o in outcomes), "outputTokens": sum(o.usage.get("outputTokens", 0) for o in outcomes)}
        if any(o.cost for o in outcomes):
            usage["cost"] = round(sum(o.cost for o in outcomes), 6)
        cost = sum(o.cost for o in outcomes)
        stats = {
            "scanned": fetched.scanned, "protected": protected, "judged": len(outcomes), "requests": batches, "batchSize": cfg.batch_size,
            "decider": cfg.decider, "provider": decider.provider, "model": decider.model, "mailboxes": len(cfg.source.account_ids), "cost": round(cost, 6) if cost else None,
            "inRange": fetched.total, "remaining": fetched.remaining,
        }
        run_id = str(uuid.uuid4())

        if mode == "run" and workflow:
            await self._repo.record([
                LedgerEntry(str(uuid.uuid4()), workflow.id, who.user_id, o.item.id, o.item.title, o.item.text[:MAX_ITEM_CHARS], o.label, o.confidence, o.reason, o.status, run_id, workflow.version, data=o.data)
                for o in outcomes if o.status != "failed" and not (o.error and not o.data)
            ] + [
                # Kept by a rule: remembered, so the next run goes on to older mail instead of reading these again.
                LedgerEntry(str(uuid.uuid4()), workflow.id, who.user_id, item.id, item.title, item.text[:MAX_ITEM_CHARS], None, 0.0, f"Kept: {why}", "protected", run_id, workflow.version)
                for item, why in fetched.protected
            ])
            await self._repo.touch(workflow.id, started)

        summary = f"{counts['items']} item{'s' if counts['items'] != 1 else ''}: {counts['proposed']} proposed, {counts['needsReview']} to review" + (f", {counts['failed']} failed" if counts["failed"] else "")
        for key, words in (("applied", "moved to Trash"), ("kept", "kept"), ("protected", "protected"), ("queued", "for your attention"), ("events", "events to approve"), ("published", "on the news page"), ("irrelevant", "not relevant")):
            if counts.get(key):
                summary += f", {counts[key]} {words}"
        if fetched.remaining:
            summary += f". {fetched.remaining} more to go"
        if mode == "run" and workflow:
            failed_all = bool(outcomes) and counts["failed"] == len(outcomes)
            await self._repo.add_execution(Execution(
                run_id, workflow.id, who.user_id, started, int((time.monotonic() - began) * 1000), "error" if failed_all else "ok", summary, counts, stats,
                next((o.error for o in outcomes if o.error), None) if failed_all else None, workflow.version,
            ))
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
                usage=usage, version=workflow.version, agent_name=workflow.name,
            ))
        return {
            "runId": run_id if workflow else None, "mode": mode, "counts": counts, "matched": matched, "usage": usage, "stats": stats, "summary": summary,
            "durationMs": int((time.monotonic() - began) * 1000), "items": [o.to_json() for o in outcomes],
        }

    async def _analyst(self, who: Who, cfg: WorkflowConfig, s: UserSettings, workflow_id: str | None):
        """Reads one email that matters and says what it is about. Needs a chat model: the decision model only chooses between labels."""
        sel, _ = selection_of(s, workflow_id or "draft")
        if not sel or not sel.get("modelId"):
            raise ValidationFailed("Choose a provider and a model in this agent's Settings: it reads the emails that matter, and the decision model can't write a summary or find a date.")
        client = self._llm(sel["providerId"], s.keys[sel["providerId"]])
        model, thinking = sel["modelId"], sel.get("thinking", "off")
        today = datetime.now(UTC).date().isoformat()

        async def read(label: str, route: str, email: str) -> tuple[dict[str, Any] | None, dict[str, int], float]:
            system, user = analysis_prompt(cfg, who.name, label, route, today, email)
            messages = [Message("system", system), Message("user", user)]
            usage: dict[str, int] = {}
            for attempt in (1, 2):
                reply = await client.chat(model=model, messages=messages, tools=[], thinking=thinking)
                for k, v in (reply.usage or {}).items():
                    usage[k] = usage.get(k, 0) + v
                parsed = parse_analysis(reply.text)
                if parsed or attempt == 2:
                    return parsed, usage, float(usage.pop("cost", 0))
                messages = [*messages, Message("assistant", reply.text), Message("user", "That wasn't valid. Answer again with the JSON only.")]
            return None, usage, 0.0

        return read

    async def _publish(self, who: Who, cfg: WorkflowConfig, s: UserSettings, workflow_id: str | None, targets: list[Outcome]) -> None:
        """News that passed the first question is asked a second: how much does it matter. Only those, so the cost follows what is kept.
        Then each is published, with what the page needs to show it."""
        levels = {lv.name: lv.weight for lv in cfg.importance.levels} if cfg.importance else {}
        graded: dict[str, Outcome] = {}
        if levels:
            second = importance_config(cfg)
            results, _ = await self._decide_all(second, [o.item for o in targets], await self._decider(who, second, s, workflow_id))
            graded = {r.item.id: r for r in results}
        for o in targets:
            r = graded.get(o.item.id)
            level = r.label if r and r.label else None
            if r:
                for k, v in r.usage.items():
                    o.usage[k] = o.usage.get(k, 0) + v
                o.cost += r.cost
            m = o.item.meta
            o.data = {
                "kind": "news", "title": o.item.title, "source": m.get("source"), "link": m.get("link"), "publishedAt": o.item.when, "description": m.get("description") or None,
                "imageUrl": m.get("image") or None, "lang": m.get("lang"), "feed": m.get("feed"), "importance": level, "weight": levels.get(level or "", 2),
                "relevance": round(o.confidence, 3), "importanceConfidence": round(r.confidence, 3) if r else None,
            }
            o.status = "published"

    async def _route(self, who: Who, token: str, cfg: WorkflowConfig, s: UserSettings, workflow_id: str | None, outcomes: list[Outcome], mode: str = "run") -> None:
        """What a label means when it has a route: the email is read in full, summarised and put in the Attention queue; if it names a
        date, a calendar event is proposed for the owner to approve. A label with no route is simply kept."""
        routes = {lb.name: lb.route for lb in cfg.labels if lb.route != "none"}
        if not routes:
            return
        for o in outcomes:
            if o.label and o.label not in routes and o.status in ("proposed", "needs_review"):
                o.status = "irrelevant" if cfg.source.kind == "feed" else "kept"
        publishing = [o for o in outcomes if routes.get(o.label or "") == "publish" and o.status in ("proposed", "needs_review")]
        if publishing:
            await self._publish(who, cfg, s, workflow_id, publishing)
        targets = [o for o in outcomes if routes.get(o.label or "") in ("attention", "event", "alert") and o.status in ("proposed", "needs_review")]
        if not targets:
            return
        read = await self._analyst(who, cfg, s, workflow_id)
        data = Memo(self._data(token))
        folder = cfg.source.folder
        if cfg.source.kind == "agent":
            up = await self._repo.get(who.user_id, cfg.source.upstream_id or "")
            folder = self._config(up.config).source.folder if up else "INBOX"
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(o: Outcome) -> None:
            async with gate:
                email = o.item.text
                full: dict[str, Any] = {}
                try:  # the list only has the start of an email: a date or a deadline may be further down
                    full = await data.mail_message(account_of(o.item.id), folder, uid_of(o.item.id))
                    email = f"From: {(full.get('sender') or {}).get('name', '')} <{(full.get('sender') or {}).get('address', '')}>\nSubject: {full.get('subject', '')}\n\n{str(full.get('text') or '')[:4000]}"
                except (AiError, ValueError):
                    pass
                try:
                    parsed, usage, cost = await read(o.label or "", routes[o.label or ""], email)
                except AiError as exc:
                    o.error = str(exc)
                    return
                for k, v in usage.items():
                    o.usage[k] = o.usage.get(k, 0) + v
                o.cost += cost
                if parsed is None:
                    o.data = {"summary": o.item.title, "priority": "normal", "action": None, "due": None, "event": None, "note": "The agent couldn't summarise this one."}
                    o.status = "queued"
                    return
                o.data = parsed
                o.status = "event_proposed" if routes[o.label or ""] == "event" and parsed.get("event") else "queued"
                route = routes[o.label or ""]
                # A label set to ping Telegram is always an alert. An agent set to send updates also sends what it queues: as an alert when the model
                # finds it high priority, otherwise as a quiet update.
                tell = route == "alert" or (cfg.notify.telegram and route in ("attention", "event"))
                if tell and mode == "run" and self._alerts is not None and cfg.source.kind in ("mail", "agent"):
                    sender = full.get("sender") or {}
                    urgent = route == "alert" or parsed.get("priority") == "high"
                    try:  # telling the phone must never undo the judging
                        await self._alerts.raise_alert(
                            who, token, workflow_id=workflow_id, account_id=account_of(o.item.id), folder=folder, uid=uid_of(o.item.id), item_id=o.item.id,
                            subject=str(full.get("subject") or o.item.title), sender_name=str(sender.get("name") or ""), sender_address=str(sender.get("address") or o.item.meta.get("from") or ""),
                            message_id=str(full.get("messageId") or ""), analysis={**parsed, "priority": "high" if urgent else "normal"}, call=cfg.notify.call and urgent,
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("could not raise the alert for %s", o.item.id)

        await asyncio.gather(*(one(o) for o in targets))

    async def _act(self, token: str, cfg: WorkflowConfig, outcomes: list[Outcome], mode: str) -> str:
        """What the labels mean for a mailbox. Confident "trash" items are moved to Trash only in a real run of an agent set to act on
        its own; in a test run, or when approval is required, they are only marked. Everything judged fine is kept. Returns a note when
        the move failed (the items then wait in Review as proposals)."""
        act = cfg.action
        if cfg.source.kind != "mail" or not act.trash_label:
            return ""
        for o in outcomes:
            if o.label is None or o.status == "failed":
                continue
            if o.label != act.trash_label:
                o.status = "kept"  # judged fine: kept however sure the model was, so only mail to be moved ever needs a look
            elif o.status == "proposed":
                o.action = "trash"
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

    async def run(self, who: Who, token: str, workflow_id: str, manual: str | None, *, via_sweep: bool = False) -> dict[str, Any]:
        """Decides the new items and keeps each decision in the ledger for review."""
        w = await self._mine(who, workflow_id)
        # Two runs of one agent would judge the same emails twice, move them twice, and write the same ledger rows at once. So one at a time
        # (check and add happen with nothing awaited between them, so two requests can't both get in).
        if not via_sweep and self._sweeping(workflow_id):
            raise Conflict("This agent is running through the whole inbox. Stop that first, or wait for it to finish.")
        if workflow_id in self._running:
            raise Conflict("This agent is already running. Wait for it to finish, then run it again.")
        self._running.add(workflow_id)
        try:
            return await self._execute(who, token, workflow=w, cfg=self._config(w.config), manual=manual, mode="run")
        finally:
            self._running.discard(workflow_id)

    # --- executions: every real run, and what it decided -------------------------------------------------------

    async def executions(self, who: Who, workflow_id: str) -> dict[str, Any]:
        await self._mine(who, workflow_id)
        now = await self._repo.run_status_counts(workflow_id)
        return {"items": [self._execution_json(e, now.get(e.id, {})) for e in await self._repo.executions(who.user_id, workflow_id)]}

    async def execution(self, who: Who, workflow_id: str, run_id: str) -> dict[str, Any]:
        await self._mine(who, workflow_id)
        e = await self._repo.execution(who.user_id, workflow_id, run_id)
        if e is None:
            raise NotFound("No such run.")
        rows = await self._repo.run_items(who.user_id, workflow_id, run_id)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.status] = counts.get(r.status, 0) + 1
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None  # noqa: E731
        items = [{"id": r.item_id, "title": r.title, "excerpt": r.excerpt, "label": r.label, "confidence": round(r.confidence, 3), "reason": r.reason, "status": r.status, "at": iso(r.created_at), "decidedAt": iso(r.decided_at)} for r in rows]
        return {"execution": self._execution_json(e, counts), "items": items}

    @staticmethod
    def _execution_json(e: Execution, now: dict[str, int]) -> dict[str, Any]:
        iso = e.started_at.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return {"id": e.id, "startedAt": iso, "durationMs": e.duration_ms, "status": e.status, "summary": e.summary, "counts": e.counts, "stats": e.stats, "error": e.error, "version": e.version, "now": now}

    # --- learning from corrections ----------------------------------------------------------------------------

    async def analyse_feedback(self, who: Who, workflow_id: str, item_id: str, reason: str) -> dict[str, Any]:
        """"This wasn't spam, because…": reads the whole setup, the email and the reason, and suggests one general point to add to the
        prompt. Nothing is changed here: the person sees the suggestion first. Without a model to ask, the reason itself is the suggestion."""
        w = await self._mine(who, workflow_id)
        cfg = self._config(w.config)
        reason = reason.strip()
        if not reason:
            raise ValidationFailed("Say why it isn't spam: the reason is what the agent learns from.")
        entry = await self._repo.entry(who.user_id, workflow_id, item_id)
        if entry is None:
            raise NotFound("No such email in this agent's records.")
        sender = parse_sender_domain(entry.excerpt)
        plain = {"point": reason[:300], "duplicate": False, "protectDomain": None, "note": "", "source": "reason", "senderDomain": sender}
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        sel, _ = selection_of(s, workflow_id)
        if not sel or not sel.get("modelId"):
            return {**plain, "note": "No chat model is chosen for this agent, so your own words are suggested. Choose one in Settings to have it written as a general rule."}
        system, user = feedback_prompt(cfg, who.name, entry.excerpt, reason, entry.label)
        try:
            reply = await self._llm(sel["providerId"], s.keys[sel["providerId"]]).chat(model=sel["modelId"], messages=[Message("system", system), Message("user", user)], tools=[], thinking=sel.get("thinking", "off"))
        except AiError as exc:
            return {**plain, "note": f"The model couldn't be asked ({exc}), so your own words are suggested."}
        parsed = parse_feedback(reply.text)
        if parsed is None:
            return {**plain, "note": "The model's answer couldn't be read, so your own words are suggested."}
        return {**parsed, "source": "model", "senderDomain": sender}

    async def add_refinement(self, who: Who, workflow_id: str, point: str | None, protect_domain: str | None) -> dict[str, Any]:
        """Adds a point to what the agent has learned, and/or a domain it always keeps. What the person wrote is never touched."""
        w = await self._mine(who, workflow_id)
        cfg = self._config(w.config)
        text = (point or "").strip()[:400]
        if text and text.lower() not in [r.lower() for r in cfg.refinements]:
            if len(cfg.refinements) >= MAX_REFINEMENTS:
                raise ValidationFailed(f"It has learned {MAX_REFINEMENTS} points already. Remove one in Settings first.")
            cfg.refinements = [*cfg.refinements, text]
        domain = (protect_domain or "").strip().lower().lstrip("@")
        if domain and domain not in cfg.rules.protected_domains:
            cfg.rules = cfg.rules.model_copy(update={"protected_domains": [*cfg.rules.protected_domains, domain]})
        new = self._config(cfg.model_dump(by_alias=True, mode="json")).model_dump(by_alias=True, mode="json")
        if new != w.config:
            w.version += 1
            w.config = new
            await self._repo.save(w)
        return workflow_json(w, await self._repo.counts(w.id))

    async def remove_refinement(self, who: Who, workflow_id: str, index: int) -> dict[str, Any]:
        w = await self._mine(who, workflow_id)
        cfg = self._config(w.config)
        if not 0 <= index < len(cfg.refinements):
            raise NotFound("No such point.")
        cfg.refinements = [r for i, r in enumerate(cfg.refinements) if i != index]
        w.version += 1
        w.config = cfg.model_dump(by_alias=True, mode="json")
        await self._repo.save(w)
        return workflow_json(w, await self._repo.counts(w.id))

    # --- review ------------------------------------------------------------------------------------------------

    async def review(self, who: Who, workflow_id: str) -> dict[str, Any]:
        await self._mine(who, workflow_id)
        rows = await self._repo.review(who.user_id, workflow_id, ["proposed", "needs_review"])
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {"items": [{"id": r.item_id, "title": r.title, "excerpt": r.excerpt, "label": r.label, "confidence": round(r.confidence, 3), "reason": r.reason, "status": r.status, "version": r.version, "at": iso(r.created_at)} for r in rows]}

    async def decide_review(self, who: Who, token: str, workflow_id: str, item_ids: list[str], action: str, label: str | None, note: str | None = None) -> dict[str, int]:
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
        n = await self._repo.decide(who.user_id, workflow_id, item_ids, "approved" if action == "approve" else "dismissed", label if action == "approve" else None, f"You said it isn't spam: {note.strip()}" if note and note.strip() else None)
        return {"changed": n, "moved": moved} if cfg.source.kind == "mail" else {"changed": n}
