from datetime import date, datetime

from canton_fair_alert.alert_service import AlertService
from canton_fair_alert.schedule_service import ScheduleService
from canton_fair_alert.time_utils import next_day


class Channel:
    def __init__(self, fail_for=None):
        self.fail_for = fail_for
        self.calls = []

    def send_message(self, destination, message):
        self.calls.append((destination, message))
        if destination == self.fail_for:
            raise RuntimeError("simulated channel failure")


def seed_schedule(settings, database):
    ScheduleService(settings, database).import_payload(
        {
            "edition": 140,
            "season": "autumn",
            "source_name": "manual_admin_override",
            "source_url": "manual",
            "windows": [
                {"phase": 1, "start_date": "2026-10-15", "end_date": "2026-10-19"},
                {"phase": 2, "start_date": "2026-10-23", "end_date": "2026-10-27"},
                {"phase": 3, "start_date": "2026-10-31", "end_date": "2026-11-04"},
            ],
        }
    )


def add_subscriber(
    database, name, email, webhook=None, email_enabled=1, wecom_enabled=0, workdays=1
):
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO subscribers(name, email, wecom_webhook, email_enabled, wecom_enabled,
                   only_workdays, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, 'now', 'now')""",
            (name, email, webhook, email_enabled, wecom_enabled, workdays),
        )


def test_target_date_and_non_event_quiet_exit(settings, database):
    seed_schedule(settings, database)
    summary = AlertService(settings, database, Channel(), Channel()).run(date(2026, 10, 10))
    assert summary.target_date == date(2026, 10, 11)
    assert summary.attempted == 0


def test_workday_filter(settings, database):
    seed_schedule(settings, database)
    add_subscriber(database, "worker", "worker@example.com", workdays=1)
    add_subscriber(database, "all days", "all@example.com", workdays=0)
    email = Channel()
    summary = AlertService(settings, database, email, Channel()).run(date(2026, 10, 17))
    assert summary.target_date.weekday() == 6
    assert [call[0] for call in email.calls] == ["all@example.com"]


def test_failure_isolation_between_subscribers(settings, database):
    seed_schedule(settings, database)
    add_subscriber(database, "broken", "broken@example.com")
    add_subscriber(database, "healthy", "healthy@example.com")
    email = Channel(fail_for="broken@example.com")
    summary = AlertService(settings, database, email, Channel()).run(date(2026, 10, 14))
    assert summary.failed == 1
    assert summary.sent == 1
    assert len(email.calls) == 2


def test_email_failure_does_not_block_wecom(settings, database):
    seed_schedule(settings, database)
    webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"
    add_subscriber(database, "both", "broken@example.com", webhook, 1, 1)
    email = Channel(fail_for="broken@example.com")
    wecom = Channel()
    summary = AlertService(settings, database, email, wecom).run(date(2026, 10, 14))
    assert summary.failed == 1 and summary.sent == 1
    assert len(wecom.calls) == 1


def test_dry_run_does_not_create_delivery(settings, database):
    seed_schedule(settings, database)
    add_subscriber(database, "worker", "worker@example.com")
    AlertService(settings, database, Channel(), Channel()).run(date(2026, 10, 14), dry_run=True)
    assert database.query_one("SELECT COUNT(*) AS count FROM deliveries")["count"] == 0


def test_timezone_and_month_year_rollover():
    assert next_day("Asia/Shanghai", datetime.fromisoformat("2026-12-31T23:30:00+08:00")) == date(
        2027, 1, 1
    )
    assert next_day("Asia/Shanghai", datetime.fromisoformat("2026-01-31T23:30:00+08:00")) == date(
        2026, 2, 1
    )
