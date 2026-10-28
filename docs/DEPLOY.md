# Deploying to the VPS

```
git push → main ──► GitHub Actions (publish.yml) ──► tests ──► build images ──► ghcr.io
                                                                              │
VPS:  docker compose pull && up -d  ◄─────────────────────────────────────────┘
```

## What the pipeline publishes

On every push to `main` (or manual *Run workflow*), after tests pass:

- `ghcr.io/<owner>/stiamo-be-identity` and `ghcr.io/<owner>/stiamo-be-records`
- tags: `latest` and the short commit SHA (use the SHA to pin or roll back)

The workflow uses the built-in `GITHUB_TOKEN`; no secrets to configure. Packages are private by default
(*Profile → Packages → package settings* to make them public if you prefer).

## One-time VPS setup

1. Install Docker + the compose plugin. Point a DNS `A` record for your API domain at the VPS; open ports 80/443.
2. If the packages are private: create a GitHub personal access token with `read:packages`, then
   `echo <token> | docker login ghcr.io -u <github-user> --password-stdin`.
3. Copy `deploy/docker-compose.prod.yml`, `deploy/Caddyfile`, `deploy/init-databases.sh` and `deploy/.env.prod.example`
   to a folder on the VPS (e.g. `/opt/stiamo`); rename the example to `.env` and fill in the passwords and
   `JWT_SECRET` (`openssl rand -hex 48`), `GHCR_OWNER` (lowercase) and `DOMAIN`.
4. `docker compose -f docker-compose.prod.yml up -d`

## Every release

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Roll back: set `IMAGE_TAG=<short sha>` in `.env` and run the same two commands.

## Database and migrations

- Your own PostgreSQL 16 runs in the stack with data in the `pgdata` volume. It publishes no ports; only the services reach it.
- Each service has its own database and its own role (`stiamo_identity`, `stiamo_records`), created on first start.
- **Alembic** migrations run automatically when a service container starts (`alembic upgrade head`). When you scale a
  service to several replicas, set `RUN_MIGRATIONS=false` on all but one.
- New schema change: `cd services/<name> && alembic revision -m "what changed"`, edit the file, commit. It is applied on the next deploy.
- Back up: `docker compose -f docker-compose.prod.yml exec -T postgres pg_dumpall -U postgres | gzip > backup-$(date +%F).sql.gz`

## Public routes (Caddy, automatic HTTPS)

| Path | Service |
| --- | --- |
| `/v1/auth/*`, `/v1/users/*` | identity |
| `/v1/collections/*` | records |

The frontend will use `https://<DOMAIN>` as its single API base URL. Remember to put its origin in `CORS_ORIGINS`.
