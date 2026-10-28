from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel

from app.application.use_cases.records import (
    DeleteRecord,
    GetRecord,
    ListRecords,
    SaveManyRecords,
    SaveRecord,
)
from app.presentation import deps

router = APIRouter(prefix="/v1/collections", tags=["records"])


class PageOut(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None


class BatchIn(BaseModel):
    items: list[dict[str, Any]]


class BatchOut(BaseModel):
    saved: int


@router.get("/{collection}", response_model=PageOut)
async def list_records(
    collection: str,
    principal: deps.CurrentPrincipal,
    uc: Annotated[ListRecords, Depends(deps.list_uc)],
    after: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    page = await uc(user_id=principal.user_id, collection=collection, after=after, limit=limit)
    return PageOut(items=[r.data for r in page.items], next_cursor=page.next_cursor)


@router.post("/{collection}/batch", response_model=BatchOut)
async def save_many(collection: str, body: BatchIn, principal: deps.CurrentPrincipal,
                    uc: Annotated[SaveManyRecords, Depends(deps.save_many_uc)]):
    return BatchOut(saved=await uc(user_id=principal.user_id, collection=collection, documents=body.items))


@router.get("/{collection}/{record_id}")
async def get_record(collection: str, record_id: str, principal: deps.CurrentPrincipal,
                     uc: Annotated[GetRecord, Depends(deps.get_uc)]) -> dict[str, Any]:
    return (await uc(user_id=principal.user_id, collection=collection, record_id=record_id)).data


@router.put("/{collection}/{record_id}")
async def save_record(collection: str, record_id: str, body: dict[str, Any], principal: deps.CurrentPrincipal,
                      uc: Annotated[SaveRecord, Depends(deps.save_uc)]) -> dict[str, Any]:
    return (await uc(user_id=principal.user_id, collection=collection, record_id=record_id, document=body)).data


@router.delete("/{collection}/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(collection: str, record_id: str, principal: deps.CurrentPrincipal,
                        uc: Annotated[DeleteRecord, Depends(deps.delete_uc)]):
    await uc(user_id=principal.user_id, collection=collection, record_id=record_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
