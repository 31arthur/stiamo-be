"""Composition root: wires ports to adapters per request."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.security import Principal, make_principal_dependency

from app.application.use_cases.auth import GetProfile, LoginUser, LogoutSession, RefreshSession, RegisterUser
from app.config import Settings
from app.infrastructure.db.repositories import SqlRefreshTokenRepository, SqlUnitOfWork, SqlUserRepository
from app.infrastructure.security.tokens import JwtTokenService
from app.infrastructure.system import SystemClock, UuidGenerator


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]


def _tokens(settings: Settings) -> JwtTokenService:
    return JwtTokenService(settings.token_settings(), settings.signing_key,
                           timedelta(minutes=settings.access_ttl_minutes),
                           timedelta(days=settings.refresh_ttl_days))


def register_uc(session: Session, request: Request) -> RegisterUser:
    return RegisterUser(SqlUserRepository(session), SqlRefreshTokenRepository(session),
                        request.app.state.hasher, _tokens(request.app.state.settings), SystemClock(),
                        UuidGenerator())


def login_uc(session: Session, request: Request) -> LoginUser:
    return LoginUser(SqlUserRepository(session), SqlRefreshTokenRepository(session), request.app.state.hasher,
                     _tokens(request.app.state.settings), SystemClock())


def refresh_uc(session: Session, request: Request) -> RefreshSession:
    return RefreshSession(SqlUserRepository(session), SqlRefreshTokenRepository(session),
                          _tokens(request.app.state.settings), SystemClock(), SqlUnitOfWork(session))


def logout_uc(session: Session, request: Request) -> LogoutSession:
    return LogoutSession(SqlRefreshTokenRepository(session), _tokens(request.app.state.settings), SystemClock())


def profile_uc(session: Session) -> GetProfile:
    return GetProfile(SqlUserRepository(session))


def _principal_settings():
    from app.config import get_settings

    return get_settings().token_settings()


current_principal = make_principal_dependency(_principal_settings)
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
