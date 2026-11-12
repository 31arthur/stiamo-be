"""The cast: people learned once and used in every story they appear in.

A **character** is who someone is (name, numbers, organisation): one record, shared by every story and workspace they are part of, and
matched on phone number so nobody is entered twice. A **part** is the role a character plays in one story (decider, other side, the
agent…), what they are called there, their stance and how much they sway it; it belongs to the story's workspace. A **scene** is what they
did in a chapter (or what the owner said about them), dated and quoted; it also stays in the workspace of its story. That split is what
lets a person be reused across S&B, FSA and personal stories without one workspace's assistant reading what happened in another's.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from app.domain.common import Entity, ISODate, Workspace

Stance = Literal["for_us", "neutral", "wary", "against"]
Sway = Literal["decides", "shapes", "informs"]
SceneSource = Literal["chapter", "answer", "you"]

#: Parts offered as suggestions. Any words of the owner's own are fine too.
SUGGESTED_PARTS = ("decider", "ally", "other side", "client", "counsel", "officer", "vendor", "gatekeeper", "agent", "witness")


class Character(Entity):
    name: str
    organisation: str | None = None
    title: str | None = None
    #: Normalised (+91… for Indian mobiles), so the same number written two ways is one person.
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    #: The Google contact this person is, when linked.
    google_contact_id: str | None = None
    #: The workspace they were first added in. They are also seen in every workspace where they have a part.
    home: Workspace = "personal"


class Part(Entity):
    character_id: str
    story_id: str
    #: Copied from the story: a part, and everything said about it, belongs to the story's workspace.
    workspace: Workspace = "personal"
    #: The part they play, in the owner's words: "decider", "the other side's lawyer"…
    part: str = ""
    #: What they are called in this story's chapters ("the agent", "Ravi"), so a chapter's names find them.
    known_as: list[str] = Field(default_factory=list)
    stance: Stance | None = None
    sway: Sway | None = None


class Scene(Entity):
    character_id: str
    story_id: str
    workspace: Workspace = "personal"
    chapter_id: str | None = None
    date: ISODate
    #: What they did, in a few words.
    text: str
    #: The owner's words it was read from.
    quote: str | None = None
    source: SceneSource = "chapter"


# --- phone numbers ---------------------------------------------------------------------------------------------


def normalize_phone(raw: str | None, country: str = "91") -> str | None:
    """One way of writing a number, so "098450 12345", "+91 98450-12345" and "9845012345" are the same person. Indian numbers get +91;
    a number written with its own + and country code keeps it. Too short to be a phone: None."""
    if not raw:
        return None
    text = raw.strip()
    plus = text.startswith("+") or text.startswith("00")
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        digits = digits[2:]
    if len(digits) < 6:
        return None
    if plus:
        return f"+{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+{country}{digits}"
    if len(digits) == 12 and digits.startswith(country):
        return f"+{digits}"
    return digits


def same_name(a: str | None, b: str | None) -> bool:
    def norm(s: str | None) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower().removeprefix("the ")))
    return bool(norm(a)) and norm(a) == norm(b)


def dial_line(c: Character) -> str:
    """How a task or call shows who to reach: "Name · +91…", the form the app and Telegram turn into a call button."""
    return f"{c.name} · {c.phones[0]}" if c.phones else c.name


def find_in_story(name: str, parts: list[Part], characters: dict[str, Character]) -> Character | None:
    """The character a name in a chapter means, among those already in the story: by what they're called there, or by their name."""
    hits = {p.character_id for p in parts
            if p.character_id in characters and (same_name(name, characters[p.character_id].name) or any(same_name(name, k) for k in p.known_as))}
    return characters[hits.pop()] if len(hits) == 1 else None
