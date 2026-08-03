DOMAIN_SQL = """
-- 可结算的生活领域记录
CREATE TABLE IF NOT EXISTS activity_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id TEXT NOT NULL UNIQUE,
            date TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT 'global',
            activity_type TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            started_at TEXT NOT NULL DEFAULT '',
            ended_at TEXT NOT NULL DEFAULT '',
            last_heartbeat_at TEXT NOT NULL DEFAULT '',
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'daily_plan',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS route_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin_name TEXT NOT NULL DEFAULT '',
            destination_name TEXT NOT NULL DEFAULT '',
            travel_mode TEXT NOT NULL DEFAULT 'walking',
            distance_meters REAL NOT NULL DEFAULT 0,
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            provider TEXT NOT NULL DEFAULT 'fallback',
            confidence REAL NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            UNIQUE(origin_name, destination_name, travel_mode)
        );
CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            meal_type TEXT NOT NULL DEFAULT '',
            ingredients_json TEXT NOT NULL DEFAULT '[]',
            tags_json TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'daily_plan',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS pantry_items (
            name TEXT PRIMARY KEY,
            quantity REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '',
            minimum_quantity REAL NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS pantry_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL DEFAULT '',
            delta REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            action_id TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'life_action'
        );
CREATE TABLE IF NOT EXISTS meal_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id TEXT NOT NULL UNIQUE,
            date TEXT NOT NULL DEFAULT '',
            meal_type TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            recipe_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            ingredients_json TEXT NOT NULL DEFAULT '[]',
            place TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'life_action',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            occurred_at TEXT NOT NULL DEFAULT ''
        );
CREATE TABLE IF NOT EXISTS chores (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            cadence_days INTEGER NOT NULL DEFAULT 0,
            effort INTEGER NOT NULL DEFAULT 1,
            last_completed_at TEXT NOT NULL DEFAULT '',
            next_due_at TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'daily_plan',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS chore_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id TEXT NOT NULL UNIQUE,
            chore_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            duration_minutes INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            occurred_at TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'life_action'
        );
CREATE TABLE IF NOT EXISTS fitness_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id TEXT NOT NULL UNIQUE,
            date TEXT NOT NULL DEFAULT '',
            activity TEXT NOT NULL DEFAULT '',
            duration_minutes INTEGER NOT NULL DEFAULT 0,
            intensity INTEGER NOT NULL DEFAULT 1,
            load_score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed',
            source TEXT NOT NULL DEFAULT 'life_action',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            occurred_at TEXT NOT NULL DEFAULT ''
        );
CREATE TABLE IF NOT EXISTS conversation_action_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commitment_id INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            owner TEXT NOT NULL DEFAULT '',
            due_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            source_session TEXT NOT NULL DEFAULT '',
            source_message TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(commitment_id)
        );
"""


DOMAIN_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_activity_sessions_recent ON activity_sessions(date DESC, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_activity_sessions_status ON activity_sessions(status, last_heartbeat_at DESC);
CREATE INDEX IF NOT EXISTS idx_route_cache_expiry ON route_cache(expires_at, origin_name, destination_name);
CREATE INDEX IF NOT EXISTS idx_pantry_movements_recent ON pantry_movements(occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_meal_records_recent ON meal_records(date DESC, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_chores_due ON chores(enabled, next_due_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chore_records_recent ON chore_records(occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_fitness_records_recent ON fitness_records(date DESC, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_action_items_status ON conversation_action_items(status, due_at, id DESC);
"""


__all__ = ["DOMAIN_INDEX_SQL", "DOMAIN_SQL"]
