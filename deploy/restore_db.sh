#!/usr/bin/env bash
# Restore MySQL DB from backup
# Usage: FILE=/backups/myfilmpeople-20250101-1200.sql.gz DB_NAME=myfilmpeople DB_USER=user DB_PASS=pass DB_HOST=127.0.0.1 ./restore_db.sh

FILE=${FILE:-}
DB_NAME=${DB_NAME:-myfilmpeople}
DB_USER=${DB_USER:-root}
DB_PASS=${DB_PASS:-}
DB_HOST=${DB_HOST:-127.0.0.1}

if [ -z "$FILE" ]; then
  echo "Please set FILE=/path/to/backup.sql.gz"
  exit 1
fi

if [ ! -f "$FILE" ]; then
  echo "File not found: $FILE"
  exit 1
fi

if [ -z "$DB_PASS" ]; then
  gunzip -c "$FILE" | mysql -u "$DB_USER" -h "$DB_HOST" "$DB_NAME"
else
  gunzip -c "$FILE" | mysql -u "$DB_USER" -p"$DB_PASS" -h "$DB_HOST" "$DB_NAME"
fi

echo "Restore completed."
