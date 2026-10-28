# stiamo-be

Backend for [Stiamo](https://github.com/31arthur/stiamo), built with **FastAPI**, **clean architecture** and a **microservices-ready** layout. Not yet connected to the frontend.

| Path | What |
| --- | --- |
| `libs/stiamo_common` | Shared kernel: error model, JWT verification, DB base |
| `services/identity` | Accounts, login, JWT access + refresh tokens |
| `services/records` | Per-user document store for every Stiamo collection |
| `docs/ARCHITECTURE.md` | Layers, service boundaries, auth flow |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Setup per service is in each service's README.

## Quick start (dev)

Each service is its own app (top-level package `app`), so run and test them one at a time from their folder.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scriptsctivate
pip install -e libs/stiamo_common
cd services/identity && pip install -e ".[dev]" && pytest      # then the same in services/records
```

Or run everything with Docker: `docker compose up --build` (see below).

See [docs/DEPLOY.md](docs/DEPLOY.md) for the GHCR → VPS pipeline.

## Run with Docker (local dev)

```bash
docker compose up --build        # postgres + identity (:8001) + records (:8002), migrations run on start
```

Interactive API docs: http://localhost:8001/docs and http://localhost:8002/docs. Set `JWT_SECRET` in your shell for anything beyond local dev.

Typical flow: `POST :8001/v1/auth/register` → use `tokens.access_token` as `Authorization: Bearer ...` against `:8002/v1/collections/tasks`; call `:8001/v1/auth/refresh` with the refresh token when the access token expires (15 min).
