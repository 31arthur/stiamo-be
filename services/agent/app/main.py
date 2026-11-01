import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from stiamo_common.db import make_engine, make_session_factory
from stiamo_common.errors import install_error_handlers

from app.agents import default_registry
from app.application.daily import DailyManager
from app.application.listeners import ListenerManager
from app.application.schedules import ScheduleManager
from app.application.services import Who
from app.application.sweeps import SweepManager
from app.config import Settings, get_settings
from app.infrastructure.data import HttpDataGateway
from app.infrastructure.db.repositories import SqlSettingsRepository
from app.infrastructure.db.runs import SqlRunLog
from app.infrastructure.db.workflows import SqlWorkflowRepository
from app.infrastructure.llm.gemini import GeminiClient
from app.infrastructure.llm.jev import JevClient
from app.infrastructure.llm.openai_compat import OpenAiCompatibleClient
from app.infrastructure.mailevents import HttpMailEvents
from app.infrastructure.messaging import HttpTelegramApi, HttpTwilioApi
from app.infrastructure.security import JwtAccessTokens, SecretBox
from app.presentation.deps import sync_evening, sync_listener
from app.presentation.routers import agent, attention, channels, dayplans, news, proposals, usage, webhooks, workflows


async def start_listening(app: FastAPI) -> None:
    """After a restart, the agents that were listening listen again."""
    try:
        async with app.state.session_factory() as session:
            repo = SqlWorkflowRepository(session)
            settings_repo = SqlSettingsRepository(session, app.state.box)
            for w in await repo.listening():
                s = await settings_repo.get(w.user_id)
                await sync_listener(app, Who(w.user_id, s.email if s else ""), w.id, w.config, lambda wid, uid=w.user_id: _config_of(repo, uid, wid))
    except Exception:  # noqa: BLE001  a problem here must not keep the service from starting
        logging.getLogger(__name__).exception("could not start the mailbox listeners")


async def start_evenings(app: FastAPI) -> None:
    """After a restart, everyone whose evening plan is on gets it again at the same time."""
    try:
        async with app.state.session_factory() as session:
            for s in await SqlSettingsRepository(session, app.state.box).planners():
                sync_evening(app, Who(s.user_id, s.email), s.planner)
    except Exception:  # noqa: BLE001  a problem here must not keep the service from starting
        logging.getLogger(__name__).exception("could not start the evening plans")


async def _config_of(repo, user_id: str, workflow_id: str):
    w = await repo.get(user_id, workflow_id)
    return w.config if w else None


def create_app(settings: Settings | None = None, *, mail_events=None, registry=None, llm_factory=None, decision_factory=None, data_factory=None, telegram=None, twilio=None, tokens=None) -> FastAPI:
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
        await start_listening(app)
        await start_evenings(app)
        yield
        await app.state.daily.close()
        await app.state.listeners.close()
        await app.state.schedules.close()
        await app.state.sweeps.close()
        await http.aclose()
        await engine.dispose()

    app = FastAPI(title="Stiamo Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.box = SecretBox(settings.encryption_key)
    app.state.registry = registry or default_registry()
    # Its own sessions (the factory exists once the app starts), so a failed run is still kept.
    app.state.runs = SqlRunLog(lambda: app.state.session_factory())
    #: Ids of the agents being run right now (the service runs as one process, so this is shared by every request).
    app.state.running = set()
    #: Runs through a whole inbox that are going on in the background (see application/sweeps.py).
    app.state.sweeps = SweepManager()
    #: Agents that run every so many minutes (see application/schedules.py).
    app.state.schedules = ScheduleManager()
    #: The daily planner's evening run, once a day at the time each person chose (see application/daily.py).
    app.state.daily = DailyManager()
    #: Agents listening to their mailbox (see application/listeners.py).
    app.state.listeners = ListenerManager(mail_events or HttpMailEvents(http, settings.mail_url))
    app.state.llm_factory = llm_factory or (lambda provider, key: GeminiClient(key, http) if provider == "gemini" else OpenAiCompatibleClient(provider, key, http))
    app.state.decision_factory = decision_factory or (lambda key: JevClient(key, http))
    app.state.data_factory = data_factory or (lambda token: HttpDataGateway(http, token, settings.practice_url, settings.files_url, settings.drive_url, settings.mail_url))
    app.state.telegram = telegram or HttpTelegramApi(http)
    app.state.twilio = twilio or HttpTwilioApi(http)
    app.state.tokens = tokens or JwtAccessTokens(settings.token_settings())
    #: What each chat said recently, so a follow-up question makes sense. In memory: it resets when the service restarts.
    app.state.chat_memory = {}

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    app.include_router(agent.router)
    app.include_router(channels.router)
    app.include_router(usage.router)
    app.include_router(attention.router)
    app.include_router(news.router)
    app.include_router(dayplans.router)
    app.include_router(proposals.router)
    app.include_router(workflows.router)
    app.include_router(webhooks.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
