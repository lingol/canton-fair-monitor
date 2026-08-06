from datetime import date

from canton_fair_alert.models import CommuteMessage
from canton_fair_alert.notifications.email import EmailNotifier


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, **kwargs):
        self.host = host
        self.port = port
        self.messages = []
        self.logged_in = None
        FakeSMTP.instances.append(self)

    def ehlo(self):
        return None

    def starttls(self, context=None):
        return None

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.messages.append(message)

    def quit(self):
        return None


def test_email_message_and_starttls(monkeypatch, settings):
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    EmailNotifier(settings).send_message(
        "person@example.com", CommuteMessage(date(2026, 10, 15), 1, 140)
    )
    sent = FakeSMTP.instances[-1].messages[0]
    assert sent["Subject"] == "明天广交会期间，建议不要开车上班"
    assert "2026-10-15" in sent.get_content()
    assert "第 140 届" in sent.get_content()


def test_unknown_edition_is_not_invented(settings):
    message = EmailNotifier(settings).build_email(
        "person@example.com", "subject", CommuteMessage(date(2026, 10, 15), 2, None).email_text()
    )
    assert "第 None 届" not in message.get_content()
