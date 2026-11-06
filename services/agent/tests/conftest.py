import asyncio
import time
import uuid
from typing import Any

import jwt
import pytest
from app.agents import AGENTS
from app.application.ports import (
    AccessTokens,
    CallApi,
    DataGateway,
    DecisionClient,
    LlmClient,
    TelegramApi,
    TwilioApi,
    VoiceApi,
)
from app.application.registry import AgentRegistry
from app.application.toolpacks import PackRegistry
from app.application.toolpacks.agenda import AgendaPack
from app.application.toolpacks.attention import AttentionPack
from app.application.toolpacks.cases import CasesPack
from app.application.toolpacks.changes import ChangesPack
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
        item = self.script.pop(0) if self.script else ChatResult("done")
        if isinstance(item, Exception):
            raise item
        return item


class FakeDecisions(DecisionClient):
    def __init__(self, log: list[dict[str, Any]]) -> None:
        self.log = log

    async def decide(self, *, model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        self.log.append({"model": model, "state": state, "questions": questions})
        def answer(q: dict[str, Any]) -> dict[str, Any]:
            options = list(q["criteria"]) if isinstance(q.get("criteria"), dict) and q["criteria"] else ["a", "b"]
            return {"type": q["type"], "choice": options[0], "probabilities": {options[0]: 0.9, **{o: 0.1 for o in options[1:]}}}

        return {"answers": {name: answer(q) for name, q in questions.items()}, "model": model, "cost": 0.00001}


class FakeData(DataGateway):
    def __init__(self) -> None:
        self.trashed: list[int] = []
        #: What the planning tools read besides the cases and tasks: hearings, meetings, follow-ups, plans.
        self.extra_hearings: list[dict] = []
        self.saved: list[tuple[str, dict]] = []
        #: Events in the owner's Google calendars, as the calendar service gives them.
        self.calendar: list[dict] = []
        self.tables: dict[str, list[dict]] = {}
        self.meetings: list[dict] = []
        #: News articles the feeds would give, newest first, and which feeds were asked.
        self.articles: list[dict] = []
        self.feeds_asked: list[list[str]] = []
        #: Whole bodies for the emails that need one (the list only has the start).
        self.bodies: dict[int, str] = {}
        self.trashed_by: list[tuple[str, int]] = []
        self.mail = [
            {"uid": 4, "subject": "Partnership opportunity", "sender": {"name": "Lead", "address": "lead@client.io"}, "snippet": "We would like a quote for a new app", "date": "2026-10-04T10:00:00Z"},
            {"uid": 3, "subject": "50% off everything", "sender": {"name": "Shop", "address": "deals@shop.com"}, "snippet": "Huge sale", "date": "2026-10-03T10:00:00Z"},
            {"uid": 2, "subject": "Re: workshop", "sender": {"name": "Friend", "address": "friend@elsewhere.org"}, "snippet": "see you", "date": "2026-10-02T10:00:00Z"},
            {"uid": 1, "subject": "Newsletter", "sender": {"name": "BWI", "address": "news@bwint.org"}, "snippet": "union news", "date": "2026-10-01T10:00:00Z"},
        ]
        self._cases = [
            {"id": "c1", "reference": "SA-2026-001", "title": "Alur NHAI wages", "status": "active", "client": "Alur workers", "opposingParty": "NHAI", "courtNumber": "WP 1290/2026"},
            {"id": "c2", "reference": "SA-2026-002", "title": "Brick kiln", "status": "closed"},
        ]

    async def cases(self):
        return self._cases

    async def hearings(self):
        return [{"id": "h1", "caseId": "c1", "date": "2999-01-01", "purpose": "Arguments", "outcome": {"status": "open"}}, *self.extra_hearings]

    async def attachments(self):
        return []

    async def records(self, table):
        if table == "tasks" and "tasks" not in self.tables:
            return [
                {"id": "t1", "title": "Pay the court fee", "status": "open", "workspace": "sb", "updatedAt": "2999-01-01T00:00:00Z"},
                {"id": "t2", "title": "Buy milk", "workspace": "personal", "updatedAt": "2998-01-01T00:00:00Z"},
                {"id": "t3", "title": "Old filing", "workspace": "sb", "updatedAt": "2001-01-01T00:00:00Z"},
            ]
        return self.tables.get(table, [])

    async def mail_uids(self, account_id, folder, since, before):
        if account_id == "down":
            raise AiError("The server didn't accept that username and password.")
        rows = [m for m in self.mail if (since is None or m["date"][:10] >= since.isoformat()) and (before is None or m["date"][:10] < before.isoformat())]
        return {"uidValidity": 5, "uids": sorted((m["uid"] for m in rows), reverse=True)}

    async def mail_summaries(self, account_id, folder, uids):
        return sorted((m for m in self.mail if m["uid"] in uids), key=lambda m: -m["uid"])

    async def feed_items(self, feeds, since_days, known_ids, limit):
        from app.domain.workflows import article_id

        fresh = [a for a in self.articles if article_id(a["guid"]) not in known_ids]
        self.feeds_asked.append([f["url"] for f in feeds])
        return fresh[:limit], len(fresh)

    async def mail_message(self, account_id, folder, uid):
        m = next((x for x in self.mail if x["uid"] == uid), None)
        if m is None:
            raise AiError("That message isn't in the mailbox any more.")
        return {"uid": uid, "subject": m["subject"], "sender": m["sender"], "text": self.bodies.get(uid, m["snippet"]), "date": m["date"]}

    async def create_meeting(self, record):
        self.meetings.append(record)
        return record["id"]

    async def mail_correspondents(self, account_id):
        return ["friend@elsewhere.org"]

    async def mail_trash(self, account_id, folder, uids):
        self.trashed.extend(uids)
        self.trashed_by.extend((account_id, u) for u in uids)
        return len(uids)

    async def calendar_events(self, start, end):
        return [e for e in self.calendar if start <= e["date"] <= end]

    async def save_record(self, table, record):
        self.saved.append((table, record))

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
        self.voices: list[tuple[int | str, bytes, str]] = []

    async def get_me(self, token):
        if token == "bad":
            from stiamo_common.errors import ValidationFailed

            raise ValidationFailed("Telegram doesn't recognise that bot token.")
        return {"id": 777, "username": "stiamo_bot", "first_name": "Stiamo"}

    async def set_webhook(self, token, url, secret):
        self.webhooks.append((url, secret))

    async def delete_webhook(self, token):
        self.deleted += 1

    async def send_message(self, token, chat_id, text, html=False):
        self.sent.append((chat_id, text))

    async def send_typing(self, token, chat_id):
        pass

    async def send_voice(self, token, chat_id, audio, caption=""):
        self.voices.append((chat_id, audio, caption))


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


class FakeVoice(VoiceApi):
    """Speaks by returning the words as bytes, and remembers what it was asked to say."""

    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    async def check(self, key):
        if key.startswith("bad"):
            from stiamo_common.errors import ValidationFailed

            raise ValidationFailed("Deepgram didn't accept that key.")

    async def speak(self, key, text, model):
        self.said.append((model, text))
        return text.encode()


class FakeCaller(CallApi):
    """Rings by writing the call down."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int]] = []
        self.fail = False

    async def call(self, user, text, lang, repeat):
        if self.fail:
            from stiamo_common.errors import ValidationFailed

            raise ValidationFailed("CallMeBot couldn't place the call. Calls are disabled in Telegram.")
        self.calls.append((user, text, lang, repeat))


class FakeMailEvents:
    """The mail service's feed, in the test: nothing happens until `poke()` says new mail came."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.opened: list[tuple[str, str]] = []

    def poke(self, event: str = "changed") -> None:
        self.queue.put_nowait(event)

    async def __call__(self, account_id: str, folder: str, token: str):
        self.opened.append((account_id, folder))
        while True:
            yield await self.queue.get()


class FakeTokens(AccessTokens):
    def mint(self, user_id, email):
        return f"minted-for-{user_id}"


#: A second agent, so the tests can show that agents don't share prompts or choices.
LIBRARIAN = AgentDefinition(id="librarian", name="Librarian", description="Finds documents.", system="You find documents for {{user}}.\n{{tools}}\nDrive: {{drive}}.", packs=("drive",), suggestions=("Newest letters?",))


def test_registry() -> AgentRegistry:
    return AgentRegistry([*AGENTS, LIBRARIAN], PackRegistry([CasesPack(), DrivePack(), DecisionsPack(), AgendaPack(), AttentionPack(), ChangesPack()]))


class Rig:
    """Everything a test needs: the app, a client and the fakes."""

    def __init__(self, app, client, script, llm_log, decision_log, telegram, twilio, data, events, voice=None, caller=None):
        self.app, self.client, self.script, self.llm_log, self.decision_log, self.telegram, self.twilio, self.data, self.events, self.voice, self.caller = app, client, script, llm_log, decision_log, telegram, twilio, data, events, voice, caller


@pytest.fixture
async def rig():
    script: list[ChatResult] = []
    llm_log: list[dict[str, Any]] = []
    decision_log: list[dict[str, Any]] = []
    models = [ModelInfo("o3", "o3", ("low", "medium", "high")), ModelInfo("gpt-4o", "gpt-4o", ())]
    telegram, twilio, data, events, voice = FakeTelegram(), FakeTwilio(), FakeData(), FakeMailEvents(), FakeVoice()
    caller = FakeCaller()
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", public_url=PUBLIC)
    app = create_app(
        settings,
        mail_events=events,
        registry=test_registry(),
        llm_factory=lambda provider, key: FakeLlm(key, script, models, llm_log),
        decision_factory=lambda key: FakeDecisions(decision_log),
        data_factory=lambda token: data,
        telegram=telegram,
        twilio=twilio,
        tokens=FakeTokens(),
        voice=voice,
        caller=caller,
    )
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Rig(app, client, script, llm_log, decision_log, telegram, twilio, data, events, voice, caller)
