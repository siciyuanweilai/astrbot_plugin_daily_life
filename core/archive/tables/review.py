REVIEW_SQL = """
-- 复盘与长期记忆
CREATE TABLE IF NOT EXISTS daily_reviews (
            date TEXT PRIMARY KEY,
            summary TEXT NOT NULL DEFAULT '',
            sleep_debt_delta REAL NOT NULL DEFAULT 0,
            energy_carryover REAL NOT NULL DEFAULT 60,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS daily_review_points (
            date TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'memory',
            content TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(date, kind, sort_order),
            FOREIGN KEY(date) REFERENCES daily_reviews(date) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'general',
            content TEXT NOT NULL DEFAULT '',
            weight REAL NOT NULL DEFAULT 1.0,
            evidence TEXT NOT NULL DEFAULT '',
            last_seen TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'learning',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(category, content)
        );
CREATE TABLE IF NOT EXISTS review_preferences (
            date TEXT NOT NULL,
            preference_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            PRIMARY KEY(date, preference_id),
            FOREIGN KEY(date) REFERENCES daily_reviews(date) ON DELETE CASCADE,
            FOREIGN KEY(preference_id) REFERENCES preferences(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS life_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            effect TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            source TEXT NOT NULL DEFAULT 'life_event',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
"""
