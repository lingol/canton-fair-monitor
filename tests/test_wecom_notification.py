from datetime import date

import pytest

from canton_fair_alert.models import CommuteMessage
from canton_fair_alert.notifications.wecom import WeComNotifier, mask_webhook, validate_webhook

WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=12345678-abcd"


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"errcode": 0, "errmsg": "ok"}


class Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response()


def test_wecom_markdown_delivery():
    session = Session()
    notifier = WeComNotifier(session=session)
    notifier.send_message(WEBHOOK, CommuteMessage(date(2026, 10, 15), 1, 140))
    payload = session.calls[0][1]["json"]
    assert payload["msgtype"] == "markdown"
    assert "2026-10-15" in payload["markdown"]["content"]


def test_webhook_mask_and_validation():
    masked = mask_webhook(WEBHOOK)
    assert "12345678" not in masked
    assert "abcd" in masked
    with pytest.raises(ValueError):
        validate_webhook("http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")
    with pytest.raises(ValueError):
        validate_webhook("https://evil.example/send?key=x")
