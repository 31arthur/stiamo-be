"""The priority mail watch: an agent that is simply there for each workspace that has a mailbox, listening all the time. Pure: no network.

Nobody builds it. When a mailbox is connected to a workspace, that workspace gets a watch agent whose setup is made here: what counts as
urgent in that kind of work, and that it listens to the mailbox, tells the phone, and keeps the rest for Attention.
"""

from __future__ import annotations

from typing import Any

from app.domain.agents import WORKSPACES

PREFIX = "watch-"
NAMES = {"personal": "Personal", "sb": "S&B", "fsa": "FSA", "media": "Media"}

_URGENT = "Something is urgent when someone needs the owner to {ask} by a deadline that is close, or when {who} is waiting on them. When unsure whether it is urgent, choose the label that tells the owner."

#: What the S&B watch tells the phone about, and what it never does.
_SB_PRIORITY = (
    "Notify (urgent) ONLY for: (1) mail from or about BWI (Building and Wood Workers' International); (2) mail from MFA (Migrants Forum in Asia); "
    "(3) mail from the United Nations or any of its bodies and agencies (UN, ILO, OHCHR, UNDP, UNICEF, UNHCR, IOM, UN Women, UNESCO and the like); "
    "(4) mail from a government agency, ministry, department, court, tribunal or other public authority; (5) a reply to a Right to Information (RTI) application "
    "or any RTI correspondence, including first-appeal and information-commission mail; (6) a reply to something the owner sent, that is, a message answering the owner's own mail "
    "(\"Re:\" in the subject, or a person writing back in a thread the owner started). "
    "Never notify for one-time passwords (OTP), verification or login codes, sign-in or security alerts, password resets, account or balance statements, order or payment receipts, "
    "newsletters, promotions, webinars, marketing, social-media notifications or automated notifications from apps and services, even when they look urgent or mention a deadline. "
    "Mail that does not clearly match one of the six kinds above is routine. When unsure whether something matches, choose routine."
)

#: Personal mail: only a person answering the owner, or someone offering business.
_PERSONAL_PRIORITY = (
    "Notify (urgent) ONLY for: (1) a reply to something the owner sent, that is, a person answering the owner's own mail (\"Re:\" in the subject, or someone writing back in a thread the owner started); "
    "(2) a real person or company writing to the owner to do business with them: an enquiry, a proposal, a collaboration, an offer of work, a request for a quote or a meeting. "
    "Never notify for one-time passwords (OTP), verification or login codes, sign-in or security alerts, password resets, bank or card statements, account balance mail, order confirmations, "
    "order copies, invoices and receipts, shipping updates, newsletters, promotions, offers, mass marketing, cold sales mail, social-media or app notifications. "
    "These are routine even if they look urgent. Anything that is not clearly one of the two kinds above is routine. When unsure, choose routine."
)

#: What each kind of work watches for. Everything else about the setup is the same for every workspace.
FOCUS: dict[str, tuple[str, str]] = {
    "sb": (
        "You watch the inbox of S&B Consultants, a legal and advocacy practice. Only a few kinds of mail are worth interrupting the owner for, and nothing else is. ",
        _SB_PRIORITY,
    ),
    "fsa": (
        "You watch the inbox of FSA, a software and technology consultancy, for mail that cannot wait. ",
        _URGENT.format(ask="reply, approve, pay, fix, deliver or confirm", who="a client is blocked or unhappy, a payment or contract is at stake, something is down, or a partner"),
    ),
    "media": (
        "You watch the inbox for the owner's media work for mail that cannot wait. ",
        _URGENT.format(ask="reply, send, confirm or appear", who="an editor, a producer, a journalist or a publication"),
    ),
    "personal": (
        "You watch the owner's personal inbox. Almost nothing here is worth interrupting them for. ",
        _PERSONAL_PRIORITY,
    ),
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
    if workspace == "sb":
        return [
            {"name": "urgent", "description": "From BWI, MFA, the United Nations or its organisations, a government agency or court, an RTI reply, or a reply to the owner's own mail. Tell the phone.", "route": "alert"},
            {"name": "important", "description": "Genuinely relevant to the practice (a client, a case, a funder) but not one of the priority senders. Keep it for Attention without calling the phone.", "route": "attention"},
            {"name": "routine", "description": "One-time passwords, verification and login codes, security alerts, statements, receipts, order mail, newsletters, promotions, notifications and anything else.", "route": "none"},
        ]
    if workspace == "personal":
        return [
            {"name": "urgent", "description": "A reply to something the owner sent, or a real person or company approaching the owner to do business. Tell the phone.", "route": "alert"},
            {"name": "routine", "description": "One-time passwords, verification and login codes, security alerts, bank statements and balances, order copies and confirmations, receipts, newsletters, promotions and anything else.", "route": "none"},
        ]
    return [
        {"name": "urgent", "description": "Needs the owner to act, answer or decide soon: a close deadline, or someone important is waiting on them.", "route": "alert"},
        {"name": "important", "description": "Matters and should be seen, but can wait a day or more: updates, notices, invitations with time to spare.", "route": "attention"},
        {"name": "routine", "description": "Newsletters, receipts, promotions, notifications and anything that needs nothing from the owner.", "route": "none"},
    ]


#: Bumped when the default setup of a watch changes. A watch the owner never edited is brought up to it; an edited one is left alone.
FOCUS_VERSION = 2

#: The prompts the first version made, so an unedited watch can be recognised and upgraded.
LEGACY_PROMPTS = {
    "sb": "You watch the inbox of S&B Consultants, a legal and advocacy practice, for mail that cannot wait. "
    + _URGENT.format(ask="share, send, register, confirm, pay, attend or answer", who="a client, court, government office or funder"),
    "personal": "You watch the owner's personal inbox for mail that cannot wait. "
    + _URGENT.format(ask="pay, renew, confirm, reply or turn up", who="a bank, a tax or government office, a doctor, an airline or family"),
}


def upgraded(workspace: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """The newer setup for a watch still on the first version's wording, or None when it is current or the owner changed the wording."""
    if config.get("focusVersion", 1) >= FOCUS_VERSION or workspace not in LEGACY_PROMPTS or config.get("prompt") != LEGACY_PROMPTS[workspace]:
        return None
    fresh = watch_config(workspace, [])
    return {**config, "prompt": fresh["prompt"], "labels": fresh["labels"], "focusVersion": FOCUS_VERSION}


def watch_config(workspace: str, account_ids: list[str]) -> dict[str, Any]:
    """The setup of a workspace's watch, as the agent service stores it."""
    who, urgent = FOCUS.get(workspace, FOCUS["personal"])
    return {
        "focusVersion": FOCUS_VERSION,
        "source": {"kind": "mail", "accountIds": sorted(account_ids), "folder": "INBOX"},
        # Only the last day, so a watch that has just started doesn't raise alarms about old mail.
        "filters": {"sinceDays": 1, "newOnly": True, "contains": None, "limit": 50},
        "prompt": who + urgent,
        "labels": labels_for(workspace),
        "decider": "model",
        "defaultThreshold": 0.6,
        "rules": {"protectedDomains": [], "protectedPhrases": [], "protectCorrespondents": False},
        "action": {"trashLabel": None, "auto": False},
        "listen": True,
        "notify": {"telegram": True, "call": False},
    }
