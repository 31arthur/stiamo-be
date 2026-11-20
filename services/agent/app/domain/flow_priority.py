"""The priority mail triage, as a workflow on the canvas: stages instead of one big judgement. Pure: no network.

Each stage does one job and can be swapped, retuned or deleted on the canvas like any other node:

  1. **When mail arrives** (IMAP IDLE): the workflow runs the moment mail lands in the workspace's mailboxes. There is no timer.
  2. **Only new**: a mail is judged once.
  3. **Sorter** (decision model): first the rules in code drop codes, logins, statements, orders and bulk mail without asking any model; the system's facts
     (verified reply, priority sender, active case, impersonation...) are written above the rest; then the model chooses now, digest or drop.
  4. **Reader** (chat model): opens the whole email and says what it is and what to do.
  5. **Critic** (decision model): for what the sorter called now, a second look at the reader's summary: is the phone worth it?
  6. **Trash**: what was dropped is moved to Trash (never deleted).
  7. **Alert / Attention**: the phone is told once per mail; digest and held mail wait in Attention, which the evening plan shows.

All prompts are the long briefs of domain/mailprompts.py. For "swap a stage", replace the node: a *Run another agent* node can stand in for any reader.
"""

from __future__ import annotations

from typing import Any

from app.domain.mailprompts import PROMPTS, critic_prompt, reader_prompt


def priority_flow(kind: str) -> dict[str, Any]:
    """The triage for "sb" or "personal"."""
    from app.domain.flow_templates import link, n
    from app.domain.mailwatch import _rules, labels_for

    wants = (
        "BWI, MFA, the United Nations and its bodies, government agencies and courts, RTI mail, clients and the other side in an active case, funders, and replies to the owner's own mail"
        if kind == "sb"
        else "a reply to something the owner sent, a real person or company approaching the owner to do business, a lead from a contact form, and mail that signals real harm or a real deadline"
    )
    reader = reader_prompt(kind)
    nodes = [
        n("live", "trigger.mail", "When mail arrives", 0, 60),
        n("byhand", "trigger.manual", "Run it by hand", 0, 220),
        n("mail", "data.mail", "New mail", 260, 140, days=1, limit=50),
        n("new", "core.only_new", "Only what is new", 500, 140),
        n("sort", "ai.classify", "Sorter · rules, facts, then now / digest / drop", 760, 140, prompt=PROMPTS[kind], labels=[{"name": x["name"], "description": x["description"], "threshold": 0.6 if x["name"] != "drop" else 0.0} for x in labels_for(kind)],
          threshold=0.6, decider="jev", batchSize=5, mailRules=_rules(kind)),
        n("read_now", "ai.read", "Reader · what needs me", 1060, 0, prompt=reader, route="alert"),
        n("shape", "core.set", "Put the summary where the critic reads", 1320, 0, fields=[{"name": "text", "value": "{{ $json.facts }}\nFrom: {{ $json.senderName }} <{{ $json.senderAddress }}>\nSubject: {{ $json.subject }}\nSummary: {{ $json.summary }}\nAction: {{ $json.action }}\nDeadline: {{ $json.due }}\nPriority: {{ $json.priority }}"}]),
        n("critic", "ai.classify", "Critic · worth the phone?", 1580, 0, prompt=critic_prompt(kind).replace("{what}", wants), labels=[
            {"name": "notify", "description": "A real ask, date or problem from a sender that matters, and the owner would be badly placed to learn of it late. Buzz the phone.", "threshold": 0.6},
            {"name": "hold", "description": "Real but can wait: acknowledgement, circular, FYI, automatic reply, nothing for the owner to do. Keep it in Attention.", "threshold": 0.0},
        ], threshold=0.6, decider="jev", batchSize=5),
        n("alert", "msg.alert", "Alert me", 1860, -60, call=False),
        n("attention", "msg.attention", "Add to Attention", 2120, -60),
        n("attention_hold", "msg.attention", "Keep in Attention", 1860, 120),
        n("read_digest", "ai.read", "Reader · for the evening", 1060, 260, prompt=reader, route="attention"),
        n("attention_digest", "msg.attention", "Add to Attention", 1320, 260),
        n("trash_drop", "msg.trash", "Move dropped mail to Trash", 1060, 620, folder="INBOX", auto=True, tell=False),
        n("read_unsure", "ai.read", "Reader · unsure", 1060, 440, prompt=reader, route="attention"),
        n("attention_unsure", "msg.attention", "Add to Attention", 1320, 440),
    ]
    c = [
        ("live", 0, "mail"), ("byhand", 0, "mail"), ("mail", 0, "new"), ("new", 0, "sort"),
        ("sort", 0, "read_now"), ("sort", 1, "read_digest"), ("sort", 2, "trash_drop"), ("sort", 3, "read_unsure"),  # drop goes to Trash (moved, never deleted)
        ("read_now", 0, "shape"), ("shape", 0, "critic"), ("critic", 0, "alert"), ("critic", 1, "attention_hold"), ("critic", 2, "alert"), ("alert", 0, "attention"),
        ("read_digest", 0, "attention_digest"), ("read_unsure", 0, "attention_unsure"),
    ]
    return {"nodes": nodes, "connections": link(*c)}
