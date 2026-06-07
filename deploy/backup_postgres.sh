#!/usr/bin/env bash
# Backup a PostgreSQL database for MyFilmPeople.
# Usage:
#   DATABASE_URL="postgresql://user:pass@host:5432/dbname?sslmode=require" ./backup_postgres.sh
# Or set DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASS.

set -euo pipefail

FILE=${FILE:-}
DB_URL=${DATABASE_URL:-}
DB_NAME=${DB_NAME:-myfilmpeople}
DB_USER=${DB_USER:-postgres}
DB_PASS=${DB_PASS:-}
DB_HOST=${DB_HOST:-127.0.0.1}
DB_PORT=${DB_PORT:-5432}
OUT_DIR=${OUT_DIR:-/var/backups}

if [ -z "$FILE" ]; then
  mkdir -p "$OUT_DIR"
  TIMESTAMP=$(date +"%Y%m%d-%H%M")
  FILE="$OUT_DIR/${DB_NAME}-$TIMESTAMP.dump"
fi

if [ -n "$DB_URL" ]; then
  pg_dump "$DB_URL" -Fc -f "$FILE"
else
  export PGPASSWORD="$DB_PASS"
  pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -Fc -f "$FILE" "$DB_NAME"
fi

echo "Backup written to $FILE"