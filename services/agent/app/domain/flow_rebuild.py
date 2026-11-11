"""An older mail-sorting agent, said as a workflow on the canvas: the same prompt, labels, thresholds, safe senders and what happens to each label,
as nodes you can see and change. Pure: no network, no framework. The agent it is made from is left as it is, so the two can be run side by side."""

from __future__ import annotations

from typing import Any

from stiamo_common.errors import ValidationFailed

from app.domain.flow_templates import link, n
from app.domain.workflows import WorkflowConfig, extra_guidance


def flow_from_config(cfg: WorkflowConfig, model: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    """The workflow for a mail-sorting agent's setup, and what could not be carried over. `model` is the chat model the agent was set to, which
    the sorting node is given when the agent judged with its own model."""
    if cfg.source.kind != "mail":
        raise ValidationFailed("Only agents that sort a mailbox can be rebuilt on the canvas so far. News, record and follow-on agents stay as they are.")
    notes: list[str] = []
    trash = cfg.action.trash_label
    labels = []
    for lb in cfg.labels:
        # Mail the agent set aside as fine is kept however unsure it was, so only the label that moves mail needs to be sure.
        threshold = lb.threshold if lb.threshold is not None else cfg.default_threshold
        labels.append({"name": lb.name, "description": lb.description, "threshold": 0.0 if trash and lb.name != trash else threshold})

    guidance = extra_guidance(cfg)
    nodes: list[dict[str, Any]] = []
    conns: list[tuple[str, int, str]] = []
    x = 0
    nodes.append(n("byhand", "trigger.manual", "Run it by hand", x, 160))
    first = ["byhand"]
    if cfg.listen or cfg.schedule_minutes:
        nodes.append(n("morning", "trigger.schedule", "On a schedule", x, 0, time="07:30", timezone="Asia/Kolkata", days=[0, 1, 2, 3, 4, 5, 6]))
        first.append("morning")
        notes.append("The agent ran by itself (when mail arrived or every so many minutes). Here it runs once a day at 07:30: set the time on the schedule node.")
    if cfg.filters.after or cfg.filters.before:
        notes.append("A date range was set. Here the mail read is the last days, set on the mail node.")
    x += 260
    mail_params: dict[str, Any] = {"days": cfg.filters.since_days or 30, "limit": min(cfg.filters.limit, 100), "folder": cfg.source.folder}
    if cfg.source.account_ids:
        mail_params["accounts"] = list(cfg.source.account_ids)
    nodes.append(n("mail", "data.mail", "Mail", x, 80, **mail_params))
    conns += [(f, 0, "mail") for f in first]
    prev = "mail"
    x += 240
    if cfg.filters.new_only:
        nodes.append(n("new", "core.only_new", "Only what is new", x, 80))
        conns.append((prev, 0, "new"))
        prev = "new"
        x += 240
    nodes.append(n("safe", "core.safe_senders", "Keep people I trust", x, 80, correspondents=cfg.rules.protect_correspondents, domains=list(cfg.rules.protected_domains), addresses=list(cfg.rules.protected_addresses), phrases=list(cfg.rules.protected_phrases)))
    conns.append((prev, 0, "safe"))
    x += 240
    sort_params: dict[str, Any] = {"prompt": cfg.prompt + (f"\n\n{guidance}" if guidance else ""), "labels": labels, "threshold": cfg.default_threshold, "decider": cfg.decider, "batchSize": cfg.batch_size}
    if cfg.decider == "jev" and cfg.decision_model:
        sort_params["decisionModel"] = cfg.decision_model
    if cfg.decider == "model" and model and model.get("modelId"):
        sort_params["model"] = {"providerId": model["providerId"], "modelId": model["modelId"], "thinking": model.get("thinking", "off")}
    nodes.append(n("sort", "ai.classify", "Sort the mail", x, 80, **sort_params))
    conns.append(("safe", 0, "sort"))
    x += 280

    y = 0
    lane_of = {lb.name: i for i, lb in enumerate(cfg.labels)}
    for lb in cfg.labels:
        i = lane_of[lb.name]
        if lb.name == trash:
            nodes.append(n(f"trash_{i}", "msg.trash", f"Move “{lb.name}” to Trash", x, y, folder=cfg.source.folder, auto=cfg.action.auto, tell=True))
            conns.append(("sort", i, f"trash_{i}"))
            y += 140
        elif lb.route in ("attention", "event", "alert"):
            nodes.append(n(f"read_{i}", "ai.read", f"Read “{lb.name}”", x, y, prompt=f"Mail that needs my attention: {cfg.prompt}", route=lb.route))
            conns.append(("sort", i, f"read_{i}"))
            last = f"read_{i}"
            if lb.route == "alert":
                nodes.append(n(f"alert_{i}", "msg.alert", f"Alert me about “{lb.name}”", x + 240, y, call=cfg.notify.call))
                conns.append((last, 0, f"alert_{i}"))
                last = f"alert_{i}"
            nodes.append(n(f"attention_{i}", "msg.attention", "Add to Attention", x + (480 if lb.route == "alert" else 240), y))
            conns.append((last, 0, f"attention_{i}"))
            y += 140
        elif lb.route == "publish":
            notes.append(f"“{lb.name}” published to the news page: that is for news feeds, so it is kept here.")
    if any(lb.route != "none" for lb in cfg.labels) or trash:
        nodes.append(n("read_unsure", "ai.read", "Read what it isn't sure about", x, y, prompt=f"Mail I should look at myself, because it was hard to judge: {cfg.prompt}", route="attention"))
        nodes.append(n("attention_unsure", "msg.attention", "Add to Attention", x + 240, y))
        conns += [("sort", len(cfg.labels), "read_unsure"), ("read_unsure", 0, "attention_unsure")]
    if cfg.notify.telegram and not any(lb.route == "alert" for lb in cfg.labels):
        notes.append("The agent sent what it queued to Telegram. Here add an Alert me node after Read and summarise where you want one.")
    notes.append("Mail it was unsure about used to wait in Review. Here it comes to Attention, read and summarised, for you to look at.")
    return {"nodes": nodes, "connections": link(*conns)}, notes
