from datetime import date, datetime, timedelta
from typing import Optional

from canton_fair_alert._compat import ZoneInfo


def local_today(timezone: str, now: Optional[datetime] = None) -> date:
    zone = ZoneInfo(timezone)
    current = now or datetime.now(tz=zone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=zone)
    return current.astimezone(zone).date()


def next_day(timezone: str, now: Optional[datetime] = None) -> date:
    return local_today(timezone, now) + timedelta(days=1)


def utc_now_iso() -> str:
    return datetime.now(tz=ZoneInfo("UTC")).isoformat(timespec="seconds")
