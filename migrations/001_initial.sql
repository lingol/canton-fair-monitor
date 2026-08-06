CREATE TABLE IF NOT EXISTS fair_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edition INTEGER,
    season TEXT NOT NULL CHECK (season IN ('spring', 'autumn', 'unknown')),
    phase INTEGER NOT NULL CHECK (phase IN (1, 2, 3)),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'exhibition'
        CHECK (event_type IN ('exhibition', 'setup', 'teardown')),
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP INDEX IF EXISTS ux_fair_schedules_version;
CREATE UNIQUE INDEX IF NOT EXISTS ux_fair_schedules_active_version
ON fair_schedules(COALESCE(edition, -1), season, phase, event_type)
WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS ix_fair_schedules_active_dates
ON fair_schedules(is_active, start_date, end_date);

CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    wecom_webhook TEXT,
    email_enabled INTEGER NOT NULL DEFAULT 0,
    wecom_enabled INTEGER NOT NULL DEFAULT 0,
    only_workdays INTEGER NOT NULL DEFAULT 1,
    include_setup_days INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_id INTEGER NOT NULL,
    target_date TEXT NOT NULL,
    event_key TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'wecom')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_deliveries_idempotency
ON deliveries(subscriber_id, target_date, event_key, channel);

CREATE TABLE IF NOT EXISTS admin_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_sent_at TEXT,
    resolved_at TEXT,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    last_message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
