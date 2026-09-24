import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from dotenv import dotenv_values

from canton_fair_alert._compat import ZoneInfo

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DEFAULT_OFFICIAL_URL = "https://hk.cantonfair.org.cn/en/"
RETIRED_OFFICIAL_URL = "https://cief.cantonfair.org.cn/en/cfintro/cfintro.html"
OFFICIAL_DOMAINS = ("cantonfair.org.cn", "cief.org.cn")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    name: str
    url: str
    enabled: bool = True
    priority: int = 1


@dataclass(frozen=True)
class Settings:
    app_env: str
    timezone: str
    database_path: Path
    snapshot_dir: Path
    sources_file: Path
    schedule_refresh_time: str
    alert_send_time: str
    schedule_max_stale_days: int
    admin_alert_cooldown_hours: int
    http_timeout_seconds: int
    http_max_retries: int
    http_user_agent: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_username: str
    smtp_password: str
    smtp_from_name: str
    smtp_from_email: str
    admin_email: str

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def validate_smtp(self, require_admin: bool = True) -> None:
        if not self.smtp_host:
            raise ConfigError("SMTP_HOST is required")
        if not 1 <= self.smtp_port <= 65535:
            raise ConfigError("SMTP_PORT must be between 1 and 65535")
        if self.smtp_security not in {"starttls", "ssl", "plain"}:
            raise ConfigError("SMTP_SECURITY must be starttls, ssl, or plain")
        if not EMAIL_RE.match(self.smtp_from_email):
            raise ConfigError("SMTP_FROM_EMAIL is invalid")
        if require_admin and not EMAIL_RE.match(self.admin_email):
            raise ConfigError("ADMIN_EMAIL is invalid")


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value))


def _as_int(values: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(values.get(key, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def load_settings(env_file: Optional[Path] = None) -> Settings:
    file_values: Dict[str, str] = {}
    if env_file is not None and env_file.exists():
        file_values = {k: str(v) for k, v in dotenv_values(env_file).items() if v is not None}
    values = {**file_values, **os.environ}
    cwd = Path.cwd()
    settings = Settings(
        app_env=values.get("APP_ENV", "development"),
        timezone=values.get("APP_TIMEZONE", "Asia/Shanghai"),
        database_path=Path(values.get("DATABASE_PATH", str(cwd / "app.db"))),
        snapshot_dir=Path(values.get("SNAPSHOT_DIR", str(cwd / "snapshots"))),
        sources_file=Path(values.get("SOURCES_FILE", str(cwd / "sources.json"))),
        schedule_refresh_time=values.get("SCHEDULE_REFRESH_TIME", "12:00"),
        alert_send_time=values.get("ALERT_SEND_TIME", "18:00"),
        schedule_max_stale_days=_as_int(values, "SCHEDULE_MAX_STALE_DAYS", 180),
        admin_alert_cooldown_hours=_as_int(values, "ADMIN_ALERT_COOLDOWN_HOURS", 24),
        http_timeout_seconds=_as_int(values, "HTTP_TIMEOUT_SECONDS", 20),
        http_max_retries=_as_int(values, "HTTP_MAX_RETRIES", 3),
        http_user_agent=values.get("HTTP_USER_AGENT", "CantonFairCommuteAlert/1.0"),
        smtp_host=values.get("SMTP_HOST", ""),
        smtp_port=_as_int(values, "SMTP_PORT", 587),
        smtp_security=values.get("SMTP_SECURITY", "starttls").lower(),
        smtp_username=values.get("SMTP_USERNAME", ""),
        smtp_password=values.get("SMTP_PASSWORD", ""),
        smtp_from_name=values.get("SMTP_FROM_NAME", "广交会通勤提醒"),
        smtp_from_email=values.get("SMTP_FROM_EMAIL", ""),
        admin_email=values.get("ADMIN_EMAIL", ""),
    )
    try:
        _ = settings.zoneinfo
    except Exception as exc:
        raise ConfigError(f"invalid APP_TIMEZONE: {settings.timezone}") from exc
    return settings


def load_sources(path: Path) -> List[SourceConfig]:
    if not path.exists():
        return [SourceConfig("cantonfair_official_primary", DEFAULT_OFFICIAL_URL)]
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        raw_sources = payload["sources"]
        sources = [
            SourceConfig(
                name=str(item["name"]),
                # Upgrade the retired bundled source without rewriting administrator config.
                url=(
                    DEFAULT_OFFICIAL_URL
                    if item["url"] == RETIRED_OFFICIAL_URL
                    else str(item["url"])
                ),
                enabled=bool(item.get("enabled", True)),
                priority=int(item.get("priority", 1)),
            )
            for item in raw_sources
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"invalid sources file {path}: {exc}") from exc
    return sorted((s for s in sources if s.enabled), key=lambda source: source.priority)
