"""Composition root: wires ports to adapters per request."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.errors import Unauthorized
from stiamo_common.security import Principal, make_principal_dependency

from app.application.channels import ChannelService
from app.application.services import AgentService, Who
from app.config import get_settings
from app.infrastructure.db.repositories import SqlChannelRepository, SqlSettingsRepository

current_principal = make_principal_dependency(lambda: get_settings().token_settings())
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]

_bearer = HTTPBearer(auto_error=False)


async def bearer_token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str:
    """The caller's own access token, passed on so the other services answer as they would to the browser."""
    if creds is None:
        raise Unauthorized("Missing bearer token.")
    return creds.credentials


Token = Annotated[str, Depends(bearer_token)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]


def build_services(app: FastAPI, session: AsyncSession) -> tuple[AgentService, ChannelService]:
    s = app.state
    agent = AgentService(SqlSettingsRepository(session, s.box), s.llm_factory, s.decision_factory, s.data_factory, s.settings.max_steps)
    channels = ChannelService(SqlChannelRepository(session, s.box), agent, s.telegram, s.twilio, s.tokens, public_url=s.settings.public_url, pairing_minutes=s.settings.pairing_minutes, memory=s.chat_memory)
    return agent, channels


def agent_service(request: Request, session: Session) -> AgentService:
    return build_services(request.app, session)[0]


def channel_service(request: Request, session: Session) -> ChannelService:
    return build_services(request.app, session)[1]


def who(principal: CurrentPrincipal) -> Who:
    return Who(principal.user_id, principal.email)


Agent = Annotated[AgentService, Depends(agent_service)]
Channels = Annotated[ChannelService, Depends(channel_service)]
Me = Annotated[Who, Depends(who)]
