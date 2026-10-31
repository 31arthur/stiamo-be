"""Composition root: wires ports to adapters per request."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.security import Principal, make_principal_dependency

from app.application.accounts import MailService
from app.config import get_settings
from app.infrastructure.db.repositories import SqlAccountRepository

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


def mail_service(request: Request, session: Session) -> MailService:
    s = request.app.state
    return MailService(SqlAccountRepository(session, s.box), s.gateway, s.live)


Mail = Annotated[MailService, Depends(mail_service)]
