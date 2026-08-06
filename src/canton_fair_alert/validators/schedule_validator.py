from collections import Counter
from datetime import timedelta
from typing import Sequence

from canton_fair_alert.models import FairWindow


class ScheduleValidationError(ValueError):
    pass


def validate_schedule(windows: Sequence[FairWindow]) -> None:
    errors = []
    phases = [window.phase for window in windows]
    if len(windows) != 3 or Counter(phases) != Counter({1: 1, 2: 1, 3: 1}):
        errors.append("schedule must contain exactly one window for phases 1, 2, and 3")
    ordered = sorted(windows, key=lambda window: window.phase)
    seasons = {window.season for window in windows}
    editions = {window.edition for window in windows if window.edition is not None}
    years = {window.start_date.year for window in windows} | {
        window.end_date.year for window in windows
    }
    if len(seasons) != 1 or "unknown" in seasons:
        errors.append("all windows must have the same recognized season")
    if len(editions) > 1 or any(edition is not None and edition <= 0 for edition in editions):
        errors.append("edition must be one positive value")
    if len(years) > 2:
        errors.append("schedule contains inconsistent years")
    for window in ordered:
        if window.phase not in {1, 2, 3}:
            errors.append(f"invalid phase {window.phase}")
        if window.start_date > window.end_date:
            errors.append(f"phase {window.phase} starts after it ends")
        if (window.end_date - window.start_date).days != 4:
            errors.append(f"phase {window.phase} must be exactly five consecutive days")
        allowed_months = {4, 5} if window.season == "spring" else {10, 11}
        days = [window.start_date + timedelta(days=index) for index in range(5)]
        if sum(day.month in allowed_months for day in days) < 3:
            errors.append(f"phase {window.phase} is outside the expected season months")
    for previous, current in zip(ordered, ordered[1:]):
        if previous.end_date >= current.start_date:
            errors.append(f"phases {previous.phase} and {current.phase} overlap")
        gap = (current.start_date - previous.end_date).days - 1
        if not 1 <= gap <= 14:
            errors.append(
                f"gap between phases {previous.phase} and {current.phase} is unreasonable"
            )
    if errors:
        raise ScheduleValidationError("; ".join(errors))
