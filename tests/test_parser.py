from pathlib import Path

import pytest

from canton_fair_alert.parsers.cantonfair_html import CantonFairHtmlParser, ParseError

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
