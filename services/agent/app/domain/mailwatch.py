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

#: What each kind of work watches for. Everything else about the setup is the same for every workspace.
FOCUS: dict[str, tuple[str, str]] = {
    "sb": (
        "You watch the inbox of S&B Consultants, a legal and advocacy practice, for mail that cannot wait. ",
        _URGENT.format(ask="share, send, register, confirm, pay, attend or answer", who="a client, court, government office or funder"),
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
        "You watch the owner's personal inbox for mail that cannot wait. ",
        _URGENT.format(ask="pay, renew, confirm, reply or turn up", who="a bank, a tax or government office, a doctor, an airline or family"),
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


def watch_config(workspace: str, account_ids: list[str]) -> dict[str, Any]:
    """The setup of a workspace's watch, as the agent service stores it."""
    who, urgent = FOCUS.get(workspace, FOCUS["personal"])
    return {
        "source": {"kind": "mail", "accountIds": sorted(account_ids), "folder": "INBOX"},
        # Only the last day, so a watch that has just started doesn't raise alarms about old mail.
        "filters": {"sinceDays": 1, "newOnly": True, "contains": None, "limit": 50},
        "prompt": who + urgent,
        "labels": [
            {"name": "urgent", "description": "Needs the owner to act, answer or decide soon: a close deadline, or someone important is waiting on them.", "route": "alert"},
            {"name": "important", "description": "Matters and should be seen, but can wait a day or more: updates, notices, invitations with time to spare.", "route": "attention"},
            {"name": "routine", "description": "Newsletters, receipts, promotions, notifications and anything that needs nothing from the owner.", "route": "none"},
        ],
        "decider": "model",
        "defaultThreshold": 0.6,
        "rules": {"protectedDomains": [], "protectedPhrases": [], "protectCorrespondents": False},
        "action": {"trashLabel": None, "auto": False},
        "listen": True,
        "notify": {"telegram": True, "call": False},
    }
