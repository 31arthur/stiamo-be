"""Evidence about people, the person's own lenses, and the profile worked out from them (see domain/insight.py)."""

from __future__ import annotations

import re

from stiamo_common.errors import ValidationFailed

from app.application.crud import Crud
from app.application.ports import Env
from app.domain.insight import LENSES, Evidence, Lens, Verdict, profile_of
from app.domain.rules import blank_to_none, required

_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def normalize_evidence(e: Evidence) -> Evidence:
    lens = (e.lens or "").strip()
    if lens not in LENSES and not lens.startswith("custom:"):
        raise ValidationFailed(f"The lens is one of {', '.join(LENSES)}, or custom:<lens id>")
    label = (e.label or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not _LABEL.match(label):
        raise ValidationFailed("A label is a short word, like goes_quiet")
    if e.source == "prediction" and e.verdict is None:
        e = e.model_copy(update={"verdict": "pending"})
    return e.model_copy(update={"lens": lens, "label": label, "quote": blank_to_none(e.quote), "about": blank_to_none(e.about)})


def normalize_lens(x: Lens) -> Lens:
    return x.model_copy(update={"name": required(x.name, "Name"), "pole_a": required(x.pole_a, "One end"), "pole_b": required(x.pole_b, "The other end")})


class ProfileUseCases:
    def __init__(self, env: Env) -> None:
        self.env = env

    async def refresh(self, user_id: str, character_id: str) -> dict:
        """Works the profile out again and keeps it on the character (quietly: it is derived, so it isn't history)."""
        s = self.env.stores
        c = await s.character.get(user_id, character_id)
        if c is None:
            raise ValidationFailed("That character no longer exists")
        evidence = [e for e in await s.evidence.list(user_id) if e.character_id == c.id]
        calls = [f for f in await s.follow_up.list(user_id) if f.character_id == c.id]
        profile = profile_of(evidence, calls, await s.lens.list(user_id), self.env.clock.now().date().isoformat())
        await s.character.save(c.model_copy(update={"profile": profile}))
        return profile


class EvidenceUseCases(Crud[Evidence]):
    """Evidence takes its story's workspace (or, without a story, the person's home), and every change refreshes their profile."""

    def __init__(self, env: Env) -> None:
        super().__init__(env, "evidence", Evidence, env.stores.evidence, normalize=normalize_evidence)
        self.profiles = ProfileUseCases(env)

    async def save(self, user_id, id, draft):
        c = await self.env.stores.character.get(user_id, draft.character_id)
        if c is None:
            raise ValidationFailed("That character no longer exists")
        workspace = c.home
        if draft.story_id:
            story = await self.env.stores.story.get(user_id, draft.story_id)
            if story is None:
                raise ValidationFailed("That story no longer exists")
            workspace = story.workspace
        saved = await super().save(user_id, id, draft.model_copy(update={"workspace": workspace}))
        await self.profiles.refresh(user_id, saved.character_id)
        return saved

    async def remove(self, user_id: str, id: str) -> None:
        before = await self.repo.get(user_id, id)
        await super().remove(user_id, id)
        if before is not None:
            await self.profiles.refresh(user_id, before.character_id)

    async def settle(self, user_id: str, id: str, verdict: Verdict) -> Evidence:
        e = await self.require(user_id, id, "Evidence")
        if e.source != "prediction":
            raise ValidationFailed("Only a prediction can be settled")
        saved = await self.update(e.model_copy(update={"verdict": verdict}))
        await self.profiles.refresh(user_id, e.character_id)
        return saved


class LensUseCases(Crud[Lens]):
    def __init__(self, env: Env) -> None:
        super().__init__(env, "lens", Lens, env.stores.lens, normalize=normalize_lens)

    async def remove(self, user_id: str, id: str) -> None:
        touched = {e.character_id for e in await self.env.stores.evidence.list(user_id) if e.lens == f"custom:{id}"}
        for e in [e for e in await self.env.stores.evidence.list(user_id) if e.lens == f"custom:{id}"]:
            await self.env.stores.evidence.remove(user_id, e.id)
        await super().remove(user_id, id)
        for cid in touched:
            await ProfileUseCases(self.env).refresh(user_id, cid)
