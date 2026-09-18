#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
[ -f .env ] && source .env
set +a
mkdir -p backups
stamp=$(date +%F_%H-%M-%S)
MYSQL_PWD="${MYSQL_PASSWORD:-}" mysqldump -h "${MYSQL_HOST:-127.0.0.1}" -P "${MYSQL_PORT:-3306}" -u "${MYSQL_USER:-april_ledger}" "${MYSQL_DATABASE:-april_ledger}" | gzip > "backups/april-ledger_${stamp}.sql.gz"
find backups -type f -name 'april-ledger_*.sql.gz' -mtime +7 -delete
echo "Backup created: backups/april-ledger_${stamp}.sql.gz"
