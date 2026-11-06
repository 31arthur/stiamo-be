from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field
from stiamo_common.models import DomainModel

from app.application.secrets import SecretService
from app.infrastructure.db.repositories import SqlSettingsRepository
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/secrets", tags=["environment"])


def _service(request: Request, session: deps.Session) -> SecretService:
    return SecretService(SqlSettingsRepository(session, request.app.state.box), request.app.state.voice)


Secrets = Annotated[SecretService, deps.Depends(_service)]


class ValueBody(DomainModel):
    value: Annotated[str, Field(max_length=400)]


@router.get("", summary="The environment variables you can set, and which are set (never their values)")
async def list_secrets(secrets: Secrets, me: deps.Me) -> dict[str, Any]:
    return {"items": await secrets.view(me)}


@router.put("/{secret_id}", summary="Set one: it is sealed at once and can't be read back")
async def put_secret(secret_id: str, body: ValueBody, secrets: Secrets, me: deps.Me) -> dict[str, Any]:
    return {"items": await secrets.put(me, secret_id, body.value)}


@router.delete("/{secret_id}", summary="Remove one")
async def remove_secret(secret_id: str, secrets: Secrets, me: deps.Me) -> dict[str, Any]:
    return {"items": await secrets.remove(me, secret_id)}


@router.post("/{secret_id}/test", summary="Check the stored value against the service it is for")
async def test_secret(secret_id: str, secrets: Secrets, me: deps.Me) -> dict[str, Any]:
    return await secrets.test(me, secret_id)
