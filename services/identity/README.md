# identity service

Accounts and JWT authentication.

| Method | Path | Body | Result |
| --- | --- | --- | --- |
| POST | `/v1/auth/register` | `name, email, password` | `201 {user, tokens}` |
| POST | `/v1/auth/login` | `email, password` | `{user, tokens}` |
| POST | `/v1/auth/refresh` | `refresh_token` | `{user, tokens}` (old refresh token is retired) |
| POST | `/v1/auth/logout` | `refresh_token` | `204`, ends that login session |
| GET | `/v1/users/me` | bearer access token | `{id, name, email, created_at}` |

`tokens` = `{access_token, refresh_token, token_type: "bearer", expires_in}`. Errors: `{"error": {"code", "message"}}`.

```bash
pip install -e ../../libs/stiamo_common -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:create_app --factory --reload --port 8001
pytest
```

Tests use in-memory SQLite; production uses PostgreSQL (`asyncpg`).
