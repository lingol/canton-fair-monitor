#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "Update failed at line $LINENO" >&2' ERR
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_root
SOURCE_ROOT=$(project_root)
[[ -f $SOURCE_ROOT/pyproject.toml && -d $SOURCE_ROOT/systemd ]] || die "Run from a project checkout."
stamp=$(date -u +%Y%m%dT%H%M%SZ)
systemctl stop canton-fair-refresh.timer canton-fair-alert.timer
backup_database "$DATA_DIR/app.db" "$DATA_DIR/backups/app.db.$stamp"
backup_if_present "$ENV_FILE" "$DATA_DIR/backups/app.env.$stamp"
if [[ -d $APP_DIR ]]; then
  mkdir -p "$DATA_DIR/backups/app.$stamp"
  rsync -a "$APP_DIR/" "$DATA_DIR/backups/app.$stamp/"
fi

rollback_update() {
  local status=$?
  echo "Update failed; attempting to restore the previous application and database." >&2
  if [[ -d $DATA_DIR/backups/app.$stamp ]]; then
    rsync -a --delete "$DATA_DIR/backups/app.$stamp/" "$APP_DIR/"
  fi
  if [[ -f $DATA_DIR/backups/app.db.$stamp ]]; then
    cp -a "$DATA_DIR/backups/app.db.$stamp" "$DATA_DIR/app.db"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR/app.db"
  fi
  systemctl daemon-reload || true
  systemctl start canton-fair-refresh.timer canton-fair-alert.timer || true
  exit "$status"
}
trap rollback_update ERR
rsync -a --delete --exclude '.git/' --exclude '.venv/' "$SOURCE_ROOT/" "$APP_DIR/"
"$VENV_DIR/bin/pip" install --upgrade "$APP_DIR"
run_service_cli migrate
install -m 0644 "$APP_DIR/systemd/canton-fair-refresh.service" "$APP_DIR/systemd/canton-fair-alert.service" /etc/systemd/system/
# The root-owned environment file is trusted input and supplies only deployment settings.
# shellcheck disable=SC1090
source "$ENV_FILE"
sed "s/@SCHEDULE_REFRESH_TIME@/$SCHEDULE_REFRESH_TIME/" "$APP_DIR/systemd/canton-fair-refresh.timer" >/etc/systemd/system/canton-fair-refresh.timer
sed "s/@ALERT_SEND_TIME@/$ALERT_SEND_TIME/" "$APP_DIR/systemd/canton-fair-alert.timer" >/etc/systemd/system/canton-fair-alert.timer
chmod 0644 /etc/systemd/system/canton-fair-*.timer
systemctl daemon-reload
systemctl enable --now canton-fair-refresh.timer canton-fair-alert.timer
"$APP_DIR/scripts/doctor.sh"
trap - ERR
