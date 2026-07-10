ARCHIVE_VERSION = 2

CORE_SQL = """
-- 核心元数据
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
"""
