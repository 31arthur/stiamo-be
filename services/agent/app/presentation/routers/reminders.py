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
    #: Ignored: reminders are no longer sent as voice messages (a real call is what rings you). Kept so an older screen can still save.
    voice: bool = False
    #: Also ring me on Telegram (through CallMeBot), reading the reminder aloud.
    call: bool = False
    #: Who to ring: a Telegram @username, or a number with its country code.
    call_user: Annotated[str, Field(max_length=64)] = ""
    #: A Google standard voice, like en-GB-Standard-B.
    call_voice: Annotated[str, Field(max_length=40)] = ""


@router.get("", summary="Whether reminders about calls not marked done are on, and whether they ring you")
async def get_settings(reminders: Reminders, me: deps.Me) -> dict[str, Any]:
    return await reminders.view(me)


@router.put("", summary="Switch the reminders on or off")
async def set_settings(body: SettingsBody, reminders: Reminders, me: deps.Me, request: Request) -> dict[str, Any]:
    out = await reminders.set_settings(me, body.enabled, body.call, body.call_user, body.call_voice)
    deps.sync_reminders(request.app, me, out["enabled"])
    return out


class TestCallBody(DomainModel):
    #: Who to ring, to try it before saving; the saved one when left out.
    call_user: Annotated[str | None, Field(max_length=64)] = None


@router.post("/test-call", summary="Ring now with a short test message, and say why if the call can't be placed")
async def test_call(reminders: Reminders, me: deps.Me, body: TestCallBody | None = None) -> dict[str, Any]:
    return await reminders.test_call(me, body.call_user if body else None)


@router.post("/test", summary="Send a sample reminder to the connected chats")
async def test(reminders: Reminders, me: deps.Me) -> dict[str, Any]:
    return await reminders.test(me)


@router.post("/check", summary="Check now whether a reminder is due, and send it if so")
async def check(reminders: Reminders, me: deps.Me) -> dict[str, Any]:
    return {"reminded": await reminders.check(me)}
