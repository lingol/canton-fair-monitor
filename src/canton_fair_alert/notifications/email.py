import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

from canton_fair_alert.config import Settings
from canton_fair_alert.models import CommuteMessage


class EmailNotificationError(RuntimeError):
    pass


class EmailNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(__name__)

    def build_email(self, recipient: str, subject: str, body: str) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self.settings.smtp_from_name, self.settings.smtp_from_email))
        message["To"] = recipient
        message.set_content(body)
        return message

    def send_message(self, recipient: str, message: CommuteMessage) -> None:
        self.send(recipient, message.email_subject(), message.email_text())

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.settings.validate_smtp(require_admin=False)
        email = self.build_email(recipient, subject, body)
        client: Optional[smtplib.SMTP] = None
        try:
            if self.settings.smtp_security == "ssl":
                client = smtplib.SMTP_SSL(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=20,
                    context=ssl.create_default_context(),
                )
            else:
                client = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20)
                client.ehlo()
                if self.settings.smtp_security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
            if self.settings.smtp_username:
                client.login(self.settings.smtp_username, self.settings.smtp_password)
            client.send_message(email)
        except (OSError, smtplib.SMTPException) as exc:
            self.logger.exception("SMTP delivery failed recipient=%s", recipient)
            raise EmailNotificationError(f"SMTP delivery failed: {exc}") from exc
        finally:
            if client is not None:
                try:
                    client.quit()
                except (OSError, smtplib.SMTPException):
                    self.logger.warning("SMTP quit failed", exc_info=True)

    def test_connection(self) -> None:
        self.settings.validate_smtp()
        client: Optional[smtplib.SMTP] = None
        try:
            if self.settings.smtp_security == "ssl":
                client = smtplib.SMTP_SSL(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=20,
                    context=ssl.create_default_context(),
                )
            else:
                client = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20)
                client.ehlo()
                if self.settings.smtp_security == "starttls":
                    client.starttls(context=ssl.create_default_context())
            if self.settings.smtp_username:
                client.login(self.settings.smtp_username, self.settings.smtp_password)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailNotificationError(f"SMTP connection test failed: {exc}") from exc
        finally:
            if client is not None:
                try:
                    client.quit()
                except (OSError, smtplib.SMTPException):
                    pass
