from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from stiamo_common.errors import install_error_handlers

from app.application.ports import DriveGateway
from app.config import Settings, get_settings
from app.infrastructure.google import GoogleDriveGateway, ServiceAccountTokens, load_credentials
from app.presentation.routers import router


def create_app(settings: Settings | None = None, *, gateway: DriveGateway | None = None) -> FastAPI:
    """Application factory. Run with: uvicorn app.main:create_app --factory"""
    settings = settings or get_settings()
    client = httpx.AsyncClient(timeout=settings.timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="Stiamo Drive", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    if gateway is None:
        info = load_credentials(settings.credentials_file, settings.credentials_json)
        gateway = GoogleDriveGateway(ServiceAccountTokens(info, client, settings.scope) if info else None, client)
    app.state.gateway = gateway
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
