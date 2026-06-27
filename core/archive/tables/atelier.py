WORKSHOP_SQL = """
-- 工坊自定义素材
CREATE TABLE IF NOT EXISTS custom_catalog_items (
            category TEXT NOT NULL,
            item_id TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'custom',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(category, item_id)
        );
CREATE TABLE IF NOT EXISTS custom_hair_styles (
            style_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'custom',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE IF NOT EXISTS custom_hair_options (
            style_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            hairstyle TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(style_id, sort_order),
            FOREIGN KEY(style_id) REFERENCES custom_hair_styles(style_id) ON DELETE CASCADE
        );
CREATE TABLE IF NOT EXISTS builtin_item_states (
            kind TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            item_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(kind, scope, item_id)
        );
"""
