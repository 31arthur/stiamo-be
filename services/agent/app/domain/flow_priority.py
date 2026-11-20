"""The priority mail triage, as a workflow on the canvas: several small stages instead of one big judgement. Pure: no network.

Each stage does one job and can be swapped, retuned or deleted on the canvas like any other node:

  1. **Cheap rules, no model**: mail from a known priority sender goes straight to a reader; one-time codes, logins, statements and order mail
     are dropped before any model sees them; people the owner has written to are taken as replies.
  2. **Sorter** (AI): who is writing, in a few labels.
  3. **Specialist readers** (AI, one per kind of mail): each opens the whole email with instructions for its own kind and says what it is,
     how urgent, what to do. They are separate nodes, so each can have its own prompt and model, or be replaced by a *Run another agent* node.
  4. **Critic** (AI): reads the reader's summary and decides whether this really deserves a phone alert. It is what keeps the phone quiet.
  5. **Alert / Attention**: the phone is told once per email; what is held back still waits in Attention.
"""

from __future__ import annotations

from typing import Any

from app.domain.mailrules import DISCARD_ORDER_COPY, DISCARD_SUBJECT, DISCARD_TEXT, domain_patterns
from app.domain.prompting import brief

NOISE_LIST = (
    "One-time passwords, verification, login and security codes, sign-in alerts, password resets, account and balance statements, order copies and confirmations, shipping, receipts, "
    "newsletters, promotions, webinars, social and app notifications."
)
FACTS = "A line starting \"[Facts from the system: ...]\" is established by code. Believe it."

# --- prompts: Persona, Tasks, Context, Output, Guardrails (see domain/prompting.py) -------------------------------------------------------

SB_SORTER = brief(
    persona="You are the first reader of the inbox of S&B Consultants, a legal and advocacy practice for trade unions and migrant workers. You are blunt. You say who is writing and why. You do not summarise.",
    tasks=["Find the sender or signatory.", "Pick exactly one label.", "Give a low confidence when two labels fit."],
    context=[
        "Rules have already removed codes, logins, statements, orders and bulk mail. What reaches you is mail a rule could not judge.",
        "priority_org: BWI (Building and Wood Workers' International), MFA (Migrants Forum in Asia), the United Nations or any UN body, a government agency, court, tribunal or commission, or an RTI public information officer or appellate authority. Judge by sender or signatory, not by words in the subject.",
        "reply: a person answering something the owner wrote: \"Re:\", the owner's words quoted, \"in response to your\", \"as discussed\", or the facts say the owner wrote to this sender before.",
        "If both fit, choose priority_org.",
        "other: clients and general correspondence that is neither. noise: any automated or bulk mail that slipped through.",
        FACTS,
    ],
    output=["One label: priority_org, reply, other or noise.", "Unsure: low confidence. A person reviews it."],
)

PERSONAL_SORTER = brief(
    persona="You are the first reader of the owner's personal inbox. You are blunt. Almost nothing here matters. You say who is writing and why. You do not summarise.",
    tasks=["Find the sender and what they want.", "Pick exactly one label.", "Give a low confidence when two labels fit."],
    context=[
        "Rules have already removed codes, logins, statements, orders and bulk mail. What reaches you is mail a rule could not judge.",
        "reply: a real person answering something the owner wrote: \"Re:\", the owner's words quoted, \"thank you for getting back\", \"as per your email\", or the facts say the owner wrote to this sender before.",
        "business: a real person or company writing to the owner personally to do business: an enquiry, a request for a quote or meeting, a proposal, a collaboration, an offer of work. A mass-mailed pitch, template, recruiter's bulk mail or \"grow your business\" mail is not business.",
        "other: family, friends, bills, subscriptions, services. noise: any automated or bulk mail, any bank mail.",
        FACTS,
    ],
    output=["One label: reply, business, other or noise.", "Unsure: low confidence. A person reviews it."],
)

READ_ORG = brief(
    persona="You are a paralegal reading mail from institutions for S&B Consultants: BWI, MFA, the United Nations and its agencies, governments, courts and RTI offices. You are exact and short.",
    tasks=["Say who is writing (person, role, organisation) and what they want, in at most two sentences.", "Find every deadline, hearing, meeting, consultation, comment period, reference or case number and amount.", "State the one next action and its date, or \"No action needed\" and why.", "Set the priority."],
    context=[
        "Priority high: a deadline within 7 days, a legal or procedural consequence of silence, or a direct request to the owner. Otherwise normal.",
        "Quote dates exactly as written. Work out relative dates from today.",
        "Someone is preparing the owner for a morning meeting: facts only.",
        FACTS,
    ],
    output=["JSON as the system asks: summary, priority, action, due, event, replies.", "A fact that is not in the email is null. Never invent a date, name or number."],
)

READ_REPLY = brief(
    persona="You are an assistant reading replies to the owner's own emails. You are exact and short.",
    tasks=["Say who replied and what they answered, in at most two sentences.", "Say whether the matter is settled or the owner must act, and what to do.", "Find any date, number or document they ask for.", "Set the priority."],
    context=[
        "Priority high only when the sender waits on the owner and a date is close. An automatic reply (out of office, delivery notice, \"we received your message\") is low: say so plainly.",
        FACTS,
    ],
    output=["JSON as the system asks: summary, priority, action, due, event, replies.", "A fact that is not in the email is null. Never invent anything."],
)

READ_BUSINESS = brief(
    persona="You are an assistant reading business approaches to the owner personally. You are exact, short and sceptical.",
    tasks=["Say who is writing, for which company, and what they want, in at most two sentences.", "Say what they offer (budget, timeline) if stated.", "Say whether it is a personal approach or a mass-mailed template, and why.", "Suggest the one next step.", "Set the priority."],
    context=["Priority high only for a specific, personal request with a deadline or money attached. Otherwise normal; a template is low.", FACTS],
    output=["JSON as the system asks: summary, priority, action, due, event, replies.", "A fact that is not in the email is null. Never invent anything."],
)

READ_UNSURE = brief(
    persona="You are an assistant reading mail another reader could not place. You are short and honest.",
    tasks=["Say who wrote it and what it is about, in one or two sentences.", "Say what, if anything, the owner should do."],
    context=["If it needs nothing, say so and set the priority to low.", FACTS],
    output=["JSON as the system asks: summary, priority, action, due, event, replies.", "A fact that is not in the email is null."],
)

CRITIC = brief(
    persona="You are the last check before the owner's phone buzzes. You are strict. A quiet phone is the goal.",
    tasks=["Read one specialist's summary of one email.", "Choose notify or hold."],
    context=[
        "notify: it is clearly one of the kinds the owner asked to hear about ({what}), and someone expects something of the owner or the owner would be badly placed to learn of it late.",
        "hold: real but can wait: acknowledgements, automatic replies, circulars, FYIs, out-of-office notices, thank-yous, anything with nothing for the owner to do.",
        "A mail not clearly one of the asked-for kinds is hold.",
    ],
    output=["One label: notify or hold.", "Unsure: low confidence."],
)

# --- the canvas ---------------------------------------------------------------------------------------------------------------------------------


def _matches(field: str, pattern: str) -> dict[str, Any]:
    return {"left": "{{ $json." + field + " }}", "op": "matches", "right": pattern}


def priority_flow(kind: str) -> dict[str, Any]:
    """The multi-stage triage for "sb" or "personal"."""
    from app.domain.flow_templates import link, n

    sb = kind == "sb"
    nodes: list[dict[str, Any]] = [
        n("live", "trigger.mail", "When mail arrives", 0, 60),
        n("byhand", "trigger.manual", "Run it by hand", 0, 220),
        n("mail", "data.mail", "New mail", 260, 140, days=1, limit=50),
        n("new", "core.only_new", "Only what is new", 500, 140),
    ]
    c: list[tuple[str, int, str]] = [("live", 0, "mail"), ("byhand", 0, "mail"), ("mail", 0, "new")]
    noise_rules = [_matches("subject", x) for x in DISCARD_SUBJECT.values()] + [_matches("snippet", x) for x in DISCARD_TEXT.values()] + ([] if sb else [_matches("subject", x) for x in DISCARD_ORDER_COPY.values()])
    sorter = SB_SORTER if sb else PERSONAL_SORTER
    labels = (
        [
            {"name": "priority_org", "description": "Written by BWI, MFA, the UN or a UN body, a government agency, a court or an RTI office.", "threshold": 0.6},
            {"name": "reply", "description": "A person answering something the owner wrote.", "threshold": 0.6},
            {"name": "other", "description": "Anything else that is not noise: clients, general correspondence.", "threshold": 0.0},
            {"name": "noise", "description": "Automated or bulk mail: codes, statements, receipts, newsletters.", "threshold": 0.0},
        ]
        if sb
        else [
            {"name": "reply", "description": "A real person answering something the owner wrote.", "threshold": 0.6},
            {"name": "business", "description": "A real person or company approaching the owner to do business.", "threshold": 0.7},
            {"name": "other", "description": "Anything else about the owner's own life.", "threshold": 0.0},
            {"name": "noise", "description": "Automated or bulk mail of any kind.", "threshold": 0.0},
        ]
    )
    x = 740
    prev = "new"
    if sb:
        nodes.append(n("vip", "core.if", "Known priority sender?", x, 140, combine="any", conditions=[_matches("fromAddress", p) for p in domain_patterns()]))
        c += [("new", 0, "vip")]
        nodes.append(n("read_vip", "ai.read", "Reader · institutions", x + 260, 0, prompt=READ_ORG, route="alert"))
        c += [("vip", 0, "read_vip"), ("read_vip", 0, "shape")]
        x += 260
        prev, lane = "vip", 1
    else:
        lane = 0
    nodes.append(n("noise", "core.if", "Codes, statements, orders, bulk?", x, 280, combine="any", conditions=noise_rules))
    c.append((prev, lane, "noise"))  # true: dropped, nothing follows
    x += 260
    nodes.append(n("safe", "core.safe_senders", "People I have written to", x, 280, correspondents=True, domains=[], addresses=[], phrases=[]))
    c.append(("noise", 1, "safe"))
    x += 260
    nodes.append(n("sort", "ai.classify", "Sorter · who is writing?", x, 280, prompt=sorter, labels=labels, threshold=0.6, decider="jev", batchSize=5))
    c.append(("safe", 0, "sort"))
    reader_a = ("read_org", "Reader · institutions", READ_ORG) if sb else ("read_biz", "Reader · business", READ_BUSINESS)
    nodes += [
        n("read_reply", "ai.read", "Reader · replies", x + 280, 200, prompt=READ_REPLY, route="alert"),
        n("read_unsure", "ai.read", "Reader · unsure", x + 280, 520, prompt=READ_UNSURE, route="attention"),
        n("attention_unsure", "msg.attention", "Add to Attention", x + 540, 520),
    ]
    if sb:
        nodes.append(n(reader_a[0], "ai.read", reader_a[1], x + 280, 60, prompt=reader_a[2], route="alert"))
        c += [("sort", 0, reader_a[0]), ("sort", 1, "read_reply"), ("sort", 4, "read_unsure")]
    else:
        nodes.append(n(reader_a[0], "ai.read", reader_a[1], x + 280, 60, prompt=reader_a[2], route="alert"))
        c += [("sort", 0, "read_reply"), ("sort", 1, reader_a[0]), ("sort", 4, "read_unsure")]
    c += [(reader_a[0], 0, "shape"), ("read_reply", 0, "shape"), ("safe", 1, "read_reply"), ("read_unsure", 0, "attention_unsure")]
    x += 560
    nodes.append(n("shape", "core.set", "Put the summary where the critic reads", x, 140, fields=[{"name": "text", "value": "From: {{ $json.senderName }} <{{ $json.senderAddress }}>\nSubject: {{ $json.subject }}\nSummary: {{ $json.summary }}\nAction: {{ $json.action }}\nDeadline: {{ $json.due }}\nPriority: {{ $json.priority }}"}]))
    x += 260
    wants = (
        "BWI, MFA, the United Nations and its bodies, government agencies and courts, RTI replies, and replies to the owner's own mail"
        if sb
        else "a reply to something the owner sent, or a real person or company approaching the owner to do business"
    )
    nodes.append(n("critic", "ai.classify", "Critic · worth the phone?", x, 140, prompt=CRITIC.replace("{what}", wants), labels=[
        {"name": "notify", "description": "Clearly wanted now, and the owner has something to do or would be badly placed to learn of it late.", "threshold": 0.6},
        {"name": "hold", "description": "Real but can wait; no phone alert, keep in Attention.", "threshold": 0.0},
    ], threshold=0.6, decider="jev", batchSize=5))
    x += 280
    nodes += [
        n("alert", "msg.alert", "Alert me", x, 60, call=False),
        n("attention", "msg.attention", "Add to Attention", x + 260, 60),
        n("attention_hold", "msg.attention", "Keep in Attention", x, 260),
    ]
    c += [("shape", 0, "critic"), ("critic", 0, "alert"), ("critic", 1, "attention_hold"), ("critic", 2, "alert"), ("alert", 0, "attention")]
    return {"nodes": nodes, "connections": link(*c)}
