#!/usr/bin/env bash

# Migrate PostgreSQL data into a fresh Postgres 17 Docker volume using a .sql/.sql.gz dump.
#
# Default usage (production):
#   ./ops/migrate_postgres.sh ./backups/your_backup.sql
#
# Optional flags:
#   --compose-file <path>   (default: compose.prod.example.yml)
#   --env-file <path>       (default: .env)
#   --service <name>        (default: postgres)
#   --force                 Skip confirmation prompt

set -o errexit
set -o pipefail
set -o nounset

SCRIPT_NAME="$(basename "$0")"
COMPOSE_FILE="compose.prod.example.yml"
POSTGRES_ENV_FILE=".env"
POSTGRES_SERVICE="postgres"
FORCE="false"
COMPOSE_PROJECT_DIR=""

usage() {
  cat <<EOF
Usage:
  ${SCRIPT_NAME} [--compose-file <path>] [--env-file <path>] [--service <name>] [--force] <dump.sql|dump.sql.gz>

Examples:
  ${SCRIPT_NAME} ./backups/postgres14_migration_20260316_072901.sql
  ${SCRIPT_NAME} --compose-file compose.prod.example.yml --env-file .env ./backups/prod.sql.gz
  ${SCRIPT_NAME} --compose-file apps/journalwatch/docker-compose.yml --env-file apps/journalwatch/.env --service jw_postgres ./backups/prod.sql.gz

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

compose() {
  docker compose -f "${COMPOSE_FILE}" --project-directory "${COMPOSE_PROJECT_DIR}" "$@"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    error "Required command not found: $1"
    exit 1
  fi
}

extract_env_var() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${POSTGRES_ENV_FILE}" | tail -n1 || true)"
  line="${line#${key}=}"
  line="${line%\"}"
  line="${line#\"}"
  printf "%s" "${line}"
}

dump_stream() {
  if [[ "${SQL_FILE_ABS}" == *.sql.gz ]]; then
    gzip -dc "${SQL_FILE_ABS}"
  else
    cat "${SQL_FILE_ABS}"
  fi
}

sanitize_sql_dump() {
  sed -E '
    /^\\restrict\b/d
    /^\\unrestrict\b/d
    /^ALTER .* OWNER TO /d
    /^GRANT .* TO /d
    /^REVOKE .* FROM /d
  '
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
    --service)
      POSTGRES_SERVICE="$2"
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

COMPOSE_PROJECT_DIR="$(cd "$(dirname "${COMPOSE_FILE}")" && pwd)"
COMPOSE_FILE="$(cd "$(dirname "${COMPOSE_FILE}")" && pwd)/$(basename "${COMPOSE_FILE}")"
POSTGRES_ENV_FILE="$(cd "$(dirname "${POSTGRES_ENV_FILE}")" && pwd)/$(basename "${POSTGRES_ENV_FILE}")"

POSTGRES_USER="$(extract_env_var POSTGRES_USER)"
POSTGRES_DB="$(extract_env_var POSTGRES_DB)"

: "${POSTGRES_USER:?POSTGRES_USER must be set in env file}"
: "${POSTGRES_DB:?POSTGRES_DB must be set in env file}"

if [[ ! "${SQL_FILE}" =~ \.sql$|\.sql\.gz$ ]]; then
  error "Dump must be .sql or .sql.gz"
  exit 1
fi

# Resolve absolute path to avoid shell cwd issues with stdin redirection.
SQL_FILE_ABS="$(cd "$(dirname "${SQL_FILE}")" && pwd)/$(basename "${SQL_FILE}")"

confirm

log "Ensuring postgres service '${POSTGRES_SERVICE}' exists in compose: ${COMPOSE_FILE}"
compose config --services | grep -qx "${POSTGRES_SERVICE}" || {
  error "No '${POSTGRES_SERVICE}' service found in ${COMPOSE_FILE}"
  exit 1
}

# Make sure we have (or create) a postgres container so we can discover mounted volume.
log "Preparing postgres container metadata"
compose up -d "${POSTGRES_SERVICE}" >/dev/null

POSTGRES_CONTAINER_ID="$(compose ps -aq "${POSTGRES_SERVICE}" | head -n1)"
if [[ -z "${POSTGRES_CONTAINER_ID}" ]]; then
  error "Could not resolve ${POSTGRES_SERVICE} container id."
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

log "Stopping compose project (best effort)"
compose stop >/dev/null 2>&1 || true

log "Stopping/removing postgres container"
compose stop "${POSTGRES_SERVICE}" >/dev/null 2>&1 || true
compose rm -sf "${POSTGRES_SERVICE}" >/dev/null 2>&1 || true

log "Removing old postgres data volume: ${OLD_DATA_VOLUME}"
docker volume rm "${OLD_DATA_VOLUME}" >/dev/null

log "Starting fresh postgres (17)"
compose up -d "${POSTGRES_SERVICE}" >/dev/null

log "Waiting for postgres readiness"
for _ in $(seq 1 120); do
  if compose exec -T "${POSTGRES_SERVICE}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! compose exec -T "${POSTGRES_SERVICE}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
  error "Postgres did not become ready in time."
  exit 1
fi

log "Importing sanitized dump into database '${POSTGRES_DB}' from: ${SQL_FILE_ABS}"
dump_stream \
  | sanitize_sql_dump \
  | compose exec -T "${POSTGRES_SERVICE}" psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"

log "Migration import complete."
log "Safety snapshot volume retained: ${SNAPSHOT_VOLUME}"
log "You can now start the full production stack if needed."
