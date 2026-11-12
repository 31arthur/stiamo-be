"""Stories: every running thing (a case, a client project, a home loan) told in chapters the owner writes.

A story has its own arc of checkpoints (never a fixed list of stages), the ending the owner is writing towards, and
chapters. A chapter keeps the owner's prose exactly as written, plus the margin notes read from it: the checkpoint it
reached, a twist that added a checkpoint, the work it left (ordinary tasks and calls pointing at the story), the people
in it, hunches. "Where you are" is never stored: it is the first checkpoint not yet reached or skipped.
"""

from typing import Literal

from pydantic import Field

from app.domain.common import DomainModel, Entity, EntityRef, ISODate, Workspace

StoryState = Literal["writing", "resting", "ended"]
Pacing = Literal["brisk", "steady", "slow"]
Beat = Literal["progress", "setback", "twist", "lull", "breakthrough", "note", "epilogue"]
CheckpointStatus = Literal["ahead", "reached", "skipped"]
CheckpointOrigin = Literal["you", "twist", "template", "sketch"]
ChapterSource = Literal["app", "telegram", "voice", "mail", "news", "hearing"]
NoteKind = Literal["beat", "checkpoint", "twist", "step", "person", "hunch", "chance", "cliffhanger"]

#: What a story may be about besides itself: the records it covers.
LINKABLE = ("case", "project", "client")
#: How many days of quiet each pacing allows before a story counts as behind (used by the editor, phase 3).
PACE_DAYS: dict[str, int] = {"brisk": 7, "steady": 14, "slow": 30}


class Checkpoint(DomainModel):
    """A plot point the story has to pass through. Order is the list's order."""

    id: str
    title: str
    status: CheckpointStatus = "ahead"
    target_date: ISODate | None = None
    added_by: CheckpointOrigin = "you"
    #: The chapter that added it (a twist) or reached it.
    chapter_id: str | None = None
    note: str | None = None


class BibleEntry(DomainModel):
    """One fact the story needs remembered: "Amount" → "₹25L", "File no." → "…"."""

    key: str
    value: str


class Waiting(DomainModel):
    """What the story is waiting on, and until when. While set and not due, the story isn't quiet, it's waiting."""

    on: str
    until: ISODate | None = None


class Story(Entity):
    title: str
    workspace: Workspace = "personal"
    #: The ending the owner is writing towards: the goal, and how they'll know it's over.
    ending: str = ""
    #: How much rides on it, 1 to 5, and optionally in money words ("₹1.5L fee", "₹25L loan").
    stakes: int = Field(default=3, ge=1, le=5)
    stakes_amount: str | None = None
    #: The owner's read of the chance of reaching the ending, 0–100. Each chapter keeps the chance it left behind.
    chance: int | None = Field(default=None, ge=0, le=100)
    pacing: Pacing = "steady"
    state: StoryState = "writing"
    waiting_on: Waiting | None = None
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    bible: list[BibleEntry] = Field(default_factory=list)
    #: Cases, projects and clients the story covers.
    links: list[EntityRef] = Field(default_factory=list)
    #: A short "previously…" recap, kept by the editor. Never the owner's prose.
    recap: str = ""
    ended_on: ISODate | None = None


class MarginNote(DomainModel):
    """Something read from a chapter, tied to the sentence it came from."""

    kind: NoteKind
    text: str
    quote: str | None = None
    #: What it became: a task or follow-up made for it, or the checkpoint it moved or added.
    ref: EntityRef | None = None
    checkpoint_id: str | None = None


class Hunch(DomainModel):
    """Foreshadowing: something the owner expects, brought back later to see whether it came true."""

    id: str
    text: str
    quote: str | None = None
    check_on: ISODate | None = None
    came_true: bool | None = None
    settled_in: str | None = None


class Cliffhanger(DomainModel):
    """An open question a chapter ends on, kept on the story until a later chapter answers it."""

    question: str
    answered_in: str | None = None


class CastMention(DomainModel):
    """A person in a chapter. Until characters exist (phase 2) this is a name, optionally a Google contact, and the scene."""

    name: str
    contact_id: str | None = None
    scene: str | None = None
    quote: str | None = None


class Chapter(Entity):
    story_id: str
    #: 1, 2, 3… in the order written. Assigned when it's first saved.
    number: int = 0
    date: ISODate
    title: str = ""
    #: The owner's words, exactly as written.
    prose: str
    beat: Beat = "note"
    source: ChapterSource = "app"
    chance_after: int | None = Field(default=None, ge=0, le=100)
    notes: list[MarginNote] = Field(default_factory=list)
    hunches: list[Hunch] = Field(default_factory=list)
    cliffhanger: Cliffhanger | None = None
    people: list[CastMention] = Field(default_factory=list)


class ArcTemplate(Entity):
    """An arc saved for reuse in a workspace: "Court order", "Loan", "Client project"."""

    name: str
    workspace: Workspace = "personal"
    checkpoints: list[str] = Field(default_factory=list)
    ending: str = ""


# --- the arc ----------------------------------------------------------------------------------------------------


def current_checkpoint(story: Story) -> Checkpoint | None:
    """Where the story is: the first checkpoint not yet reached or skipped. None when all are passed (or there are none)."""
    return next((c for c in story.checkpoints if c.status == "ahead"), None)


def arc_progress(story: Story) -> float | None:
    """Share of checkpoints passed (reached or skipped), or None with no checkpoints."""
    if not story.checkpoints:
        return None
    return sum(1 for c in story.checkpoints if c.status != "ahead") / len(story.checkpoints)


def insert_checkpoint(checkpoints: list[Checkpoint], new: Checkpoint, before: str | None) -> list[Checkpoint]:
    """Drops a checkpoint into the arc before the one with id `before`, or before where the story is now (so a twist
    comes up next), or at the end when everything is passed."""
    out = [c for c in checkpoints if c.id != new.id]
    if before is None:
        nxt = next((c for c in out if c.status == "ahead"), None)
        before = nxt.id if nxt else None
    index = next((i for i, c in enumerate(out) if c.id == before), len(out))
    return [*out[:index], new, *out[index:]]


def next_number(chapters: list[Chapter]) -> int:
    return max((c.number for c in chapters), default=0) + 1


def open_cliffhangers(chapters: list[Chapter]) -> list[tuple[Chapter, Cliffhanger]]:
    return [(c, c.cliffhanger) for c in sorted(chapters, key=lambda c: c.number) if c.cliffhanger and not c.cliffhanger.answered_in]


def last_written(chapters: list[Chapter]) -> str | None:
    """The date of the newest chapter."""
    return max((c.date for c in chapters), default=None)
