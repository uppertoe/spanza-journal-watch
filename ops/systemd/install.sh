#!/usr/bin/env bash
# =============================================================================
# install.sh — Set up the Journal Watch backup system on a production VPS.
# =============================================================================
#
# Run as root on the VPS after cloning the repository.
#
# USAGE
#   sudo bash ops/systemd/install.sh [--app-env PATH]
#
#   --app-env PATH   Path to the app .env file (e.g. /opt/jw/.env).
#                    Used to auto-extract PostgreSQL credentials, AWS region,
#                    and S3 bucket name.  Reduces the number of prompts.
#
# WHAT IT DOES
#   1. Installs restic, msmtp, msmtp-mta, and postgresql-client.
#   2. Copies backup and restore scripts to /opt/backup/.
#   3. Builds /etc/restic/env — auto-extracts what it can from the app .env,
#      prompts for the rest, generates RESTIC_PASSWORD automatically.
#   4. Installs systemd service and timer units.
#   5. Enables the timer.
#   6. Initialises the restic repository if it does not already exist.
#   7. Runs a dry-run to confirm the full pipeline works.
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RESTIC_VERSION="0.17.3"
ENV_DEST="/etc/restic/env"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log()  { echo "[install] $*"; }
die()  { echo "[install] ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

APP_ENV=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-env) APP_ENV="$2"; shift 2 ;;
    *) die "Unknown argument: $1.  Usage: $0 [--app-env PATH]" ;;
  esac
done

# ---------------------------------------------------------------------------
# Require root
# ---------------------------------------------------------------------------

[[ "$(id -u)" -eq 0 ]] || die "This script must be run as root (use sudo)."

# ---------------------------------------------------------------------------
# Install system dependencies
# ---------------------------------------------------------------------------

log "Installing dependencies…"

apt-get update -q
apt-get install -y --no-install-recommends \
  bzip2 \
  ca-certificates \
  curl \
  gnupg \
  msmtp \
  msmtp-mta \
  python3

# Install the official PostgreSQL apt repo so we get a recent pg client.
if [[ ! -f /usr/share/keyrings/postgresql.gpg ]]; then
  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg
  echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] \
https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
  apt-get update -q
fi
apt-get install -y --no-install-recommends postgresql-client-17

# Install restic from the official release (apt package is outdated).
if command -v restic &>/dev/null && restic version 2>/dev/null | grep -q "$RESTIC_VERSION"; then
  log "restic $RESTIC_VERSION already installed — skipping."
else
  log "Installing restic $RESTIC_VERSION…"
  ARCH="$(dpkg --print-architecture)"
  curl -fsSL \
    "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${ARCH}.bz2" \
    | bunzip2 > /usr/local/bin/restic
  chmod +x /usr/local/bin/restic
  log "restic installed: $(restic version)"
fi

# ---------------------------------------------------------------------------
# Install backup scripts
# ---------------------------------------------------------------------------

log "Installing scripts to /opt/backup/…"

install -d /opt/backup
install -m 750 "$REPO_ROOT/ops/backup/backup.sh"  /opt/backup/backup.sh
install -m 750 "$REPO_ROOT/ops/backup/restore.sh" /opt/backup/restore.sh

log "Scripts installed."

# ---------------------------------------------------------------------------
# Build /etc/restic/env
# ---------------------------------------------------------------------------

# Helper: extract a variable's value from a KEY=VALUE env file.
# Strips surrounding quotes.  Returns empty string if not found.
_extract_from_file() {
  local file="$1" key="$2"
  local raw
  raw="$(grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2-)" || true
  # Strip surrounding quotes
  raw="${raw%\"}"
  raw="${raw#\"}"
  raw="${raw%\'}"
  raw="${raw#\'}"
  echo "$raw"
}

# Helper: parse a DATABASE_URL into separate variables.
# Sets POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB.
_parse_database_url() {
  local url="$1"
  url="${url#postgresql://}"
  url="${url#postgres://}"
  POSTGRES_USER="${url%%:*}"
  url="${url#*:}"
  POSTGRES_PASSWORD="${url%%@*}"
  url="${url#*@}"
  local hostport="${url%%/*}"
  POSTGRES_DB="${url#*/}"
  # Strip query string from db name
  POSTGRES_DB="${POSTGRES_DB%%\?*}"
  if [[ "$hostport" == *:* ]]; then
    POSTGRES_HOST="${hostport%%:*}"
    POSTGRES_PORT="${hostport##*:}"
  else
    POSTGRES_HOST="$hostport"
    POSTGRES_PORT="5432"
  fi
}

# Helper: prompt for a value if not already set.
# Usage: _prompt VAR_NAME "Human label" ["default"]
_prompt() {
  local var="$1" label="$2" default="${3:-}"
  if [[ -n "${!var:-}" ]]; then
    log "  $label: ${!var} (auto-detected)"
    return
  fi
  local value
  if [[ -n "$default" ]]; then
    read -r -p "  $label [$default]: " value </dev/tty
    printf -v "$var" '%s' "${value:-$default}"
  else
    read -r -p "  $label: " value </dev/tty
    [[ -n "$value" ]] || die "$label is required."
    printf -v "$var" '%s' "$value"
  fi
}

log ""
log "================================================================="
log "Building /etc/restic/env"
log "================================================================="

# --- Try to auto-extract from the app .env -----------------------------------

POSTGRES_HOST=""
POSTGRES_PORT=""
POSTGRES_DB=""
POSTGRES_USER=""
POSTGRES_PASSWORD=""
PLANKA_DB_HOST=""
PLANKA_DB_PORT=""
AWS_DEFAULT_REGION=""
RESTIC_REPOSITORY=""

if [[ -n "$APP_ENV" ]]; then
  [[ -f "$APP_ENV" ]] || die "App env file not found: $APP_ENV"
  log "Reading config from $APP_ENV…"

  # Try separate postgres vars first
  POSTGRES_DB="$(_extract_from_file "$APP_ENV" "POSTGRES_DB")"
  POSTGRES_USER="$(_extract_from_file "$APP_ENV" "POSTGRES_USER")"
  POSTGRES_PASSWORD="$(_extract_from_file "$APP_ENV" "POSTGRES_PASSWORD")"
  POSTGRES_HOST="$(_extract_from_file "$APP_ENV" "POSTGRES_HOST")"
  POSTGRES_PORT="$(_extract_from_file "$APP_ENV" "POSTGRES_PORT")"

  # Fall back to DATABASE_URL
  if [[ -z "$POSTGRES_DB" ]]; then
    local_db_url="$(_extract_from_file "$APP_ENV" "DATABASE_URL")"
    if [[ -n "$local_db_url" ]]; then
      _parse_database_url "$local_db_url"
      log "  Parsed DATABASE_URL."
    fi
  fi

  # AWS region and bucket
  AWS_DEFAULT_REGION="$(_extract_from_file "$APP_ENV" "DJANGO_AWS_DEFAULT_REGION")"
  [[ -z "$AWS_DEFAULT_REGION" ]] && AWS_DEFAULT_REGION="$(_extract_from_file "$APP_ENV" "AWS_DEFAULT_REGION")"

  local_bucket="$(_extract_from_file "$APP_ENV" "DJANGO_AWS_STORAGE_BUCKET_NAME")"
  if [[ -n "$local_bucket" && -z "$RESTIC_REPOSITORY" ]]; then
    RESTIC_REPOSITORY="s3:s3.amazonaws.com/${local_bucket}/backups"
    log "  Derived RESTIC_REPOSITORY from bucket: $RESTIC_REPOSITORY"
  fi
fi

# The backup service runs on the host and connects to postgres via an exposed
# port — the host address is always 127.0.0.1 regardless of what the app .env says.
POSTGRES_HOST="127.0.0.1"
PLANKA_DB_HOST="127.0.0.1"

# --- Prompt for anything that couldn't be auto-detected ----------------------

log ""
log "PostgreSQL (Django database):"
_prompt POSTGRES_DB      "  Database name"
_prompt POSTGRES_USER    "  Username"
_prompt POSTGRES_PASSWORD "  Password"
_prompt POSTGRES_PORT    "  Port" "5432"

log ""
log "Planka PostgreSQL:"
_prompt PLANKA_DB_PORT   "  Host-exposed port" "5433"

log ""
log "Restic repository:"
_prompt RESTIC_REPOSITORY "  Repository URL (e.g. s3:s3.amazonaws.com/bucket/backups)"

log ""
log "AWS credentials (backup IAM user — separate from the Django IAM user):"
_prompt AWS_ACCESS_KEY_ID     "  AWS_ACCESS_KEY_ID"
_prompt AWS_SECRET_ACCESS_KEY "  AWS_SECRET_ACCESS_KEY"
_prompt AWS_DEFAULT_REGION    "  AWS_DEFAULT_REGION" "ap-southeast-2"

log ""
log "Notifications:"
_prompt ALERT_EMAIL "  Alert email address"
_prompt SMTP_HOST   "  SMTP host" "email-smtp.${AWS_DEFAULT_REGION}.amazonaws.com"
_prompt SMTP_PORT   "  SMTP port" "587"
_prompt SMTP_FROM   "  From address" "backup@$(echo "$ALERT_EMAIL" | cut -d@ -f2)"
SMTP_TLS="${SMTP_TLS:-on}"
_prompt SMTP_USER     "  SMTP username (SES SMTP credential)"
_prompt SMTP_PASSWORD "  SMTP password (SES SMTP credential)"

# --- Generate RESTIC_PASSWORD ------------------------------------------------

log ""
if [[ -f "$ENV_DEST" ]]; then
  existing_pw="$(_extract_from_file "$ENV_DEST" "RESTIC_PASSWORD")"
else
  existing_pw=""
fi

if [[ -n "$existing_pw" ]]; then
  RESTIC_PASSWORD="$existing_pw"
  log "Keeping existing RESTIC_PASSWORD from $ENV_DEST."
else
  RESTIC_PASSWORD="$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")"
  log "Generated RESTIC_PASSWORD."
  log ""
  log "  ╔══════════════════════════════════════════════════════════════╗"
  log "  ║  SAVE THIS PASSWORD IN YOUR PASSWORD MANAGER — NOW          ║"
  log "  ║                                                              ║"
  log "  ║  RESTIC_PASSWORD = $RESTIC_PASSWORD"
  log "  ║                                                              ║"
  log "  ║  Losing it means losing access to all backups.              ║"
  log "  ╚══════════════════════════════════════════════════════════════╝"
  log ""
  read -r -p "  Press Enter once you have saved it… " </dev/tty
fi

# --- Write the env file atomically -------------------------------------------

install -d -m 700 /etc/restic

TMP_ENV="$(mktemp /etc/restic/.env.XXXXXX)"
chmod 600 "$TMP_ENV"

cat > "$TMP_ENV" <<EOF
# /etc/restic/env — Journal Watch backup environment.
# Managed by ops/systemd/install.sh — do not edit by hand unless necessary.
# Permissions: 600 (root only).

# Restic
RESTIC_REPOSITORY=${RESTIC_REPOSITORY}
RESTIC_PASSWORD=${RESTIC_PASSWORD}

# AWS (backup IAM user — scoped to S3 only, separate from Django user)
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION}

# Django PostgreSQL (connected via host-exposed port)
POSTGRES_HOST=${POSTGRES_HOST}
POSTGRES_PORT=${POSTGRES_PORT:-5432}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# Planka PostgreSQL (trust auth — no password required)
PLANKA_DB_HOST=${PLANKA_DB_HOST}
PLANKA_DB_PORT=${PLANKA_DB_PORT:-5433}

# Retention
KEEP_DAILY=${KEEP_DAILY:-7}
KEEP_WEEKLY=${KEEP_WEEKLY:-4}
KEEP_MONTHLY=${KEEP_MONTHLY:-6}

# Notifications
ALERT_EMAIL=${ALERT_EMAIL}
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT:-587}
SMTP_TLS=${SMTP_TLS:-on}
SMTP_FROM=${SMTP_FROM}
SMTP_USER=${SMTP_USER}
SMTP_PASSWORD=${SMTP_PASSWORD}
NOTIFY_ON_SUCCESS=${NOTIFY_ON_SUCCESS:-false}
EOF

mv "$TMP_ENV" "$ENV_DEST"
chown root:root "$ENV_DEST"
chmod 600 "$ENV_DEST"

log "Wrote $ENV_DEST (600 root:root)."

# ---------------------------------------------------------------------------
# Install systemd units
# ---------------------------------------------------------------------------

log "Installing systemd units…"

install -m 644 "$SCRIPT_DIR/backup.service" /etc/systemd/system/backup.service
install -m 644 "$SCRIPT_DIR/backup.timer"   /etc/systemd/system/backup.timer

systemctl daemon-reload
systemctl enable backup.timer

log "Timer enabled."

# ---------------------------------------------------------------------------
# Initialise the restic repository (if not already done)
# ---------------------------------------------------------------------------

log ""
log "Checking restic repository…"

# Source the env we just wrote so restic can reach the repository.
# shellcheck source=/dev/null
set +u
source "$ENV_DEST"
set -u

if restic snapshots --no-lock &>/dev/null 2>&1; then
  log "Repository already exists at $RESTIC_REPOSITORY — skipping init."
else
  log "Initialising new repository at $RESTIC_REPOSITORY…"
  restic init
  log "Repository initialised."
fi

# ---------------------------------------------------------------------------
# Dry-run to validate the full pipeline
# ---------------------------------------------------------------------------

log ""
log "Running dry-run to validate configuration…"
/opt/backup/backup.sh --dry-run
log "Dry-run passed."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

log ""
log "================================================================="
log "Installation complete."
log ""
log "The backup timer is enabled and will run daily at 02:00 UTC."
log ""
log "To run a backup now and watch the output:"
log "  systemctl start backup.service"
log "  journalctl -u backup.service -f"
log ""
log "To confirm the timer schedule:"
log "  systemctl list-timers backup.timer"
log "================================================================="
