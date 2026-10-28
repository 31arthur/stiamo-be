from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from stiamo_common.db import make_engine, make_session_factory
from stiamo_common.errors import install_error_handlers

from app.application.ports import FileStorage, HistoryNotifier
from app.config import Settings, get_settings
from app.infrastructure.history_client import PracticeHistoryNotifier
from app.infrastructure.storage import LocalFileStorage
from app.presentation.routers import router


def create_app(settings: Settings | None = None, *, storage: FileStorage | None = None,
               history: HistoryNotifier | None = None) -> FastAPI:
    """Application factory. Run with: uvicorn app.main:create_app --factory"""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="Stiamo Files", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.storage = storage or LocalFileStorage(settings.storage_dir)
    app.state.history = history or PracticeHistoryNotifier(settings.practice_url)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
