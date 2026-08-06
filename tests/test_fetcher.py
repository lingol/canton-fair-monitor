from dataclasses import replace

import pytest

from canton_fair_alert.fetchers.cantonfair_official import (
    MAX_BODY_BYTES,
    FetchError,
    OfficialFetcher,
)


class Response:
    def __init__(self, url, status=200, headers=None, body=b"<html></html>"):
        self.url = url
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html"}
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def fetcher(settings, responses):
    return OfficialFetcher(replace(settings, http_max_retries=1), Session(responses))


def test_success_and_conditional_headers(settings):
    response = Response("https://www.cantonfair.org.cn/schedule")
    instance = fetcher(settings, [response])
    result = instance.fetch("https://www.cantonfair.org.cn/schedule", {"If-None-Match": '"old"'})
    assert result.body == b"<html></html>"
    assert instance.session.calls[0][1]["headers"]["If-None-Match"] == '"old"'
    assert response.closed


def test_non_official_source_rejected_before_request(settings):
    session = Session([])
    with pytest.raises(FetchError, match="approved official domain"):
        OfficialFetcher(settings, session).fetch("https://evil.example/schedule", {})
    assert session.calls == []


def test_redirect_to_non_official_domain_is_not_followed(settings):
    redirect = Response(
        "https://www.cantonfair.org.cn/schedule",
        status=302,
        headers={"Location": "https://evil.example/payload"},
    )
    instance = fetcher(settings, [redirect])
    with pytest.raises(FetchError, match="redirected"):
        instance.fetch("https://www.cantonfair.org.cn/schedule", {})
    assert len(instance.session.calls) == 1


@pytest.mark.parametrize(
    "headers,body,error",
    [
        ({"Content-Type": "application/json"}, b"{}", "Content-Type"),
        (
            {"Content-Type": "text/html", "Content-Length": str(MAX_BODY_BYTES + 1)},
            b"",
            "exceeds",
        ),
    ],
)
def test_invalid_response_is_rejected(settings, headers, body, error):
    instance = fetcher(
        settings, [Response("https://www.cantonfair.org.cn/schedule", headers=headers, body=body)]
    )
    with pytest.raises(FetchError, match=error):
        instance.fetch("https://www.cantonfair.org.cn/schedule", {})
