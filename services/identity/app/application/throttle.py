"""Slows down guessing passwords: too many wrong tries for one email from one place pauses sign-in for a while."""

import time
from collections.abc import Callable

from stiamo_common.errors import AppError


class TooManyAttempts(AppError):
    """Too many wrong passwords. Wait a little before trying again."""

    status_code = 429
    code = "too_many_attempts"


class LoginThrottle:
    """Counts recent failures per key in memory (one server process). A right password clears the count."""

    def __init__(self, max_failures: int = 5, lockout_seconds: int = 900, now: Callable[[], float] = time.monotonic) -> None:
        self._max, self._lockout, self._now = max_failures, lockout_seconds, now
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = self._now() - self._lockout
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def check(self, key: str) -> None:
        recent = self._recent(key)
        if len(recent) >= self._max:
            wait = max(1, int(recent[0] + self._lockout - self._now()))
            raise TooManyAttempts(f"Too many wrong passwords. Try again in {-(-wait // 60)} minute(s).")

    def failed(self, key: str) -> None:
        self._failures.setdefault(key, []).append(self._now())
        if len(self._failures) > 10_000:  # never grow without bound
            self._failures.pop(next(iter(self._failures)))

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)
