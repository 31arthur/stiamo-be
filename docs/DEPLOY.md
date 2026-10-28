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

```bash
docker compose pull
docker compose up -d
```

Roll back: set `IMAGE_TAG=<short sha>` in `.env` and run the same two commands.

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

The frontend uses `https://stiamo-be.fullstackarthur.com` as its single API base URL, and its origin goes in `CORS_ORIGINS`.
Interactive docs (`/docs`) are not routed publicly. The database is only on the private `stiamo-net` network.
