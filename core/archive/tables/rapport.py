"""版本 33 的固定关系账本定义，迁移和新建数据库共用。"""

RELATIONSHIP_V33_SQL = """
CREATE TABLE IF NOT EXISTS relationship_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    event_key TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    requested_json TEXT NOT NULL,
    applied_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('applied','uncertain','superseded')),
    supersedes_id INTEGER REFERENCES relationship_changes(id) ON DELETE SET NULL,
    UNIQUE(profile_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_relationship_changes_profile ON relationship_changes(profile_id, id);
CREATE TABLE IF NOT EXISTS relationship_evidence_claims (
    profile_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL,
    change_id INTEGER NOT NULL REFERENCES relationship_changes(id) ON DELETE CASCADE,
    PRIMARY KEY(profile_id, source_key)
);
CREATE TABLE IF NOT EXISTS relationship_states (
    profile_id TEXT PRIMARY KEY REFERENCES relationships(id) ON DELETE CASCADE,
    familiarity REAL NOT NULL CHECK(familiarity BETWEEN 0 AND 1),
    trust REAL NOT NULL CHECK(trust BETWEEN 0 AND 1),
    affinity REAL NOT NULL CHECK(affinity BETWEEN 0 AND 1),
    observed_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""
