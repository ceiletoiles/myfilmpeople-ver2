#!/usr/bin/env bash
# Backup MySQL DB for MyFilmPeople
# Usage: DB_NAME=myfilmpeople DB_USER=user DB_PASS=pass DB_HOST=127.0.0.1 ./backup_db.sh

DB_NAME=${DB_NAME:-myfilmpeople}
DB_USER=${DB_USER:-root}
DB_PASS=${DB_PASS:-}
DB_HOST=${DB_HOST:-127.0.0.1}
OUT_DIR=${OUT_DIR:-/var/backups}

TIMESTAMP=$(date +"%Y%m%d-%H%M")
OUTFILE="$OUT_DIR/${DB_NAME}-$TIMESTAMP.sql.gz"

mkdir -p "$OUT_DIR"

if [ -z "$DB_PASS" ]; then
  mysqldump -u "$DB_USER" -h "$DB_HOST" "$DB_NAME" | gzip > "$OUTFILE"
else
  mysqldump -u "$DB_USER" -p"$DB_PASS" -h "$DB_HOST" "$DB_NAME" | gzip > "$OUTFILE"
fi

echo "Backup written to $OUTFILE"
