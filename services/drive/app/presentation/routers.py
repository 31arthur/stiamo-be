from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from pydantic import Field
from stiamo_common.models import DomainModel

from app.domain.search import MAX_TERM_CHARS, MAX_TERMS, DriveFile
from app.presentation.deps import CurrentPrincipal, Search

router = APIRouter(prefix="/v1/drive", tags=["drive"])


def dump(model: DomainModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, exclude_none=True, mode="json")


@router.get("/status", summary="Whether Drive search is available to the signed-in user")
async def status(principal: CurrentPrincipal, search: Search) -> dict[str, Any]:
    s = await search.status(principal.email)
    return {k: v for k, v in {"available": s.available, "folder": s.folder, "reason": s.reason}.items() if v is not None}


@router.get("/search", summary="Files whose name or contents contain every word (nothing is copied)")
async def find(
    principal: CurrentPrincipal,
    search: Search,
    q: Annotated[str, Query(max_length=300)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    page_token: Annotated[str | None, Query(alias="pageToken")] = None,
) -> dict[str, Any]:
    page = await search.search(principal.email, q, limit=limit, page_token=page_token)
    out: dict[str, Any] = {"items": [dump(f) for f in page.files]}
    if page.next_page_token:
        out["nextPageToken"] = page.next_page_token
    return out


@router.get("/files/{file_id}/content", summary="The file itself, fetched with the service account (Google's documents come as PDF or xlsx)")
async def content(file_id: str, principal: CurrentPrincipal, search: Search) -> Response:
    c = await search.content(principal.email, file_id)
    return Response(content=c.data, media_type=c.mime_type, headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


class SuggestBody(DomainModel):
    #: What identifies the matter: its case number, the court's number, the parties…
    terms: list[Annotated[str, Field(max_length=MAX_TERM_CHARS * 2)]] = Field(max_length=MAX_TERMS * 3)


@router.post("/suggest", summary="Files that mention any of the given details, best match first")
async def suggest(body: SuggestBody, principal: CurrentPrincipal, search: Search) -> dict[str, Any]:
    matches = await search.suggest(principal.email, body.terms)
    return {"items": [dump(m) for m in matches]}


__all__ = ["DriveFile", "router"]
