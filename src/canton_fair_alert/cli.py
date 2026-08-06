import argparse
import getpass
import grp
import json
import os
import socket
import sqlite3
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from canton_fair_alert.admin_alerts import AdminAlertService
from canton_fair_alert.alert_service import AlertService
from canton_fair_alert.config import ConfigError, Settings, is_valid_email, load_settings
from canton_fair_alert.db import Database
from canton_fair_alert.logging_config import configure_logging
from canton_fair_alert.migrations import SCHEMA_VERSION, migrate
from canton_fair_alert.models import CommuteMessage
from canton_fair_alert.notifications.email import EmailNotificationError, EmailNotifier
from canton_fair_alert.notifications.wecom import (
    WeComNotificationError,
    WeComNotifier,
    mask_webhook,
    validate_webhook,
)
from canton_fair_alert.schedule_service import ScheduleService
from canton_fair_alert.time_utils import utc_now_iso
from canton_fair_alert.validators.schedule_validator import validate_schedule

EXIT_OK = 0
EXIT_WARNING = 1
EXIT_ARGUMENT = 2
EXIT_CONFIG = 3
EXIT_NOT_FOUND = 4
EXIT_EXTERNAL = 5
EXIT_INTERNAL = 10


def default_env_path() -> Path:
    explicit = os.environ.get("CANTON_FAIR_ENV_FILE")
    if explicit:
        return Path(explicit)
    system = Path("/etc/canton-fair-alert/app.env")
    return system if system.exists() else Path(".env")


def runtime() -> Tuple[Settings, Database]:
    settings = load_settings(default_env_path())
    return settings, Database(settings.database_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canton-fair-alert")
    subparsers = parser.add_subparsers(dest="command", required=True)
    refresh = subparsers.add_parser("refresh")
    refresh.add_argument("--force", action="store_true")
    refresh.add_argument("--accept-official", action="store_true")
    alert = subparsers.add_parser("alert")
    alert.add_argument("--date", type=date.fromisoformat)
    alert.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("status")
    subparsers.add_parser("doctor")
    subparsers.add_parser("migrate")

    subscribers = subparsers.add_parser("subscribers")
    subscriber_sub = subscribers.add_subparsers(dest="subscriber_command")
    add = subscriber_sub.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--email")
    add.add_argument("--wecom-webhook")
    add.add_argument("--enable-email", action="store_true")
    add.add_argument("--enable-wecom", action="store_true")
    add.add_argument("--all-days", action="store_true")
    subscriber_sub.add_parser("list")
    show = subscriber_sub.add_parser("show")
    show.add_argument("id", type=int)
    show.add_argument("--show-secrets", action="store_true")
    update = subscriber_sub.add_parser("update")
    update.add_argument("id", type=int)
    update.add_argument("--name")
    update.add_argument("--email")
    update.add_argument("--wecom-webhook")
    _boolean_flags(update, "email")
    _boolean_flags(update, "wecom")
    enable = subscriber_sub.add_parser("enable")
    enable.add_argument("id", type=int)
    disable = subscriber_sub.add_parser("disable")
    disable.add_argument("id", type=int)
    delete = subscriber_sub.add_parser("delete")
    delete.add_argument("id", type=int)
    delete.add_argument("--yes", action="store_true")
    test = subscriber_sub.add_parser("test")
    test.add_argument("id", type=int)
    test.add_argument("--channel", choices=("email", "wecom", "all"), default="all")

    schedule = subparsers.add_parser("schedule")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_sub.add_parser("show")
    schedule_refresh = schedule_sub.add_parser("refresh")
    schedule_refresh.add_argument("--force", action="store_true")
    schedule_refresh.add_argument("--accept-official", action="store_true")
    validate = schedule_sub.add_parser("validate-json")
    validate.add_argument("path", type=Path)
    import_json = schedule_sub.add_parser("import-json")
    import_json.add_argument("path", type=Path)
    export_json = schedule_sub.add_parser("export-json")
    export_json.add_argument("path", type=Path)

    admin = subparsers.add_parser("admin-email")
    admin_sub = admin.add_subparsers(dest="admin_command")
    admin_sub.add_parser("show")
    admin_sub.add_parser("test")
    admin_set = admin_sub.add_parser("set")
    admin_set.add_argument("--smtp-host", required=True)
    admin_set.add_argument("--smtp-port", required=True, type=int)
    admin_set.add_argument("--smtp-security", required=True, choices=("starttls", "ssl", "plain"))
    admin_set.add_argument("--smtp-username", default="")
    admin_set.add_argument("--smtp-password-file", type=Path)
    admin_set.add_argument("--smtp-from-email", required=True)
    admin_set.add_argument("--smtp-from-name", default="广交会通勤提醒")
    admin_set.add_argument("--admin-email", required=True)
    admin_set.add_argument("--send-test", action="store_true")
    return parser


def _boolean_flags(parser: argparse.ArgumentParser, name: str) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(f"--enable-{name}", dest=f"{name}_enabled", action="store_true")
    group.add_argument(f"--disable-{name}", dest=f"{name}_enabled", action="store_false")
    parser.set_defaults(**{f"{name}_enabled": None})


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_logging()
    parser = build_parser()
    loaded_settings: Optional[Settings] = None
    try:
        args = parser.parse_args(argv)
        settings, database = runtime()
        loaded_settings = settings
        if args.command == "migrate":
            migrate(database)
            print(f"Database schema is at version {SCHEMA_VERSION}.")
            return EXIT_OK
        migrate(database)
        if args.command == "refresh":
            return refresh_command(settings, database, args.force, args.accept_official)
        if args.command == "alert":
            admin = AdminAlertService(settings, database) if settings.admin_email else None
            try:
                summary = AlertService(settings, database, admin_alerts=admin).run(
                    args.date, args.dry_run
                )
            except Exception as exc:
                if admin is not None and settings.smtp_host:
                    admin.report(
                        "alert_run_failed",
                        "提醒任务未能正常完成",
                        f"失败时间: {utc_now_iso()}\n异常摘要: {exc}",
                    )
                raise
            print(
                f"target={summary.target_date} attempted={summary.attempted} "
                f"sent={summary.sent} skipped={summary.skipped} failed={summary.failed}"
            )
            return EXIT_EXTERNAL if summary.failed else EXIT_OK
        if args.command == "status":
            return status(settings, database)
        if args.command == "doctor":
            return doctor(settings, database)
        if args.command == "subscribers":
            return subscribers_command(args, settings, database)
        if args.command == "schedule":
            return schedule_command(args, settings, database)
        if args.command == "admin-email":
            return admin_email_command(args, settings)
        parser.error("unsupported command")
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except (ValueError, argparse.ArgumentError) as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        return EXIT_ARGUMENT
    except sqlite3.Error as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        if (
            loaded_settings is not None
            and loaded_settings.smtp_host
            and loaded_settings.admin_email
        ):
            try:
                EmailNotifier(loaded_settings).send(
                    loaded_settings.admin_email,
                    "[广交会提醒][异常] 数据库或迁移失败",
                    f"失败时间: {utc_now_iso()}\n异常摘要: {exc}",
                )
            except Exception as email_exc:
                print(f"Administrator alert also failed: {email_exc}", file=sys.stderr)
        return EXIT_INTERNAL
    except (EmailNotificationError, WeComNotificationError) as exc:
        print(f"External notification failed: {exc}", file=sys.stderr)
        return EXIT_EXTERNAL
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NOT_FOUND
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    return EXIT_INTERNAL


def refresh_command(
    settings: Settings, database: Database, force: bool, accept_official: bool
) -> int:
    service = ScheduleService(settings, database)
    try:
        windows = service.refresh(force, accept_official)
    except Exception as exc:
        if settings.admin_email and settings.smtp_host:
            snapshots = sorted(settings.snapshot_dir.glob("failure-*.html"), reverse=True)
            active = service.export_payload()
            details = (
                f"失败时间: {utc_now_iso()}\n"
                f"异常摘要: {exc}\n"
                f"parser version: {service.parser.version}\n"
                f"最新失败快照: {snapshots[0] if snapshots else 'none'}\n"
                f"最后有效日程: {json.dumps(active, ensure_ascii=False)}\n"
                "诊断命令: sudo canton-fair-doctor; "
                "journalctl -u canton-fair-refresh.service"
            )
            AdminAlertService(settings, database).report(
                "schedule_refresh_failed", "官网日程刷新失败", details, "official"
            )
        raise
    if settings.admin_email and settings.smtp_host:
        AdminAlertService(settings, database).recover(
            "schedule_refresh_failed", "官网日程刷新已恢复"
        )
    print(f"Refreshed {len(windows)} schedule windows.")
    return EXIT_OK


def subscribers_command(args: argparse.Namespace, settings: Settings, database: Database) -> int:
    command = args.subscriber_command
    if command is None:
        return subscribers_interactive(settings, database)
    if command == "add":
        validate_subscriber(args.email, args.wecom_webhook, args.enable_email, args.enable_wecom)
        now = utc_now_iso()
        with database.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO subscribers(
                       name, email, wecom_webhook, email_enabled, wecom_enabled,
                       only_workdays, created_at, updated_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    args.name.strip(),
                    args.email,
                    args.wecom_webhook,
                    int(args.enable_email),
                    int(args.enable_wecom),
                    int(not args.all_days),
                    now,
                    now,
                ),
            )
            subscriber_id = cursor.lastrowid
        print(f"Created subscriber {subscriber_id}.")
        return EXIT_OK
    if command == "list":
        rows = database.query_all("SELECT * FROM subscribers ORDER BY id")
        print("ID  Enabled  Name  Email  WeCom")
        for row in rows:
            webhook = mask_webhook(row["wecom_webhook"]) if row["wecom_webhook"] else "-"
            print(
                f"{row['id']}  {bool(row['enabled'])}  {row['name']}  "
                f"{row['email'] or '-'}  {webhook}"
            )
        return EXIT_OK
    if command == "show":
        row = require_subscriber(database, args.id)
        webhook = row["wecom_webhook"] or "-"
        if not args.show_secrets:
            webhook = mask_webhook(webhook) if webhook != "-" else webhook
        elif os.geteuid() != 0:
            raise ConfigError("--show-secrets requires root")
        for key in row.keys():
            value = webhook if key == "wecom_webhook" else row[key]
            print(f"{key}: {value}")
        return EXIT_OK
    if command == "update":
        row = require_subscriber(database, args.id)
        values: Dict[str, Any] = {
            "name": args.name if args.name is not None else row["name"],
            "email": args.email if args.email is not None else row["email"],
            "wecom_webhook": (
                args.wecom_webhook if args.wecom_webhook is not None else row["wecom_webhook"]
            ),
            "email_enabled": (
                int(args.email_enabled) if args.email_enabled is not None else row["email_enabled"]
            ),
            "wecom_enabled": (
                int(args.wecom_enabled) if args.wecom_enabled is not None else row["wecom_enabled"]
            ),
        }
        validate_subscriber(
            values["email"],
            values["wecom_webhook"],
            bool(values["email_enabled"]),
            bool(values["wecom_enabled"]),
        )
        with database.transaction() as connection:
            connection.execute(
                """UPDATE subscribers SET name=?, email=?, wecom_webhook=?, email_enabled=?,
                       wecom_enabled=?, updated_at=? WHERE id=?""",
                (*values.values(), utc_now_iso(), args.id),
            )
        print(f"Updated subscriber {args.id}.")
        return EXIT_OK
    if command in {"enable", "disable"}:
        require_subscriber(database, args.id)
        with database.transaction() as connection:
            connection.execute(
                "UPDATE subscribers SET enabled=?, updated_at=? WHERE id=?",
                (int(command == "enable"), utc_now_iso(), args.id),
            )
        print(f"Subscriber {args.id} {command}d.")
        return EXIT_OK
    if command == "delete":
        require_subscriber(database, args.id)
        if (
            not args.yes
            and input(f"Delete subscriber {args.id}? Type yes: ").strip().lower() != "yes"
        ):
            print("Not deleted.")
            return EXIT_OK
        with database.transaction() as connection:
            connection.execute("DELETE FROM deliveries WHERE subscriber_id=?", (args.id,))
            connection.execute("DELETE FROM subscribers WHERE id=?", (args.id,))
        print(f"Deleted subscriber {args.id}.")
        return EXIT_OK
    if command == "test":
        row = require_subscriber(database, args.id)
        test_message = CommuteMessage(date.today(), 1, None)
        send_email = args.channel == "email" or (
            args.channel == "all" and bool(row["email_enabled"])
        )
        send_wecom = args.channel == "wecom" or (
            args.channel == "all" and bool(row["wecom_enabled"])
        )
        if send_email:
            if not row["email"]:
                raise ValueError("subscriber has no email")
            EmailNotifier(settings).send_message(row["email"], test_message)
        if send_wecom:
            if not row["wecom_webhook"]:
                raise ValueError("subscriber has no WeCom webhook")
            WeComNotifier(settings.http_timeout_seconds).send_message(
                row["wecom_webhook"], test_message
            )
        print("Test notification sent.")
        return EXIT_OK
    return EXIT_ARGUMENT


def subscribers_interactive(settings: Settings, database: Database) -> int:
    while True:
        print("\n1. 新增订阅者\n2. 查看订阅者列表\n3. 查看订阅者详情\n4. 修改订阅者")
        print("5. 删除订阅者\n6. 启用或停用订阅者\n7. 测试通知\n8. 退出")
        choice = input("选择: ").strip()
        if choice == "8":
            return EXIT_OK
        if choice == "2":
            subscribers_command(argparse.Namespace(subscriber_command="list"), settings, database)
        elif choice == "1":
            name = input("名称: ").strip()
            email = input("邮箱（可空）: ").strip() or None
            enable_email = input("启用邮件？[y/N]: ").lower() == "y"
            webhook = getpass.getpass("企业微信 Webhook（可空，不回显）: ").strip() or None
            enable_wecom = bool(webhook) and input("启用企业微信？[y/N]: ").lower() == "y"
            ns = argparse.Namespace(
                subscriber_command="add",
                name=name,
                email=email,
                wecom_webhook=webhook,
                enable_email=enable_email,
                enable_wecom=enable_wecom,
                all_days=False,
            )
            subscribers_command(ns, settings, database)
        elif choice == "3":
            subscriber_id = int(input("订阅者 ID: "))
            subscribers_command(
                argparse.Namespace(subscriber_command="show", id=subscriber_id, show_secrets=False),
                settings,
                database,
            )
        elif choice == "4":
            subscriber_id = int(input("订阅者 ID: "))
            row = require_subscriber(database, subscriber_id)
            edit_name = input(f"名称 [{row['name']}]: ").strip() or None
            edit_email = input(f"邮箱 [{row['email'] or 'empty'}]: ").strip() or None
            edit_webhook = getpass.getpass("新 Webhook（留空保持原值）: ").strip() or None
            email_answer = input(
                f"启用邮件？[y/n，当前 {'y' if row['email_enabled'] else 'n'}]: "
            ).lower()
            wecom_answer = input(
                f"启用企业微信？[y/n，当前 {'y' if row['wecom_enabled'] else 'n'}]: "
            ).lower()
            subscribers_command(
                argparse.Namespace(
                    subscriber_command="update",
                    id=subscriber_id,
                    name=edit_name,
                    email=edit_email,
                    wecom_webhook=edit_webhook,
                    email_enabled=(email_answer == "y" if email_answer in {"y", "n"} else None),
                    wecom_enabled=(wecom_answer == "y" if wecom_answer in {"y", "n"} else None),
                ),
                settings,
                database,
            )
        elif choice == "5":
            subscriber_id = int(input("订阅者 ID: "))
            subscribers_command(
                argparse.Namespace(subscriber_command="delete", id=subscriber_id, yes=False),
                settings,
                database,
            )
        elif choice == "6":
            subscriber_id = int(input("订阅者 ID: "))
            row = require_subscriber(database, subscriber_id)
            action = "disable" if row["enabled"] else "enable"
            subscribers_command(
                argparse.Namespace(subscriber_command=action, id=subscriber_id),
                settings,
                database,
            )
        elif choice == "7":
            subscriber_id = int(input("订阅者 ID: "))
            channel = input("通道 email/wecom/all [all]: ").strip() or "all"
            if channel not in {"email", "wecom", "all"}:
                print("无效通道。")
                continue
            subscribers_command(
                argparse.Namespace(subscriber_command="test", id=subscriber_id, channel=channel),
                settings,
                database,
            )
        else:
            print("无效选择。")


def require_subscriber(database: Database, subscriber_id: int) -> sqlite3.Row:
    row = database.query_one("SELECT * FROM subscribers WHERE id=?", (subscriber_id,))
    if row is None:
        raise LookupError(f"subscriber {subscriber_id} not found")
    return row


def validate_subscriber(
    email: Optional[str], webhook: Optional[str], email_enabled: bool, wecom_enabled: bool
) -> None:
    if not email_enabled and not wecom_enabled:
        raise ValueError("at least one notification channel must be enabled")
    if email_enabled and (not email or not is_valid_email(email)):
        raise ValueError("a valid email is required when email is enabled")
    if wecom_enabled:
        if not webhook:
            raise ValueError("a webhook is required when WeCom is enabled")
        validate_webhook(webhook)


def schedule_command(args: argparse.Namespace, settings: Settings, database: Database) -> int:
    service = ScheduleService(settings, database)
    if args.schedule_command == "show":
        print(json.dumps(service.export_payload(), ensure_ascii=False, indent=2))
    elif args.schedule_command == "refresh":
        return refresh_command(settings, database, args.force, args.accept_official)
    elif args.schedule_command in {"validate-json", "import-json"}:
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        if args.schedule_command == "validate-json":
            windows = service.parse_payload(payload)
            validate_schedule(windows)
            print("Schedule JSON is valid.")
        else:
            windows = service.import_payload(payload)
            print(f"Imported {len(windows)} windows.")
    elif args.schedule_command == "export-json":
        args.path.write_text(
            json.dumps(service.export_payload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Exported schedule to {args.path}.")
    return EXIT_OK


def admin_email_command(args: argparse.Namespace, settings: Settings) -> int:
    if args.admin_command is None:
        return admin_email_interactive(settings)
    if args.admin_command == "show":
        print(f"SMTP host: {settings.smtp_host}")
        print(f"SMTP port: {settings.smtp_port}")
        print(f"SMTP security: {settings.smtp_security}")
        print(f"SMTP username: {settings.smtp_username}")
        print(f"SMTP password: {'configured' if settings.smtp_password else 'not configured'}")
        print(f"From: {settings.smtp_from_name} <{settings.smtp_from_email}>")
        print(f"Administrator: {settings.admin_email}")
        return EXIT_OK
    if args.admin_command == "test":
        notifier = EmailNotifier(settings)
        notifier.test_connection()
        notifier.send(settings.admin_email, "[广交会提醒] 管理员邮件测试", "SMTP 配置测试成功。")
        print("Administrator test email sent.")
        return EXIT_OK
    password = settings.smtp_password
    if args.smtp_password_file:
        password = args.smtp_password_file.read_text(encoding="utf-8").rstrip("\r\n")
    elif os.environ.get("CANTON_FAIR_SMTP_PASSWORD") is not None:
        password = os.environ["CANTON_FAIR_SMTP_PASSWORD"]
    candidate = replace(
        settings,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_security=args.smtp_security,
        smtp_username=args.smtp_username,
        smtp_password=password,
        smtp_from_name=args.smtp_from_name,
        smtp_from_email=args.smtp_from_email,
        admin_email=args.admin_email,
    )
    candidate.validate_smtp()
    notifier = EmailNotifier(candidate)
    notifier.test_connection()
    if args.send_test:
        notifier.send(
            candidate.admin_email, "[广交会提醒] 管理员邮件测试", "新 SMTP 配置测试成功。"
        )
    write_env_atomic(default_env_path(), candidate)
    print("Administrator email configuration updated.")
    return EXIT_OK


def admin_email_interactive(settings: Settings) -> int:
    host = input(f"SMTP host [{settings.smtp_host}]: ").strip() or settings.smtp_host
    port = int(input(f"SMTP port [{settings.smtp_port}]: ").strip() or settings.smtp_port)
    security = (
        input(f"Security starttls/ssl/plain [{settings.smtp_security}]: ").strip()
        or settings.smtp_security
    )
    username = (
        input(f"SMTP username [{settings.smtp_username}]: ").strip() or settings.smtp_username
    )
    password = getpass.getpass("SMTP password (empty keeps current): ") or settings.smtp_password
    from_name = input(f"From name [{settings.smtp_from_name}]: ").strip() or settings.smtp_from_name
    from_email = (
        input(f"From email [{settings.smtp_from_email}]: ").strip() or settings.smtp_from_email
    )
    admin_email = (
        input(f"Administrator email [{settings.admin_email}]: ").strip() or settings.admin_email
    )
    candidate = replace(
        settings,
        smtp_host=host,
        smtp_port=port,
        smtp_security=security,
        smtp_username=username,
        smtp_password=password,
        smtp_from_name=from_name,
        smtp_from_email=from_email,
        admin_email=admin_email,
    )
    candidate.validate_smtp()
    EmailNotifier(candidate).test_connection()
    if input("Send a test email now? [y/N]: ").lower() == "y":
        EmailNotifier(candidate).send(
            admin_email, "[广交会提醒] 管理员邮件测试", "SMTP 配置测试成功。"
        )
    write_env_atomic(default_env_path(), candidate)
    print("Administrator email configuration updated.")
    return EXIT_OK


def write_env_atomic(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {
        "APP_ENV": settings.app_env,
        "APP_TIMEZONE": settings.timezone,
        "DATABASE_PATH": str(settings.database_path),
        "SNAPSHOT_DIR": str(settings.snapshot_dir),
        "SOURCES_FILE": str(settings.sources_file),
        "SCHEDULE_REFRESH_TIME": settings.schedule_refresh_time,
        "ALERT_SEND_TIME": settings.alert_send_time,
        "SCHEDULE_MAX_STALE_DAYS": settings.schedule_max_stale_days,
        "ADMIN_ALERT_COOLDOWN_HOURS": settings.admin_alert_cooldown_hours,
        "HTTP_TIMEOUT_SECONDS": settings.http_timeout_seconds,
        "HTTP_MAX_RETRIES": settings.http_max_retries,
        "HTTP_USER_AGENT": settings.http_user_agent,
        "SMTP_HOST": settings.smtp_host,
        "SMTP_PORT": settings.smtp_port,
        "SMTP_SECURITY": settings.smtp_security,
        "SMTP_USERNAME": settings.smtp_username,
        "SMTP_PASSWORD": settings.smtp_password,
        "SMTP_FROM_NAME": settings.smtp_from_name,
        "SMTP_FROM_EMAIL": settings.smtp_from_email,
        "ADMIN_EMAIL": settings.admin_email,
    }
    content = "".join(f"{key}={dotenv_quote(str(value))}\n" for key, value in values.items())
    fd, temporary = tempfile.mkstemp(prefix="app.env.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        try:
            os.chown(temporary, 0, grp.getgrnam("cantonfair").gr_gid)
        except (KeyError, PermissionError):
            # Development environments do not have the service group; deployment does.
            pass
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def dotenv_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def status(settings: Settings, database: Database) -> int:
    states = {
        row["key"]: row["value"] for row in database.query_all("SELECT key, value FROM app_state")
    }
    print(f"last successful fetch: {states.get('last_successful_fetch', 'never')}")
    print(f"last successful parse: {states.get('last_successful_parse', 'never')}")
    print(f"last alert run: {states.get('last_alert_run', 'never')}")
    count = database.query_one("SELECT COUNT(*) AS count FROM subscribers WHERE enabled=1")
    failures = database.query_one("SELECT COUNT(*) AS count FROM deliveries WHERE status='failed'")
    print(f"enabled subscribers: {count['count'] if count else 0}")
    print(f"failed deliveries: {failures['count'] if failures else 0}")
    print(
        json.dumps(
            ScheduleService(settings, database).export_payload(), ensure_ascii=False, indent=2
        )
    )
    return EXIT_OK


def doctor(settings: Settings, database: Database) -> int:
    warnings = failures = 0

    def report(level: str, name: str, detail: str = "") -> None:
        print(f"[{level}] {name}{': ' + detail if detail else ''}")

    env_path = default_env_path()
    if env_path.exists():
        mode = env_path.stat().st_mode & 0o777
        if mode & 0o007:
            failures += 1
            report("FAIL", "configuration permissions", oct(mode))
        else:
            report("OK", "configuration")
    else:
        warnings += 1
        report("WARN", "configuration", f"{env_path} does not exist")
    try:
        with database.transaction() as connection:
            connection.execute("SELECT 1")
        report("OK", "database")
    except sqlite3.Error as exc:
        failures += 1
        report("FAIL", "database", str(exc))
    schema = database.query_one("SELECT value FROM app_state WHERE key='schema_version'")
    if not schema or schema["value"] != SCHEMA_VERSION:
        failures += 1
        report("FAIL", "schema version")
    else:
        report("OK", "schema version")
    subscribers = database.query_one("SELECT COUNT(*) AS count FROM subscribers WHERE enabled=1")
    if not subscribers or subscribers["count"] == 0:
        warnings += 1
        report("WARN", "subscribers", "none configured")
    else:
        report("OK", "subscribers", str(subscribers["count"]))
    invalid_subscribers = 0
    for subscriber in database.query_all("SELECT * FROM subscribers WHERE enabled=1"):
        try:
            validate_subscriber(
                subscriber["email"],
                subscriber["wecom_webhook"],
                bool(subscriber["email_enabled"]),
                bool(subscriber["wecom_enabled"]),
            )
        except ValueError:
            invalid_subscribers += 1
    if invalid_subscribers:
        failures += 1
        report("FAIL", "subscriber configuration", f"{invalid_subscribers} invalid")
    failed_deliveries = database.query_one(
        "SELECT COUNT(*) AS count FROM deliveries WHERE status='failed' AND attempts>=3"
    )
    if failed_deliveries and failed_deliveries["count"]:
        warnings += 1
        report("WARN", "persistent failed deliveries", str(failed_deliveries["count"]))
    try:
        settings.snapshot_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(settings.snapshot_dir, os.W_OK):
            raise OSError("not writable")
        report("OK", "snapshot directory")
    except OSError as exc:
        failures += 1
        report("FAIL", "snapshot directory", str(exc))
    try:
        ScheduleService(settings, database).ensure_fresh(date.today())
        report("OK", "schedule freshness")
    except Exception as exc:
        failures += 1
        report("FAIL", "schedule freshness", str(exc))
    if not settings.smtp_host:
        warnings += 1
        report("WARN", "SMTP", "not configured")
    else:
        try:
            settings.validate_smtp()
            EmailNotifier(settings).test_connection()
            report("OK", "SMTP connection")
        except Exception as exc:
            failures += 1
            report("FAIL", "SMTP connection", str(exc))
    try:
        socket.getaddrinfo("www.cantonfair.org.cn", 443)
        report("OK", "official site DNS")
    except OSError as exc:
        failures += 1
        report("FAIL", "official site DNS", str(exc))
    return 2 if failures else (1 if warnings else 0)


if __name__ == "__main__":
    raise SystemExit(main())
