#!/usr/bin/env bash
# Restore a PostgreSQL database for MyFilmPeople.
# Usage:
#   FILE=/backups/myfilmpeople-20250101-1200.dump DATABASE_URL="postgresql://user:pass@host:5432/dbname?sslmode=require" ./restore_postgres.sh

set -euo pipefail

FILE=${FILE:-}
DB_URL=${DATABASE_URL:-}
DB_NAME=${DB_NAME:-myfilmpeople}
DB_USER=${DB_USER:-postgres}
DB_PASS=${DB_PASS:-}
DB_HOST=${DB_HOST:-127.0.0.1}
DB_PORT=${DB_PORT:-5432}

if [ -z "$FILE" ]; then
  echo "Please set FILE=/path/to/backup.dump"
  exit 1
fi

if [ ! -f "$FILE" ]; then
  echo "File not found: $FILE"
  exit 1
fi

if [ -n "$DB_URL" ]; then
  pg_restore --clean --if-exists --no-owner --no-privileges --dbname "$DB_URL" "$FILE"
else
  export PGPASSWORD="$DB_PASS"
  pg_restore --clean --if-exists --no-owner --no-privileges -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$FILE"
fi

echo "Restore completed."