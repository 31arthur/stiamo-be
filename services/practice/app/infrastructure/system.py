import uuid
from datetime import UTC, datetime

from app.application.ports import Clock, IdGenerator


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator(IdGenerator):
    def next(self) -> str:
        return str(uuid.uuid4())
