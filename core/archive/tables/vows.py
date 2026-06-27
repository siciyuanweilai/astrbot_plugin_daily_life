COMMITMENT_SQL = """
-- 承诺与邀约
CREATE TABLE IF NOT EXISTS commitments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'plan',
            trigger_date TEXT NOT NULL DEFAULT '',
            trigger_time TEXT NOT NULL DEFAULT '',
            time_window TEXT NOT NULL DEFAULT '',
            place TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            confidence REAL NOT NULL DEFAULT 1.0,
            source TEXT NOT NULL DEFAULT 'manual',
            source_session TEXT NOT NULL DEFAULT '',
            source_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            activated_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT ''
        );
CREATE TABLE IF NOT EXISTS commitment_people (
            commitment_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            person TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(commitment_id, sort_order),
            FOREIGN KEY(commitment_id) REFERENCES commitments(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS day_commitments (
            date TEXT NOT NULL,
            commitment_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(date, commitment_id),
            FOREIGN KEY(commitment_id) REFERENCES commitments(id) ON DELETE CASCADE
        );
"""
