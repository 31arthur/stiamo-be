#!/bin/sh
set -e
# Apply Alembic migrations first. Set RUN_MIGRATIONS=false on extra replicas so only one instance migrates.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
