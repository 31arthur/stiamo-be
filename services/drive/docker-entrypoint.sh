#!/bin/sh
set -e
# Stateless: no database and no migrations.
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
