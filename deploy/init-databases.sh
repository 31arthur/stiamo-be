#!/bin/sh
# Runs once, on first start of an empty data volume. One database + one least-privilege role per service.
set -e
create() {  # $1 database/role, $2 password
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE ROLE $1 LOGIN PASSWORD '$2';
CREATE DATABASE $1 OWNER $1;
REVOKE ALL ON DATABASE $1 FROM PUBLIC;
SQL
}
create stiamo_identity "$IDENTITY_DB_PASSWORD"
create stiamo_records  "$RECORDS_DB_PASSWORD"
create stiamo_practice "$PRACTICE_DB_PASSWORD"
create stiamo_files    "$FILES_DB_PASSWORD"
create stiamo_agent    "$AGENT_DB_PASSWORD"
