from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field
from stiamo_common.models import DomainModel

from app.application.reminders import ReminderService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/reminders", tags=["reminders"])


def _service(request: Request, session: deps.Session) -> ReminderService:
    return deps.build_reminders(request.app, session)


Reminders = Annotated[ReminderService, deps.Depends(_service)]


class SettingsBody(DomainModel):
    enabled: bool
    #: Also speak it, as a voice message on Telegram (needs a Deepgram key).
    voice: bool = True


class KeyBody(DomainModel):
    api_key: Annotated[str, Field(max_length=300)]


@router.get("", summary="Whether reminders about calls not marked done are on, and whether they are spoken")
async def get_settings(reminders: Reminders, me: deps.Me) -> dict[str, Any]:
    return await reminders.view(me)


@router.put("", summary="Switch the reminders on or off")
async def set_settings(body: SettingsBody, reminders: Reminders, me: deps.Me, request: Request) -> dict[str, Any]:
    out = await reminders.set_settings(me, body.enabled, body.voice)
    deps.sync_reminders(request.app, me, out["enabled"])
    return out


@router.put("/voice-key", summary="Keep a Deepgram key, so reminders can be spoken")
async def put_key(body: KeyBody, reminders: Reminders, me: deps.Me) -> dict[str, Any]:
    return await reminders.put_key(me, body.api_key)


@router.delete("/voice-key", summary="Forget the Deepgram key")
async def remove_key(reminders: Reminders, me: deps.Me) -> dict[str, Any]:
    return await reminders.remove_key(me)


@router.post("/test", summary="Send a sample reminder to the connected chats")
async def test(reminders: Reminders, me: deps.Me) -> dict[str, Any]:
    return await reminders.test(me)


@router.post("/check", summary="Check now whether a reminder is due, and send it if so")
async def check(reminders: Reminders, me: deps.Me) -> dict[str, Any]:
    return {"reminded": await reminders.check(me)}
