from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from stiamo_common.db import make_engine, make_session_factory
from stiamo_common.errors import install_error_handlers

from app.application.live import LiveHub
from app.config import Settings, get_settings
from app.infrastructure.imap import ImapGateway
from app.infrastructure.security import SecretBox
from app.presentation.routers import mail


def create_app(settings: Settings | None = None, *, gateway=None) -> FastAPI:
    """Application factory. Run with: uvicorn app.main:create_app --factory

    `gateway` swaps in a fake mail server for tests. Run a single replica: live IMAP IDLE connections are held in this process.
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        yield
        await app.state.live.close()
        await engine.dispose()

    app = FastAPI(title="Stiamo Mail", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.box = SecretBox(settings.encryption_key)
    app.state.gateway = gateway or ImapGateway(timeout=settings.imap_timeout, idle_seconds=settings.idle_seconds, allow_private=settings.allow_private_hosts)
    app.state.live = LiveHub(app.state.gateway)

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    app.include_router(mail.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
