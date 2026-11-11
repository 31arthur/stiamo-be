"""Running an agent as a graph of nodes (see domain/graph.py), and keeping what each node did.

The default graph (trigger → scope → prompt → agent → output) does exactly what the single loop always did, so an agent that was never edited
behaves as before; `AgentService.chat` picks this engine or the classic one by the `engine` setting. Every visit to a node is kept in the run's
`trace`: how long it took, what went in and what came out, so an execution can be read node by node.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.runner import AgentRun, Step, run_agent
from app.domain.graph import MAX_LOOP, MAX_VISITS, Graph, default_graph, holds, render, validate
from app.domain.llm import AiError, Message
from app.domain.prompt import build_package

log = logging.getLogger(__name__)

#: How much of a node's input and output is kept in the trace.
CLIP = 600


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= CLIP else text[: CLIP - 1] + "…"


def total_usage(runs: list[AgentRun]) -> dict[str, Any]:
    """What the model calls of a run used, added up: tokens, and the cost when the provider reported one."""
    out: dict[str, Any] = {"inputTokens": 0, "outputTokens": 0}
    for r in runs:
        for k, v in (r.usage or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = out.get(k, 0) + v
    return out


def graph_of(settings_graphs: dict[str, Any], agent_id: str) -> Graph:
    """The agent's own workflow if it has one that can run, else the default. A stored graph that no longer validates never breaks the agent."""
    raw = (settings_graphs or {}).get(agent_id)
    if raw:
        try:
            g = Graph.from_json(raw)
            if not validate(g):
                return g
            log.warning("the stored workflow of %s can't run; using the default", agent_id)
        except ValueError:
            log.warning("the stored workflow of %s can't be read; using the default", agent_id)
    return default_graph()


class GraphRunner:
    def __init__(self, svc: Any) -> None:
        self._svc = svc

    async def run(self, who: Any, token: str, agent: Any, message: str, past: list[Message], preface: str, channel: str, *, trigger: str, channel_id: str | None, workspace: str | None, plan_for: str | None, prepared: Any) -> AgentRun:
        from app.application.services import MAX_INPUT_CHARS  # (here: services imports this module)
        from app.domain.agents import WORKSPACES
        from app.domain.llm import AiError as _AiError  # noqa: F401

        svc = self._svc
        prep = prepared
        graph = graph_of(prep.settings.graphs, agent.id)
        state: dict[str, Any] = {"input": message, "asked": f"{preface}\n\nThe person's latest message:\n{message}" if preface else message, "answer": "", "found": 0, "steps": 0, "workspace": prep.scope, "vars": {}, "nodes": {}, "loops": {}}
        record = None
        began = time.monotonic()
        runs: list[AgentRun] = []
        trace: list[dict[str, Any]] = []
        pkg = None
        used_history = False
        final: str | None = None
        cur = next(n for n in graph.nodes if n.type == "trigger")
        visits = 0
        failure: Exception | None = None

        def new_record(sel: dict[str, Any], package: Any) -> Any:
            from app.application.ports import RunRecord

            return RunRecord(
                id=str(uuid.uuid4()), user_id=who.user_id, agent_id=agent.id, trigger=trigger, status="ok", started_at=datetime.now(UTC),
                provider=sel["providerId"], model=sel["modelId"], thinking=sel.get("thinking", "off"), input=message[:MAX_INPUT_CHARS],
                user_message=package.user[: MAX_INPUT_CHARS * 2], system_prompt=package.system, tools=[t.name for t in package.tools], channel=channel_id, agent_name=agent.name, workspace=prep.scope,
            )

        try:
            while True:
                visits += 1
                if visits > MAX_VISITS:
                    raise ValidationFailed("This workflow went round too many times and was stopped.")
                t0 = time.monotonic()
                entry: dict[str, Any] = {"node": cur.id, "type": cur.type, "label": cur.label or cur.type, "state": "ok", "in": "", "out": ""}
                label = "next"
                cfg = cur.config
                try:
                    if cur.type == "scope" and cfg.get("mode") == "fixed" and cfg.get("workspace") in WORKSPACES:
                        ws = cfg["workspace"]
                        prep = await svc._prepare(who, token, agent, channel, channel_id=channel_id, workspace=ws, plan_for=None)
                        state["workspace"] = prep.scope
                        pkg = None
                        entry["out"] = f"Limited to {ws}"
                    elif cur.type == "scope":
                        entry["out"] = f"Limited to {prep.scope}" if prep.scope else "Every workspace"
                    elif cur.type == "prompt":
                        config = prep.config
                        override = {k: v for k, v in (("system", cfg.get("system")), ("input_template", cfg.get("inputTemplate"))) if v}
                        if override:
                            config = config.model_copy(update=override)
                        pkg = build_package(config, [t.spec for t in prep.tools], prep.context, state["asked"])
                        state["_config"] = config
                        entry["in"], entry["out"] = _clip(state["asked"]), _clip(pkg.user)
                    elif cur.type == "agent":
                        sel = cfg.get("model") or svc._selection(prep.settings, agent.id)[0]
                        if not sel or not sel.get("modelId"):
                            raise ValidationFailed(f"Choose a provider and a model for the {agent.name} first (AI agents → {agent.name}).")
                        question = state["asked"] if cfg.get("input") in (None, "", "{{input}}") else render(str(cfg["input"]), state)
                        if pkg is None or question != state["asked"]:
                            pkg = build_package(state.get("_config") or prep.config, [t.spec for t in prep.tools], prep.context, question)
                        allowed = cfg.get("tools")
                        tools = svc._tools_for(pkg, prep.tools)
                        if isinstance(allowed, list):
                            tools = [t for t in tools if t.spec.name in allowed]
                        if record is None:
                            record = new_record(sel, pkg)
                        client = svc._llm(sel["providerId"], prep.settings.keys[sel["providerId"]])
                        hist = past if (not used_history and cfg.get("history", True)) else []
                        used_history = True
                        run = await run_agent(client=client, model=sel["modelId"], thinking=sel.get("thinking", "off"), system=pkg.system, user=pkg.user, tools=tools, history=hist, max_steps=int(cfg.get("maxSteps") or svc._max_steps))
                        runs.append(run)
                        state["answer"] = run.answer
                        state["steps"] = sum(len(r.steps) for r in runs)
                        state["found"] = sum(len(r.found) for r in runs)
                        state["nodes"][cur.id] = run.answer
                        entry["in"], entry["out"] = _clip(question), _clip(run.answer)
                        entry["calls"] = [{"tool": s.call.name, "ms": s.ms, "found": len(s.found), **({"error": s.error} if s.error else {})} for s in run.steps]
                        entry["usage"] = run.usage
                        pkg = None  # a later agent node builds its own
                    elif cur.type == "branch":
                        label = next((str(c["label"]) for c in cfg.get("cases") or [] if holds(c, state)), "else")
                        entry["out"] = f"→ {label}"
                    elif cur.type == "set":
                        state["vars"][str(cfg["name"])] = render(str(cfg.get("value") or ""), state)
                        entry["out"] = f"{cfg['name']} = {_clip(state['vars'][str(cfg['name'])])}"
                    elif cur.type == "loop":
                        n = state["loops"].get(cur.id, 0) + 1
                        state["loops"][cur.id] = n
                        stop = n > min(int(cfg.get("max") or 3), MAX_LOOP) or (bool(cfg.get("until")) and holds(cfg["until"], state))
                        label = "done" if stop else "again"
                        entry["out"] = f"round {n}: {label}"
                    elif cur.type == "output":
                        final = render(str(cfg["template"]), state) if cfg.get("template") else state["answer"]
                        entry["out"] = _clip(final)
                except Exception as exc:
                    entry["state"], entry["out"] = "error", _clip(getattr(exc, "message", None) or exc)
                    raise
                finally:
                    entry["ms"] = int((time.monotonic() - t0) * 1000)
                    trace.append(entry)
                if cur.type == "output":
                    break
                edges = graph.out(cur.id)
                nxt = next((e for e in edges if e.label == label), None) or next((e for e in edges if e.label in ("next", "else")), None)
                if nxt is None:
                    raise ValidationFailed(f"The workflow stops at “{cur.label or cur.id}”: it has no edge to follow.")
                cur = graph.node(nxt.target)
        except AiError as exc:
            failure = exc
            if record is not None:
                record.status, record.error = "error", str(exc)
            raise ValidationFailed(str(exc)) from exc
        except ValidationFailed as exc:
            failure = exc
            if record is not None:
                record.status, record.error = "error", str(getattr(exc, "message", None) or exc)
            raise
        except Exception as exc:
            failure = exc
            if record is not None:
                record.status, record.error = "error", "Something went wrong inside the agent."
            log.exception("agent %s failed", agent.id)
            raise
        finally:
            if record is not None:
                record.duration_ms = int((time.monotonic() - began) * 1000)
                record.trace = trace
                if failure is None and runs:
                    record.steps = [s.to_log() for r in runs for s in r.steps]
                    record.output = final if final is not None else state["answer"]
                    record.found_count = sum(len(r.found) for r in runs)
                    record.usage = total_usage(runs)
                await svc._keep(record)

        last = runs[-1]
        steps: list[Step] = [s for r in runs for s in r.steps]
        found = [f for r in runs for f in r.found]
        return AgentRun(final if final is not None else last.answer, steps, found, last.messages, total_usage(runs))
