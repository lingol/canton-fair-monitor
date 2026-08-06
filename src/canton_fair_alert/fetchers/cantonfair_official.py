import hashlib
import logging
import time
from typing import Mapping, Optional
from urllib.parse import urljoin, urlparse

import requests

from canton_fair_alert.config import OFFICIAL_DOMAINS, Settings
from canton_fair_alert.fetchers.base import FetchResult

MAX_BODY_BYTES = 5 * 1024 * 1024


class FetchError(RuntimeError):
    pass


def is_official_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)


class OfficialFetcher:
    def __init__(self, settings: Settings, session: Optional[requests.Session] = None):
        self.settings = settings
        self.session = session or requests.Session()
        self.logger = logging.getLogger(__name__)

    def fetch(self, url: str, conditional_headers: Mapping[str, str]) -> FetchResult:
        if not is_official_url(url):
            raise FetchError("source URL is not on an approved official domain")
        headers = {"User-Agent": self.settings.http_user_agent, **conditional_headers}
        last_error: Optional[Exception] = None
        for attempt in range(1, self.settings.http_max_retries + 1):
            try:
                return self._request(url, headers)
            except (requests.RequestException, FetchError) as exc:
                last_error = exc
                if attempt < self.settings.http_max_retries:
                    delay = 3 ** (attempt - 1)
                    self.logger.warning(
                        "official fetch attempt failed attempt=%s delay=%s error=%s",
                        attempt,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
        raise FetchError(f"official fetch failed after retries: {last_error}")

    def _request(self, url: str, headers: Mapping[str, str]) -> FetchResult:
        current_url = url
        response: Optional[requests.Response] = None
        for _ in range(6):
            response = self.session.get(
                current_url,
                headers=dict(headers),
                timeout=(self.settings.http_timeout_seconds, self.settings.http_timeout_seconds),
                allow_redirects=False,
                stream=True,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise FetchError("official source returned a redirect without Location")
            current_url = urljoin(current_url, location)
            if not is_official_url(current_url):
                raise FetchError("official source redirected to a non-official domain")
        else:
            raise FetchError("official source exceeded five redirects")
        if response is None:  # pragma: no cover - defensive guard
            raise FetchError("official source returned no response")
        if not is_official_url(response.url):
            response.close()
            raise FetchError("response URL is not on an approved official domain")
        if response.status_code == 304:
            result = FetchResult(
                url=url,
                final_url=response.url,
                status_code=304,
                content_type=response.headers.get("Content-Type", ""),
                body=b"",
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                not_modified=True,
            )
            response.close()
            return result
        if response.status_code != 200:
            response.close()
            raise FetchError(f"unexpected HTTP status {response.status_code}")
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            response.close()
            raise FetchError(f"unexpected Content-Type {content_type!r}")
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                too_large = int(content_length) > MAX_BODY_BYTES
            except ValueError as exc:
                response.close()
                raise FetchError("invalid Content-Length header") from exc
            if too_large:
                response.close()
                raise FetchError("response body exceeds 5 MB")
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                response.close()
                raise FetchError("response body exceeds 5 MB")
            chunks.append(chunk)
        body = b"".join(chunks)
        response.close()
        self.logger.info(
            "official fetch success url=%s bytes=%s sha256=%s",
            response.url,
            len(body),
            hashlib.sha256(body).hexdigest(),
        )
        return FetchResult(
            url=url,
            final_url=response.url,
            status_code=response.status_code,
            content_type=content_type,
            body=body,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )
