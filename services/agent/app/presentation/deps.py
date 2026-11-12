"""Composition root: wires ports to adapters per request."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.errors import Unauthorized
from stiamo_common.security import Principal, make_principal_dependency

from app.application.alerts import AlertService
from app.application.availability import AvailabilityService
from app.application.channels import ChannelService
from app.application.conversations import ConversationService
from app.application.dayplans import DayPlanService, plan_buttons, planner_settings
from app.application.editor import EditorService
from app.application.health import HealthService
from app.application.insights import InsightService
from app.application.mailwatch import MailWatchService
from app.application.proposals import ProposalService
from app.application.reminders import ReminderActions, ReminderService
from app.application.services import AgentService, Who
from app.application.voicenotes import VoiceNotes
from app.application.workflows import WorkflowService
from app.config import get_settings
from app.domain.dial import call_rows
from app.infrastructure.db.repositories import (
    SqlAlertRepository,
    SqlChannelRepository,
    SqlConversationRepository,
    SqlDayPlanRepository,
    SqlProposalRepository,
    SqlSettingsRepository,
)
from app.infrastructure.db.workflows import SqlWorkflowRepository

current_principal = make_principal_dependency(lambda: get_settings().token_settings())
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]

_bearer = HTTPBearer(auto_error=False)


async def bearer_token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str:
    """The caller's own access token, passed on so the other services answer as they would to the browser."""
    if creds is None:
        raise Unauthorized("Missing bearer token.")
    return creds.credentials


Token = Annotated[str, Depends(bearer_token)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]


def build_services(app: FastAPI, session: AsyncSession) -> tuple[AgentService, ChannelService]:
    s = app.state
    agent = AgentService(SqlSettingsRepository(session, s.box), s.registry, s.runs, SqlWorkflowRepository(session), s.llm_factory, s.decision_factory, s.data_factory, s.settings.max_steps, SqlProposalRepository(session))
    agent.engine = s.settings.engine
    box: list[ChannelService] = []  # (the channels are made after the agent, and the agent sends through them)
    agent.notify = lambda who, workspace, text: box[0].tell(who, workspace, text)
    agent.sorter = lambda: WorkflowService(SqlWorkflowRepository(session), SqlSettingsRepository(session, s.box), s.runs, s.llm_factory, s.decision_factory, s.data_factory, s.running, s.sweeps.is_active, box[0].alerts)
    agent.ledger = lambda: SqlWorkflowRepository(session)
    agent.raise_alert = lambda who, token, **kw: box[0].alerts.raise_alert(who, token, **kw)
    agent.send_telegram = lambda who, workspace, channel_id, chat, text: box[0].send_with(who, workspace, channel_id, chat, text)
    agent.ask_phone = lambda who, workspace, text, buttons: box[0].tell(who, workspace, text, buttons=buttons)
    channels = ChannelService(SqlChannelRepository(session, s.box), agent, s.telegram, s.twilio, s.tokens, public_url=s.settings.public_url, pairing_minutes=s.settings.pairing_minutes, memory=s.chat_memory, proposals=build_proposals(app, session), conversations=ConversationService(SqlConversationRepository(session), agent, SqlProposalRepository(session)))
    box.append(channels)
    channels.deliveries = s.deliveries
    channels.app_url = s.settings.app_url
    channels.reminder_actions = ReminderActions(SqlSettingsRepository(session, s.box), s.data_factory, lambda: s.clock())
    plans = DayPlanService(SqlDayPlanRepository(session), SqlSettingsRepository(session, s.box), agent, s.data_factory, lambda who, text, workspace, plan_id, calls=(): channels.announce_plan(who, text, workspace, plan_buttons(plan_id) + call_rows(s.settings.app_url, list(calls))))
    channels.plan_actions = plans
    channels.availability_actions = AvailabilityService(SqlSettingsRepository(session, s.box), s.data_factory, lambda who, text, workspace, buttons: channels.announce_plan(who, text, workspace, buttons), plans)
    channels.editor_actions = EditorService(SqlSettingsRepository(session, s.box), s.data_factory, s.llm_factory, lambda who, text, workspace, buttons: channels.announce_plan(who, text, workspace, buttons), lambda: s.clock())
    channels.voice_notes = VoiceNotes(SqlSettingsRepository(session, s.box), s.voice)
    channels.alerts = AlertService(SqlAlertRepository(session), SqlSettingsRepository(session, s.box), s.llm_factory, s.data_factory, s.tokens, channels, app_url=s.settings.app_url, clock_now=lambda: s.clock(), caller=s.caller, calls=s.alert_calls, deliveries=s.deliveries)
    return agent, channels


def build_conversations(app: FastAPI, session: AsyncSession) -> ConversationService:
    agent, _ = build_services(app, session)
    return ConversationService(SqlConversationRepository(session), agent, SqlProposalRepository(session))


def build_proposals(app: FastAPI, session: AsyncSession) -> ProposalService:
    return ProposalService(SqlProposalRepository(session), app.state.data_factory)


def build_day_plans(app: FastAPI, session: AsyncSession) -> DayPlanService:
    agent, channels = build_services(app, session)
    return DayPlanService(SqlDayPlanRepository(session), SqlSettingsRepository(session, app.state.box), agent, app.state.data_factory, lambda who, text, workspace, plan_id, calls=(): channels.announce_plan(who, text, workspace, plan_buttons(plan_id) + call_rows(app.state.settings.app_url, list(calls))), ask=channels.availability_actions.ask, editor=channels.editor_actions.evening)


def evening_run(app: FastAPI, who: Who):
    """The planner's evening: its own session and a fresh token for the owner (nobody is signed in at nine at night)."""

    async def run_once() -> None:
        async with app.state.session_factory() as session:
            try:
                await build_day_plans(app, session).evening(who, app.state.tokens.mint(who.user_id, who.email))
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return run_once


def sync_evening(app: FastAPI, who: Who, planner: dict) -> None:
    """Starts, moves or stops the evening plan to match what the person set."""
    p = planner_settings(planner)
    if p["enabled"]:
        app.state.daily.ensure(who.user_id, p["time"], p["timezone"], evening_run(app, who))
    else:
        app.state.daily.stop(who.user_id)


def build_reminders(app: FastAPI, session: AsyncSession) -> ReminderService:
    s = app.state
    _, channels = build_services(app, session)
    return ReminderService(SqlSettingsRepository(session, s.box), s.data_factory, s.tokens, s.caller, lambda who, text, buttons=None: channels.announce(who, text, kind="reminder", buttons=buttons), lambda: s.clock(), s.deliveries, s.settings.app_url)


def reminder_check(app: FastAPI, who: Who):
    """One minute's check, in its own session: nobody is signed in, so it runs as the owner."""

    async def run_once() -> object:
        async with app.state.session_factory() as session:
            try:
                out = await build_reminders(app, session).check(who)
                await session.commit()
                return out
            except Exception:
                await session.rollback()
                raise

    return run_once


def flow_run(app: FastAPI, who: Who, agent_id: str, node_id: str, days: list[int] | None = None, timezone: str = "Asia/Kolkata"):
    """One scheduled run of a workflow, from its schedule trigger: its own session and a fresh token for the owner (nobody is signed in at 7am).
    It runs only on the weekdays ticked (0 is Monday), by the clock in the node's time zone."""

    async def run_once() -> None:
        if days is not None and datetime.now(ZoneInfo(timezone)).weekday() not in days:
            return
        async with app.state.session_factory() as session:
            try:
                agent, _ = build_services(app, session)
                await agent.chat(who, app.state.tokens.mint(who.user_id, who.email), agent_id, "Scheduled run", [], "the schedule", trigger="schedule", entry=node_id)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return run_once


def apply_schedules(app: FastAPI, who: Who, schedules: list[dict[str, Any]]) -> None:
    """Starts, moves or stops a person's workflow schedules to match what their workflows say."""
    mgr = app.state.flowsched
    prefix = f"{who.user_id}|"
    wanted = {f"{prefix}{s['agent']}|{s['node']}": s for s in schedules}
    for key in mgr.keys(prefix):
        if key not in wanted:
            mgr.stop(key)
    for key, s in wanted.items():
        mgr.ensure(key, s["time"], s["timezone"], flow_run(app, who, s["agent"], s["node"], s.get("days"), s["timezone"]))


def sync_reminders(app: FastAPI, who: Who, enabled: bool) -> None:
    """Starts or stops the minute-by-minute check to match whether the reminders are on."""
    if enabled:
        app.state.reminders.ensure(who.user_id, reminder_check(app, who))
    else:
        app.state.reminders.stop(who.user_id)


def build_mailwatch(app: FastAPI, session: AsyncSession) -> MailWatchService:
    s = app.state
    _, channels = build_services(app, session)

    async def listen(who: Who, w) -> None:
        async def resolve(_wid: str):
            return None  # a watch reads mailboxes directly

        await sync_listener(app, who, w.id, w.config, resolve)

    return MailWatchService(SqlWorkflowRepository(session), SqlSettingsRepository(session, s.box), s.data_factory, listen, s.listeners.status, channels.phone_for)


async def watch_everyone(app: FastAPI) -> None:
    """Every person's workspaces with a mailbox get a watch, and the watches listen. Runs when the service starts and every so often, so a
    mailbox connected since is picked up without anyone opening anything."""
    async with app.state.session_factory() as session:
        try:
            for s in await SqlSettingsRepository(session, app.state.box).everyone():
                who = Who(s.user_id, s.email)
                try:
                    await build_mailwatch(app, session).ensure(who, app.state.tokens.mint(who.user_id, who.email))
                except Exception:  # noqa: BLE001  one person's trouble is not everyone's
                    logging.getLogger(__name__).exception("could not set up the mail watch for %s", s.user_id)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def build_health(app: FastAPI, session: AsyncSession) -> HealthService:
    s = app.state
    agent, channels = build_services(app, session)
    return HealthService(SqlSettingsRepository(session, s.box), agent, channels, build_mailwatch(app, session), s.deliveries, s.data_factory, s.settings.public_url, s.settings.app_url, lambda: s.clock())


def build_insights(app: FastAPI, session: AsyncSession) -> InsightService:
    s = app.state
    return InsightService(SqlWorkflowRepository(session), SqlSettingsRepository(session, s.box), s.data_factory, s.llm_factory, s.insight_cache, lambda: s.clock())


def build_workflow_service(app: FastAPI, session: AsyncSession) -> WorkflowService:
    s = app.state
    _, channels = build_services(app, session)
    return WorkflowService(SqlWorkflowRepository(session), SqlSettingsRepository(session, s.box), s.runs, s.llm_factory, s.decision_factory, s.data_factory, s.running, s.sweeps.is_active, channels.alerts)


def workflow_service(request: Request, session: Session) -> WorkflowService:
    return build_workflow_service(request.app, session)


async def sync_listener(app: FastAPI, who: Who, workflow_id: str, config: dict, resolve) -> None:
    """Starts, changes or stops what makes an agent run by itself, to match its setup: listening to a mailbox when new mail arrives,
    and running every so many minutes. `resolve` finds the config of the agent it goes on from."""
    from app.domain.workflows import WorkflowConfig

    cfg = WorkflowConfig(**config)

    async def trigger() -> None:
        if not app.state.sweeps.is_active(workflow_id):
            app.state.sweeps.start(workflow_id, who.user_id, sweep_run(app, who, workflow_id))

    if cfg.schedule_minutes:
        app.state.schedules.ensure(workflow_id, cfg.schedule_minutes, trigger)
    else:
        app.state.schedules.stop(workflow_id)

    listeners = app.state.listeners
    if not cfg.listen:
        listeners.stop(workflow_id)
        return
    source = cfg
    if cfg.source.kind == "agent":  # an agent that goes on from another listens to the mailbox that one reads
        up = await resolve(cfg.source.upstream_id or "")
        if up is None:
            listeners.stop(workflow_id)
            return
        source = WorkflowConfig(**up)
    if source.source.kind != "mail":
        listeners.stop(workflow_id)
        return
    listeners.ensure(workflow_id, source.source.account_ids, source.source.folder, lambda: app.state.tokens.mint(who.user_id, who.email), trigger)


def sweep_run(app: FastAPI, who: Who, workflow_id: str):
    """One run for a background sweep: its own session, and a fresh token for the owner (the browser's expires in minutes)."""

    async def run_once():
        async with app.state.session_factory() as session:
            try:
                result = await build_workflow_service(app, session).run(who, app.state.tokens.mint(who.user_id, who.email), workflow_id, None, via_sweep=True)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise

    return run_once


def agent_service(request: Request, session: Session) -> AgentService:
    return build_services(request.app, session)[0]


def channel_service(request: Request, session: Session) -> ChannelService:
    return build_services(request.app, session)[1]


def who(principal: CurrentPrincipal) -> Who:
    return Who(principal.user_id, principal.email)


Agent = Annotated[AgentService, Depends(agent_service)]
Workflows = Annotated[WorkflowService, Depends(workflow_service)]
Channels = Annotated[ChannelService, Depends(channel_service)]
Me = Annotated[Who, Depends(who)]
