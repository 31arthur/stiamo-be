import time
import uuid
from typing import Any

import jwt
import pytest
from app.agents import AGENTS
from app.application.ports import AccessTokens, DataGateway, DecisionClient, LlmClient, TelegramApi, TwilioApi
from app.application.registry import AgentRegistry
from app.application.toolpacks import PackRegistry
from app.application.toolpacks.cases import CasesPack
from app.application.toolpacks.decisions import DecisionsPack
from app.application.toolpacks.drive import DrivePack
from app.config import Settings
from app.domain.agents import AgentDefinition
from app.domain.llm import AiError, ChatResult, Message, ModelInfo, ToolSpec
from app.infrastructure.db import models as _tables  # noqa: F401  (register tables)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from stiamo_common.db import Base

OWNER = "owner@example.com"
PUBLIC = "https://api.example.com"


def make_token(email: str = OWNER, user_id: str = "u1") -> str:
    s = Settings().token_settings()
    now = int(time.time())
    return jwt.encode({"sub": user_id, "email": email, "typ": "access", "jti": str(uuid.uuid4()), "iat": now, "exp": now + 600, "iss": s.issuer, "aud": s.audience}, s.verification_key, algorithm=s.algorithm)


def headers(email: str = OWNER, user_id: str = "u1") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(email, user_id)}"}


class FakeLlm(LlmClient):
    """Answers from a script and remembers what it was asked."""

    def __init__(self, key: str, script: list[ChatResult], models: list[ModelInfo], log: list[dict[str, Any]]) -> None:
        self.key, self.script, self.models, self.log = key, script, models, log

    async def list_models(self) -> list[ModelInfo]:
        if self.key.startswith("bad"):
            raise AiError("didn't accept the key.", 401)
        return self.models

    async def chat(self, *, model: str, messages: list[Message], tools: list[ToolSpec], thinking: str) -> ChatResult:
        self.log.append({"model": model, "thinking": thinking, "system": messages[0].content, "roles": [m.role for m in messages], "last": messages[-1].content, "tools": [t.name for t in tools], "tool_descriptions": {t.name: t.description for t in tools}})
        return self.script.pop(0) if self.script else ChatResult("done")


class FakeDecisions(DecisionClient):
    def __init__(self, log: list[dict[str, Any]]) -> None:
        self.log = log

    async def decide(self, *, model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        self.log.append({"model": model, "state": state, "questions": questions})
        return {"answers": {name: {"type": q["type"], "choice": "a", "probabilities": {"a": 0.9, "b": 0.1}} for name, q in questions.items()}, "model": model, "cost": 0.00001}


class FakeData(DataGateway):
    def __init__(self) -> None:
        self._cases = [
            {"id": "c1", "reference": "SA-2026-001", "title": "Alur NHAI wages", "status": "active", "client": "Alur workers", "opposingParty": "NHAI", "courtNumber": "WP 1290/2026"},
            {"id": "c2", "reference": "SA-2026-002", "title": "Brick kiln", "status": "closed"},
        ]

    async def cases(self):
        return self._cases

    async def hearings(self):
        return [{"id": "h1", "caseId": "c1", "date": "2999-01-01", "purpose": "Arguments", "outcome": {"status": "open"}}]

    async def attachments(self):
        return []

    async def drive_available(self):
        return True

    async def drive_search(self, query):
        return [{"id": "f1" * 8, "name": "Wage Breakdown.pdf", "mimeType": "application/pdf"}]

    async def drive_suggest(self, terms):
        return [{"id": "f2" * 8, "name": "Reply from NHAI.pdf", "mimeType": "application/pdf", "matched": ["NHAI"]}]


class FakeTelegram(TelegramApi):
    def __init__(self) -> None:
        self.sent: list[tuple[Any, str]] = []
        self.webhooks: list[tuple[str, str]] = []
        self.deleted = 0

    async def get_me(self, token):
        if token == "bad":
            from stiamo_common.errors import ValidationFailed

            raise ValidationFailed("Telegram doesn't recognise that bot token.")
        return {"id": 777, "username": "stiamo_bot", "first_name": "Stiamo"}

    async def set_webhook(self, token, url, secret):
        self.webhooks.append((url, secret))

    async def delete_webhook(self, token):
        self.deleted += 1

    async def send_message(self, token, chat_id, text):
        self.sent.append((chat_id, text))

    async def send_typing(self, token, chat_id):
        pass


class FakeTwilio(TwilioApi):
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def check_account(self, sid, auth_token):
        if auth_token == "bad":
            from stiamo_common.errors import ValidationFailed

            raise ValidationFailed("Twilio didn't accept that Account SID and Auth Token.")
        return "My Twilio"

    async def send_whatsapp(self, sid, auth_token, sender, to, text):
        self.sent.append((sender, to, text))


class FakeTokens(AccessTokens):
    def mint(self, user_id, email):
        return f"minted-for-{user_id}"


#: A second agent, so the tests can show that agents don't share prompts or choices.
LIBRARIAN = AgentDefinition(id="librarian", name="Librarian", description="Finds documents.", system="You find documents for {{user}}.\n{{tools}}\nDrive: {{drive}}.", packs=("drive",), suggestions=("Newest letters?",))


def test_registry() -> AgentRegistry:
    return AgentRegistry([*AGENTS, LIBRARIAN], PackRegistry([CasesPack(), DrivePack(), DecisionsPack()]))


class Rig:
    """Everything a test needs: the app, a client and the fakes."""

    def __init__(self, app, client, script, llm_log, decision_log, telegram, twilio):
        self.app, self.client, self.script, self.llm_log, self.decision_log, self.telegram, self.twilio = app, client, script, llm_log, decision_log, telegram, twilio


@pytest.fixture
async def rig():
    script: list[ChatResult] = []
    llm_log: list[dict[str, Any]] = []
    decision_log: list[dict[str, Any]] = []
    models = [ModelInfo("o3", "o3", ("low", "medium", "high")), ModelInfo("gpt-4o", "gpt-4o", ())]
    telegram, twilio = FakeTelegram(), FakeTwilio()
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", public_url=PUBLIC)
    app = create_app(
        settings,
        registry=test_registry(),
        llm_factory=lambda provider, key: FakeLlm(key, script, models, llm_log),
        decision_factory=lambda key: FakeDecisions(decision_log),
        data_factory=lambda token: FakeData(),
        telegram=telegram,
        twilio=twilio,
        tokens=FakeTokens(),
    )
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Rig(app, client, script, llm_log, decision_log, telegram, twilio)
