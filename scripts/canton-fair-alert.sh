#!/usr/bin/env bash
set -Eeuo pipefail
umask 0007
APP_ROOT=${CANTON_FAIR_APP_ROOT:-/opt/canton-fair-alert}
ENV_FILE=${CANTON_FAIR_ENV_FILE:-/etc/canton-fair-alert/app.env}
export CANTON_FAIR_ENV_FILE=$ENV_FILE
exec "$APP_ROOT/venv/bin/python" -m canton_fair_alert "$@"
