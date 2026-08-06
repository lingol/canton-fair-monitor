#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_root
read -r -p "Remove application code? [y/N] " remove_app
read -r -p "Remove systemd units? [y/N] " remove_units
read -r -p "Keep database? [Y/n] " keep_database
read -r -p "Keep configuration? [Y/n] " keep_config
read -r -p "Keep backups? [Y/n] " keep_backups
systemctl disable --now canton-fair-refresh.timer canton-fair-alert.timer 2>/dev/null || true
if [[ $remove_units =~ ^[Yy]$ ]]; then
  rm -f /etc/systemd/system/canton-fair-{refresh,alert}.{service,timer}
  systemctl daemon-reload
fi
if [[ $remove_app =~ ^[Yy]$ ]]; then rm -rf -- "$APP_ROOT"; fi
if [[ $keep_database =~ ^[Nn]$ ]]; then rm -f -- "$DATA_DIR/app.db" "$DATA_DIR/app.db-shm" "$DATA_DIR/app.db-wal"; fi
if [[ $keep_backups =~ ^[Nn]$ ]]; then rm -rf -- "$DATA_DIR/backups"; fi
if [[ $keep_config =~ ^[Nn]$ ]]; then rm -rf -- "$CONFIG_DIR"; fi
rm -f /usr/local/bin/canton-fair-{alert,subscribers,schedule,admin-email,doctor}
echo "Uninstall complete. Database and configuration were preserved unless explicitly removed."
