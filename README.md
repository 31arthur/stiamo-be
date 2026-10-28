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

Or run everything with Docker: `docker compose up --build` (see below once added).
