#!/usr/bin/env bash
# Pull the latest images, recreate the app containers (the database is left running), then follow the API logs.
set -euo pipefail

# Run from the folder this script lives in (where docker-compose.yml and .env are).
cd "$(dirname "$(readlink -f "$0")")"

APP_SERVICES=(identity records practice files drive)   # the database ("db") is deliberately not recreated

echo "==> Pulling latest images..."
docker compose pull "${APP_SERVICES[@]}"

echo "==> Recreating app containers (migrations run on start)..."
docker compose up -d --force-recreate --remove-orphans "${APP_SERVICES[@]}"

echo "==> Container status:"
docker compose ps

echo "==> Cleaning up dangling images..."
docker image prune -f >/dev/null

echo "==> Following logs for ${APP_SERVICES[*]} (Ctrl+C to stop; the containers keep running)..."
docker compose logs -f --tail 100 "${APP_SERVICES[@]}"
