import hashlib
from pathlib import Path

import pytest

from canton_fair_alert.fetchers.base import FetchResult
from canton_fair_alert.schedule_service import ScheduleService, ScheduleServiceError

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFetcher:
    def __init__(self, body: bytes):
        self.body = body

    def fetch(self, url, conditional_headers):
        return FetchResult(
            url=url,
            final_url="https://www.cantonfair.org.cn/schedule",
            status_code=200,
            content_type="text/html",
            body=self.body,
            etag='"test"',
            last_modified=None,
        )


def test_valid_refresh_is_transactional(settings, database):
    body = (FIXTURES / "official_page_valid.html").read_bytes()
    service = ScheduleService(settings, database, fetcher=FakeFetcher(body))
    windows = service.refresh()
    assert len(windows) == 3
    assert len(service.active_windows()) == 3
    assert database.query_one("SELECT value FROM app_state WHERE key='last_successful_parse'")


def test_invalid_refresh_preserves_last_valid_and_saves_snapshot(settings, database):
    valid_body = (FIXTURES / "official_page_valid.html").read_bytes()
    service = ScheduleService(settings, database, fetcher=FakeFetcher(valid_body))
    service.refresh()
    invalid_body = (FIXTURES / "official_page_format_changed.html").read_bytes()
    broken = ScheduleService(settings, database, fetcher=FakeFetcher(invalid_body))
    with pytest.raises(ScheduleServiceError, match="no deterministic"):
        broken.refresh()
    assert len(broken.active_windows()) == 3
    assert len(list(settings.snapshot_dir.glob("failure-*.html"))) == 1


def test_manual_schedule_requires_explicit_official_acceptance(settings, database):
    service = ScheduleService(settings, database)
    payload = manual_payload()
    service.import_payload(payload)
    body = (FIXTURES / "official_page_valid.html").read_bytes()
    official = ScheduleService(settings, database, fetcher=FakeFetcher(body))
    with pytest.raises(Exception, match="manual schedule"):
        official.refresh()
    assert official.active_windows()[0].source_name == "manual_admin_override"
    official.refresh(accept_official=True)
    assert official.active_windows()[0].source_name == "cantonfair_official_primary"


def test_reimporting_identical_manual_schedule_is_idempotent(settings, database):
    service = ScheduleService(settings, database)
    service.import_payload(manual_payload())
    service.import_payload(manual_payload())
    assert len(service.active_windows()) == 3


def test_snapshot_retention_is_ten(settings, database):
    service = ScheduleService(settings, database)
    for index in range(12):
        body = f"broken-{index}".encode()
        result = FetchResult("u", "u", 200, "text/html", body, None, None)
        service.save_failure_snapshot(result, hashlib.sha256(body).hexdigest())
    assert len(list(settings.snapshot_dir.glob("failure-*.html"))) <= 10


def manual_payload():
    return {
        "edition": 140,
        "season": "autumn",
        "source_name": "manual_admin_override",
        "source_url": "manual",
        "windows": [
            {"phase": 1, "start_date": "2026-10-15", "end_date": "2026-10-19"},
            {"phase": 2, "start_date": "2026-10-23", "end_date": "2026-10-27"},
            {"phase": 3, "start_date": "2026-10-31", "end_date": "2026-11-04"},
        ],
    }
