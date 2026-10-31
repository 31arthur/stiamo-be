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
    selection: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    prompt: dict[str, Any] | None = None
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
