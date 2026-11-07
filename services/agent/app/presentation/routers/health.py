from typing import Any

from fastapi import APIRouter, Request
from stiamo_common.errors import ValidationFailed

from app.application.health import HealthService
from app.domain.llm import AiError
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/health", tags=["health"])


def _service(request: Request, session: deps.Session) -> HealthService:
    return deps.build_health(request.app, session)


Health = deps.Annotated[HealthService, deps.Depends(_service)]


@router.get("", summary="Every connection the app depends on, and whether it is working")
async def health(svc: Health, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await svc.run(me, token)


@router.post("/mailbox/{account_id}/test", summary="Sign in to a mailbox's server now, and say why if it can't")
async def test_mailbox(account_id: str, request: Request, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    data = request.app.state.data_factory(token)
    accounts = await data.mail_accounts()
    if not any(str(a.get("id")) == account_id for a in accounts):
        raise ValidationFailed("That mailbox isn't connected.")
    try:
        await data.mail_test(account_id)
    except AiError as exc:
        raise ValidationFailed(str(exc)) from exc
    return {"ok": True, "message": "The mail server accepted the sign-in."}
