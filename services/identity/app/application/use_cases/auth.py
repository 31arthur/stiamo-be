"""Use cases: one class per intent."""

from __future__ import annotations

from stiamo_common.errors import Conflict, NotFound, Unauthorized, ValidationFailed

from app.application.dto import AuthResult
from app.application.ports import (
    Clock,
    IdGenerator,
    PasswordHasher,
    RefreshTokenRepository,
    TokenService,
    UnitOfWork,
    UserRepository,
)
from app.application.session import start_session
from app.domain.entities import User

MIN_PASSWORD = 8
MAX_PASSWORD = 72  # bcrypt ignores everything past 72 bytes


def normalize_email(email: str) -> str:
    return email.strip().lower()


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValidationFailed(f"Password must be at least {MIN_PASSWORD} characters.")
    if len(password.encode()) > MAX_PASSWORD:
        raise ValidationFailed(f"Password must be at most {MAX_PASSWORD} bytes.")


class RegisterUser:
    def __init__(self, users: UserRepository, refresh_tokens: RefreshTokenRepository, hasher: PasswordHasher,
                 tokens: TokenService, clock: Clock, ids: IdGenerator) -> None:
        self._users, self._refresh, self._hasher = users, refresh_tokens, hasher
        self._tokens, self._clock, self._ids = tokens, clock, ids

    async def __call__(self, *, name: str, email: str, password: str) -> AuthResult:
        name, email = name.strip(), normalize_email(email)
        if not name:
            raise ValidationFailed("Name is required.")
        check_password_rules(password)
        if await self._users.get_by_email(email):
            raise Conflict("An account with this email already exists.")
        now = self._clock.now()
        user = User(id=self._ids.new(), email=email, name=name,
                    password_hash=self._hasher.hash(password), created_at=now)
        await self._users.add(user)
        pair, _ = await start_session(user, tokens=self._tokens, refresh_tokens=self._refresh, now=now)
        return AuthResult(user, pair)


class LoginUser:
    def __init__(self, users: UserRepository, refresh_tokens: RefreshTokenRepository, hasher: PasswordHasher,
                 tokens: TokenService, clock: Clock) -> None:
        self._users, self._refresh, self._hasher = users, refresh_tokens, hasher
        self._tokens, self._clock = tokens, clock

    async def __call__(self, *, email: str, password: str) -> AuthResult:
        user = await self._users.get_by_email(normalize_email(email))
        if user is None:
            self._hasher.verify_dummy(password)
            raise Unauthorized("Incorrect email or password.")
        if not self._hasher.verify(password, user.password_hash) or not user.is_active:
            raise Unauthorized("Incorrect email or password.")
        pair, _ = await start_session(user, tokens=self._tokens, refresh_tokens=self._refresh,
                                      now=self._clock.now())
        return AuthResult(user, pair)


class RefreshSession:
    """Exchanges a refresh token for a new pair. The old token is retired (rotation); presenting a
    retired token again means it leaked, so the whole login chain (family) is revoked."""

    def __init__(self, users: UserRepository, refresh_tokens: RefreshTokenRepository, tokens: TokenService,
                 clock: Clock, uow: UnitOfWork) -> None:
        self._users, self._refresh, self._tokens = users, refresh_tokens, tokens
        self._clock, self._uow = clock, uow

    async def __call__(self, *, refresh_token: str) -> AuthResult:
        claims = self._tokens.decode_refresh(refresh_token)
        now = self._clock.now()
        record = await self._refresh.get(claims.jti)
        if record is None or record.token_hash != self._tokens.fingerprint(refresh_token):
            raise Unauthorized("Refresh token is not valid.")
        if record.revoked_at is not None:
            await self._refresh.revoke_family(record.family_id, now)
            await self._uow.commit()  # keep the revocation even though we reject this request
            raise Unauthorized("Refresh token was already used; please sign in again.")
        if record.is_expired(now):
            raise Unauthorized("Refresh token has expired.")
        user = await self._users.get(record.user_id)
        if user is None or not user.is_active:
            raise Unauthorized("Account is not available.")
        pair, new_jti = await start_session(user, tokens=self._tokens, refresh_tokens=self._refresh,
                                            now=now, family_id=record.family_id)
        await self._refresh.revoke(record.jti, now, replaced_by=new_jti)
        return AuthResult(user, pair)


class LogoutSession:
    """Ends the login chain the refresh token belongs to. Idempotent; unknown tokens are ignored."""

    def __init__(self, refresh_tokens: RefreshTokenRepository, tokens: TokenService, clock: Clock) -> None:
        self._refresh, self._tokens, self._clock = refresh_tokens, tokens, clock

    async def __call__(self, *, refresh_token: str) -> None:
        try:
            claims = self._tokens.decode_refresh(refresh_token)
        except Unauthorized:
            return
        record = await self._refresh.get(claims.jti)
        if record is not None:
            await self._refresh.revoke_family(record.family_id, self._clock.now())


class GetProfile:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def __call__(self, *, user_id: str) -> User:
        user = await self._users.get(user_id)
        if user is None or not user.is_active:
            raise NotFound("User not found.")
        return user
