"""One REST resource per aggregate: list (paged), get, create, replace, delete.

The JSON is camelCase and leaves out unset optional fields, so it matches the frontend's domain types.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from stiamo_common.errors import NotFound

from app.domain.common import DomainModel, Entity, draft_of
from app.presentation.deps import CurrentPrincipal


def dump(model: DomainModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, exclude_none=True, mode="json")


def crud_router(*, prefix: str, tag: str, model: type[Entity], service_dep) -> APIRouter:
    draft = draft_of(model)
    router = APIRouter(prefix=prefix, tags=[tag])
    service = Depends(service_dep)

    @router.get("", summary=f"List {tag}")
    async def list_records(
        principal: CurrentPrincipal,
        svc=service,
        after: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        updated_after: Annotated[str | None, Query(alias="updatedAfter")] = None,
    ) -> dict[str, Any]:
        items = await svc.list(principal.user_id, after=after, limit=limit + 1, updated_after=updated_after)
        more = len(items) > limit
        items = items[:limit]
        return {"items": [dump(i) for i in items], "nextCursor": items[-1].id if more and items else None}

    @router.get("/{record_id}", response_model=model, response_model_exclude_none=True, summary=f"Get one of {tag}")
    async def get_record(record_id: str, principal: CurrentPrincipal, svc=service):
        found = await svc.get(principal.user_id, record_id)
        if found is None:
            raise NotFound(f"{model.__name__} not found.")
        return found

    @router.post("", response_model=model, response_model_exclude_none=True, status_code=status.HTTP_201_CREATED,
                 summary=f"Create one of {tag}")
    async def create_record(body: draft, principal: CurrentPrincipal, svc=service):  # type: ignore[valid-type]
        return await svc.save(principal.user_id, body.id, body)

    @router.put("/{record_id}", response_model=model, response_model_exclude_none=True,
                summary=f"Create or replace one of {tag}")
    async def put_record(record_id: str, body: draft, principal: CurrentPrincipal, svc=service):  # type: ignore[valid-type]
        return await svc.save(principal.user_id, record_id, body)

    @router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT, summary=f"Delete one of {tag}")
    async def delete_record(record_id: str, principal: CurrentPrincipal, svc=service):
        await svc.remove(principal.user_id, record_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
