from datetime import date

from canton_fair_alert.alert_service import AlertService
from tests.test_alert_service import Channel, add_subscriber, seed_schedule


def test_two_runs_send_each_channel_once(settings, database):
    seed_schedule(settings, database)
    webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"
    add_subscriber(database, "both", "person@example.com", webhook, 1, 1)
    email = Channel()
    wecom = Channel()
    service = AlertService(settings, database, email, wecom)
    first = service.run(date(2026, 10, 14))
    second = service.run(date(2026, 10, 14))
    assert first.sent == 2
    assert second.sent == 0 and second.skipped == 2
    assert len(email.calls) == 1
    assert len(wecom.calls) == 1
