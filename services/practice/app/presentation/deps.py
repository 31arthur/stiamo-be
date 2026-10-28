"""Composition root: wires ports to adapters, one unit of work per request."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.security import Principal, make_principal_dependency

from app.application.crud import Crud
from app.application.ports import Env
from app.application.use_cases.directory import ClientUseCases, counsel_crud, project_crud
from app.application.use_cases.history import HistoryUseCases
from app.config import get_settings
from app.infrastructure.db.repositories import build_stores
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


def get_env(session: Session) -> Env:
    return Env(build_stores(session), SystemClock(), UuidGenerator())


EnvDep = Annotated[Env, Depends(get_env)]


# --- aggregate services (each is a Crud or a subclass with extra rules) -------------------------------------


def counsel_service(env: EnvDep) -> Crud:
    return counsel_crud(env)


def client_service(env: EnvDep) -> ClientUseCases:
    return ClientUseCases(env)


def project_service(env: EnvDep) -> Crud:
    return project_crud(env)


def history_service(env: EnvDep) -> HistoryUseCases:
    return HistoryUseCases(env)


current_principal = make_principal_dependency(lambda: get_settings().token_settings())
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
