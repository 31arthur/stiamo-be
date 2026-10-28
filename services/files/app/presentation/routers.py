from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status
from stiamo_common.errors import ValidationFailed
from stiamo_common.models import DomainModel

from app.domain.attachment import Attachment, EntityKind, EntityRef, is_inline_safe
from app.infrastructure import signing
from app.presentation.deps import BearerToken, CurrentPrincipal, Service

router = APIRouter(tags=["attachments"])


def dump(a: DomainModel) -> dict[str, Any]:
    return a.model_dump(by_alias=True, exclude_none=True, mode="json")


# --- adding --------------------------------------------------------------------------------------------------


@router.post("/v1/attachments", response_model=Attachment, response_model_exclude_none=True,
             status_code=status.HTTP_201_CREATED, summary="Upload a file to a record")
async def upload(
    request: Request, principal: CurrentPrincipal, token: BearerToken, svc: Service,
    file: Annotated[UploadFile, File()],
    owner_kind: Annotated[EntityKind, Form(alias="ownerKind")],
    owner_id: Annotated[str, Form(alias="ownerId")],
    owner_title: Annotated[str, Form(alias="ownerTitle")] = "",
):
    limit = request.app.state.settings.max_bytes
    data = await file.read(limit + 1)  # one byte past the limit is enough to know it is too big
    return await svc.attach(principal.user_id, token, EntityRef(kind=owner_kind, id=owner_id), owner_title,
                            file.filename or "file", file.content_type or "", data)


class LinkBody(DomainModel):
    owner: EntityRef
    owner_title: str = ""
    url: str
    name: str | None = None


@router.post("/v1/attachments/links", response_model=Attachment, response_model_exclude_none=True,
             status_code=status.HTTP_201_CREATED, summary="Attach a link to a document kept elsewhere")
async def attach_link(body: LinkBody, principal: CurrentPrincipal, token: BearerToken, svc: Service):
    return await svc.attach_link(principal.user_id, token, body.owner, body.owner_title, body.url, body.name)


# --- reading -------------------------------------------------------------------------------------------------


@router.get("/v1/attachments", summary="Attachments, optionally of one record")
async def list_attachments(
    principal: CurrentPrincipal, svc: Service, kind: EntityKind | None = None, id: str | None = None,
    after: str | None = None, limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, Any]:
    if (kind is None) != (id is None):
        raise ValidationFailed("Give both kind and id, or neither")
    owner = EntityRef(kind=kind, id=id) if kind and id else None
    items = await svc.list(principal.user_id, owner, after, limit + 1)
    more = len(items) > limit
    items = items[:limit]
    return {"items": [dump(a) for a in items], "nextCursor": items[-1].id if more and items else None}


@router.get("/v1/attachments/{attachment_id}", response_model=Attachment, response_model_exclude_none=True,
            summary="One attachment's details")
async def get_attachment(attachment_id: str, principal: CurrentPrincipal, svc: Service):
    return await svc.require(principal.user_id, attachment_id)


def _file_response(a: Attachment, data: bytes) -> Response:
    disposition = "inline" if is_inline_safe(a.mime) else "attachment"
    return Response(content=data, media_type=a.mime, headers={
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(a.name)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "private, max-age=300",
    })


@router.get("/v1/attachments/{attachment_id}/content", summary="Download a file (needs the Authorization header)")
async def content(attachment_id: str, principal: CurrentPrincipal, svc: Service):
    return _file_response(*await svc.content(principal.user_id, attachment_id))


@router.post("/v1/attachments/{attachment_id}/url",
             summary="A short-lived address for showing a file in an <img> or <iframe>")
async def share_url(attachment_id: str, request: Request, principal: CurrentPrincipal, svc: Service) -> dict[str, Any]:
    a = await svc.require(principal.user_id, attachment_id)
    if a.link:
        return {"url": a.link.url, "expiresIn": None}
    s = request.app.state.settings
    token = signing.sign(s.signing_secret, principal.user_id, a.id, s.url_ttl_seconds)
    return {"url": f"/v1/files/{token}", "expiresIn": s.url_ttl_seconds}


@router.get("/v1/files/{token}", summary="Fetch a file with a short-lived address (no Authorization header)")
async def shared_file(token: str, request: Request, svc: Service):
    user_id, attachment_id = signing.verify(request.app.state.settings.signing_secret, token)
    return _file_response(*await svc.content(user_id, attachment_id))


# --- changing ------------------------------------------------------------------------------------------------


class CaptionBody(DomainModel):
    caption: str


@router.patch("/v1/attachments/{attachment_id}", response_model=Attachment, response_model_exclude_none=True,
              summary="Rename (caption) an attachment")
async def rename(attachment_id: str, body: CaptionBody, principal: CurrentPrincipal, svc: Service):
    return await svc.rename(principal.user_id, attachment_id, body.caption)


@router.delete("/v1/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove an attachment")
async def remove(attachment_id: str, principal: CurrentPrincipal, token: BearerToken, svc: Service,
                 owner_title: Annotated[str, Query(alias="ownerTitle")] = ""):
    await svc.remove(principal.user_id, token, attachment_id, owner_title)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- housekeeping, called by the practice service ------------------------------------------------------------


@router.delete("/v1/attachments", summary="Remove every attachment of a record (it was deleted)")
async def remove_owner(principal: CurrentPrincipal, svc: Service, kind: EntityKind, id: str) -> dict[str, int]:
    return {"removed": await svc.remove_owner(principal.user_id, EntityRef(kind=kind, id=id))}


class TransferBody(DomainModel):
    source: EntityRef
    target: EntityRef


@router.post("/v1/attachments/transfer", summary="Move every attachment of one record to another (they were merged)")
async def transfer(body: TransferBody, principal: CurrentPrincipal, svc: Service) -> dict[str, int]:
    return {"moved": await svc.transfer(principal.user_id, body.source, body.target)}
