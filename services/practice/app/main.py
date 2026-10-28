from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from stiamo_common.db import make_engine, make_session_factory
from stiamo_common.errors import install_error_handlers

from app.application.ports import FilesClient, NoFiles
from app.config import Settings, get_settings
from app.infrastructure.files_client import HttpFilesClient
from app.presentation.routers import directory, history, legal, plans, views, work


def create_app(settings: Settings | None = None, *, files_factory: Callable[[str], FilesClient] | None = None) -> FastAPI:
    """Application factory. Run with: uvicorn app.main:create_app --factory"""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="Stiamo Practice", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.files_factory = files_factory or (
        (lambda token: HttpFilesClient(settings.files_url, token)) if settings.files_url else (lambda token: NoFiles())
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)

    for router in (legal.views, views.router, legal.cases, legal.hearings, legal.extra, directory.counsel, directory.clients, directory.extra, directory.projects, history.router,
                   work.tasks, work.follow_ups, work.meetings, work.extra,
                   plans.plans, plans.goals, plans.extra):
        app.include_router(router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
