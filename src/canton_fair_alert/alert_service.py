import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from canton_fair_alert._compat import ZoneInfo
from canton_fair_alert.admin_alerts import AdminAlertService
from canton_fair_alert.config import Settings
from canton_fair_alert.db import Database
from canton_fair_alert.models import CommuteMessage, FairWindow
from canton_fair_alert.notifications.base import EmailChannel, WeComChannel
from canton_fair_alert.notifications.email import EmailNotifier
from canton_fair_alert.notifications.wecom import WeComNotifier
from canton_fair_alert.schedule_service import ScheduleService
from canton_fair_alert.time_utils import next_day, utc_now_iso


@dataclass(frozen=True)
class AlertSummary:
    target_date: date
    attempted: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0


class AlertService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        email: Optional[EmailChannel] = None,
        wecom: Optional[WeComChannel] = None,
        admin_alerts: Optional[AdminAlertService] = None,
    ):
        self.settings = settings
        self.database = database
        self.email = email or EmailNotifier(settings)
        self.wecom = wecom or WeComNotifier(settings.http_timeout_seconds)
        self.admin_alerts = admin_alerts
        self.logger = logging.getLogger(__name__)

    def run(
        self,
        run_date: Optional[date] = None,
        dry_run: bool = False,
        now: Optional[datetime] = None,
    ) -> AlertSummary:
        today = run_date or (
            now.astimezone(ZoneInfo(self.settings.timezone)).date()
            if now is not None
            else next_day(self.settings.timezone) - date.resolution
        )
        target = today + date.resolution
        ScheduleService(self.settings, self.database).ensure_fresh(today)
        window = self._matching_window(target)
        if window is None:
            self._set_last_run(target)
            return AlertSummary(target)
        message = CommuteMessage(target, window.phase, window.edition)
        attempted = sent = skipped = failed = 0
        subscribers = self.database.query_all(
            "SELECT * FROM subscribers WHERE enabled=1 ORDER BY id"
        )
        for subscriber in subscribers:
            if subscriber["only_workdays"] and target.weekday() >= 5:
                continue
            channels = []
            if subscriber["email_enabled"]:
                channels.append(("email", subscriber["email"]))
            if subscriber["wecom_enabled"]:
                channels.append(("wecom", subscriber["wecom_webhook"]))
            for channel, destination in channels:
                attempted += 1
                if dry_run:
                    sent += 1
                    continue
                result = self._deliver(
                    subscriber["id"], target, window, channel, destination, message
                )
                if result == "sent":
                    sent += 1
                elif result == "skipped":
                    skipped += 1
                else:
                    failed += 1
        self._set_last_run(target)
        if attempted and failed / attempted > 0.2 and self.admin_alerts is not None:
            self.admin_alerts.report(
                "notification_batch_failed",
                "通知批量投递失败",
                f"目标日期 {target.isoformat()}：尝试 {attempted}，失败 {failed}。",
            )
        return AlertSummary(target, attempted, sent, skipped, failed)

    def _matching_window(self, target: date) -> Optional[FairWindow]:
        row = self.database.query_one(
            """SELECT * FROM fair_schedules
               WHERE is_active=1 AND event_type='exhibition'
                 AND start_date<=? AND end_date>=?
               ORDER BY phase LIMIT 1""",
            (target.isoformat(), target.isoformat()),
        )
        if row is None:
            return None
        return FairWindow(
            edition=row["edition"],
            season=row["season"],
            phase=row["phase"],
            start_date=date.fromisoformat(row["start_date"]),
            end_date=date.fromisoformat(row["end_date"]),
            event_type=row["event_type"],
            source_name=row["source_name"],
            source_url=row["source_url"],
        )

    def _deliver(
        self,
        subscriber_id: int,
        target: date,
        window: FairWindow,
        channel: str,
        destination: str,
        message: CommuteMessage,
    ) -> str:
        now = utc_now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO deliveries(
                       subscriber_id, target_date, event_key, channel, status,
                       attempts, created_at, updated_at
                   ) VALUES(?, ?, ?, ?, 'pending', 0, ?, ?)""",
                (subscriber_id, target.isoformat(), window.event_key, channel, now, now),
            )
            delivery = connection.execute(
                """SELECT * FROM deliveries WHERE subscriber_id=? AND target_date=?
                   AND event_key=? AND channel=?""",
                (subscriber_id, target.isoformat(), window.event_key, channel),
            ).fetchone()
            if delivery["status"] == "sent" or delivery["attempts"] >= 3:
                return "skipped"
            connection.execute(
                "UPDATE deliveries SET attempts=attempts+1, status='pending', "
                "updated_at=? WHERE id=?",
                (now, delivery["id"]),
            )
            delivery_id = delivery["id"]
        try:
            if channel == "email":
                self.email.send_message(destination, message)
            else:
                self.wecom.send_message(destination, message)
        except Exception as exc:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE deliveries SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    (str(exc)[:1000], utc_now_iso(), delivery_id),
                )
            self.logger.exception(
                "delivery failed subscriber_id=%s channel=%s target_date=%s",
                subscriber_id,
                channel,
                target,
            )
            return "failed"
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE deliveries SET status='sent', sent_at=?, last_error=NULL, updated_at=?
                   WHERE id=?""",
                (utc_now_iso(), utc_now_iso(), delivery_id),
            )
        return "sent"

    def _set_last_run(self, target: date) -> None:
        now = utc_now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO app_state(key, value, updated_at) VALUES('last_alert_run', ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, updated_at=excluded.updated_at""",
                (target.isoformat(), now),
            )
