WORLD_SQL_BASE = """
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
            latitude REAL,
            longitude REAL,
            coordinate_source TEXT NOT NULL DEFAULT '',
            coordinate_updated_at TEXT NOT NULL DEFAULT '',
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

STYLE_CATALOG_SQL = """
-- 视觉衣橱候选与语义反馈
CREATE TABLE IF NOT EXISTS style_catalog_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN (
                'outfit', 'top', 'bottom', 'footwear',
                'accessory', 'hair', 'makeup', 'nails'
            )),
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            image_path TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_scope TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT 'user_image',
            source_image_hash TEXT NOT NULL,
            attributes_json TEXT NOT NULL DEFAULT '{}',
            confidence REAL NOT NULL DEFAULT 0,
            preference_score REAL NOT NULL DEFAULT 0,
            feedback_count INTEGER NOT NULL DEFAULT 0,
            seen_count INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            last_used_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_image_hash, kind)
        );
CREATE TABLE IF NOT EXISTS style_catalog_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '',
            sentiment TEXT NOT NULL DEFAULT 'neutral',
            score_delta REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(item_id) REFERENCES style_catalog_items(id) ON DELETE CASCADE
        );
"""

WORLD_SQL = WORLD_SQL_BASE + STYLE_CATALOG_SQL


__all__ = ["STYLE_CATALOG_SQL", "WORLD_SQL", "WORLD_SQL_BASE"]
