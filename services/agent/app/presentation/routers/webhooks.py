"""Where Telegram and Twilio deliver messages. These are public, so each one proves who sent it before anything runs."""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, FastAPI, Header, Request, Response
from stiamo_common.errors import Forbidden

from app.infrastructure.messaging import valid_twilio_signature
from app.presentation.deps import build_services

router = APIRouter(prefix="/v1/agent/webhooks", tags=["webhooks"])


async def _telegram(app: FastAPI, channel_id: str, update: dict[str, Any]) -> None:
    async with app.state.session_factory() as session:
        _, channels = build_services(app, session)
        c = await channels.lookup(channel_id)
        if c is not None:
            await channels.handle_telegram(c, update)
        await session.commit()


async def _whatsapp(app: FastAPI, channel_id: str, sender: str, body: str) -> None:
    async with app.state.session_factory() as session:
        _, channels = build_services(app, session)
        c = await channels.lookup(channel_id)
        if c is not None:
            await channels.handle_whatsapp(c, sender, body)
        await session.commit()


@router.post("/telegram/{channel_id}", summary="Telegram delivers a message here")
async def telegram(channel_id: str, request: Request, background: BackgroundTasks, secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None) -> dict[str, bool]:
    async with request.app.state.session_factory() as session:
        _, channels = build_services(request.app, session)
        channels.check_telegram(await channels.lookup(channel_id), secret)
    update = await request.json()
    # Answered after the reply to Telegram, which wants an answer within seconds and retries if it doesn't get one.
    background.add_task(_telegram, request.app, channel_id, update if isinstance(update, dict) else {})
    return {"ok": True}


@router.post("/whatsapp/{channel_id}", summary="Twilio delivers a WhatsApp message here")
async def whatsapp(channel_id: str, request: Request, background: BackgroundTasks, signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    async with request.app.state.session_factory() as session:
        _, channels = build_services(request.app, session)
        c = channels.check_whatsapp(await channels.lookup(channel_id))
    # Twilio signs the address it called, which behind the proxy is the public one, not the one this server sees.
    url = f"{request.app.state.settings.public_url.rstrip('/')}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
    if not valid_twilio_signature(c.secrets.get("authToken", ""), url, form, signature):
        raise Forbidden("Bad signature.")
    background.add_task(_whatsapp, request.app, channel_id, form.get("From", ""), form.get("Body", ""))
    # An empty reply: the answer is sent as its own message, because the agent can take longer than Twilio waits.
    return Response("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response></Response>", media_type="text/xml")
