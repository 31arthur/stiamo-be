import asyncio

from alembic import context
from app.config import get_settings
from app.infrastructure.db import models  # noqa: F401
from sqlalchemy.ext.asyncio import create_async_engine
from stiamo_common.db import Base, UTCDateTime

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Migrations must not depend on application types."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False

url = get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_item=render_item, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
