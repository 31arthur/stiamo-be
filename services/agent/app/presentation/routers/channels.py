from typing import Annotated, Any

from fastapi import APIRouter, Response, status
from pydantic import Field
from stiamo_common.models import DomainModel

from app.presentation import deps

router = APIRouter(prefix="/v1/agent/channels", tags=["channels"])


class TelegramBody(DomainModel):
    bot_token: Annotated[str, Field(max_length=200)]


class WhatsAppBody(DomainModel):
    account_sid: Annotated[str, Field(max_length=100)]
    auth_token: Annotated[str, Field(max_length=200)]
    #: The Twilio WhatsApp number to send from (the sandbox number, or your own).
    sender: Annotated[str, Field(max_length=40)]
    #: Who may use the agent: your own number(s).
    allowed: list[Annotated[str, Field(max_length=40)]] = Field(max_length=10)


class AllowedBody(DomainModel):
    allowed: list[Annotated[str, Field(max_length=40)]] = Field(max_length=10)


@router.get("", summary="The connected channels")
async def list_channels(channels: deps.Channels, me: deps.Me) -> dict[str, Any]:
    return {"items": await channels.all_for(me)}


@router.post("/telegram", status_code=status.HTTP_201_CREATED, summary="Connect a Telegram bot (sets its webhook)")
async def connect_telegram(body: TelegramBody, channels: deps.Channels, me: deps.Me) -> dict[str, Any]:
    return await channels.connect_telegram(me, body.bot_token)


@router.post("/whatsapp", status_code=status.HTTP_201_CREATED, summary="Connect WhatsApp through Twilio")
async def connect_whatsapp(body: WhatsAppBody, channels: deps.Channels, me: deps.Me) -> dict[str, Any]:
    return await channels.connect_whatsapp(me, body.account_sid, body.auth_token, body.sender, body.allowed)


@router.post("/{channel_id}/pairing", summary="A fresh code to pair a Telegram chat")
async def pairing(channel_id: str, channels: deps.Channels, me: deps.Me) -> dict[str, Any]:
    return await channels.pairing_code(me, channel_id)


@router.delete("/{channel_id}/pairing/{chat_id}", summary="Unlink a Telegram chat")
async def unpair(channel_id: str, chat_id: str, channels: deps.Channels, me: deps.Me) -> dict[str, Any]:
    return await channels.unpair(me, channel_id, chat_id)


@router.put("/{channel_id}/allowed", summary="Who may use a WhatsApp channel")
async def allowed(channel_id: str, body: AllowedBody, channels: deps.Channels, me: deps.Me) -> dict[str, Any]:
    return await channels.set_allowed(me, channel_id, body.allowed)


@router.post("/{channel_id}/test", status_code=status.HTTP_204_NO_CONTENT, summary="Send a hello to the linked chat or number")
async def test(channel_id: str, channels: deps.Channels, me: deps.Me) -> Response:
    await channels.send_test(me, channel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect a channel")
async def remove(channel_id: str, channels: deps.Channels, me: deps.Me) -> Response:
    await channels.remove(me, channel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
