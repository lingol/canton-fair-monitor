from pathlib import Path
from typing import Optional

from canton_fair_alert.db import Database
from canton_fair_alert.time_utils import utc_now_iso

SCHEMA_VERSION = "1"


def migration_path() -> Path:
    project_path = Path(__file__).resolve().parents[2] / "migrations" / "001_initial.sql"
    if project_path.exists():
        return project_path
    return Path("/opt/canton-fair-alert/app/migrations/001_initial.sql")


def migrate(database: Database, sql_path: Optional[Path] = None) -> None:
    path = sql_path or migration_path()
    schema = path.read_text(encoding="utf-8")
    with database.transaction() as connection:
        connection.executescript(schema)
        connection.execute(
            """INSERT INTO app_state(key, value, updated_at) VALUES('schema_version', ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, updated_at=excluded.updated_at""",
            (SCHEMA_VERSION, utc_now_iso()),
        )
