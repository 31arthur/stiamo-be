"""Stories, their chapters and arc templates.

Writing a chapter is one step: the prose is saved as written, and what was read from it (checkpoints reached, twists that
add checkpoints, the work it leaves, hunches, a cliffhanger, the chance it leaves behind) is applied in the same unit of
work. Ids can be given for everything a chapter creates, so writing the same chapter again never doubles anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field
from stiamo_common.errors import ValidationFailed

from app.application.crud import Crud
from app.application.ports import Env
from app.application.use_cases.cast import CastUseCases, JoinCast, SceneIn
from app.application.use_cases.work import FollowUpUseCases, task_crud
from app.domain.cases import Hearing, hearing_case_ids, is_diary_entry
from app.domain.cast import Character, Part, dial_line, same_name
from app.domain.common import DomainModel, Duration, EntityRef, ISODate, draft_of
from app.domain.rules import blank_to_none, required, unique
from app.domain.stories import (
    LINKABLE,
    ArcTemplate,
    Beat,
    CastMention,
    Chapter,
    ChapterSource,
    Checkpoint,
    CheckpointStatus,
    Cliffhanger,
    Hunch,
    MarginNote,
    Story,
    Waiting,
    current_checkpoint,
    insert_checkpoint,
    next_number,
    open_cliffhangers,
)
from app.domain.work import FollowUp, FollowUpChannel, Task

TaskDraft = draft_of(Task)
ChapterDraft = draft_of(Chapter)
FollowUpDraft = draft_of(FollowUp)

# --- normalising ------------------------------------------------------------------------------------------------


def normalize_story(s: Story) -> Story:
    ids = [c.id for c in s.checkpoints]
    if len(ids) != len(set(ids)):
        raise ValidationFailed("Two checkpoints have the same id")
    for ref in s.links:
        if ref.kind not in LINKABLE:
            raise ValidationFailed("A story can be linked to cases, projects and clients")
    return s.model_copy(update={
        "title": required(s.title, "Title"),
        "ending": (s.ending or "").strip(),
        "stakes_amount": blank_to_none(s.stakes_amount),
        "checkpoints": [c.model_copy(update={"title": required(c.title, "A checkpoint's title")}) for c in s.checkpoints],
        "bible": [b.model_copy(update={"key": b.key.strip(), "value": b.value.strip()}) for b in s.bible if b.key.strip()],
        "links": list({(r.kind, r.id): r for r in s.links}.values()),
        "waiting_on": s.waiting_on if s.waiting_on and s.waiting_on.on.strip() else None,
    })


def normalize_chapter(c: Chapter) -> Chapter:
    return c.model_copy(update={"prose": required(c.prose, "The chapter"), "title": (c.title or "").strip()})


def normalize_template(t: ArcTemplate) -> ArcTemplate:
    return t.model_copy(update={
        "name": required(t.name, "Name"), "checkpoints": unique([c.strip() for c in t.checkpoints if c.strip()]),
    })


def arc_template_crud(env: Env) -> Crud[ArcTemplate]:
    return Crud(env, "arcTemplate", ArcTemplate, env.stores.arc_template, normalize=normalize_template)


class StoryUseCases(Crud[Story]):
    """Deleting a story deletes its chapters. The tasks and calls it left stay, untied, like a client's meetings."""

    def __init__(self, env: Env) -> None:
        super().__init__(env, "story", Story, env.stores.story, normalize=normalize_story)

    async def chapters_of(self, user_id: str, story_id: str) -> list[Chapter]:
        return sorted((c for c in await self.env.stores.chapter.list(user_id) if c.story_id == story_id), key=lambda c: c.number)

    async def remove(self, user_id: str, id: str) -> None:
        chapters = ChapterUseCases(self.env)
        for c in await self.chapters_of(user_id, id):
            await chapters.repo.remove(user_id, c.id)
        # The characters stay in the cast; only their part in this story, and its scenes, go with it.
        for p in [p for p in await self.env.stores.part.list(user_id) if p.story_id == id]:
            await self.env.stores.part.remove(user_id, p.id)
        for sc in [x for x in await self.env.stores.scene.list(user_id) if x.story_id == id]:
            await self.env.stores.scene.remove(user_id, sc.id)
        for ev in [x for x in await self.env.stores.evidence.list(user_id) if x.story_id == id]:
            await self.env.stores.evidence.remove(user_id, ev.id)
        await super().remove(user_id, id)


class ChapterUseCases(Crud[Chapter]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "chapter", Chapter, env.stores.chapter, normalize=normalize_chapter)

    async def save(self, user_id, id, draft):
        story = await self.env.stores.story.get(user_id, draft.story_id)
        if story is None:
            raise ValidationFailed("That story no longer exists")
        existing = await self.repo.get(user_id, id) if id else None
        if existing is not None:
            # The number never changes once given, and a chapter can't move to another story.
            draft = draft.model_copy(update={"number": existing.number, "story_id": existing.story_id})
        else:
            chapters = [c for c in await self.repo.list(user_id) if c.story_id == story.id]
            draft = draft.model_copy(update={"number": next_number(chapters)})
        return await super().save(user_id, id, draft)


# --- writing a chapter ------------------------------------------------------------------------------------------


class CheckpointMove(DomainModel):
    id: str
    status: CheckpointStatus
    quote: str | None = None


class Twist(DomainModel):
    """A checkpoint the chapter adds to the arc. Without `before` it goes in just before where the story is now."""

    title: str
    id: str | None = None
    before: str | None = None
    target_date: ISODate | None = None
    quote: str | None = None


class Step(DomainModel):
    """Work the chapter leaves: a task, or a call / message / visit (a follow-up)."""

    kind: Literal["task", "followUp"] = "task"
    title: str
    id: str | None = None
    due: ISODate | None = None
    minutes: Duration | None = None
    channel: FollowUpChannel = "call"
    contact: str | None = None
    contact_id: str | None = None
    #: The character it is about. Without it, a step whose contact names someone in the chapter's people points at them.
    character_id: str | None = None
    quote: str | None = None


class PersonIn(DomainModel):
    """Someone in the chapter. With `character_id` (or `create`) they are part of the cast: they get a part in this story (if they lack
    one) and a scene. Without either, they are only named in the chapter."""

    name: str
    character_id: str | None = None
    create: bool = False
    #: The id for a new character, fixed by the caller so a retry never makes two.
    new_id: str | None = None
    phones: list[str] = Field(default_factory=list)
    part: str | None = None
    scene: str | None = None
    quote: str | None = None
    contact_id: str | None = None


class HunchIn(DomainModel):
    text: str
    id: str | None = None
    check_on: ISODate | None = None
    quote: str | None = None


class WriteChapter(DomainModel):
    prose: str
    id: str | None = None
    date: ISODate | None = None
    title: str = ""
    beat: Beat = "note"
    source: ChapterSource = "app"
    chance_after: int | None = Field(default=None, ge=0, le=100)
    checkpoints: list[CheckpointMove] = Field(default_factory=list)
    twists: list[Twist] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    hunches: list[HunchIn] = Field(default_factory=list)
    people: list[PersonIn] = Field(default_factory=list)
    cliffhanger: str | None = None
    #: Earlier chapters whose cliffhanger this one answers.
    answers: list[str] = Field(default_factory=list)
    #: Set what the story now waits on; `clear_waiting` lifts it.
    waiting_on: Waiting | None = None
    clear_waiting: bool = False


@dataclass
class Written:
    chapter: Chapter
    story: Story
    tasks: list[Task] = field(default_factory=list)
    follow_ups: list[FollowUp] = field(default_factory=list)


@dataclass
class TimelineEntry:
    kind: Literal["chapter", "hearing"]
    date: str
    chapter: Chapter | None = None
    hearing: Hearing | None = None


@dataclass
class Timeline:
    story: Story
    entries: list[TimelineEntry]
    here: Checkpoint | None
    open_tasks: list[Task]
    open_follow_ups: list[FollowUp]
    cliffhangers: list[tuple[Chapter, Cliffhanger]]
    hunches: list[tuple[Chapter, Hunch]]
    cast: list[tuple[Part, Character]] = field(default_factory=list)


class StoryWriting:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.stories = StoryUseCases(env)
        self.chapters = ChapterUseCases(env)
        self.tasks = task_crud(env)
        self.follow_ups = FollowUpUseCases(env)
        self.cast = CastUseCases(env)

    async def write(self, user_id: str, story_id: str, w: WriteChapter) -> Written:
        story = await self.stories.require(user_id, story_id, "Story")
        prose = required(w.prose, "The chapter")
        existing = await self.chapters.get(user_id, w.id) if w.id else None
        if existing is not None and existing.story_id != story.id:
            raise ValidationFailed("That chapter belongs to another story")
        chapter_id = w.id or self.env.ids.next()
        today = self.env.clock.now().date().isoformat()
        ref = EntityRef(kind="story", id=story.id)
        notes: list[MarginNote] = [MarginNote(kind="beat", text=w.beat)] if w.beat != "note" else []

        # The arc: moves first, then twists dropped in where they belong.
        checkpoints = list(story.checkpoints)
        by_id = {c.id: i for i, c in enumerate(checkpoints)}
        for move in w.checkpoints:
            if move.id not in by_id:
                raise ValidationFailed("One of the checkpoints isn't in this story's arc")
            i = by_id[move.id]
            checkpoints[i] = checkpoints[i].model_copy(update={"status": move.status, "chapter_id": chapter_id if move.status != "ahead" else None})
            notes.append(MarginNote(kind="checkpoint", text=f"{checkpoints[i].title}: {move.status}", quote=move.quote, checkpoint_id=move.id))
        for t in w.twists:
            title = required(t.title, "A twist's checkpoint")
            if t.before is not None and t.before not in {c.id for c in checkpoints}:
                raise ValidationFailed("A twist is placed before a checkpoint that isn't in the arc")
            cid = t.id or self.env.ids.next()
            checkpoints = insert_checkpoint(checkpoints, Checkpoint(id=cid, title=title, target_date=t.target_date, added_by="twist", chapter_id=chapter_id), t.before)
            notes.append(MarginNote(kind="twist", text=title, quote=t.quote, checkpoint_id=cid))

        # The people in it: found in the cast (or brought into it), each with a part in this story and a scene.
        day = w.date or (existing.date if existing else today)
        mentions: list[CastMention] = []
        named: list[tuple[str, Character]] = []
        for n, person in enumerate(w.people):
            name = required(person.name, "A person's name")
            character = None
            if person.character_id or person.create:
                joined = await self.cast.join(user_id, JoinCast(name=name, character_id=person.character_id, id=person.new_id,
                                                                phones=person.phones, story_id=story.id, part=person.part, known_as=[name]))
                character = joined.character
                if person.scene or person.quote:
                    await self.cast.add_scene(user_id, f"{chapter_id[:56]}-p{n}", story, character, SceneIn(
                        text=person.scene or "Appears in a chapter", date=day, quote=person.quote, chapter_id=chapter_id))
                named.append((name, character))
            mentions.append(CastMention(name=name, character_id=character.id if character else None, contact_id=person.contact_id,
                                        scene=person.scene, quote=person.quote))

        async def reach(s: Step) -> tuple[Character | None, str | None]:
            """Who a step is about, and how it shows them: "Name · +91…" when the cast has their number, so the call is a tap away."""
            c = await self.cast.characters.get(user_id, s.character_id) if s.character_id else None
            if c is None and s.contact:
                c = next((ch for nm, ch in named if same_name(nm, s.contact) or same_name(ch.name, s.contact)), None)
            if c is None:
                return None, blank_to_none(s.contact)
            return c, dial_line(c) if c.phones else (blank_to_none(s.contact) or c.name)

        # The work it leaves: ordinary tasks and calls pointing at the story, in the story's workspace.
        tasks: list[Task] = []
        follow_ups: list[FollowUp] = []
        for s in w.steps:
            title = required(s.title, "A step's title")
            sid = s.id or self.env.ids.next()
            who, contact = await reach(s)
            if s.kind == "task":
                made = await self.tasks.get(user_id, sid)
                if made is None:
                    made = await self.tasks.save(user_id, sid, TaskDraft(
                        title=title, workspace=story.workspace, due_date=s.due, estimate=s.minutes or 30, related=ref,
                        contact=contact, contact_id=s.contact_id, character_id=who.id if who else None, notes=f"From “{story.title}”"))
                tasks.append(made)
                notes.append(MarginNote(kind="step", text=title, quote=s.quote, ref=EntityRef(kind="task", id=made.id)))
            else:
                made_f = await self.follow_ups.get(user_id, sid)
                if made_f is None:
                    made_f = await self.follow_ups.save(user_id, sid, FollowUpDraft(
                        title=title, channel=s.channel, workspace=story.workspace, contact=contact,
                        contact_id=s.contact_id, character_id=who.id if who else None, due={"date": s.due} if s.due else None, duration=s.minutes or 15,
                        subject=ref, notes=f"From “{story.title}”"))
                follow_ups.append(made_f)
                notes.append(MarginNote(kind="step", text=title, quote=s.quote, ref=EntityRef(kind="followUp", id=made_f.id)))

        hunches = [Hunch(id=h.id or self.env.ids.next(), text=required(h.text, "A hunch"), quote=h.quote, check_on=h.check_on) for h in w.hunches]
        notes += [MarginNote(kind="hunch", text=h.text, quote=h.quote) for h in hunches]
        notes += [MarginNote(kind="person", text=m.name + (f": {m.scene}" if m.scene else ""), quote=m.quote,
                             ref=EntityRef(kind="character", id=m.character_id) if m.character_id else None) for m in mentions]
        if w.chance_after is not None:
            notes.append(MarginNote(kind="chance", text=f"{story.chance if story.chance is not None else '–'} → {w.chance_after}%"))
        cliff = blank_to_none(w.cliffhanger)
        if cliff:
            notes.append(MarginNote(kind="cliffhanger", text=cliff))

        chapter = await self.chapters.save(user_id, chapter_id, ChapterDraft(
            story_id=story.id, date=day, title=w.title, prose=prose, beat=w.beat,
            source=w.source, chance_after=w.chance_after, notes=notes, hunches=hunches,
            cliffhanger=Cliffhanger(question=cliff) if cliff else None, people=mentions,
        ))

        # Earlier cliffhangers this chapter answers.
        for other_id in w.answers:
            other = await self.chapters.get(user_id, other_id)
            if other and other.story_id == story.id and other.cliffhanger and other.id != chapter.id:
                await self.chapters.update(other.model_copy(update={"cliffhanger": other.cliffhanger.model_copy(update={"answered_in": chapter.id})}))

        update: dict = {"checkpoints": checkpoints}
        if w.chance_after is not None:
            update["chance"] = w.chance_after
        if w.clear_waiting:
            update["waiting_on"] = None
        elif w.waiting_on is not None:
            update["waiting_on"] = w.waiting_on
        if w.beat == "epilogue" and story.state != "ended":
            update.update(state="ended", ended_on=chapter.date)
        story = await self.stories.update(story.model_copy(update=update))
        return Written(chapter, story, tasks, follow_ups)

    async def timeline(self, user_id: str, story_id: str) -> Timeline:
        """The story's chapters with the recorded hearings of its linked cases among them (a hearing is a chapter by
        itself: nothing is written twice), plus where it stands: the current checkpoint and the open work."""
        story = await self.stories.require(user_id, story_id, "Story")
        chapters = await self.stories.chapters_of(user_id, story.id)
        cases = {r.id for r in story.links if r.kind == "case"}
        hearings = [h for h in await self.env.stores.hearing.list(user_id) if is_diary_entry(h) and cases & set(hearing_case_ids(h))] if cases else []
        entries = [TimelineEntry("chapter", c.date, chapter=c) for c in chapters] + [TimelineEntry("hearing", h.date, hearing=h) for h in hearings]
        entries.sort(key=lambda e: (e.date, 0 if e.kind == "hearing" else 1, e.chapter.number if e.chapter else 0))
        ref = EntityRef(kind="story", id=story.id)
        tasks = [t for t in await self.env.stores.task.list(user_id) if ref.same_as(t.related) and t.outcome.status == "open"]
        follow_ups = [f for f in await self.env.stores.follow_up.list(user_id) if ref.same_as(f.subject) and f.outcome.status == "open"]
        hunches = [(c, h) for c in chapters for h in c.hunches if h.came_true is None]
        return Timeline(story, entries, current_checkpoint(story), tasks, follow_ups, open_cliffhangers(chapters), hunches,
                        await self.cast.cast_of(user_id, story.id))
