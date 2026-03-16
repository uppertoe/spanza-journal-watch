#!/usr/bin/env bash

# Migrate PostgreSQL data into a fresh Postgres 17 Docker volume using a .sql/.sql.gz dump.
#
# Default usage (production):
#   ./scripts/migrate_postgres17_from_sql.sh ./backups/your_backup.sql
#
# Optional flags:
#   --compose-file <path>   (default: production.yml)
#   --env-file <path>       (default: ./.envs/.production/.postgres)
#   --force                 Skip confirmation prompt

set -o errexit
set -o pipefail
set -o nounset

SCRIPT_NAME="$(basename "$0")"
COMPOSE_FILE="production.yml"
POSTGRES_ENV_FILE="./.envs/.production/.postgres"
FORCE="false"

usage() {
  cat <<EOF
Usage:
  ${SCRIPT_NAME} [--compose-file <path>] [--env-file <path>] [--force] <dump.sql|dump.sql.gz>

Examples:
  ${SCRIPT_NAME} ./backups/postgres14_migration_20260316_072901.sql
  ${SCRIPT_NAME} --compose-file production.yml --env-file ./.envs/.production/.postgres ./backups/prod.sql.gz

What this does:
  1) Finds the current postgres data volume attached to the compose postgres service
  2) Creates a safety snapshot volume copy of that data
  3) Removes the old postgres data volume (required for 14 -> 17 compatibility)
  4) Starts postgres (17) with a fresh volume
  5) Imports the provided SQL dump into the new postgres volume
EOF
}

log() {
  printf "[%s] %s\n" "${SCRIPT_NAME}" "$*"
}

error() {
  printf "[%s] ERROR: %s\n" "${SCRIPT_NAME}" "$*" >&2
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    error "Required command not found: $1"
    exit 1
  fi
}

confirm() {
  if [[ "${FORCE}" == "true" ]]; then
    return 0
  fi

  echo
  echo "This operation will replace the current postgres data volume after taking a snapshot."
  read -r -p "Continue? [y/N]: " answer
  case "${answer}" in
    y|Y|yes|YES) ;;
    *)
      log "Cancelled."
      exit 0
      ;;
  esac
}

SQL_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file)
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --env-file)
      POSTGRES_ENV_FILE="$2"
      shift 2
      ;;
    --force)
      FORCE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "${SQL_FILE}" ]]; then
        SQL_FILE="$1"
        shift
      else
        error "Unexpected argument: $1"
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ -z "${SQL_FILE}" ]]; then
  error "Missing dump file argument."
  usage
  exit 1
fi

require_command docker

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  error "Compose file not found: ${COMPOSE_FILE}"
  exit 1
fi

if [[ ! -f "${POSTGRES_ENV_FILE}" ]]; then
  error "Postgres env file not found: ${POSTGRES_ENV_FILE}"
  exit 1
fi

if [[ ! -f "${SQL_FILE}" ]]; then
  error "Dump file not found: ${SQL_FILE}"
  exit 1
fi

# Load DB env vars (POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB)
set -a
# shellcheck disable=SC1090
source "${POSTGRES_ENV_FILE}"
set +a

: "${POSTGRES_USER:?POSTGRES_USER must be set in env file}"
: "${POSTGRES_DB:?POSTGRES_DB must be set in env file}"

if [[ ! "${SQL_FILE}" =~ \.sql$|\.sql\.gz$ ]]; then
  error "Dump must be .sql or .sql.gz"
  exit 1
fi

# Resolve absolute path to avoid shell cwd issues with stdin redirection.
SQL_FILE_ABS="$(cd "$(dirname "${SQL_FILE}")" && pwd)/$(basename "${SQL_FILE}")"

confirm

log "Ensuring postgres service exists in compose: ${COMPOSE_FILE}"
docker compose -f "${COMPOSE_FILE}" config --services | grep -qx postgres || {
  error "No 'postgres' service found in ${COMPOSE_FILE}"
  exit 1
}

# Make sure we have (or create) a postgres container so we can discover mounted volume.
log "Preparing postgres container metadata"
docker compose -f "${COMPOSE_FILE}" up -d postgres >/dev/null

POSTGRES_CONTAINER_ID="$(docker compose -f "${COMPOSE_FILE}" ps -aq postgres | head -n1)"
if [[ -z "${POSTGRES_CONTAINER_ID}" ]]; then
  error "Could not resolve postgres container id."
  exit 1
fi

OLD_DATA_VOLUME="$(docker inspect "${POSTGRES_CONTAINER_ID}" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}')"
if [[ -z "${OLD_DATA_VOLUME}" ]]; then
  error "Could not resolve current postgres data volume."
  exit 1
fi

SNAPSHOT_VOLUME="${OLD_DATA_VOLUME}_pre_pg17_$(date +%Y%m%d_%H%M%S)"
log "Creating snapshot volume: ${SNAPSHOT_VOLUME}"
docker volume create "${SNAPSHOT_VOLUME}" >/dev/null

docker run --rm \
  -v "${OLD_DATA_VOLUME}:/from" \
  -v "${SNAPSHOT_VOLUME}:/to" \
  alpine sh -c 'cd /from && cp -a . /to'

log "Stopping dependent services (best effort)"
docker compose -f "${COMPOSE_FILE}" stop django celeryworker celerybeat flower traefik node >/dev/null 2>&1 || true

log "Stopping/removing postgres container"
docker compose -f "${COMPOSE_FILE}" stop postgres >/dev/null 2>&1 || true
docker compose -f "${COMPOSE_FILE}" rm -sf postgres >/dev/null 2>&1 || true

log "Removing old postgres data volume: ${OLD_DATA_VOLUME}"
docker volume rm "${OLD_DATA_VOLUME}" >/dev/null

log "Starting fresh postgres (17)"
docker compose -f "${COMPOSE_FILE}" up -d postgres >/dev/null

log "Waiting for postgres readiness"
for _ in $(seq 1 120); do
  if docker compose -f "${COMPOSE_FILE}" exec -T postgres pg_isready -U "${POSTGRES_USER}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker compose -f "${COMPOSE_FILE}" exec -T postgres pg_isready -U "${POSTGRES_USER}" >/dev/null 2>&1; then
  error "Postgres did not become ready in time."
  exit 1
fi

log "Importing dump into Postgres 17 volume from: ${SQL_FILE_ABS}"
if [[ "${SQL_FILE_ABS}" == *.sql.gz ]]; then
  gzip -dc "${SQL_FILE_ABS}" | docker compose -f "${COMPOSE_FILE}" exec -T postgres psql -U "${POSTGRES_USER}" -d postgres
else
  cat "${SQL_FILE_ABS}" | docker compose -f "${COMPOSE_FILE}" exec -T postgres psql -U "${POSTGRES_USER}" -d postgres
fi

log "Migration import complete."
log "Safety snapshot volume retained: ${SNAPSHOT_VOLUME}"
log "You can now start the full production stack if needed."
