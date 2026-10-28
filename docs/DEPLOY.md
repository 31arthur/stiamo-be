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

The stack joins your existing Traefik through the external `traefik-proxy` network (same pattern as `qs-erp`), served at
**https://stiamo-be.fullstackarthur.com**.

1. DNS: an `A` record for `stiamo-be.fullstackarthur.com` pointing at the VPS.
2. If the GHCR packages are private: `echo <token> | docker login ghcr.io -u <github-user> --password-stdin`
   (token with `read:packages`), or make the two packages public.
3. `mkdir ~/stiamo-be && cd ~/stiamo-be`, then copy in `deploy/docker-compose.prod.yml` (as `docker-compose.yml`),
   `deploy/init-databases.sh` and `deploy/.env.prod.example` (as `.env`).
4. Fill `.env`: use hex passwords (`openssl rand -hex 24`, they go into URLs), `JWT_SECRET` (`openssl rand -hex 48`),
   `GHCR_OWNER` (lowercase GitHub owner of this repo) and `CORS_ORIGINS`.
5. `docker compose up -d`

## Every release

On the VPS, `./deploy.sh` does the pull, recreate and log-follow in one go (copy `deploy/deploy.sh`, then `chmod +x deploy.sh`). Manually:

```bash
docker compose pull
docker compose up -d
```

Roll back: set `IMAGE_TAG=<short sha>` in `.env` and run the same two commands.

## Adding the practice and files services to an existing install

Postgres only creates databases the first time its volume is created, so a stack deployed before `practice` and `files`
existed needs their databases added once:

1. Add `PRACTICE_DB_PASSWORD` and `FILES_DB_PASSWORD` to `.env` (`openssl rand -hex 24` each).
2. Copy the new `docker-compose.yml`, `deploy.sh` and `add-databases.sh` from `deploy/`, then `chmod +x add-databases.sh deploy.sh`.
3. `./add-databases.sh` (safe to repeat), then `./deploy.sh`.

Uploaded files live in the `stiamo-files` Docker volume; include it in your backups next to the database dump.

## Database and migrations

- Your own PostgreSQL 16 runs in the stack with data in the `pgdata` volume. It publishes no ports; only the services reach it.
- Each service has its own database and its own role (`stiamo_identity`, `stiamo_records`), created on first start.
- **Alembic** migrations run automatically when a service container starts (`alembic upgrade head`). When you scale a
  service to several replicas, set `RUN_MIGRATIONS=false` on all but one.
- New schema change: `cd services/<name> && alembic revision -m "what changed"`, edit the file, commit. It is applied on the next deploy.
- Back up: `docker compose exec -T db pg_dumpall -U stiamo | gzip > backup-$(date +%F).sql.gz`

## Public routes (Traefik, Let's Encrypt via your `letsencrypt` resolver)

| Path | Service |
| --- | --- |
| `/v1/auth/*`, `/v1/users/*` | identity |
| `/v1/collections/*` | records |
| `/v1/tasks`, `/v1/follow-ups`, `/v1/meetings`, `/v1/cases`, `/v1/hearings`, `/v1/plans`, `/v1/goals`, `/v1/counsel`, `/v1/clients`, `/v1/projects`, `/v1/links`, `/v1/activity`, `/v1/outcomes`, `/v1/agenda`, `/v1/relations`, `/v1/steps` | practice |
| `/v1/attachments/*`, `/v1/files/*` | files |

The frontend uses `https://stiamo-be.fullstackarthur.com` as its single API base URL, and its origin goes in `CORS_ORIGINS`.
Interactive docs (`/docs`) are not routed publicly. The database is only on the private `stiamo-net` network.
