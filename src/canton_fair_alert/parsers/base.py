from typing import Protocol, Sequence

from canton_fair_alert.models import FairWindow


class Parser(Protocol):
    version: str

    def parse(self, html: bytes, source_name: str, source_url: str) -> Sequence[FairWindow]: ...
