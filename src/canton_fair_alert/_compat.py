try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - exercised on Python 3.8
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

__all__ = ["ZoneInfo"]
