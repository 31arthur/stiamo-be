"""Composition root: wires ports to adapters per request."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.errors import Unauthorized
from stiamo_common.security import Principal, make_principal_dependency

from app.application.service import CalendarService
from app.config import get_settings
from app.infrastructure.db.repositories import SqlCalendarRepository

current_principal = make_principal_dependency(lambda: get_settings().token_settings())
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)


async def bearer_token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str:
    """The caller's own access token, passed on so the practice service answers as it would to the browser."""
    if creds is None:
        raise Unauthorized("Missing bearer token.")
    return creds.credentials


Token = Annotated[str, Depends(bearer_token)]


def build_service(app: FastAPI, session: AsyncSession) -> CalendarService:
    s = app.state
    return CalendarService(SqlCalendarRepository(session, s.box), s.google, s.practice, s.settings, s.access, s.mint)


def calendar_service(request: Request, session: Session) -> CalendarService:
    return build_service(request.app, session)


Calendar = Annotated[CalendarService, Depends(calendar_service)]
