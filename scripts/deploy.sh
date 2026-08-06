#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "Deployment failed at line $LINENO while running: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_root
SOURCE_ROOT=$(project_root)
NON_INTERACTIVE=0
DEPLOY_CONFIG=

while (($#)); do
  case $1 in
    --non-interactive) NON_INTERACTIVE=1 ;;
    --config) shift; DEPLOY_CONFIG=${1:?missing path after --config} ;;
    *) die "Unknown option: $1" ;;
  esac
  shift
done

if [[ -n $DEPLOY_CONFIG ]]; then
  [[ -f $DEPLOY_CONFIG ]] || die "Deploy config not found: $DEPLOY_CONFIG"
  # This file is explicitly selected by the administrator and must be root-readable only.
  # shellcheck disable=SC1090
  source "$DEPLOY_CONFIG"
fi

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 20.04 ]]; then
    echo "WARNING: designed for Ubuntu 20.04; detected ${PRETTY_NAME:-unknown}." >&2
    if ((NON_INTERACTIVE)); then
      [[ ${ALLOW_UNSUPPORTED_OS:-no} == yes ]] || die "Set ALLOW_UNSUPPORTED_OS=yes to continue."
    else
      read -r -p "Continue on this compatible system? [y/N] " answer
      [[ $answer =~ ^[Yy]$ ]] || exit 1
    fi
  fi
fi

prompt_default() {
  local variable=$1 prompt=$2 default=$3 secret=${4:-no} value
  if ((NON_INTERACTIVE)); then
    value=${!variable:-$default}
    [[ -n $value ]] || die "$variable is required in non-interactive mode"
  elif [[ $secret == yes ]]; then
    read -r -s -p "$prompt [$([[ -n ${!variable:-} ]] && echo configured || echo empty)]: " value
    echo
    value=${value:-${!variable:-$default}}
  else
    read -r -p "$prompt [$default]: " value
    value=${value:-${!variable:-$default}}
  fi
  printf -v "$variable" '%s' "$value"
}

prompt_default APP_TIMEZONE "Application timezone" "${APP_TIMEZONE:-Asia/Shanghai}"
prompt_default SCHEDULE_REFRESH_TIME "Official schedule refresh time" "${SCHEDULE_REFRESH_TIME:-12:00}"
prompt_default ALERT_SEND_TIME "Next-day alert time" "${ALERT_SEND_TIME:-18:00}"
prompt_default SMTP_HOST "SMTP host (required for administrator alerts)" "${SMTP_HOST:-}"
prompt_default SMTP_PORT "SMTP port" "${SMTP_PORT:-587}"
prompt_default SMTP_SECURITY "SMTP security (starttls/ssl/plain)" "${SMTP_SECURITY:-starttls}"
prompt_default SMTP_USERNAME "SMTP username" "${SMTP_USERNAME:-}"
prompt_default SMTP_PASSWORD "SMTP password" "${SMTP_PASSWORD:-}" yes
prompt_default SMTP_FROM_NAME "SMTP sender name" "${SMTP_FROM_NAME:-广交会通勤提醒}"
prompt_default SMTP_FROM_EMAIL "SMTP sender email" "${SMTP_FROM_EMAIL:-}"
prompt_default ADMIN_EMAIL "Administrator email" "${ADMIN_EMAIL:-}"
prompt_default SEND_ADMIN_TEST "Send administrator test email? (yes/no)" "${SEND_ADMIN_TEST:-no}"
prompt_default CREATE_FIRST_SUBSCRIBER "Create the first subscriber? (yes/no)" "${CREATE_FIRST_SUBSCRIBER:-no}"
if [[ $CREATE_FIRST_SUBSCRIBER == yes ]]; then
  prompt_default FIRST_SUBSCRIBER_NAME "First subscriber name" "${FIRST_SUBSCRIBER_NAME:-}"
  prompt_default FIRST_ENABLE_EMAIL "Enable subscriber email? (yes/no)" "${FIRST_ENABLE_EMAIL:-yes}"
  if [[ $FIRST_ENABLE_EMAIL == yes ]]; then
    prompt_default FIRST_SUBSCRIBER_EMAIL "Subscriber email" "${FIRST_SUBSCRIBER_EMAIL:-}"
  fi
  prompt_default FIRST_ENABLE_WECOM "Enable subscriber WeCom? (yes/no)" "${FIRST_ENABLE_WECOM:-no}"
  if [[ $FIRST_ENABLE_WECOM == yes ]]; then
    prompt_default FIRST_WECOM_WEBHOOK "Subscriber WeCom webhook" "${FIRST_WECOM_WEBHOOK:-}" yes
  fi
  prompt_default SEND_SUBSCRIBER_TEST "Send subscriber test notifications? (yes/no)" "${SEND_SUBSCRIBER_TEST:-no}"
fi

[[ $SCHEDULE_REFRESH_TIME =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die "Invalid refresh time"
[[ $ALERT_SEND_TIME =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die "Invalid alert time"
[[ -n $SMTP_HOST ]] || die "SMTP host is required so runtime failures can alert the administrator"
if [[ ! $SMTP_PORT =~ ^[0-9]+$ ]] || ((SMTP_PORT < 1 || SMTP_PORT > 65535)); then
  die "Invalid SMTP port"
fi
[[ $SMTP_SECURITY == starttls || $SMTP_SECURITY == ssl || $SMTP_SECURITY == plain ]] || die "Invalid SMTP security"
[[ $SMTP_FROM_EMAIL == *@*.* ]] || die "A valid SMTP sender email is required"
[[ $ADMIN_EMAIL == *@*.* ]] || die "A valid administrator email is required"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip sqlite3 ca-certificates curl git rsync

getent group "$SERVICE_GROUP" >/dev/null || groupadd --system "$SERVICE_GROUP"
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --gid "$SERVICE_GROUP" --home "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o root -g root -m 0755 "$APP_ROOT" "$CONFIG_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 2770 "$DATA_DIR" "$DATA_DIR/backups"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 2750 "$DATA_DIR/snapshots" /var/log/canton-fair-alert

stamp=$(date -u +%Y%m%dT%H%M%SZ)
systemctl stop canton-fair-refresh.timer canton-fair-alert.timer 2>/dev/null || true
backup_database "$DATA_DIR/app.db" "$DATA_DIR/backups/app.db.$stamp"
backup_if_present "$ENV_FILE" "$DATA_DIR/backups/app.env.$stamp"

mkdir -p "$APP_DIR"
rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude 'app.db' "$SOURCE_ROOT/" "$APP_DIR/"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install "$APP_DIR"

env_quote() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  printf '"%s"' "$value"
}

temporary_env=$(mktemp "$CONFIG_DIR/app.env.XXXXXX")
{
  printf 'APP_ENV=production\nAPP_TIMEZONE=%s\n' "$(env_quote "$APP_TIMEZONE")"
  printf 'DATABASE_PATH=%s\nSNAPSHOT_DIR=%s\nSOURCES_FILE=%s\n' \
    "$(env_quote "$DATA_DIR/app.db")" "$(env_quote "$DATA_DIR/snapshots")" "$(env_quote "$CONFIG_DIR/sources.json")"
  printf 'SCHEDULE_REFRESH_TIME=%s\nALERT_SEND_TIME=%s\n' "$(env_quote "$SCHEDULE_REFRESH_TIME")" "$(env_quote "$ALERT_SEND_TIME")"
  printf 'SCHEDULE_MAX_STALE_DAYS=180\nADMIN_ALERT_COOLDOWN_HOURS=24\nHTTP_TIMEOUT_SECONDS=20\nHTTP_MAX_RETRIES=3\n'
  printf 'HTTP_USER_AGENT=%s\n' "$(env_quote 'CantonFairCommuteAlert/1.0')"
  printf 'SMTP_HOST=%s\nSMTP_PORT=%s\nSMTP_SECURITY=%s\n' "$(env_quote "$SMTP_HOST")" "$SMTP_PORT" "$(env_quote "$SMTP_SECURITY")"
  printf 'SMTP_USERNAME=%s\nSMTP_PASSWORD=%s\n' "$(env_quote "$SMTP_USERNAME")" "$(env_quote "$SMTP_PASSWORD")"
  printf 'SMTP_FROM_NAME=%s\nSMTP_FROM_EMAIL=%s\nADMIN_EMAIL=%s\n' \
    "$(env_quote "$SMTP_FROM_NAME")" "$(env_quote "$SMTP_FROM_EMAIL")" "$(env_quote "$ADMIN_EMAIL")"
} >"$temporary_env"
chown root:"$SERVICE_GROUP" "$temporary_env"
chmod 0640 "$temporary_env"
mv -f "$temporary_env" "$ENV_FILE"

if [[ ! -f $CONFIG_DIR/sources.json ]]; then
  install -o root -g "$SERVICE_GROUP" -m 0640 "$APP_DIR/sources.example.json" "$CONFIG_DIR/sources.json"
fi

install -o root -g root -m 0644 "$APP_DIR/systemd/canton-fair-refresh.service" /etc/systemd/system/
install -o root -g root -m 0644 "$APP_DIR/systemd/canton-fair-alert.service" /etc/systemd/system/
sed "s/@SCHEDULE_REFRESH_TIME@/$SCHEDULE_REFRESH_TIME/" "$APP_DIR/systemd/canton-fair-refresh.timer" >/etc/systemd/system/canton-fair-refresh.timer
sed "s/@ALERT_SEND_TIME@/$ALERT_SEND_TIME/" "$APP_DIR/systemd/canton-fair-alert.timer" >/etc/systemd/system/canton-fair-alert.timer
chmod 0644 /etc/systemd/system/canton-fair-*.timer

install -o root -g root -m 0755 "$APP_DIR/scripts/manage-subscribers.sh" /usr/local/bin/canton-fair-subscribers
install -o root -g root -m 0755 "$APP_DIR/scripts/manage-schedule.sh" /usr/local/bin/canton-fair-schedule
install -o root -g root -m 0755 "$APP_DIR/scripts/configure-admin-email.sh" /usr/local/bin/canton-fair-admin-email
install -o root -g root -m 0755 "$APP_DIR/scripts/doctor.sh" /usr/local/bin/canton-fair-doctor
install -o root -g root -m 0755 "$APP_DIR/scripts/canton-fair-alert.sh" /usr/local/bin/canton-fair-alert
printf '%s\n' '1.0.0' >"$APP_ROOT/VERSION"

run_service_cli migrate
chown "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR/app.db" "$DATA_DIR"/*.db-* 2>/dev/null || true
chmod 0660 "$DATA_DIR/app.db"

if [[ $SEND_ADMIN_TEST == yes && -n $SMTP_HOST && -n $ADMIN_EMAIL ]]; then
  if ! run_service_cli admin-email test; then
    echo "WARNING: administrator email test failed; review the saved backup and SMTP settings." >&2
  fi
fi

if [[ $CREATE_FIRST_SUBSCRIBER == yes ]]; then
  subscriber_args=(subscribers add --name "$FIRST_SUBSCRIBER_NAME")
  if [[ ${FIRST_ENABLE_EMAIL:-no} == yes ]]; then
    subscriber_args+=(--email "$FIRST_SUBSCRIBER_EMAIL" --enable-email)
  fi
  if [[ ${FIRST_ENABLE_WECOM:-no} == yes ]]; then
    subscriber_args+=(--wecom-webhook "$FIRST_WECOM_WEBHOOK" --enable-wecom)
  fi
  create_output=$(run_service_cli "${subscriber_args[@]}")
  echo "$create_output"
  if [[ ${SEND_SUBSCRIBER_TEST:-no} == yes ]]; then
    subscriber_id=$(awk '/Created subscriber/ {gsub(/\./, "", $3); print $3}' <<<"$create_output")
    if [[ -n $subscriber_id ]]; then
      run_service_cli subscribers test "$subscriber_id" --channel all || \
        echo "WARNING: subscriber test notification failed." >&2
    fi
  fi
fi

systemctl daemon-reload
systemctl enable --now canton-fair-refresh.timer canton-fair-alert.timer

if ! run_service_cli refresh; then
  echo "WARNING: initial official refresh failed; the service remains installed in degraded state." >&2
fi
if ! "$APP_DIR/scripts/doctor.sh"; then
  echo "WARNING: doctor reported issues; review the output above." >&2
fi

echo
echo "Deployment complete."
echo "Application: $APP_DIR"
echo "Configuration: $CONFIG_DIR"
echo "Database: $DATA_DIR/app.db"
echo "Timezone: $APP_TIMEZONE"
echo "Official refresh: daily at $SCHEDULE_REFRESH_TIME"
echo "Next-day alert: daily at $ALERT_SEND_TIME"
echo
echo "Commands: canton-fair-doctor, canton-fair-subscribers, canton-fair-admin-email, canton-fair-schedule show"
