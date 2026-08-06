import hashlib
import re
from datetime import datetime, timedelta
from typing import Optional

from canton_fair_alert._compat import ZoneInfo
from canton_fair_alert.config import Settings
from canton_fair_alert.db import Database
from canton_fair_alert.notifications.email import EmailNotifier


def normalize_error(message: str) -> str:
    value = re.sub(r"\b20\d{2}-\d{2}-\d{2}[T ][0-9:.+Z-]+", "<timestamp>", message)
    value = re.sub(r"\b\d{4,}\b", "<number>", value)
    return " ".join(value.split()).lower()


def alert_fingerprint(category: str, message: str, source_name: str = "") -> str:
    raw = f"{category}\0{normalize_error(message)}\0{source_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AdminAlertService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        notifier: Optional[EmailNotifier] = None,
    ):
        self.settings = settings
        self.database = database
        self.notifier = notifier or EmailNotifier(settings)

    def report(self, category: str, title: str, message: str, source_name: str = "") -> bool:
        fingerprint = alert_fingerprint(category, message, source_name)
        now = datetime.now(tz=ZoneInfo("UTC"))
        now_iso = now.isoformat(timespec="seconds")
        should_send = False
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM admin_alerts WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO admin_alerts(
                           fingerprint, category, first_seen_at, last_seen_at,
                           occurrence_count, last_message
                       ) VALUES(?, ?, ?, ?, 1, ?)""",
                    (fingerprint, category, now_iso, now_iso, message),
                )
                should_send = True
            else:
                connection.execute(
                    """UPDATE admin_alerts SET last_seen_at=?, occurrence_count=occurrence_count+1,
                           last_message=?, resolved_at=NULL WHERE fingerprint=?""",
                    (now_iso, message, fingerprint),
                )
                last_sent = (
                    datetime.fromisoformat(row["last_sent_at"]) if row["last_sent_at"] else None
                )
                should_send = last_sent is None or now - last_sent >= timedelta(
                    hours=self.settings.admin_alert_cooldown_hours
                )
        if not should_send:
            return False
        self.notifier.send(
            self.settings.admin_email,
            f"[广交会提醒][异常] {title}",
            message,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE admin_alerts SET last_sent_at=? WHERE fingerprint=?",
                (now_iso, fingerprint),
            )
        return True

    def recover(self, category: str, title: str) -> int:
        rows = self.database.query_all(
            "SELECT fingerprint FROM admin_alerts WHERE category=? AND resolved_at IS NULL",
            (category,),
        )
        if not rows:
            return 0
        now_iso = datetime.now(tz=ZoneInfo("UTC")).isoformat(timespec="seconds")
        self.notifier.send(
            self.settings.admin_email,
            f"[广交会提醒][恢复] {title}",
            f"异常已于 {now_iso} 恢复。",
        )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE admin_alerts SET resolved_at=? WHERE category=? AND resolved_at IS NULL",
                (now_iso, category),
            )
        return len(rows)
