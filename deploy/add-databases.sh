#!/usr/bin/env bash
# Creates (or re-syncs) the database user and database of each service on a Postgres that is already running.
# Postgres only runs init-databases.sh when its data volume is first created, so a stack that was deployed before
# the practice and files services existed needs this once. Safe to run again: it never touches existing data.
#
#   ./add-databases.sh        # run from the folder that holds docker-compose.yml and .env
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

set -a
. ./.env
set +a

declare -A passwords=(
  [identity]="${IDENTITY_DB_PASSWORD:?missing IDENTITY_DB_PASSWORD}"
  [records]="${RECORDS_DB_PASSWORD:?missing RECORDS_DB_PASSWORD}"
  [practice]="${PRACTICE_DB_PASSWORD:?add PRACTICE_DB_PASSWORD to .env (openssl rand -hex 24)}"
  [files]="${FILES_DB_PASSWORD:?add FILES_DB_PASSWORD to .env (openssl rand -hex 24)}"
)

for service in "${!passwords[@]}"; do
  name="stiamo_${service}"
  password="${passwords[$service]}"
  echo "==> ${name}"
  docker compose exec -T db psql -U stiamo -d postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${name}') THEN
    CREATE ROLE ${name} LOGIN PASSWORD '${password}';
  ELSE
    ALTER ROLE ${name} LOGIN PASSWORD '${password}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${name} OWNER ${name}'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${name}')\gexec
REVOKE ALL ON DATABASE ${name} FROM PUBLIC;
SQL
done
echo "Done. Now run ./deploy.sh"
