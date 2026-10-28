from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.security import Principal, make_principal_dependency

from app.application.use_cases.records import (
    DeleteRecord,
    GetRecord,
    ListRecords,
    SaveManyRecords,
    SaveRecord,
)
from app.config import get_settings
from app.infrastructure.db.repository import SqlRecordRepository
from app.infrastructure.system import SystemClock


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]


def list_uc(session: Session) -> ListRecords:
    return ListRecords(SqlRecordRepository(session))


def get_uc(session: Session) -> GetRecord:
    return GetRecord(SqlRecordRepository(session))


def save_uc(session: Session, request: Request) -> SaveRecord:
    return SaveRecord(SqlRecordRepository(session), SystemClock(), request.app.state.settings.max_document_bytes)


def save_many_uc(session: Session, request: Request) -> SaveManyRecords:
    s = request.app.state.settings
    return SaveManyRecords(SqlRecordRepository(session), SystemClock(), s.max_document_bytes, s.max_batch)


def delete_uc(session: Session) -> DeleteRecord:
    return DeleteRecord(SqlRecordRepository(session))


current_principal = make_principal_dependency(lambda: get_settings().token_settings())
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
