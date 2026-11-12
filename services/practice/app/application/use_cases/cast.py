"""The cast: characters, the parts they play in stories, and their scenes.

Joining the cast finds the person before it makes one: by id, then by phone number, then by Google contact, so the same person met in
an S&B case and a personal errand is one character with two parts. A part and its scenes always take their story's workspace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field
from stiamo_common.errors import Conflict, ValidationFailed

from app.application.crud import Crud
from app.application.ports import Env
from app.domain.cast import Character, Part, Scene, SceneSource, Stance, Sway, normalize_phone, same_name
from app.domain.common import DomainModel, ISODate, Workspace, draft_of
from app.domain.rules import blank_to_none, required, unique, valid_email
from app.domain.stories import Story

CharacterDraft = draft_of(Character)
PartDraft = draft_of(Part)
SceneDraft = draft_of(Scene)


def clean_phones(phones: list[str]) -> list[str]:
    return unique([n for n in (normalize_phone(p) for p in phones) if n])


def normalize_character(c: Character) -> Character:
    return c.model_copy(update={
        "name": required(c.name, "Name"), "organisation": blank_to_none(c.organisation), "title": blank_to_none(c.title),
        "phones": clean_phones(c.phones), "emails": unique([e.lower() for e in (valid_email(x) for x in c.emails) if e]),
        "google_contact_id": blank_to_none(c.google_contact_id),
    })


def normalize_part(p: Part) -> Part:
    names = unique([" ".join(k.split()) for k in p.known_as if k.strip()])
    return p.model_copy(update={"part": (p.part or "").strip(), "known_as": names[:12]})


def normalize_scene(s: Scene) -> Scene:
    return s.model_copy(update={"text": required(s.text, "What they did"), "quote": blank_to_none(s.quote)})


class CharacterUseCases(Crud[Character]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "character", Character, env.stores.character, normalize=normalize_character)

    async def by_phone(self, user_id: str, phones: list[str], *, besides: str | None = None) -> Character | None:
        wanted = set(clean_phones(phones))
        if not wanted:
            return None
        return next((c for c in await self.repo.list(user_id) if c.id != besides and wanted & set(c.phones)), None)

    async def save(self, user_id, id, draft):
        clash = await self.by_phone(user_id, draft.phones, besides=id)
        if clash is not None:
            raise Conflict(f"That number is already {clash.name}'s in your cast.")
        return await super().save(user_id, id, draft)

    async def remove(self, user_id: str, id: str) -> None:
        s = self.env.stores
        for p in [p for p in await s.part.list(user_id) if p.character_id == id]:
            await s.part.remove(user_id, p.id)
        for sc in [x for x in await s.scene.list(user_id) if x.character_id == id]:
            await s.scene.remove(user_id, sc.id)
        await _retie(self.env, user_id, id, None)
        await super().remove(user_id, id)

    async def merge(self, user_id: str, source_id: str, into_id: str) -> Character:
        """Two records turned out to be one person: everything of `source` moves to `into`, and `source` goes."""
        if source_id == into_id:
            raise ValidationFailed("A character can't be merged into itself")
        source = await self.require(user_id, source_id, "Character")
        into = await self.require(user_id, into_id, "Character")
        s = self.env.stores
        parts = await s.part.list(user_id)
        kept = {p.story_id: p for p in parts if p.character_id == into.id}
        for p in [p for p in parts if p.character_id == source.id]:
            if p.story_id in kept:
                k = kept[p.story_id]
                await s.part.save(normalize_part(k.model_copy(update={"known_as": [*k.known_as, *p.known_as, source.name],
                                                                       "part": k.part or p.part, "stance": k.stance or p.stance, "sway": k.sway or p.sway})))
                await s.part.remove(user_id, p.id)
            else:
                await s.part.save(normalize_part(p.model_copy(update={"character_id": into.id, "known_as": [*p.known_as, source.name]})))
        for sc in [x for x in await s.scene.list(user_id) if x.character_id == source.id]:
            await s.scene.save(sc.model_copy(update={"character_id": into.id}))
        await _retie(self.env, user_id, source.id, into.id)
        await self.repo.remove(user_id, source.id)
        await self.log.deleted(user_id, "character", source)
        return await self.update(into.model_copy(update={
            "phones": [*into.phones, *source.phones], "emails": [*into.emails, *source.emails],
            "organisation": into.organisation or source.organisation, "title": into.title or source.title,
            "google_contact_id": into.google_contact_id or source.google_contact_id,
        }))


async def _retie(env: Env, user_id: str, old: str, new: str | None) -> None:
    """Tasks and calls that pointed at a character follow it (merged), or let go of it (deleted)."""
    for t in [t for t in await env.stores.task.list(user_id) if t.character_id == old]:
        await env.stores.task.save(t.model_copy(update={"character_id": new}))
    for f in [f for f in await env.stores.follow_up.list(user_id) if f.character_id == old]:
        await env.stores.follow_up.save(f.model_copy(update={"character_id": new}))


async def _story(env: Env, user_id: str, story_id: str) -> Story:
    story = await env.stores.story.get(user_id, story_id)
    if story is None:
        raise ValidationFailed("That story no longer exists")
    return story


class PartUseCases(Crud[Part]):
    """A part takes its story's workspace, whatever was sent, and a character has one part per story."""

    def __init__(self, env: Env) -> None:
        super().__init__(env, "part", Part, env.stores.part, normalize=normalize_part)

    async def save(self, user_id, id, draft):
        story = await _story(self.env, user_id, draft.story_id)
        if await self.env.stores.character.get(user_id, draft.character_id) is None:
            raise ValidationFailed("That character no longer exists")
        other = next((p for p in await self.repo.list(user_id) if p.story_id == story.id and p.character_id == draft.character_id and p.id != id), None)
        if other is not None:
            raise Conflict("They already have a part in this story.")
        return await super().save(user_id, id, draft.model_copy(update={"workspace": story.workspace}))


class SceneUseCases(Crud[Scene]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "scene", Scene, env.stores.scene, normalize=normalize_scene)

    async def save(self, user_id, id, draft):
        story = await _story(self.env, user_id, draft.story_id)
        if await self.env.stores.character.get(user_id, draft.character_id) is None:
            raise ValidationFailed("That character no longer exists")
        return await super().save(user_id, id, draft.model_copy(update={"workspace": story.workspace}))


# --- joining the cast -------------------------------------------------------------------------------------------


class JoinCast(DomainModel):
    """Someone entering the cast, and (with a story) the part they play in it. Found before made: by `character_id`, by a phone number,
    by Google contact; `id` fixes a new character's id so trying again never makes two."""

    name: str
    character_id: str | None = None
    id: str | None = None
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    organisation: str | None = None
    title: str | None = None
    google_contact_id: str | None = None
    home: Workspace | None = None
    story_id: str | None = None
    part: str | None = None
    known_as: list[str] = Field(default_factory=list)
    stance: Stance | None = None
    sway: Sway | None = None


@dataclass
class Joined:
    character: Character
    part: Part | None
    matched: Literal["id", "phone", "google", "new"]


class SceneIn(DomainModel):
    text: str
    date: ISODate
    quote: str | None = None
    chapter_id: str | None = None
    source: SceneSource = "chapter"


@dataclass
class CharacterView:
    character: Character
    parts: list[tuple[Part, Story | None]] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)


class CastUseCases:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.characters = CharacterUseCases(env)
        self.parts = PartUseCases(env)
        self.scenes = SceneUseCases(env)

    async def join(self, user_id: str, j: JoinCast) -> Joined:
        story = await _story(self.env, user_id, j.story_id) if j.story_id else None
        found: Character | None = None
        matched: Literal["id", "phone", "google", "new"] = "new"
        if j.character_id:
            found, matched = await self.characters.require(user_id, j.character_id, "Character"), "id"
        if found is None and (found := await self.characters.by_phone(user_id, j.phones)) is not None:
            matched = "phone"
        if found is None and j.google_contact_id:
            found = next((c for c in await self.characters.repo.list(user_id) if c.google_contact_id == j.google_contact_id), None)
            matched = "google" if found else matched
        if found is None and j.id and (found := await self.characters.get(user_id, j.id)) is not None:
            matched = "id"
        if found is None:
            found = await self.characters.save(user_id, j.id, CharacterDraft(
                name=j.name, phones=j.phones, emails=j.emails, organisation=j.organisation, title=j.title,
                google_contact_id=j.google_contact_id, home=(story.workspace if story else j.home) or "personal"))
        else:
            # What we learn about someone we already know is added, never overwritten: a second number, an organisation we lacked.
            phones = clean_phones([*found.phones, *j.phones])
            emails = unique([*found.emails, *[e.lower() for e in j.emails if e]])
            more = {"phones": phones, "emails": emails, "organisation": found.organisation or blank_to_none(j.organisation),
                    "title": found.title or blank_to_none(j.title), "google_contact_id": found.google_contact_id or blank_to_none(j.google_contact_id)}
            if any(getattr(found, k) != v for k, v in more.items()):
                found = await self.characters.update(found.model_copy(update=more))
        part = None
        if story is not None:
            part = await self.ensure_part(user_id, story, found, j)
        return Joined(found, part, matched)

    async def ensure_part(self, user_id: str, story: Story, c: Character, j: JoinCast | None = None) -> Part:
        names = [n for n in [*(j.known_as if j else []), j.name if j else None] if n and not same_name(n, c.name)]
        existing = next((p for p in await self.parts.repo.list(user_id) if p.story_id == story.id and p.character_id == c.id), None)
        if existing is None:
            return await self.parts.save(user_id, None, PartDraft(
                character_id=c.id, story_id=story.id, part=(j.part if j else None) or "", known_as=names,
                stance=j.stance if j else None, sway=j.sway if j else None))
        update = {"known_as": [*existing.known_as, *names]}
        if j and j.part:
            update["part"] = j.part
        if j and j.stance:
            update["stance"] = j.stance
        if j and j.sway:
            update["sway"] = j.sway
        revised = normalize_part(existing.model_copy(update=update))
        return existing if revised == existing else await self.parts.update(revised)

    async def add_scene(self, user_id: str, scene_id: str, story: Story, c: Character, s: SceneIn) -> Scene:
        return await self.scenes.save(user_id, scene_id, SceneDraft(
            character_id=c.id, story_id=story.id, chapter_id=s.chapter_id, date=s.date, text=s.text, quote=s.quote, source=s.source))

    async def cast_of(self, user_id: str, story_id: str) -> list[tuple[Part, Character]]:
        chars = {c.id: c for c in await self.characters.repo.list(user_id)}
        return [(p, chars[p.character_id]) for p in await self.parts.repo.list(user_id) if p.story_id == story_id and p.character_id in chars]

    async def view(self, user_id: str, character_id: str) -> CharacterView:
        c = await self.characters.require(user_id, character_id, "Character")
        stories = {s.id: s for s in await self.env.stores.story.list(user_id)}
        parts = [(p, stories.get(p.story_id)) for p in await self.parts.repo.list(user_id) if p.character_id == c.id]
        scenes = sorted((s for s in await self.scenes.repo.list(user_id) if s.character_id == c.id), key=lambda s: s.date, reverse=True)
        return CharacterView(c, parts, scenes)
