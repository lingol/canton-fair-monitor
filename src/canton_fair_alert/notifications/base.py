from typing import Protocol

from canton_fair_alert.models import CommuteMessage


class EmailChannel(Protocol):
    def send_message(self, recipient: str, message: CommuteMessage) -> None: ...


class WeComChannel(Protocol):
    def send_message(self, webhook: str, message: CommuteMessage) -> None: ...
