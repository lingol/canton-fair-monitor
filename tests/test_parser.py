from pathlib import Path

import pytest

from canton_fair_alert.parsers.cantonfair_html import CantonFairHtmlParser, ParseError
from canton_fair_alert.validators.schedule_validator import (
    ScheduleValidationError,
    validate_schedule,
)

FIXTURES = Path(__file__).parent / "fixtures"


def parse(name: str):
    return CantonFairHtmlParser().parse(
        (FIXTURES / name).read_bytes(), "official", "https://www.cantonfair.org.cn/schedule"
    )


def test_chinese_page_dates_and_edition():
    windows = parse("official_page_valid.html")
    assert [window.phase for window in windows] == [1, 2, 3]
    assert windows[0].edition == 140
    assert windows[2].end_date.isoformat() == "2026-11-04"


def test_english_page_dates():
    windows = parse("official_page_english.html")
    assert len(windows) == 3
    assert windows[0].start_date.isoformat() == "2026-10-15"


def test_current_official_general_information_format():
    html = b"""<h2>The 139th (Spring)</h2>
    <p>Phase 1: April 15 - 19, 2026</p>
    <p>Phase 2: April 23 - 27, 2026</p>
    <p>Phase 3: May 1 - 5, 2026</p>"""
    windows = CantonFairHtmlParser().parse(
        html, "official", "https://cief.cantonfair.org.cn/en/cfintro/cfintro.html"
    )
    assert windows[0].edition == 139
    assert [window.season for window in windows] == ["spring"] * 3


def test_dom_data_attributes():
    html = b"""<div data-phase='1' data-start='2026-04-15' data-end='2026-04-19'></div>
    <div data-phase='2' data-start='2026-04-23' data-end='2026-04-27'></div>
    <div data-phase='3' data-start='2026-05-01' data-end='2026-05-05'></div>"""
    windows = CantonFairHtmlParser().parse(html, "official", "https://cantonfair.org.cn")
    assert [window.season for window in windows] == ["spring"] * 3


def test_format_change_fails_deterministically():
    with pytest.raises(ParseError):
        parse("official_page_format_changed.html")


def test_missing_phase_is_returned_for_validator_to_reject():
    html = b"<p>Phase 1: October 15-19, 2026</p><p>Phase 2: October 23-27, 2026</p>"
    assert len(CantonFairHtmlParser().parse(html, "x", "https://cantonfair.org.cn")) == 2


def test_official_hk_calendar_uses_row_local_edition_and_dates():
    windows = parse("official_hk_calendar.html")
    validate_schedule(windows)
    assert [(w.edition, w.phase, str(w.start_date), str(w.end_date)) for w in windows] == [
        (140, 1, "2026-10-15", "2026-10-19"),
        (140, 2, "2026-10-23", "2026-10-27"),
        (140, 3, "2026-10-31", "2026-11-04"),
    ]


def test_soft_404_has_specific_diagnostic():
    with pytest.raises(ParseError, match="soft 404"):
        parse("official_page_soft_404.html")


@pytest.mark.parametrize("phase", ["1", "2", "3"])
def test_incomplete_calendar_does_not_fall_back_to_old_article(phase):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup((FIXTURES / "official_hk_calendar.html").read_bytes(), "lxml")
    for row in soup.select("li"):
        if "Phase " + phase in row.get_text():
            row.decompose()
    windows = CantonFairHtmlParser().parse(
        str(soup).encode(), "official", "https://hk.cantonfair.org.cn/en/"
    )
    with pytest.raises(ScheduleValidationError, match="exactly one"):
        validate_schedule(windows)


@pytest.mark.parametrize("bad_date", ["10-15 - 10-19", "2026-10-32 - 2026-11-4"])
def test_calendar_rejects_missing_year_and_invalid_dates(bad_date):
    body = (
        (FIXTURES / "official_hk_calendar.html")
        .read_text()
        .replace("2026-10-15 - 2026-10-19", bad_date)
    )
    with pytest.raises(ParseError, match="calendar row"):
        CantonFairHtmlParser().parse(body.encode(), "official", "https://hk.cantonfair.org.cn/en/")


def test_calendar_rejects_conflicting_phase_dates():
    body = (FIXTURES / "official_hk_calendar.html").read_text()
    body = body.replace(
        "</ul>",
        '<li><a><span class="date">2026-10-16 - 2026-10-20</span>'
        "The 140th Canton Fair (Phase 1)</a></li></ul>",
    )
    with pytest.raises(ParseError, match="conflicting"):
        CantonFairHtmlParser().parse(body.encode(), "official", "https://hk.cantonfair.org.cn/en/")


def test_calendar_actual_gb18030_bytes_with_incorrect_utf8_declaration():
    body = (FIXTURES / "official_hk_calendar.html").read_text().encode("gb18030")
    windows = CantonFairHtmlParser().parse(body, "official", "https://hk.cantonfair.org.cn/en/")
    validate_schedule(windows)
    assert [w.edition for w in windows] == [140, 140, 140]


def test_missing_calendar_does_not_parse_old_article():
    html = (FIXTURES / "official_page_english.html").read_bytes()
    with pytest.raises(ParseError, match="no Canton Fair exhibition calendar rows"):
        CantonFairHtmlParser().parse(html, "official", "https://hk.cantonfair.org.cn/en/")
