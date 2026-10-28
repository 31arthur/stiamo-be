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

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e libs/stiamo_common -e "services/identity[dev]" -e "services/records[dev]"
pytest services/identity services/records libs/stiamo_common
```
