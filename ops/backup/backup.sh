#!/usr/bin/env bash
# =============================================================================
# backup.sh — Back up Django and Planka databases to a Restic repository.
# =============================================================================
#
# USAGE
#   backup.sh [--dry-run] [--verify]
#
#   --dry-run   Print every command that would run; make no changes.
#   --verify    After backing up, run `restic check` and list new snapshots.
#
# ENVIRONMENT
#   Required:
#     RESTIC_REPOSITORY   Restic repo URL.
#                         Local path:  /restic-repo
#                         S3:          s3:s3.amazonaws.com/bucket/prefix
#     RESTIC_PASSWORD     Restic encryption passphrase.
#     POSTGRES_HOST       Django PostgreSQL host.
#     POSTGRES_DB         Django database name.
#     POSTGRES_USER       Django database user.
#     POSTGRES_PASSWORD   Django database password.
#     PLANKA_DB_HOST      Planka PostgreSQL host.
#
#   Optional:
#     POSTGRES_PORT       Django PostgreSQL port (default: 5432).
#     PLANKA_DB_PORT      Planka PostgreSQL port (default: 5432).
#     KEEP_DAILY          Snapshots to keep daily (default: 7).
#     KEEP_WEEKLY         Snapshots to keep weekly (default: 4).
#     KEEP_MONTHLY        Snapshots to keep monthly (default: 6).
#     NOTIFY_ON_SUCCESS   Set to "true" to send an email on success as well
#                         as failure (default: false).
#     ALERT_EMAIL         Address to receive notifications. Required for any
#                         notification to be sent.
#     SMTP_HOST           SMTP server hostname (default: localhost).
#     SMTP_PORT           SMTP port (default: 587).
#     SMTP_TLS            "on" or "off" (default: on).
#     SMTP_USER           SMTP username. Omit for unauthenticated servers
#                         such as Mailhog.
#     SMTP_PASSWORD       SMTP password. Required when SMTP_USER is set.
#     SMTP_FROM           Sender address (default: backup@localhost).
#
#   AWS S3 backend (required when RESTIC_REPOSITORY is an s3: URL):
#     AWS_ACCESS_KEY_ID
#     AWS_SECRET_ACCESS_KEY
#     AWS_DEFAULT_REGION
#
# EXIT CODES
#   0   All steps completed successfully.
#   1   A step failed; a notification email is sent if ALERT_EMAIL is set.
#
# TESTING LOCALLY
#   docker compose --profile backup run --rm backup /backup/backup.sh --dry-run
#   docker compose --profile backup run --rm backup /backup/backup.sh --verify
#   docker compose --profile backup run --rm backup bash
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

DRY_RUN=false
VERIFY=false

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true  ;;
    --verify)  VERIFY=true   ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--dry-run] [--verify]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

log()  { echo "[$(date -u +%H:%M:%SZ)] $*"; }
info() { log "INFO  $*"; }
warn() { log "WARN  $*"; }
die()  { log "ERROR $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Dry-run wrapper
# ---------------------------------------------------------------------------
# run CMD [ARGS…] either executes CMD or prints "DRY-RUN: CMD ARGS" depending
# on --dry-run.  Use for commands that make external changes; pure reads (e.g.
# `restic snapshots`) can be called directly.

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
  require_var POSTGRES_HOST
  require_var POSTGRES_DB
  require_var POSTGRES_USER
  require_var POSTGRES_PASSWORD
  require_var PLANKA_DB_HOST
}

# ---------------------------------------------------------------------------
# Notifications (msmtp)
# ---------------------------------------------------------------------------

# Write a temporary msmtp config file from environment variables and print its
# path.  Supports authenticated SMTP (e.g. Amazon SES) and unauthenticated
# servers (e.g. Mailhog) depending on whether SMTP_USER is set.
_setup_msmtp() {
  local config_file
  config_file="$(mktemp /tmp/msmtprc.XXXXXX)"
  local tls="${SMTP_TLS:-on}"

  {
    echo "defaults"
    echo "logfile /tmp/msmtp.log"
    if [[ "$tls" == "on" ]]; then
      echo "tls on"
      echo "tls_starttls on"
    else
      echo "tls off"
      echo "tls_starttls off"
    fi
    echo ""
    echo "account default"
    echo "host ${SMTP_HOST:-localhost}"
    echo "port ${SMTP_PORT:-587}"
    echo "from ${SMTP_FROM:-backup@localhost}"
    if [[ -n "${SMTP_USER:-}" ]]; then
      echo "auth plain"
      echo "user ${SMTP_USER}"
      echo "password ${SMTP_PASSWORD:-}"
    else
      echo "auth off"
    fi
  } > "$config_file"

  chmod 600 "$config_file"
  echo "$config_file"
}

send_notification() {
  local subject="$1"
  local body="$2"
  local recipient="${ALERT_EMAIL:-}"

  if [[ -z "$recipient" ]]; then
    warn "ALERT_EMAIL not set — skipping notification."
    return 0
  fi

  if "$DRY_RUN"; then
    echo "DRY-RUN: send email to $recipient — $subject"
    return 0
  fi

  local config_file
  config_file="$(_setup_msmtp)"

  if printf 'Subject: %s\n\n%s\n' "$subject" "$body" \
       | msmtp --file="$config_file" "$recipient"; then
    info "Notification sent to $recipient."
  else
    warn "Failed to send notification — check /tmp/msmtp.log."
  fi

  rm -f "$config_file"
}

# ---------------------------------------------------------------------------
# Failure trap
# ---------------------------------------------------------------------------
# Fires when any command exits non-zero (set -e).  Sends a failure notification
# with the line number and exit code, then exits 1.

_NOTIFICATION_SENT=false

on_error() {
  local exit_code=$?
  local line_no="${1:-unknown}"

  warn "Backup failed at line $line_no (exit code $exit_code)."

  if ! "$_NOTIFICATION_SENT"; then
    local body
    body="$(printf \
      'Backup FAILED on %s at %s.\n\nScript: %s\nLine:   %s\nExit:   %s\n\nCheck the system journal:\n  journalctl -u backup.service -n 100' \
      "$(hostname)" "$TIMESTAMP" "$0" "$line_no" "$exit_code")"
    send_notification "[BACKUP FAILED] $(hostname) — $TIMESTAMP" "$body"
    _NOTIFICATION_SENT=true
  fi
}

trap 'on_error $LINENO' ERR

# ---------------------------------------------------------------------------
# Restic helpers
# ---------------------------------------------------------------------------

restic_init_if_needed() {
  if restic snapshots --no-lock &>/dev/null; then
    info "Restic repository already initialised at $RESTIC_REPOSITORY."
  else
    info "Initialising new Restic repository at $RESTIC_REPOSITORY…"
    run restic init
  fi
}

# ---------------------------------------------------------------------------
# Backup steps
# ---------------------------------------------------------------------------

backup_django_db() {
  info "Backing up Django database ($POSTGRES_DB on $POSTGRES_HOST)…"

  if "$DRY_RUN"; then
    echo "DRY-RUN: pg_dump $POSTGRES_DB | restic backup --stdin --stdin-filename django-db.sql"
    return 0
  fi

  PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
      --host     "$POSTGRES_HOST" \
      --port     "${POSTGRES_PORT:-5432}" \
      --username "$POSTGRES_USER" \
      --no-password \
      "$POSTGRES_DB" \
    | restic backup \
        --stdin \
        --stdin-filename django-db.sql \
        --tag django \
        --tag "$TIMESTAMP"

  info "Django database backed up."
}

# Planka backup uses trust authentication (no password).  If the Planka
# postgres container is not running, this step is skipped with a warning
# rather than failing the whole backup — the Django database is the higher
# priority target.
backup_planka_db() {
  info "Backing up Planka database (planka on $PLANKA_DB_HOST)…"

  if "$DRY_RUN"; then
    echo "DRY-RUN: pg_dump planka | restic backup --stdin --stdin-filename planka-db.sql"
    return 0
  fi

  if ! pg_isready \
         --host "${PLANKA_DB_HOST}" \
         --port "${PLANKA_DB_PORT:-5432}" \
         --username postgres \
         --quiet; then
    warn "Planka PostgreSQL is not reachable — skipping Planka backup."
    warn "Start the Planka stack (--profile planka) to include it."
    return 0
  fi

  pg_dump \
      --host     "$PLANKA_DB_HOST" \
      --port     "${PLANKA_DB_PORT:-5432}" \
      --username postgres \
      planka \
    | restic backup \
        --stdin \
        --stdin-filename planka-db.sql \
        --tag planka \
        --tag "$TIMESTAMP"

  info "Planka database backed up."
}

apply_retention() {
  local keep_daily="${KEEP_DAILY:-7}"
  local keep_weekly="${KEEP_WEEKLY:-4}"
  local keep_monthly="${KEEP_MONTHLY:-6}"

  info "Applying retention policy (daily=$keep_daily, weekly=$keep_weekly, monthly=$keep_monthly)…"

  run restic forget \
    --keep-daily   "$keep_daily"  \
    --keep-weekly  "$keep_weekly" \
    --keep-monthly "$keep_monthly" \
    --prune

  info "Retention policy applied."
}

verify_repository() {
  info "Verifying Restic repository integrity…"
  run restic check
  info "Repository OK."

  info "Recent snapshots:"
  restic snapshots --latest 5
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  info "================================================================="
  info "Backup started — $TIMESTAMP"
  "$DRY_RUN" && info "(dry-run mode — no changes will be made)"
  info "Repository: $RESTIC_REPOSITORY"
  info "================================================================="

  check_required_vars
  restic_init_if_needed
  backup_django_db
  backup_planka_db
  apply_retention

  if "$VERIFY"; then
    verify_repository
  fi

  info "================================================================="
  info "Backup completed successfully."
  info "================================================================="

  if [[ "${NOTIFY_ON_SUCCESS:-false}" == "true" ]]; then
    local body
    body="$(printf \
      'Backup completed successfully on %s at %s.\n\nRepository: %s' \
      "$(hostname)" "$TIMESTAMP" "$RESTIC_REPOSITORY")"
    send_notification "[BACKUP OK] $(hostname) — $TIMESTAMP" "$body"
  fi
}

main
