from dataclasses import replace

from canton_fair_alert.admin_alerts import AdminAlertService, alert_fingerprint


class Mailer:
    def __init__(self):
        self.sent = []

    def send(self, recipient, subject, body):
        self.sent.append((recipient, subject, body))


def test_fingerprint_normalizes_timestamps():
    first = alert_fingerprint("parse", "failed 2026-01-01T10:00:00Z", "official")
    second = alert_fingerprint("parse", "failed 2026-01-02T11:00:00Z", "official")
    assert first == second


def test_admin_alert_cooldown_and_recovery(settings, database):
    mailer = Mailer()
    service = AdminAlertService(settings, database, mailer)
    assert service.report("parse", "官网日程解析失败", "same error", "official")
    assert not service.report("parse", "官网日程解析失败", "same error", "official")
    assert len(mailer.sent) == 1
    assert service.recover("parse", "官网日程解析已恢复") == 1
    assert "[恢复]" in mailer.sent[-1][1]


def test_zero_cooldown_resends(settings, database):
    mailer = Mailer()
    service = AdminAlertService(replace(settings, admin_alert_cooldown_hours=0), database, mailer)
    service.report("database", "数据库错误", "locked")
    service.report("database", "数据库错误", "locked")
    assert len(mailer.sent) == 2
