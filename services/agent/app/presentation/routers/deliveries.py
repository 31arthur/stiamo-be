from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from app.presentation import deps

router = APIRouter(prefix="/v1/agent/deliveries", tags=["deliveries"])


@router.get("", summary="What was sent to your phone (alerts, reminders, calls) and whether it got there, newest first")
async def deliveries(request: Request, me: deps.Me, limit: Annotated[int, Query(ge=1, le=200)] = 50, failed: bool = False) -> dict[str, Any]:
    log = request.app.state.deliveries
    iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None  # noqa: E731
    items = [
        {"id": d.id, "kind": d.kind, "channel": d.channel, "target": d.target, "ok": d.ok, "error": d.error, "summary": d.summary, "workspace": d.workspace, "at": iso(d.created_at)}
        for d in await log.recent(me.user_id, limit, failed)
    ]
    return {"items": items, "last24h": await log.counts(me.user_id, datetime.now(UTC) - timedelta(hours=24))}
