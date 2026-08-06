from dataclasses import replace
from pathlib import Path

import pytest

from canton_fair_alert.config import load_settings
from canton_fair_alert.db import Database
from canton_fair_alert.migrations import migrate


@pytest.fixture
def settings(tmp_path: Path):
    return replace(
        load_settings(Path("/nonexistent")),
        database_path=tmp_path / "app.db",
        snapshot_dir=tmp_path / "snapshots",
        sources_file=tmp_path / "sources.json",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
        smtp_from_email="alerts@example.com",
        admin_email="admin@example.com",
        schedule_max_stale_days=180,
    )


@pytest.fixture
def database(settings):
    database = Database(settings.database_path)
    migrate(database)
    return database
