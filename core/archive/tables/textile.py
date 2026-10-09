"""数字衣橱的物品、事实、审美与后台补衣任务。"""

TEXTILE_SQL = """
CREATE TABLE IF NOT EXISTS wardrobe_units (
    unit_id TEXT PRIMARY KEY, ownership TEXT NOT NULL DEFAULT 'candidate',
    condition TEXT NOT NULL DEFAULT 'clean', acquired_at TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS wardrobe_links (
    catalog_id INTEGER NOT NULL REFERENCES style_catalog_items(id) ON DELETE CASCADE,
    unit_id TEXT NOT NULL REFERENCES wardrobe_units(unit_id) ON DELETE CASCADE,
    PRIMARY KEY(catalog_id, unit_id)
);
CREATE TABLE IF NOT EXISTS wardrobe_events (
    event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, occurred_at TEXT NOT NULL,
    source TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS wardrobe_profile (
    name TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS wardrobe_jobs (
    job_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'planned',
    updated_at TEXT NOT NULL, next_at TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '', lease_until TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}', progress_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_wardrobe_jobs_pending ON wardrobe_jobs(status, next_at);
CREATE INDEX IF NOT EXISTS idx_wardrobe_events_time ON wardrobe_events(occurred_at DESC);
"""
