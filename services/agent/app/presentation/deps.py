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
from app.application.workflows import WorkflowService
from app.config import get_settings
from app.infrastructure.db.repositories import SqlChannelRepository, SqlSettingsRepository
from app.infrastructure.db.workflows import SqlWorkflowRepository

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
    agent = AgentService(SqlSettingsRepository(session, s.box), s.registry, s.runs, SqlWorkflowRepository(session), s.llm_factory, s.decision_factory, s.data_factory, s.settings.max_steps)
    channels = ChannelService(SqlChannelRepository(session, s.box), agent, s.telegram, s.twilio, s.tokens, public_url=s.settings.public_url, pairing_minutes=s.settings.pairing_minutes, memory=s.chat_memory)
    return agent, channels


def build_workflow_service(app: FastAPI, session: AsyncSession) -> WorkflowService:
    s = app.state
    return WorkflowService(SqlWorkflowRepository(session), SqlSettingsRepository(session, s.box), s.runs, s.llm_factory, s.decision_factory, s.data_factory, s.running, s.sweeps.is_active)


def workflow_service(request: Request, session: Session) -> WorkflowService:
    return build_workflow_service(request.app, session)


async def sync_listener(app: FastAPI, who: Who, workflow_id: str, config: dict, resolve) -> None:
    """Starts, changes or stops listening for one agent to match its setup. `resolve` finds the config of the agent it goes on from."""
    from app.domain.workflows import WorkflowConfig

    listeners = app.state.listeners
    cfg = WorkflowConfig(**config)
    if not cfg.listen:
        listeners.stop(workflow_id)
        return
    source = cfg
    if cfg.source.kind == "agent":  # an agent that goes on from another listens to the mailbox that one reads
        up = await resolve(cfg.source.upstream_id or "")
        if up is None:
            listeners.stop(workflow_id)
            return
        source = WorkflowConfig(**up)
    if source.source.kind != "mail":
        listeners.stop(workflow_id)
        return

    async def trigger() -> None:
        if not app.state.sweeps.is_active(workflow_id):
            app.state.sweeps.start(workflow_id, who.user_id, sweep_run(app, who, workflow_id))

    listeners.ensure(workflow_id, source.source.account_ids, source.source.folder, lambda: app.state.tokens.mint(who.user_id, who.email), trigger)


def sweep_run(app: FastAPI, who: Who, workflow_id: str):
    """One run for a background sweep: its own session, and a fresh token for the owner (the browser's expires in minutes)."""

    async def run_once():
        async with app.state.session_factory() as session:
            try:
                result = await build_workflow_service(app, session).run(who, app.state.tokens.mint(who.user_id, who.email), workflow_id, None, via_sweep=True)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise

    return run_once


def agent_service(request: Request, session: Session) -> AgentService:
    return build_services(request.app, session)[0]


def channel_service(request: Request, session: Session) -> ChannelService:
    return build_services(request.app, session)[1]


def who(principal: CurrentPrincipal) -> Who:
    return Who(principal.user_id, principal.email)


Agent = Annotated[AgentService, Depends(agent_service)]
Workflows = Annotated[WorkflowService, Depends(workflow_service)]
Channels = Annotated[ChannelService, Depends(channel_service)]
Me = Annotated[Who, Depends(who)]
