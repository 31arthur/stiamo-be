"""Ports: what the use cases need from the outside world."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.llm import ChatResult, Message, ModelInfo, ToolSpec


class LlmClient(ABC):
    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """The models this key can use. Raises AiError."""

    @abstractmethod
    async def chat(self, *, model: str, messages: list[Message], tools: list[ToolSpec], thinking: str) -> ChatResult:
        """One turn. Raises AiError."""


class DecisionClient(ABC):
    @abstractmethod
    async def decide(self, *, model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        """Typed answers (with probabilities) to typed questions about `state`. Raises AiError."""


class DataGateway(ABC):
    """The practice's own data, as the signed-in person would see it."""

    @abstractmethod
    async def cases(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def hearings(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def attachments(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def records(self, table: str) -> list[dict[str, Any]]:
        """Every record of a Stiamo collection (see `TABLES`), as the practice service gives them."""

    @abstractmethod
    async def drive_available(self) -> bool: ...

    @abstractmethod
    async def drive_search(self, query: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def drive_suggest(self, terms: list[str]) -> list[dict[str, Any]]: ...


@dataclass
class UserSettings:
    user_id: str
    email: str = ""
    #: provider id -> API key (decrypted).
    keys: dict[str, str] = field(default_factory=dict)
    #: agent id -> {providerId, modelId, thinking}. The key "*" is the choice made last anywhere, which an agent
    #: that has none of its own starts from.
    selections: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision: dict[str, Any] | None = None
    #: agent id -> the prompt this person wrote for it. An agent with no entry uses its own default.
    prompts: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: agent id -> the workspaces it is shown in. An agent with no entry uses its own default.
    workspaces: dict[str, list[str]] = field(default_factory=dict)
    updated_at: datetime | None = None


class SettingsRepository(ABC):
    @abstractmethod
    async def get(self, user_id: str) -> UserSettings | None: ...

    @abstractmethod
    async def save(self, settings: UserSettings) -> None: ...


@dataclass
class Channel:
    id: str
    user_id: str
    kind: str  # "telegram" | "whatsapp"
    #: Secrets (decrypted): the bot token or the Twilio auth token, and the webhook secret.
    secrets: dict[str, str] = field(default_factory=dict)
    #: What can be shown: the bot's name, the sender number, who is allowed.
    public: dict[str, Any] = field(default_factory=dict)
    #: Working state: the pairing code, the last Telegram update handled.
    state: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


class ChannelRepository(ABC):
    @abstractmethod
    async def get(self, channel_id: str) -> Channel | None: ...

    @abstractmethod
    async def for_user(self, user_id: str) -> list[Channel]: ...

    @abstractmethod
    async def save(self, channel: Channel) -> None: ...

    @abstractmethod
    async def delete(self, channel_id: str) -> None: ...


@dataclass
class RunRecord:
    """One time an agent was asked, kept so a person can see what it was given and what it did."""

    id: str
    user_id: str
    agent_id: str
    #: chat, telegram, whatsapp, test or schedule.
    trigger: str
    #: ok or error.
    status: str
    started_at: datetime
    duration_ms: int = 0
    provider: str = ""
    model: str = ""
    thinking: str = "off"
    #: What was asked, as typed.
    input: str = ""
    #: The message as the model got it (the input wrapped by the agent's template) and the system prompt, filled in.
    user_message: str = ""
    system_prompt: str = ""
    #: The tools the model was offered.
    tools: list[str] = field(default_factory=list)
    #: What the model did, one entry per tool call (see Step.to_log).
    steps: list[dict[str, Any]] = field(default_factory=list)
    #: How many steps, kept apart so the history list needn't load the steps themselves.
    steps_count: int = 0
    output: str = ""
    found_count: int = 0
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    channel: str | None = None
    #: Which version of a workflow agent ran (0 for chat agents).
    version: int = 0


class RunLog(ABC):
    """Where runs are kept. Writes never share the request's transaction: a failed run is logged too."""

    @abstractmethod
    async def add(self, record: RunRecord) -> None: ...

    @abstractmethod
    async def recent(self, user_id: str, agent_id: str, *, limit: int, before: datetime | None = None) -> list[RunRecord]: ...

    @abstractmethod
    async def get(self, user_id: str, run_id: str) -> RunRecord | None: ...

    @abstractmethod
    async def since(self, user_id: str, agent_id: str, since: datetime) -> list[RunRecord]:
        """Light copies (no prompts or steps) of every run since a time, for statistics."""

    @abstractmethod
    async def count(self, user_id: str, agent_id: str) -> int: ...

    @abstractmethod
    async def clear(self, user_id: str, agent_id: str) -> int: ...


class TelegramApi(ABC):
    @abstractmethod
    async def get_me(self, token: str) -> dict[str, Any]: ...

    @abstractmethod
    async def set_webhook(self, token: str, url: str, secret: str) -> None: ...

    @abstractmethod
    async def delete_webhook(self, token: str) -> None: ...

    @abstractmethod
    async def send_message(self, token: str, chat_id: int | str, text: str) -> None: ...

    @abstractmethod
    async def send_typing(self, token: str, chat_id: int | str) -> None: ...


class TwilioApi(ABC):
    @abstractmethod
    async def check_account(self, sid: str, auth_token: str) -> str:
        """The account's friendly name. Raises AiError-like ValidationFailed on bad credentials."""

    @abstractmethod
    async def send_whatsapp(self, sid: str, auth_token: str, sender: str, to: str, text: str) -> None: ...


class AccessTokens(ABC):
    @abstractmethod
    def mint(self, user_id: str, email: str) -> str:
        """A short-lived access token for the owner, so the other services answer as they would to the browser."""


@dataclass
class Workflow:
    """An agent a person built: a source, filters, a prompt and labels. Configuration only; the pipeline is code."""

    id: str
    user_id: str
    name: str
    description: str
    config: dict[str, Any]
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_run_at: datetime | None = None


@dataclass
class LedgerEntry:
    """What was decided about one item, and what a person did with it. Keeps a re-run from deciding the same item twice."""

    id: str
    agent_id: str
    user_id: str
    item_id: str
    title: str = ""
    #: A short excerpt, not the whole item.
    excerpt: str = ""
    label: str | None = None
    confidence: float = 0.0
    reason: str = ""
    #: proposed, needs_review, approved, dismissed.
    status: str = "proposed"
    run_id: str = ""
    version: int = 1
    created_at: datetime | None = None
    decided_at: datetime | None = None


class WorkflowRepository(ABC):
    @abstractmethod
    async def all(self, user_id: str) -> list[Workflow]: ...

    @abstractmethod
    async def get(self, user_id: str, workflow_id: str) -> Workflow | None: ...

    @abstractmethod
    async def save(self, workflow: Workflow) -> None:
        """Creates it, or replaces it and keeps the earlier version's configuration."""

    @abstractmethod
    async def delete(self, user_id: str, workflow_id: str) -> None:
        """The workflow, its versions and its ledger."""

    @abstractmethod
    async def seen(self, workflow_id: str) -> set[str]:
        """The ids of every item already decided."""

    @abstractmethod
    async def record(self, entries: list[LedgerEntry]) -> None:
        """Adds entries, replacing an earlier entry for the same item."""

    @abstractmethod
    async def review(self, user_id: str, workflow_id: str, statuses: list[str], limit: int = 200) -> list[LedgerEntry]: ...

    @abstractmethod
    async def decide(self, user_id: str, workflow_id: str, item_ids: list[str], status: str, label: str | None = None) -> int:
        """Approve or dismiss items awaiting a person. Returns how many were changed."""

    @abstractmethod
    async def counts(self, workflow_id: str) -> dict[str, int]:
        """How many ledger entries are in each status."""

    @abstractmethod
    async def touch(self, workflow_id: str, when: datetime) -> None: ...
