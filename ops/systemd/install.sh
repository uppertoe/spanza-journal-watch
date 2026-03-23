#!/usr/bin/env bash
# =============================================================================
# install.sh — Set up the Journal Watch backup system on a production VPS.
# =============================================================================
#
# Run as root on the VPS after cloning the repository.
#
# USAGE
#   sudo bash ops/systemd/install.sh
#
# WHAT IT DOES
#   1. Installs restic, msmtp, msmtp-mta, and postgresql-client.
#   2. Copies backup and restore scripts to /opt/backup/.
#   3. Creates /etc/restic/ and copies the env template.
#   4. Installs systemd service and timer units.
#   5. Enables the timer (does NOT start it — initialise the repo first).
#
# AFTER RUNNING THIS SCRIPT
#   1. Fill in /etc/restic/env (copy from env.example, set real values).
#   2. Initialise the Restic repository:
#        source /etc/restic/env && restic init
#   3. Run a manual backup to verify everything works:
#        systemctl start backup.service
#        journalctl -u backup.service -f
#   4. Check the timer is scheduled:
#        systemctl list-timers backup.timer
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

log()  { echo "[install] $*"; }
die()  { echo "[install] ERROR: $*" >&2; exit 1; }

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
  ca-certificates \
  curl \
  msmtp \
  msmtp-mta \
  postgresql-client

# Install restic from the official release.  The apt package in bookworm is
# outdated; the binary is a single static executable with no dependencies.
RESTIC_VERSION="0.17.3"
RESTIC_URL="https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_amd64.bz2"

if command -v restic &>/dev/null && restic version | grep -q "$RESTIC_VERSION"; then
  log "restic $RESTIC_VERSION already installed — skipping."
else
  log "Installing restic $RESTIC_VERSION…"
  curl -fsSL "$RESTIC_URL" | bunzip2 > /usr/local/bin/restic
  chmod +x /usr/local/bin/restic
  log "restic $(restic version) installed."
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
# Create /etc/restic and copy env template
# ---------------------------------------------------------------------------

install -d -m 700 /etc/restic

if [[ ! -f /etc/restic/env ]]; then
  install -m 600 "$SCRIPT_DIR/env.example" /etc/restic/env
  log "Created /etc/restic/env from template."
  log ""
  log "  *** ACTION REQUIRED ***"
  log "  Edit /etc/restic/env and fill in real credentials before"
  log "  starting the backup service."
  log ""
else
  log "/etc/restic/env already exists — not overwriting."
fi

# ---------------------------------------------------------------------------
# Install systemd units
# ---------------------------------------------------------------------------

log "Installing systemd units…"

install -m 644 "$SCRIPT_DIR/backup.service" /etc/systemd/system/backup.service
install -m 644 "$SCRIPT_DIR/backup.timer"   /etc/systemd/system/backup.timer

systemctl daemon-reload
systemctl enable backup.timer

log "Timer enabled (not started — initialise the repo first)."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

log ""
log "================================================================="
log "Installation complete."
log ""
log "Next steps:"
log "  1. Edit /etc/restic/env with real credentials."
log "  2. Initialise the Restic repo:"
log "       source /etc/restic/env && restic init"
log "  3. Run a test backup:"
log "       systemctl start backup.service"
log "       journalctl -u backup.service -f"
log "  4. Confirm the timer:"
log "       systemctl list-timers backup.timer"
log "================================================================="
