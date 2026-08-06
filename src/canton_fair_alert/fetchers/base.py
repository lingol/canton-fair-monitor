from dataclasses import dataclass
from typing import Mapping, Optional, Protocol


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    etag: Optional[str]
    last_modified: Optional[str]
    not_modified: bool = False


class Fetcher(Protocol):
    def fetch(self, url: str, conditional_headers: Mapping[str, str]) -> FetchResult: ...
