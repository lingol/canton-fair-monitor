#!/usr/bin/env bash
set -Eeuo pipefail

# These constants are consumed by scripts that source this library.
# shellcheck disable=SC2034
APP_ROOT=/opt/canton-fair-alert
# shellcheck disable=SC2034
APP_DIR=$APP_ROOT/app
VENV_DIR=$APP_ROOT/venv
CONFIG_DIR=/etc/canton-fair-alert
# shellcheck disable=SC2034
DATA_DIR=/var/lib/canton-fair-alert
ENV_FILE=$CONFIG_DIR/app.env
# shellcheck disable=SC2034
SERVICE_USER=cantonfair
# shellcheck disable=SC2034
SERVICE_GROUP=cantonfair

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run this command as root."
}

project_root() {
  local script_dir
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
  printf '%s\n' "$script_dir"
}

backup_if_present() {
  local source=$1 destination=$2
  [[ -e $source ]] || return 0
  mkdir -p -- "$(dirname -- "$destination")"
  cp -a -- "$source" "$destination"
}

backup_database() {
  local source=$1 destination=$2
  [[ -f $source ]] || return 0
  mkdir -p -- "$(dirname -- "$destination")"
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$source" ".backup '$destination'"
  else
    cp -a -- "$source" "$destination"
  fi
}

run_cli() {
  umask 0007
  CANTON_FAIR_ENV_FILE=$ENV_FILE "$VENV_DIR/bin/python" -m canton_fair_alert "$@"
}

run_service_cli() {
  runuser -u "$SERVICE_USER" -- env CANTON_FAIR_ENV_FILE="$ENV_FILE" \
    "$VENV_DIR/bin/python" -m canton_fair_alert "$@"
}
