#!/usr/bin/env bash
# Start local Postgres for Countersign without docker compose. Reads COUNTERSIGN_DB_PASSWORD from .env.
set -euo pipefail
cd "$(dirname "$0")/.."
PASSWORD=$(grep -E '^COUNTERSIGN_DB_PASSWORD=' .env | cut -d= -f2-)
if docker ps -a --format '{{.Names}}' | grep -qx countersign-db; then
  docker start countersign-db >/dev/null
else
  docker run -d --name countersign-db -p 127.0.0.1:55432:5432 \
    -e POSTGRES_DB=countersign -e POSTGRES_USER=countersign -e POSTGRES_PASSWORD="$PASSWORD" \
    -v countersign-pg:/var/lib/postgresql/data postgres:17.6-alpine >/dev/null
fi
until docker exec countersign-db pg_isready -U countersign -d countersign >/dev/null 2>&1; do sleep 1; done
echo "countersign-db ready on 127.0.0.1:55432"
