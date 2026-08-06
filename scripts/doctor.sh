#!/usr/bin/env bash
set -uo pipefail
umask 0007
APP_ROOT=${CANTON_FAIR_APP_ROOT:-/opt/canton-fair-alert}
ENV_FILE=${CANTON_FAIR_ENV_FILE:-/etc/canton-fair-alert/app.env}
failures=0
warnings=0

ok() { echo "[OK] $*"; }
warn() { echo "[WARN] $*"; warnings=$((warnings + 1)); }
fail() { echo "[FAIL] $*"; failures=$((failures + 1)); }

if [[ -x $APP_ROOT/venv/bin/python ]]; then ok "Python virtualenv"; else fail "Python virtualenv missing"; fi
if [[ -f $ENV_FILE ]]; then
  permissions=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null)
  if [[ $permissions == 640 || $permissions == 600 ]]; then ok "configuration permissions"; else fail "unsafe configuration permissions: $permissions"; fi
else
  fail "configuration file missing"
fi

if [[ -x $APP_ROOT/venv/bin/python && -f $ENV_FILE ]]; then
  if [[ ${EUID:-$(id -u)} -eq 0 ]] && id cantonfair >/dev/null 2>&1; then
    runuser -u cantonfair -- env CANTON_FAIR_ENV_FILE="$ENV_FILE" \
      "$APP_ROOT/venv/bin/python" -m canton_fair_alert doctor
  else
    CANTON_FAIR_ENV_FILE=$ENV_FILE "$APP_ROOT/venv/bin/python" -m canton_fair_alert doctor
  fi
  python_status=$?
  if ((python_status == 1)); then warnings=$((warnings + 1)); fi
  if ((python_status >= 2)); then failures=$((failures + 1)); fi
fi

if command -v systemctl >/dev/null 2>&1; then
  for timer in canton-fair-refresh.timer canton-fair-alert.timer; do
    if systemctl is-enabled --quiet "$timer"; then ok "$timer enabled"; else fail "$timer not enabled"; fi
  done
  if systemctl list-timers --all 'canton-fair-*' --no-legend 2>/dev/null | grep -q 'canton-fair-'; then
    ok "next timer execution visible"
  else
    fail "next timer execution unavailable"
  fi
else
  warn "systemctl unavailable; skipped timer checks"
fi

if ((failures > 0)); then exit 2; fi
if ((warnings > 0)); then exit 1; fi
exit 0
