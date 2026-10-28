"""Composition root: wires ports to adapters, one unit of work per request."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.errors import Unauthorized
from stiamo_common.security import Principal, make_principal_dependency

from app.application.use_cases import AttachmentUseCases
from app.config import get_settings
from app.infrastructure.db.repository import SqlAttachmentRepository


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]


def attachment_service(session: Session, request: Request) -> AttachmentUseCases:
    s = request.app.state
    return AttachmentUseCases(SqlAttachmentRepository(session), s.storage, s.history, s.settings.max_bytes)


current_principal = make_principal_dependency(lambda: get_settings().token_settings())
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def bearer_token(request: Request) -> str:
    """The caller's own access token, forwarded when this service talks to another on their behalf."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token.")
    return header[7:].strip()


BearerToken = Annotated[str, Depends(bearer_token)]
Service = Annotated[AttachmentUseCases, Depends(attachment_service)]
