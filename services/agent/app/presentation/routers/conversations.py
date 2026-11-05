from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import Field
from stiamo_common.models import DomainModel

from app.application.conversations import ConversationService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/conversations", tags=["conversations"])


def _service(request: Request, session: deps.Session) -> ConversationService:
    return deps.build_conversations(request.app, session)


Conversations = Annotated[ConversationService, deps.Depends(_service)]


class CreateBody(DomainModel):
    agent_id: Annotated[str, Field(max_length=64)]
    #: A workspace id, or "all".
    workspace: Annotated[str, Field(max_length=16)]


class SendBody(DomainModel):
    message: Annotated[str, Field(min_length=1, max_length=4000)]


class RenameBody(DomainModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]


@router.get("", summary="The person's conversations, newest first; those of one workspace when asked")
async def list_conversations(svc: Conversations, me: deps.Me, workspace: str | None = None) -> dict[str, Any]:
    return await svc.list(me, workspace)


@router.post("", status_code=201, summary="Start a conversation with an agent")
async def create(body: CreateBody, svc: Conversations, me: deps.Me) -> dict[str, Any]:
    return await svc.create(me, body.agent_id, body.workspace)


@router.get("/{conversation_id}", summary="One conversation in full")
async def get(conversation_id: str, svc: Conversations, me: deps.Me) -> dict[str, Any]:
    return await svc.get(me, conversation_id)


@router.post("/{conversation_id}/messages", summary="Say something in a conversation and get the agent's answer")
async def send(conversation_id: str, body: SendBody, svc: Conversations, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await svc.send(me, token, conversation_id, body.message)


@router.patch("/{conversation_id}", summary="Rename a conversation")
async def rename(conversation_id: str, body: RenameBody, svc: Conversations, me: deps.Me) -> dict[str, Any]:
    return await svc.rename(me, conversation_id, body.title)


@router.delete("/{conversation_id}", status_code=204, summary="Delete a conversation")
async def delete(conversation_id: str, svc: Conversations, me: deps.Me) -> Response:
    await svc.delete(me, conversation_id)
    return Response(status_code=204)
