from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from canton_fair_alert.models import FairWindow
from canton_fair_alert.parsers.cantonfair_html import CantonFairHtmlParser
from canton_fair_alert.validators.schedule_validator import (
    ScheduleValidationError,
    validate_schedule,
)

FIXTURES = Path(__file__).parent / "fixtures"


def valid():
    return list(
        CantonFairHtmlParser().parse(
            (FIXTURES / "official_page_valid.html").read_bytes(),
            "official",
            "https://cantonfair.org.cn",
        )
    )


def test_valid_schedule():
    validate_schedule(valid())


@pytest.mark.parametrize(
    "windows",
    [
        lambda values: values[:2],
        lambda values: values + [values[-1]],
        lambda values: [values[0], replace(values[1], start_date=date(2026, 10, 18)), values[2]],
        lambda values: [replace(values[0], end_date=date(2026, 10, 20)), *values[1:]],
        lambda values: [replace(item, season="spring") for item in values],
        lambda values: [replace(values[0], edition=139), *values[1:]],
    ],
)
def test_invalid_schedules(windows):
    with pytest.raises(ScheduleValidationError):
        validate_schedule(windows(valid()))


def test_unknown_season_rejected():
    windows = [
        FairWindow(
            140,
            "unknown",
            phase,
            date(2026, 7, start),
            date(2026, 7, start + 4),
            "exhibition",
            "x",
            "x",
        )
        for phase, start in ((1, 1), (2, 9), (3, 17))
    ]
    with pytest.raises(ScheduleValidationError):
        validate_schedule(windows)
