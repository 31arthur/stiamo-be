from typing import Any

from fastapi import APIRouter, Request

from app.presentation import deps

router = APIRouter(prefix="/v1/agent/alerts", tags=["alerts"])


@router.post("/test", summary="Send a sample urgent-email alert to the connected Telegram chats, to see it and try the buttons")
async def test_alert(request: Request, session: deps.Session, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    _, channels = deps.build_services(request.app, session)
    alert = await channels.alerts.sample(me, token)
    return {"sent": len(alert.chats) if alert else 0}
