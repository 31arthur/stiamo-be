from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field
from stiamo_common.models import DomainModel

from app.application.ports import UserSettings
from app.application.services import Who
from app.domain.themes import clean_theme, clean_themes, render_email
from app.infrastructure.db.repositories import SqlSettingsRepository
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/themes", tags=["themes"])


class ThemesBody(DomainModel):
    themes: list[dict[str, Any]] = Field(max_length=30)


class PreviewBody(DomainModel):
    theme: dict[str, Any]
    body: Annotated[str, Field(max_length=3000)] = "Thank you for your email. We confirm that we will attend, and the delegate details will follow today.\n\nPlease let us know if anything else is needed."
    name: Annotated[str, Field(max_length=80)] = "Priya"


async def _load(request: Request, session: deps.Session, me: Who) -> tuple[SqlSettingsRepository, UserSettings]:
    repo = SqlSettingsRepository(session, request.app.state.box)
    return repo, await repo.get(me.user_id) or UserSettings(me.user_id, me.email)


@router.get("", summary="The organisation themes: how replies to each organisation sound and look")
async def list_themes(request: Request, session: deps.Session, me: deps.Me) -> dict[str, Any]:
    _, s = await _load(request, session, me)
    return {"themes": s.themes}


@router.put("", summary="Replace the set of themes")
async def put_themes(body: ThemesBody, request: Request, session: deps.Session, me: deps.Me) -> dict[str, Any]:
    repo, s = await _load(request, session, me)
    s.themes = clean_themes(body.themes)
    s.email = me.email or s.email
    await repo.save(s)
    return {"themes": s.themes}


@router.post("/preview", summary="A reply as it would look and read in this theme")
async def preview(body: PreviewBody) -> dict[str, Any]:
    theme = clean_theme(body.theme)
    text, html = render_email(theme, body.body, body.name)
    return {"text": text, "html": html, "theme": theme}
