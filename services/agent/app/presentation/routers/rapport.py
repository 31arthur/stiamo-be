from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.application.rapport import RapportService
from app.domain.rapport import summary
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/rapport", tags=["rapport"])


def _service(request: Request, session: deps.Session) -> RapportService:
    return deps.build_services(request.app, session)[1].rapport_actions


Rapport = deps.Annotated[RapportService, deps.Depends(_service)]


class Do(BaseModel):
    action: str = Field(pattern="^(pause|exclude|tier)$")
    tier: str | None = Field(default=None, pattern="^[ABC]$")


@router.get("", summary="The rapport plans: who, which tier, what was sent, what is next")
async def plans(svc: Rapport, me: deps.Me) -> dict[str, Any]:
    rows = await svc._repo.all(me.user_id)
    out = [{"id": p["id"], "contactId": p.get("contactId"), "name": p["name"], "workspace": p.get("workspace"), "zone": p.get("zone"), "source": p.get("source"), **summary(p)} for p in rows]
    out.sort(key=lambda r: (r["status"] != "active", (r["next"] or {"day": "9999"})["day"]))
    return {"status": await svc.status(me), "plans": out}


@router.post("/{plan_id}/do", summary="Pause a plan, stop it for this person, or change its tier")
async def do(plan_id: str, body: Do, svc: Rapport, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    action = {"pause": "p", "exclude": "x", "tier": "t"}[body.action]
    rest = plan_id[:8] + (f":{body.tier}" if body.action == "tier" and body.tier else "")
    said = await svc.press(me, token, action, rest)
    return {"said": said if isinstance(said, str) else said[0]}
