import hashlib
import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from canton_fair_alert._compat import ZoneInfo
from canton_fair_alert.config import Settings, SourceConfig, load_sources
from canton_fair_alert.db import Database
from canton_fair_alert.fetchers.base import FetchResult
from canton_fair_alert.fetchers.cantonfair_official import OfficialFetcher
from canton_fair_alert.models import FairWindow
from canton_fair_alert.parsers.cantonfair_html import CantonFairHtmlParser
from canton_fair_alert.time_utils import utc_now_iso
from canton_fair_alert.validators.schedule_validator import validate_schedule


class ScheduleServiceError(RuntimeError):
    pass


class ScheduleService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        fetcher: Optional[OfficialFetcher] = None,
        parser: Optional[CantonFairHtmlParser] = None,
    ):
        self.settings = settings
        self.database = database
        self.fetcher = fetcher or OfficialFetcher(settings)
        self.parser = parser or CantonFairHtmlParser()
        self.logger = logging.getLogger(__name__)

    def refresh(self, force: bool = False, accept_official: bool = False) -> List[FairWindow]:
        errors = []
        for source in load_sources(self.settings.sources_file):
            try:
                return self._refresh_source(source, force, accept_official)
            except Exception as exc:
                errors.append(f"{source.name}: {exc}")
                self.logger.exception("schedule refresh source failed source=%s", source.name)
        raise ScheduleServiceError("; ".join(errors) or "no enabled official sources")

    def _refresh_source(
        self, source: SourceConfig, force: bool, accept_official: bool
    ) -> List[FairWindow]:
        conditional = {} if force else self._conditional_headers(source.name, source.url)
        result = self.fetcher.fetch(source.url, conditional)
        if result.not_modified:
            self._set_state("last_successful_fetch", utc_now_iso())
            return self.active_windows()
        digest = hashlib.sha256(result.body).hexdigest()
        try:
            windows = list(self.parser.parse(result.body, source.name, result.final_url))
            validate_schedule(windows)
            self._check_manual_override(accept_official)
            self._check_large_change(windows)
            self.replace_active(windows, digest, fetched_at=utc_now_iso())
        except Exception:
            snapshot = self.save_failure_snapshot(result, digest)
            self.logger.exception("parse or validation failed snapshot=%s", snapshot)
            raise
        self._set_fetch_state(source.name, result, digest)
        return windows

    def _check_manual_override(self, accept_official: bool) -> None:
        row = self.database.query_one(
            "SELECT source_name FROM fair_schedules WHERE is_active=1 LIMIT 1"
        )
        if row and str(row["source_name"]).startswith("manual") and not accept_official:
            raise ScheduleServiceError(
                "manual schedule is active; use --accept-official to replace it"
            )

    def _check_large_change(self, windows: Sequence[FairWindow]) -> None:
        existing = self.active_windows()
        if len(existing) != 3 or len(windows) != 3:
            return
        old_first, new_first = existing[0], windows[0]
        if old_first.edition != new_first.edition or old_first.season != new_first.season:
            return
        changes = []
        for old, new in zip(existing, windows):
            start_shift = abs((new.start_date - old.start_date).days)
            end_shift = abs((new.end_date - old.end_date).days)
            if max(start_shift, end_shift) > 3:
                changes.append(
                    f"phase {old.phase}: {old.start_date}..{old.end_date} -> "
                    f"{new.start_date}..{new.end_date}"
                )
        if changes:
            raise ScheduleServiceError("large same-edition schedule change: " + "; ".join(changes))

    def replace_active(
        self, windows: Sequence[FairWindow], source_hash: str, fetched_at: Optional[str] = None
    ) -> None:
        validate_schedule(windows)
        now = utc_now_iso()
        fetched = fetched_at or now
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE fair_schedules SET is_active=0, updated_at=? WHERE is_active=1", (now,)
            )
            for window in windows:
                connection.execute(
                    """INSERT INTO fair_schedules(
                           edition, season, phase, start_date, end_date, event_type,
                           source_name, source_url, source_hash, fetched_at, verified_at,
                           is_active, created_at, updated_at
                       ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        window.edition,
                        window.season,
                        window.phase,
                        window.start_date.isoformat(),
                        window.end_date.isoformat(),
                        window.event_type,
                        window.source_name,
                        window.source_url,
                        source_hash,
                        fetched,
                        now,
                        now,
                        now,
                    ),
                )
            connection.execute(
                """INSERT INTO app_state(key, value, updated_at)
                   VALUES('last_successful_parse', ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, updated_at=excluded.updated_at""",
                (now, now),
            )

    def import_payload(self, payload: Mapping[str, object]) -> List[FairWindow]:
        windows = self.parse_payload(payload)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self.replace_active(windows, digest)
        return windows

    def parse_payload(self, payload: Mapping[str, object]) -> List[FairWindow]:
        edition_value = payload.get("edition")
        if edition_value is None:
            edition = None
        elif isinstance(edition_value, (str, int)) and not isinstance(edition_value, bool):
            edition = int(edition_value)
        else:
            raise ValueError("edition must be an integer or null")
        season = str(payload["season"])
        source_name = str(payload.get("source_name", "manual_admin_override"))
        if not source_name.startswith("manual"):
            raise ValueError("manual import source_name must start with 'manual'")
        source_url = str(payload.get("source_url", "manual"))
        raw_windows = payload["windows"]
        if not isinstance(raw_windows, list):
            raise ValueError("windows must be a list")
        windows = [
            FairWindow(
                edition=edition,
                season=season,
                phase=int(item["phase"]),
                start_date=date.fromisoformat(str(item["start_date"])),
                end_date=date.fromisoformat(str(item["end_date"])),
                event_type=str(item.get("event_type", "exhibition")),
                source_name=source_name,
                source_url=source_url,
            )
            for item in raw_windows
            if isinstance(item, dict)
        ]
        validate_schedule(windows)
        return windows

    def active_windows(self) -> List[FairWindow]:
        rows = self.database.query_all(
            "SELECT * FROM fair_schedules WHERE is_active=1 ORDER BY phase"
        )
        return [
            FairWindow(
                edition=row["edition"],
                season=row["season"],
                phase=row["phase"],
                start_date=date.fromisoformat(row["start_date"]),
                end_date=date.fromisoformat(row["end_date"]),
                event_type=row["event_type"],
                source_name=row["source_name"],
                source_url=row["source_url"],
            )
            for row in rows
        ]

    def ensure_fresh(self, today: date) -> None:
        row = self.database.query_one(
            "SELECT MAX(verified_at) AS verified_at FROM fair_schedules WHERE is_active=1"
        )
        if not row or not row["verified_at"]:
            raise ScheduleServiceError("no valid active schedule")
        verified = datetime.fromisoformat(row["verified_at"]).date()
        if today - verified > timedelta(days=self.settings.schedule_max_stale_days):
            raise ScheduleServiceError("last valid schedule is stale")

    def save_failure_snapshot(self, result: FetchResult, digest: str) -> Path:
        self.settings.snapshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(tz=ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
        path = self.settings.snapshot_dir / f"failure-{stamp}-{digest[:12]}.html"
        path.write_bytes(result.body)
        path.chmod(0o600)
        snapshots = sorted(
            self.settings.snapshot_dir.glob("failure-*.html"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in snapshots[10:]:
            old.unlink()
        return path

    def export_payload(self) -> Dict[str, object]:
        windows = self.active_windows()
        if not windows:
            return {"windows": []}
        first = windows[0]
        return {
            "edition": first.edition,
            "season": first.season,
            "source_name": first.source_name,
            "source_url": first.source_url,
            "windows": [
                {
                    **asdict(window),
                    "start_date": window.start_date.isoformat(),
                    "end_date": window.end_date.isoformat(),
                }
                for window in windows
            ],
        }

    def _conditional_headers(self, source_name: str, source_url: str) -> Dict[str, str]:
        cached_url = self.database.query_one(
            "SELECT value FROM app_state WHERE key=?", (f"http_source_url:{source_name}",)
        )
        if not cached_url or cached_url["value"] != source_url:
            return {}
        headers = {}
        for header, key in (
            ("If-None-Match", "http_etag"),
            ("If-Modified-Since", "http_last_modified"),
        ):
            row = self.database.query_one(
                "SELECT value FROM app_state WHERE key=?", (f"{key}:{source_name}",)
            )
            if row and row["value"]:
                headers[header] = row["value"]
        return headers

    def _set_fetch_state(self, source_name: str, result: FetchResult, digest: str) -> None:
        values = {
            "last_successful_fetch": utc_now_iso(),
            "last_official_hash": digest,
            f"http_source_url:{source_name}": result.url,
            f"http_etag:{source_name}": result.etag or "",
            f"http_last_modified:{source_name}": result.last_modified or "",
        }
        with self.database.transaction() as connection:
            for key, value in values.items():
                now = utc_now_iso()
                connection.execute(
                    """INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET
                           value=excluded.value, updated_at=excluded.updated_at""",
                    (key, value, now),
                )

    def _set_state(self, key: str, value: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, utc_now_iso()),
            )
