#!/usr/bin/env bash
# =============================================================================
# restore.sh — Restore a Django or Planka database from a Restic snapshot.
# =============================================================================
#
# USAGE
#   restore.sh [OPTIONS]
#
#   Options:
#     --database django|planka|all
#                     Which database(s) to restore (default: all).
#     --snapshot ID   Restic snapshot ID (default: latest).
#     --target DB     Target database name.  Defaults to the real database name.
#                     Set to a different name (e.g. django_restore_test) to
#                     restore into a new database without touching production.
#                     The target database is created if it does not exist.
#     --list          List available snapshots and exit.
#     --dry-run       Print commands without executing.
#
# ENVIRONMENT
#   Same variables as backup.sh.  At minimum:
#     RESTIC_REPOSITORY, RESTIC_PASSWORD,
#     POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
#     PLANKA_DB_HOST
#
# EXAMPLES
#   # List available snapshots
#   restore.sh --list
#
#   # Test restore into a temporary database (safe — does not touch production)
#   restore.sh --database django --target django_restore_test
#
#   # Restore Planka into a test database
#   restore.sh --database planka --target planka_restore_test
#
#   # Production restore from a specific snapshot (destructive — prompts first)
#   restore.sh --database django --snapshot abc1234
#
# SAFETY
#   When --target matches the real database name, the script prints a prominent
#   warning and requires interactive confirmation unless RESTORE_NO_CONFIRM=true
#   is set.  Use RESTORE_NO_CONFIRM=true only in automated testing pipelines.
#
# TESTING LOCALLY
#   docker compose --profile backup run --rm backup \
#     /backup/restore.sh --database django --target django_restore_test
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

DATABASE="all"
SNAPSHOT="latest"
TARGET_DJANGO=""
TARGET_PLANKA=""
LIST_ONLY=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --database) DATABASE="$2";  shift 2 ;;
    --snapshot) SNAPSHOT="$2";  shift 2 ;;
    --target)   TARGET_DJANGO="$2"; TARGET_PLANKA="$2"; shift 2 ;;
    --list)     LIST_ONLY=true; shift ;;
    --dry-run)  DRY_RUN=true;   shift ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--database django|planka|all] [--snapshot ID] [--target DB] [--list] [--dry-run]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log()  { echo "[$(date -u +%H:%M:%SZ)] $*"; }
info() { log "INFO  $*"; }
warn() { log "WARN  $*"; }
die()  { log "ERROR $*" >&2; exit 1; }

run() {
  if "$DRY_RUN"; then
    echo "DRY-RUN: $*"
  else
    "$@"
  fi
}

# ---------------------------------------------------------------------------
# Environment validation
# ---------------------------------------------------------------------------

require_var() {
  local var="$1"
  [[ -n "${!var:-}" ]] || die "Required environment variable \$$var is not set."
}

check_required_vars() {
  require_var RESTIC_REPOSITORY
  require_var RESTIC_PASSWORD

  if [[ "$DATABASE" == "django" || "$DATABASE" == "all" ]]; then
    require_var POSTGRES_HOST
    require_var POSTGRES_DB
    require_var POSTGRES_USER
    require_var POSTGRES_PASSWORD
  fi

  if [[ "$DATABASE" == "planka" || "$DATABASE" == "all" ]]; then
    require_var PLANKA_DB_HOST
  fi
}

# ---------------------------------------------------------------------------
# Confirm destructive operations
# ---------------------------------------------------------------------------

confirm_destructive() {
  local target="$1"
  local real="$2"

  if [[ "$target" != "$real" ]]; then
    # Restoring to a different name — safe, no confirmation needed.
    return 0
  fi

  if [[ "${RESTORE_NO_CONFIRM:-false}" == "true" ]]; then
    warn "RESTORE_NO_CONFIRM=true — skipping confirmation prompt."
    return 0
  fi

  echo ""
  echo "  ╔══════════════════════════════════════════════════════════╗"
  echo "  ║              ⚠  DESTRUCTIVE OPERATION  ⚠                ║"
  echo "  ║                                                          ║"
  echo "  ║  You are about to overwrite the database '$target'.      ║"
  echo "  ║  ALL EXISTING DATA WILL BE REPLACED.                     ║"
  echo "  ║                                                          ║"
  echo "  ║  Snapshot: $SNAPSHOT"
  echo "  ║                                                          ║"
  echo "  ║  To restore safely without this prompt, use --target     ║"
  echo "  ║  with a different database name, verify the data, then   ║"
  echo "  ║  swap databases manually.                                ║"
  echo "  ╚══════════════════════════════════════════════════════════╝"
  echo ""
  read -r -p "  Type the database name to confirm: " confirmation

  if [[ "$confirmation" != "$target" ]]; then
    die "Confirmation did not match — aborting."
  fi
}

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

psql_django() {
  PGPASSWORD="$POSTGRES_PASSWORD" psql \
    --host     "$POSTGRES_HOST" \
    --port     "${POSTGRES_PORT:-5432}" \
    --username "$POSTGRES_USER" \
    --no-password \
    "$@"
}

psql_planka() {
  psql \
    --host     "$PLANKA_DB_HOST" \
    --port     "${PLANKA_DB_PORT:-5432}" \
    --username postgres \
    "$@"
}

create_db_if_missing_django() {
  local db="$1"
  if ! PGPASSWORD="$POSTGRES_PASSWORD" psql \
        --host "$POSTGRES_HOST" --port "${POSTGRES_PORT:-5432}" \
        --username "$POSTGRES_USER" --no-password \
        --tuples-only --command "SELECT 1 FROM pg_database WHERE datname='$db'" \
        postgres | grep -q 1; then
    info "Creating database '$db'…"
    run psql_django --dbname postgres --command "CREATE DATABASE \"$db\";"
  else
    info "Database '$db' already exists."
  fi
}

create_db_if_missing_planka() {
  local db="$1"
  if ! psql_planka \
        --tuples-only --command "SELECT 1 FROM pg_database WHERE datname='$db'" \
        postgres | grep -q 1; then
    info "Creating database '$db'…"
    run psql_planka --dbname postgres --command "CREATE DATABASE \"$db\";"
  else
    info "Database '$db' already exists."
  fi
}

# ---------------------------------------------------------------------------
# Restore steps
# ---------------------------------------------------------------------------

restore_django() {
  local real_db="$POSTGRES_DB"
  local target="${TARGET_DJANGO:-$real_db}"

  info "Restoring Django database from snapshot '$SNAPSHOT'…"
  info "  Source:  django-db.sql  (in snapshot $SNAPSHOT)"
  info "  Target:  $target on $POSTGRES_HOST"

  confirm_destructive "$target" "$real_db"

  if "$DRY_RUN"; then
    echo "DRY-RUN: restic dump $SNAPSHOT django-db.sql | psql $target"
    return 0
  fi

  create_db_if_missing_django "$target"

  restic dump "$SNAPSHOT" django-db.sql \
    | psql_django --dbname "$target" --quiet

  info "Verifying restore — checking Django migrations table…"
  local count
  count="$(psql_django --dbname "$target" --tuples-only \
             --command "SELECT COUNT(*) FROM django_migrations;" | tr -d ' ')"
  info "  django_migrations rows: $count"
  [[ "$count" -gt 0 ]] || die "Restore verification failed — django_migrations is empty."

  info "Django database restored successfully into '$target'."

  if [[ "$target" != "$real_db" ]]; then
    echo ""
    echo "  Restored into '$target' (not '$real_db')."
    echo "  To use this as the live database, stop the app and run:"
    echo ""
    echo "    DROP DATABASE \"$real_db\";"
    echo "    ALTER DATABASE \"$target\" RENAME TO \"$real_db\";"
    echo ""
  fi
}

restore_planka() {
  local real_db="planka"
  local target="${TARGET_PLANKA:-$real_db}"

  info "Restoring Planka database from snapshot '$SNAPSHOT'…"
  info "  Source:  planka-db.sql  (in snapshot $SNAPSHOT)"
  info "  Target:  $target on $PLANKA_DB_HOST"

  confirm_destructive "$target" "$real_db"

  if "$DRY_RUN"; then
    echo "DRY-RUN: restic dump $SNAPSHOT planka-db.sql | psql $target"
    return 0
  fi

  create_db_if_missing_planka "$target"

  restic dump "$SNAPSHOT" planka-db.sql \
    | psql_planka --dbname "$target" --quiet

  info "Verifying restore — checking Planka users table…"
  local count
  count="$(psql_planka --dbname "$target" --tuples-only \
             --command "SELECT COUNT(*) FROM users;" | tr -d ' ')"
  info "  users rows: $count"
  [[ "$count" -ge 0 ]] || die "Restore verification failed."

  info "Planka database restored successfully into '$target'."

  if [[ "$target" != "$real_db" ]]; then
    echo ""
    echo "  Restored into '$target' (not '$real_db')."
    echo "  To use this as the live database:"
    echo "    1. Stop Planka."
    echo "    2. DROP DATABASE \"$real_db\";"
    echo "    3. ALTER DATABASE \"$target\" RENAME TO \"$real_db\";"
    echo "    4. Start Planka."
    echo ""
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  check_required_vars

  if "$LIST_ONLY"; then
    info "Available snapshots:"
    restic snapshots
    exit 0
  fi

  info "================================================================="
  info "Restore started"
  info "  Snapshot:  $SNAPSHOT"
  info "  Database:  $DATABASE"
  "$DRY_RUN" && info "  (dry-run mode — no changes will be made)"
  info "================================================================="

  case "$DATABASE" in
    django) restore_django ;;
    planka) restore_planka ;;
    all)    restore_django; restore_planka ;;
    *)      die "Unknown database '$DATABASE'. Use: django, planka, or all." ;;
  esac

  info "================================================================="
  info "Restore complete."
  info "================================================================="
}

main
