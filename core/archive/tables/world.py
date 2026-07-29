WORLD_SQL = """
-- 世界记忆与关系
CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            summary TEXT NOT NULL,
            place TEXT NOT NULL DEFAULT '',
            importance TEXT NOT NULL DEFAULT 'normal',
            source TEXT NOT NULL DEFAULT 'event',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, summary)
        );
CREATE TABLE IF NOT EXISTS event_people (
            event_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            person TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(event_id, sort_order),
            FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS places (
            name TEXT PRIMARY KEY,
            type TEXT NOT NULL DEFAULT 'place',
            hint TEXT NOT NULL DEFAULT '',
            visits INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL DEFAULT '',
            last_seen TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'daily'
        );
CREATE TABLE IF NOT EXISTS relationships (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            first_seen TEXT NOT NULL DEFAULT '',
            last_seen TEXT NOT NULL DEFAULT '',
            interactions INTEGER NOT NULL DEFAULT 0,
            platform TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT '',
            alias TEXT NOT NULL DEFAULT '',
            persona_hint TEXT NOT NULL DEFAULT '',
            subjective_name TEXT NOT NULL DEFAULT '',
            subjective_tags TEXT NOT NULL DEFAULT '',
            relationship_story TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat'
        );
CREATE TABLE IF NOT EXISTS relationship_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            date TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat',
            FOREIGN KEY(profile_id) REFERENCES relationships(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS relationship_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            date TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'memory',
            weight REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(profile_id) REFERENCES relationships(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS relationship_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT '',
            contact_type TEXT NOT NULL DEFAULT 'unknown',
            target_scope TEXT NOT NULL DEFAULT '',
            group_id TEXT NOT NULL DEFAULT '',
            group_name TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL DEFAULT '',
            last_seen TEXT NOT NULL DEFAULT '',
            is_reachable INTEGER NOT NULL DEFAULT 1,
            blocked_reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat',
            UNIQUE(profile_id, contact_type, target_scope, group_id),
            FOREIGN KEY(profile_id) REFERENCES relationships(id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS chat_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            brief TEXT NOT NULL DEFAULT '',
            long_summary TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'chat',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS chat_summary_people (
            summary_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            person TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(summary_id, sort_order),
            FOREIGN KEY(summary_id) REFERENCES chat_summaries(id) ON DELETE CASCADE
        );
"""
