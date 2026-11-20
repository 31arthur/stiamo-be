"""The priority mail watch: an agent that is simply there for each workspace that has a mailbox, listening all the time. Pure: no network.

Nobody builds it. When a mailbox is connected to a workspace, that workspace gets a watch agent whose setup is made here: what counts as
urgent in that kind of work, and that it listens to the mailbox, tells the phone, and keeps the rest for Attention.
"""

from __future__ import annotations

from typing import Any

from app.domain.agents import WORKSPACES
from app.domain.mailrules import DISCARD_ORDER_COPY, DISCARD_SUBJECT, DISCARD_TEXT, PRIORITY_DOMAINS
from app.domain.mailprompts import PROMPTS

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

#: The prompts, written out in domain/mailprompts.py: one long prompt per workspace (the shared rules and facts, then that workspace's own priorities and examples).
FOCUS: dict[str, str] = PROMPTS

#: The persona lines of the wording before this one, so a watch the owner never edited can still be recognised and upgraded.
_V3_PERSONAS = (
    "## Persona\nYou are the intake clerk of S&B Consultants, a legal and advocacy practice for trade unions and migrant workers.",
    "## Persona\nYou are the gatekeeper of the owner's personal inbox. You are blunt. Almost nothing here deserves an interruption.",
    "## Persona\nYou are the intake clerk of FSA, a software and technology consultancy. You are blunt.",
    "## Persona\nYou are the intake clerk for the owner's media work. You are blunt.",
)

#: Subjects that must reach the model even when a dropping rule matched: harm, deadlines, legal words and leads.
RESCUE = {
    "sb": [r"(court|order dated|certified copy|hearing|summons|notice|appeal|\brti\b|tribunal|registry|undeliver|delivery (status|failure))"],
    "personal": [r"(fraud|suspicious|unauthori[sz]ed|blocked|declined|failed|bounced|lapse|expir|overdue|cancell?ed|rescheduled|delayed|gate change|enquiry|inquiry|quote|undeliver|delivery (status|failure))"],
    "fsa": [r"(down|outage|breach|incident|vulnerab|enquiry|inquiry|quote|undeliver|delivery (status|failure))"],
    "media": [r"(deadline|correction|takedown|copyright|licen[cs]e|interview|comment|undeliver|delivery (status|failure))"],
}


#: The pre-model rules per workspace (see domain/mailrules.py). Only personal drops "order copy": in a law practice an order is often a court order.
def _rules(workspace: str) -> dict[str, Any]:
    subject = list(DISCARD_SUBJECT.values()) + (list(DISCARD_ORDER_COPY.values()) if workspace == "personal" else [])
    return {
        "protectedDomains": [], "protectedPhrases": [], "protectCorrespondents": False,
        "discardPatterns": subject, "discardTextPatterns": list(DISCARD_TEXT.values()), "rescuePatterns": list(RESCUE.get(workspace, [])),
        "priorityDomains": list(PRIORITY_DOMAINS) if workspace in ("sb", "media", "fsa") else [], "noteCorrespondents": True,
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
    """Three outcomes for every workspace, chosen by the decision model: the phone, the Attention list (shown in the evening plan), or nothing."""
    return [
        {"name": "now", "description": "Worth interrupting the owner for: a priority sender asking or announcing something, a verified reply from a person who is waiting, a close deadline, something breaking, a possible impersonation.", "route": "alert"},
        {"name": "digest", "description": "Real and relevant but can wait until the evening: acknowledgements, circulars from priority senders, information with nothing to do, anything unclear.", "route": "attention"},
        {"name": "drop", "description": "Automated, bulk or not for the owner: codes, logins, statements, orders, receipts, newsletters, promotions, notifications, cold pitches.", "route": "none"},
    ]


#: Bumped when the default setup of a watch changes. A watch the owner never edited is brought up to it; an edited one is left alone.
FOCUS_VERSION = 4


def _is_default(workspace: str, prompt: Any) -> bool:
    """Whether the prompt is one an earlier version of this app wrote, so the owner never changed it."""
    p = str(prompt or "")
    return p == _V1.get(workspace) or (workspace == "sb" and p.startswith(_V2_SB)) or (workspace == "personal" and p.startswith(_V2_PERSONAL)) or any(p.startswith(x) for x in _V3_PERSONAS) or p == FOCUS.get(workspace)


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
