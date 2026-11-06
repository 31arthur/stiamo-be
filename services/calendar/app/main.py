import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from stiamo_common.db import make_engine, make_session_factory
from stiamo_common.errors import install_error_handlers

from app.application.service import AccessCache
from app.config import Settings, get_settings
from app.infrastructure.google import HttpGoogle, HttpPeople, HttpPractice
from app.infrastructure.security import SecretBox, TokenMint
from app.presentation.deps import build_contacts, build_service
from app.presentation.routers import calendar, contacts

log = logging.getLogger(__name__)


async def sync_loop(app: FastAPI, minutes: int) -> None:
    """Reads everyone's calendars every few minutes, and sends meetings and hearings to Google."""
    while True:
        await asyncio.sleep(minutes * 60)
        try:
            async with app.state.session_factory() as session:
                await build_service(app, session).sync_everyone()
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("the calendar sync failed")


async def contacts_loop(app: FastAPI, minutes: int) -> None:
    """Reads everyone's Google contacts again every so often. They change rarely, so this is far slower than the calendar."""
    while True:
        await asyncio.sleep(minutes * 60)
        try:
            async with app.state.session_factory() as session:
                await build_contacts(app, session).sync_everyone()
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("the contacts sync failed")


def create_app(settings: Settings | None = None, *, google=None, practice=None, people=None) -> FastAPI:
    """Application factory. Run with: uvicorn app.main:create_app --factory

    `google` and `practice` swap in fakes for tests. Run a single replica: the background sync runs in this process.
    """
    settings = settings or get_settings()
    http = httpx.AsyncClient(timeout=20.0)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        loop = asyncio.create_task(sync_loop(app, settings.sync_minutes)) if settings.sync_minutes > 0 else None
        contacts_task = asyncio.create_task(contacts_loop(app, settings.contacts_sync_minutes)) if settings.contacts_sync_minutes > 0 else None
        yield
        for task in (loop, contacts_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await http.aclose()
        await engine.dispose()

    app = FastAPI(title="Stiamo Calendar", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.box = SecretBox(settings.encryption_key)
    app.state.google = google or HttpGoogle(http, settings.google_client_id, settings.google_client_secret, settings.redirect_uri)
    app.state.practice = practice or HttpPractice(http, settings.practice_url)
    app.state.access = AccessCache()
    app.state.people = people or HttpPeople(http, settings.google_client_id, settings.google_client_secret, settings.redirect_uri)
    app.state.people_access = AccessCache()
    #: Sending to Google needs to read meetings as their owner, which takes a token this service signs: only with a shared HS256 secret.
    app.state.mint = TokenMint(settings.jwt_secret, settings.jwt_issuer, settings.jwt_audience) if settings.jwt_algorithm.startswith("HS") and settings.practice_url else None

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    app.include_router(calendar.router)
    app.include_router(contacts.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
