# records service

Per-user JSON documents for every Stiamo collection (`tasks`, `meetings`, `follow_ups`, `cases`, `hearings`,
`plans`, `goals`, `links`, `activity`, `attachments`, `counsel`, `clients`, `projects`). It mirrors the frontend's
`Repository` port: `list`, `get`, `save`, `saveMany`, `remove`.

All routes need `Authorization: Bearer <access token>` issued by the identity service. The token is verified
locally; the user id (`sub`) scopes every query and is stamped into each document as `userId`.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v1/collections/{collection}?after=&limit=` | `{items: [doc], next_cursor}` ordered by id, `limit` 1-1000 (default 500) |
| GET | `/v1/collections/{collection}/{id}` | the document, or 404 |
| PUT | `/v1/collections/{collection}/{id}` | upsert (replaces the document) |
| POST | `/v1/collections/{collection}/batch` | `{items: [doc...]}` (each needs `id`, max 500) -> `{saved}` |
| DELETE | `/v1/collections/{collection}/{id}` | idempotent, 204 |

```bash
pip install -e ../../libs/stiamo_common -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:create_app --factory --reload --port 8002
pytest
```
