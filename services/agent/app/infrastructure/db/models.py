from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from stiamo_common.db import Base, UTCDateTime

Json = JSON().with_variant(JSONB(), "postgresql")


class SettingsModel(Base):
    """One row per person: their AI keys (sealed), the model in use, the decision model and the prompt."""

    __tablename__ = "agent_settings"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    keys_sealed: Mapped[str | None] = mapped_column(Text)
    #: agent id -> the provider, model and thinking level chosen for it.
    selection: Mapped[dict[str, Any] | None] = mapped_column(Json)
    decision: Mapped[dict[str, Any] | None] = mapped_column(Json)
    #: agent id -> the prompt the person wrote for it.
    prompt: Mapped[dict[str, Any] | None] = mapped_column(Json)
    #: agent id -> the workspaces it is shown in.
    workspaces: Mapped[dict[str, list[str]] | None] = mapped_column(Json)
    #: The daily planner's evening run: whether it is on, when, in which time zone.
    planner: Mapped[dict[str, Any] | None] = mapped_column(Json)
    #: Reminders about calls not marked done: whether they are on, spoken or not, and where each day's have got to.
    reminders: Mapped[dict[str, Any] | None] = mapped_column(Json)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ChannelModel(Base):
    """A Telegram bot or a WhatsApp (Twilio) number the person has connected."""

    __tablename__ = "agent_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(12))
    secrets_sealed: Mapped[str | None] = mapped_column(Text)
    public: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_agent_channels_user", "user_id"),)


class RunModel(Base):
    """One time an agent was asked: what it was given, what it did, what it answered."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    agent_id: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(16), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    thinking: Mapped[str] = mapped_column(String(8), default="off")
    input: Mapped[str] = mapped_column(Text, default="")
    user_message: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list[str]] = mapped_column(Json, default=list)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    steps_count: Mapped[int] = mapped_column(Integer, default=0)
    output: Mapped[str] = mapped_column(Text, default="")
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, int]] = mapped_column(Json, default=dict)
    channel: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_agent_runs_agent", "user_id", "agent_id", "started_at"),)


class UsageModel(Base):
    """What one run of an agent used: kept apart from the run's own record, so history pruned or cleared doesn't take the totals with it."""

    __tablename__ = "agent_usage"

    #: The run's id.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    agent_id: Mapped[str] = mapped_column(String(64))
    #: The agent's name when it ran, so the line still reads right after the agent is renamed or deleted.
    agent_name: Mapped[str] = mapped_column(String(100), default="")
    kind: Mapped[str] = mapped_column(String(8), default="chat")
    trigger: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(8), default="ok")
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(16), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    #: US dollars, when the provider says what it charged (OpenRouter and the decision model do); empty otherwise.
    cost: Mapped[float | None] = mapped_column(Float)
    #: How many emails or records a workflow run judged.
    items: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_agent_usage_user", "user_id", "at"), Index("ix_agent_usage_agent", "user_id", "agent_id", "at"))


class WorkflowModel(Base):
    """An agent a person built."""

    __tablename__ = "workflow_agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="")
    config: Mapped[dict[str, Any]] = mapped_column(Json)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_workflow_agents_user", "user_id"),)


class ExecutionModel(Base):
    """One real run of a workflow agent: what it read, what it decided, and how it went. The items are in the ledger, under the run's id."""

    __tablename__ = "workflow_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(8), default="ok")
    summary: Mapped[str] = mapped_column(Text, default="")
    counts: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (Index("ix_workflow_executions_agent", "agent_id", "started_at"),)


class WorkflowVersionModel(Base):
    """Every configuration a workflow has had, so a change can be traced and rolled back."""

    __tablename__ = "workflow_versions"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class LedgerModel(Base):
    """What was decided about each item of a workflow, and what happened to it."""

    __tablename__ = "workflow_ledger"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(36))
    item_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200), default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    label: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[str] = mapped_column(String(36), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    data: Mapped[dict[str, Any] | None] = mapped_column(Json)

    __table_args__ = (UniqueConstraint("agent_id", "item_id", name="uq_ledger_item"), Index("ix_ledger_status", "agent_id", "status"))


class DayPlanModel(Base):
    """What the daily planner made for one day: the briefing and the schedule it proposed, until the person approves or dismisses it."""

    __tablename__ = "day_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    day: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12))
    briefing: Mapped[str] = mapped_column(Text, default="")
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    run_id: Mapped[str | None] = mapped_column(String(36))
    trigger: Mapped[str] = mapped_column(String(12), default="evening")
    card: Mapped[dict[str, Any] | None] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_day_plans_user_day"),)


class ProposalModel(Base):
    """What an agent proposed to add or change, kept until the person approves or drops it."""

    __tablename__ = "agent_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    agent_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    lines: Mapped[list[str]] = mapped_column(Json, default=list)
    ops: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    status: Mapped[str] = mapped_column(String(10))
    channel_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_agent_proposals_user", "user_id", "status"),)


class ConversationModel(Base):
    """A thread with one of the team's agents."""

    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    agent_id: Mapped[str] = mapped_column(String(64))
    workspace: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(120), default="")
    turns: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    context: Mapped[dict[str, Any] | None] = mapped_column(Json)
    channel_key: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_agent_conversations_user", "user_id", "updated_at"), Index("ix_agent_conversations_channel", "user_id", "channel_key"))
