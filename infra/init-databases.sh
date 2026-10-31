#!/bin/sh
# One database per service: services never share tables.
set -e
for db in stiamo_identity stiamo_records stiamo_practice stiamo_files stiamo_agent; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -c "CREATE DATABASE $db"
done
