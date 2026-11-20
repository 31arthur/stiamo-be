"""The priority mail watch: an agent that is simply there for each workspace that has a mailbox, listening all the time. Pure: no network.

Nobody builds it. When a mailbox is connected to a workspace, that workspace gets a watch agent whose setup is made here: what counts as
urgent in that kind of work, and that it listens to the mailbox, tells the phone, and keeps the rest for Attention.
"""

from __future__ import annotations

from typing import Any

from app.domain.agents import WORKSPACES
from app.domain.mailrules import DISCARD_ORDER_COPY, DISCARD_SUBJECT, DISCARD_TEXT, PRIORITY_DOMAINS
from app.domain.prompting import brief

PREFIX = "watch-"
NAMES = {"personal": "Personal", "sb": "S&B", "fsa": "FSA", "media": "Media"}

# --- the first wordings, kept only so a watch the owner never edited can be recognised and brought up to date --------------------------------

_URGENT = "Something is urgent when someone needs the owner to {ask} by a deadline that is close, or when {who} is waiting on them. When unsure whether it is urgent, choose the label that tells the owner."
_V1 = {
    "sb": "You watch the inbox of S&B Consultants, a legal and advocacy practice, for mail that cannot wait. " + _URGENT.format(ask="share, send, register, confirm, pay, attend or answer", who="a client, court, government office or funder"),
    "fsa": "You watch the inbox of FSA, a software and technology consultancy, for mail that cannot wait. " + _URGENT.format(ask="reply, approve, pay, fix, deliver or confirm", who="a client is blocked or unhappy, a payment or contract is at stake, something is down, or a partner"),
    "media": "You watch the inbox for the owner's media work for mail that cannot wait. " + _URGENT.format(ask="reply, send, confirm or appear", who="an editor, a producer, a journalist or a publication"),
    "personal": "You watch the owner's personal inbox for mail that cannot wait. " + _URGENT.format(ask="pay, renew, confirm, reply or turn up", who="a bank, a tax or government office, a doctor, an airline or family"),
}
_V2_SB = (
    "You watch the inbox of S&B Consultants, a legal and advocacy practice. Only a few kinds of mail are worth interrupting the owner for, and nothing else is. Notify (urgent) ONLY for: (1) mail from or about BWI"
)
_V2_PERSONAL = "You watch the owner's personal inbox. Almost nothing here deserves an interruption. "

#: What each kind of work needs to hear about. The one thing every inbox shares: replies to the owner's own mail.
_REPLY = "A reply to the owner's own mail: \"Re:\" in the subject, the owner's words quoted, \"in response to your\", \"as discussed\", or the facts say the owner has written to this sender before."
_NOT = (
    "One-time passwords, verification, login and security codes, sign-in alerts, password resets, account and balance statements, order copies and confirmations, shipping, receipts, invoices from shops, "
    "newsletters, promotions, webinars, social and app notifications."
)

FOCUS: dict[str, str] = {
    "sb": brief(
        persona="You are the intake clerk of S&B Consultants, a legal and advocacy practice for trade unions and migrant workers. You are blunt. Silence is the default; the owner is interrupted rarely and only for the list below.",
        tasks=["Read the sender and the subject first, the start of the text second.", "Decide: needed or discarded.", "Choose needed only if the email is one of the kinds in Context."],
        context=[
            "Needed, kind 1: BWI (Building and Wood Workers' International).",
            "Needed, kind 2: MFA (Migrants Forum in Asia).",
            "Needed, kind 3: the United Nations or any UN body or agency (ILO, OHCHR, UNDP, UNICEF, UNHCR, IOM, UN Women, UNESCO and the like).",
            "Needed, kind 4: a government agency, ministry, department, court, tribunal or commission.",
            "Needed, kind 5: Right to Information (RTI) mail: a reply, a fee demand, a transfer, a first-appeal or information-commission notice.",
            "Needed, kind 6: " + _REPLY,
            "Judge by who sent it or signed it, not by words in the subject. A promotional mail that mentions the UN is discarded.",
            "A line starting \"[Facts from the system: ...]\" is established by code. Believe it. A sender on the priority list is usually kind 1 to 4, but a circular or newsletter from them is still discarded.",
            "Discarded: " + _NOT + " Also clients and general correspondence that are not one of the six kinds.",
        ],
        output=["One label: needed or discarded.", "Unsure: discarded, with low confidence. A person reviews low-confidence mail."],
    ),
    "fsa": brief(
        persona="You are the intake clerk of FSA, a software and technology consultancy. You are blunt. Silence is the default; the owner is interrupted rarely.",
        tasks=["Read the sender and the subject first, the start of the text second.", "Decide: needed or discarded.", "Choose needed only for the kinds in Context."],
        context=[
            "Needed: a client who is blocked, unhappy or waiting on a reply, approval, fix or delivery.",
            "Needed: a payment, invoice dispute, contract or renewal with a client or partner that needs the owner.",
            "Needed: a person or monitoring service reporting that a client's system is down, breached or failing.",
            "Needed: a real person or company asking FSA to build, quote or partner.",
            "Needed: " + _REPLY,
            "A line starting \"[Facts from the system: ...]\" is established by code. Believe it.",
            "Discarded: " + _NOT + " Also cold sales pitches, recruiters' bulk mail and vendor marketing.",
        ],
        output=["One label: needed or discarded.", "Unsure: discarded, with low confidence. A person reviews low-confidence mail."],
    ),
    "media": brief(
        persona="You are the intake clerk for the owner's media work. You are blunt. Silence is the default; the owner is interrupted rarely.",
        tasks=["Read the sender and the subject first, the start of the text second.", "Decide: needed or discarded.", "Choose needed only for the kinds in Context."],
        context=[
            "Needed: an editor, producer, journalist or publication asking for a piece, an interview, a comment, a correction or a deadline.",
            "Needed: a commission, a booking or an invitation to appear that names a date.",
            "Needed: " + _REPLY,
            "A line starting \"[Facts from the system: ...]\" is established by code. Believe it.",
            "Discarded: " + _NOT + " Also press releases and mass pitches sent to a list.",
        ],
        output=["One label: needed or discarded.", "Unsure: discarded, with low confidence. A person reviews low-confidence mail."],
    ),
    "personal": brief(
        persona="You are the gatekeeper of the owner's personal inbox. You are blunt. Almost nothing here deserves an interruption. Silence is the default.",
        tasks=["Read the sender and the subject first, the start of the text second.", "Decide: needed or discarded.", "Choose needed only for the two kinds in Context."],
        context=[
            "Needed, kind 1: " + _REPLY,
            "Needed, kind 2: a real person or company writing to the owner personally to do business: an enquiry, a request for a quote or a meeting, a proposal, a collaboration, an offer of work.",
            "A mass-mailed pitch, a template, a recruiter's bulk mail or \"grow your business\" mail is not kind 2.",
            "A line starting \"[Facts from the system: ...]\" is established by code. Believe it.",
            "Discarded, always, even if it looks urgent: " + _NOT + " Also bank and card mail of any kind, and anything else that is not kind 1 or 2.",
        ],
        output=["One label: needed or discarded.", "Unsure: discarded, with low confidence. A person reviews low-confidence mail."],
    ),
}

#: The pre-model rules per workspace (see domain/mailrules.py). Only personal drops "order copy": in a law practice an order is often a court order.
def _rules(workspace: str) -> dict[str, Any]:
    subject = list(DISCARD_SUBJECT.values()) + (list(DISCARD_ORDER_COPY.values()) if workspace == "personal" else [])
    return {
        "protectedDomains": [], "protectedPhrases": [], "protectCorrespondents": False,
        "discardPatterns": subject, "discardTextPatterns": list(DISCARD_TEXT.values()),
        "priorityDomains": list(PRIORITY_DOMAINS) if workspace in ("sb", "media") else [], "noteCorrespondents": True,
    }


def watch_id(workspace: str, user_id: str) -> str:
    return f"{PREFIX}{workspace}-{user_id[:8]}"


def workspace_of(workflow_id: str) -> str | None:
    """The workspace a watch belongs to, or None when this isn't one."""
    if not workflow_id.startswith(PREFIX):
        return None
    ws = workflow_id[len(PREFIX):].split("-", 1)[0]
    return ws if ws in WORKSPACES else None


def watch_name(workspace: str) -> str:
    return f"Priority mail watch · {NAMES.get(workspace, workspace)}"


def watch_description(workspace: str) -> str:
    return f"Always on: reads new {NAMES.get(workspace, workspace)} mail as it arrives, and tells your phone about anything that can't wait."


def labels_for(workspace: str) -> list[dict[str, str]]:
    """Two labels, for every workspace: the decision model chooses between them, and only "needed" reaches the phone."""
    return [
        {"name": "needed", "description": "One of the kinds listed in the instructions for this inbox. The owner is told now.", "route": "alert"},
        {"name": "discarded", "description": "Anything else, including every code, login, statement, order, receipt, newsletter and promotion. Nothing happens.", "route": "none"},
    ]


#: Bumped when the default setup of a watch changes. A watch the owner never edited is brought up to it; an edited one is left alone.
FOCUS_VERSION = 3


def _is_default(workspace: str, prompt: Any) -> bool:
    """Whether the prompt is one an earlier version of this app wrote, so the owner never changed it."""
    p = str(prompt or "")
    return p == _V1.get(workspace) or (workspace == "sb" and p.startswith(_V2_SB)) or (workspace == "personal" and p.startswith(_V2_PERSONAL)) or p == FOCUS.get(workspace)


def upgraded(workspace: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """The newer setup for a watch still on an earlier default wording, or None when it is current or the owner changed the wording."""
    if config.get("focusVersion", 1) >= FOCUS_VERSION or workspace not in FOCUS or not _is_default(workspace, config.get("prompt")):
        return None
    fresh = watch_config(workspace, [])
    return {**config, "prompt": fresh["prompt"], "labels": fresh["labels"], "decider": fresh["decider"], "defaultThreshold": fresh["defaultThreshold"], "rules": fresh["rules"], "focusVersion": FOCUS_VERSION}


def watch_config(workspace: str, account_ids: list[str]) -> dict[str, Any]:
    """The setup of a workspace's watch, as the agent service stores it. It listens to the mailbox (IMAP IDLE): each new mail is judged the moment
    it arrives, first by the rules in code, then by the decision model."""
    return {
        "focusVersion": FOCUS_VERSION,
        "source": {"kind": "mail", "accountIds": sorted(account_ids), "folder": "INBOX"},
        # Only the last day, so a watch that has just started doesn't raise alarms about old mail.
        "filters": {"sinceDays": 1, "newOnly": True, "contains": None, "limit": 50},
        "prompt": FOCUS.get(workspace, FOCUS["personal"]),
        "labels": labels_for(workspace),
        "decider": "jev",
        "defaultThreshold": 0.7,
        "rules": _rules(workspace),
        "action": {"trashLabel": None, "auto": False},
        "listen": True,
        "notify": {"telegram": True, "call": False},
    }
