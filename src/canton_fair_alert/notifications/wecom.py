import hashlib
import logging
import time
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from canton_fair_alert.models import CommuteMessage


class WeComNotificationError(RuntimeError):
    pass


def validate_webhook(webhook: str) -> None:
    parsed = urlparse(webhook)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("WeCom webhook must be an HTTPS URL")
    if parsed.hostname != "qyapi.weixin.qq.com":
        raise ValueError("WeCom webhook host must be qyapi.weixin.qq.com")
    if not parse_qs(parsed.query).get("key"):
        raise ValueError("WeCom webhook must contain a key")


def mask_webhook(webhook: str) -> str:
    try:
        parsed = urlparse(webhook)
        query = parse_qs(parsed.query)
        key = query.get("key", [""])[0]
        suffix = key[-4:] if key else ""
        masked_query = urlencode({"key": f"****{suffix}"})
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", masked_query, ""))
    except Exception:
        return "<invalid webhook>"


def webhook_log_identity(webhook: str) -> str:
    parsed = urlparse(webhook)
    digest = hashlib.sha256(webhook.encode("utf-8")).hexdigest()[:8]
    return f"host={parsed.hostname or 'invalid'} webhook_hash={digest}"


class WeComNotifier:
    def __init__(
        self,
        timeout_seconds: int = 20,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.logger = logging.getLogger(__name__)

    def send_message(self, webhook: str, message: CommuteMessage) -> None:
        self.send(webhook, message.wecom_markdown())

    def send(self, webhook: str, markdown: str) -> None:
        validate_webhook(webhook)
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(
                    webhook,
                    json={"msgtype": "markdown", "markdown": {"content": markdown}},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                result = response.json()
                if result.get("errcode") != 0:
                    raise WeComNotificationError(
                        f"WeCom API error {result.get('errcode')}: {result.get('errmsg', '')}"
                    )
                return
            except (requests.RequestException, ValueError, WeComNotificationError) as exc:
                last_error = exc
                self.logger.warning(
                    "WeCom delivery failed attempt=%s %s error=%s",
                    attempt,
                    webhook_log_identity(webhook),
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(3 ** (attempt - 1))
        raise WeComNotificationError(f"WeCom delivery failed: {last_error}")
