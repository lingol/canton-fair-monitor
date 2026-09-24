import hashlib
import json
from pathlib import Path

import pytest

from canton_fair_alert.config import DEFAULT_OFFICIAL_URL, RETIRED_OFFICIAL_URL
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


def test_old_installation_refresh_migrates_source_and_replaces_spring(settings, database):
    service = ScheduleService(settings, database)
    spring_html = b"""<h2>The 139th (Spring)</h2>
    <p>Phase 1: April 15-19, 2026</p><p>Phase 2: April 23-27, 2026</p>
    <p>Phase 3: May 1-5, 2026</p>"""
    spring = service.parser.parse(spring_html, "cantonfair_official_primary", RETIRED_OFFICIAL_URL)
    service.replace_active(spring, "spring-hash")
    service._set_state("http_etag:cantonfair_official_primary", '"old-etag"')
    service._set_state("http_last_modified:cantonfair_official_primary", "old-date")
    settings.sources_file.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "name": "cantonfair_official_primary",
                        "url": RETIRED_OFFICIAL_URL,
                    }
                ]
            }
        )
    )
    body = (FIXTURES / "official_hk_calendar.html").read_text().encode("gb18030")

    class CalendarFetcher:
        def __init__(self):
            self.calls = []

        def fetch(self, url, conditional_headers):
            self.calls.append((url, conditional_headers))
            return FetchResult(url, url, 200, "text/html", body, '"calendar-etag"', None)

    fetcher = CalendarFetcher()
    service = ScheduleService(settings, database, fetcher=fetcher)
    windows = service.refresh()
    assert fetcher.calls == [(DEFAULT_OFFICIAL_URL, {})]
    assert len(windows) == 3
    assert {w.edition for w in windows} == {140}
    assert {w.source_url for w in service.active_windows()} == {DEFAULT_OFFICIAL_URL}
    service.refresh()
    assert fetcher.calls[-1][1] == {"If-None-Match": '"calendar-etag"'}
    assert (
        database.query_one("SELECT COUNT(*) AS n FROM fair_schedules WHERE is_active=1")["n"] == 3
    )


def test_soft_404_preserves_schedule_and_saves_snapshot(settings, database):
    service = ScheduleService(settings, database)
    service.import_payload(manual_payload())
    before = service.active_windows()
    service.fetcher = FakeFetcher((FIXTURES / "official_page_soft_404.html").read_bytes())
    with pytest.raises(ScheduleServiceError, match="soft 404"):
        service.refresh(force=True)
    assert service.active_windows() == before
    assert len(list(settings.snapshot_dir.glob("failure-*.html"))) == 1
