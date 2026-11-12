"""The cast: characters, their parts in stories and their scenes; joining the cast (found before made), merging two records of one
person, and a character's whole view."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.application.use_cases.cast import CastUseCases, JoinCast
from app.domain.cast import Character, Part, Scene, normalize_phone
from app.domain.common import DomainModel
from app.presentation import deps
from app.presentation.crud_router import crud_router, dump

characters = crud_router(prefix="/v1/characters", tag="cast", model=Character, service_dep=deps.character_service)
parts = crud_router(prefix="/v1/parts", tag="cast", model=Part, service_dep=deps.part_service)
scenes = crud_router(prefix="/v1/scenes", tag="cast", model=Scene, service_dep=deps.scene_service)
#: Registered before the CRUD routers so `/v1/characters/join` isn't read as a character id.
extra = APIRouter(tags=["cast"])

Cast = Annotated[CastUseCases, Depends(deps.cast_service)]


@extra.post("/v1/characters/join", summary="Bring someone into the cast (found by id, phone or Google contact before a new one is made), "
                                           "optionally with their part in a story")
async def join(body: JoinCast, principal: deps.CurrentPrincipal, svc: Cast) -> dict[str, Any]:
    done = await svc.join(principal.user_id, body)
    return {"character": dump(done.character), "part": dump(done.part) if done.part else None, "matched": done.matched}


@extra.get("/v1/characters/by-phone", summary="Who in the cast has this number")
async def by_phone(phone: str, principal: deps.CurrentPrincipal, svc: Cast) -> dict[str, Any]:
    found = await svc.characters.by_phone(principal.user_id, [phone])
    return {"character": dump(found) if found else None, "normalized": normalize_phone(phone)}


class MergeBody(DomainModel):
    into: str


@extra.post("/v1/characters/{character_id}/merge", summary="Two records were one person: move everything of this one into `into`")
async def merge(character_id: str, body: MergeBody, principal: deps.CurrentPrincipal, svc: Cast) -> dict[str, Any]:
    return dump(await svc.characters.merge(principal.user_id, character_id, body.into))


@extra.get("/v1/characters/{character_id}/view", summary="A character with every part they play and their scenes, newest first")
async def view(character_id: str, principal: deps.CurrentPrincipal, svc: Cast) -> dict[str, Any]:
    v = await svc.view(principal.user_id, character_id)
    return {
        "character": dump(v.character),
        "parts": [{**dump(p), "story": {"id": s.id, "title": s.title, "workspace": s.workspace} if s else None} for p, s in v.parts],
        "scenes": [dump(s) for s in v.scenes],
    }


@extra.get("/v1/stories/{story_id}/cast", summary="The characters in a story, with the part each plays")
async def story_cast(story_id: str, principal: deps.CurrentPrincipal, svc: Cast) -> dict[str, Any]:
    await svc.env.stores.story.get(principal.user_id, story_id)
    return {"items": [{"character": dump(c), "part": dump(p)} for p, c in await svc.cast_of(principal.user_id, story_id)]}
