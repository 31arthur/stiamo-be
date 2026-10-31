from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from stiamo_common.db import make_engine, make_session_factory
from stiamo_common.errors import install_error_handlers

from app.application.throttle import LoginThrottle
from app.config import Settings, get_settings
from app.infrastructure.security.hasher import BcryptHasher
from app.presentation.routers import auth


def create_app(settings: Settings | None = None, *, hasher_rounds: int = 12) -> FastAPI:
    """Application factory. Run with: uvicorn app.main:create_app --factory"""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="Stiamo Identity", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.hasher = BcryptHasher(hasher_rounds)
    app.state.login_throttle = LoginThrottle(settings.login_max_failures, settings.login_lockout_minutes * 60)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])
    install_error_handlers(app)
    app.include_router(auth.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
