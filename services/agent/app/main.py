from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from stiamo_common.db import make_engine, make_session_factory
from stiamo_common.errors import install_error_handlers

from app.agents import default_registry
from app.config import Settings, get_settings
from app.infrastructure.data import HttpDataGateway
from app.infrastructure.llm.gemini import GeminiClient
from app.infrastructure.llm.jev import JevClient
from app.infrastructure.llm.openai_compat import OpenAiCompatibleClient
from app.infrastructure.messaging import HttpTelegramApi, HttpTwilioApi
from app.infrastructure.security import JwtAccessTokens, SecretBox
from app.presentation.routers import agent, channels, webhooks


def create_app(settings: Settings | None = None, *, registry=None, llm_factory=None, decision_factory=None, data_factory=None, telegram=None, twilio=None, tokens=None) -> FastAPI:
    """Application factory. Run with: uvicorn app.main:create_app --factory

    The keyword arguments swap in fakes for tests; in production they are the real thing.
    """
    settings = settings or get_settings()
    http = httpx.AsyncClient(timeout=settings.timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        yield
        await http.aclose()
        await engine.dispose()

    app = FastAPI(title="Stiamo Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.box = SecretBox(settings.encryption_key)
    app.state.registry = registry or default_registry()
    app.state.llm_factory = llm_factory or (lambda provider, key: GeminiClient(key, http) if provider == "gemini" else OpenAiCompatibleClient(provider, key, http))
    app.state.decision_factory = decision_factory or (lambda key: JevClient(key, http))
    app.state.data_factory = data_factory or (lambda token: HttpDataGateway(http, token, settings.practice_url, settings.files_url, settings.drive_url))
    app.state.telegram = telegram or HttpTelegramApi(http)
    app.state.twilio = twilio or HttpTwilioApi(http)
    app.state.tokens = tokens or JwtAccessTokens(settings.token_settings())
    #: What each chat said recently, so a follow-up question makes sense. In memory: it resets when the service restarts.
    app.state.chat_memory = {}

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    app.include_router(agent.router)
    app.include_router(channels.router)
    app.include_router(webhooks.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
