"""v32 的固定 DDL；后续结构演进另加迁移，不改写已发布定义。"""

MEMORY_LIFE_V32_SQL = """
CREATE TABLE IF NOT EXISTS memory_identity_redirects (
    entity_id INTEGER PRIMARY KEY REFERENCES memory_entities(id) ON DELETE CASCADE,
    canonical_id INTEGER NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_associations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id INTEGER NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
    target_entity_id INTEGER NOT NULL REFERENCES memory_entities(id) ON DELETE CASCADE,
    memory_id INTEGER NOT NULL REFERENCES long_term_memories(id) ON DELETE CASCADE,
    source_revision TEXT NOT NULL,
    relation TEXT NOT NULL,
    occurred_at TEXT NOT NULL DEFAULT '',
    UNIQUE(source_entity_id, target_entity_id, memory_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_memory_associations_source ON memory_associations(memory_id);
CREATE TABLE IF NOT EXISTS memory_enrichments (
    memory_id INTEGER PRIMARY KEY REFERENCES long_term_memories(id) ON DELETE CASCADE,
    source_revision TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    level TEXT NOT NULL,
    period TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    children_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    UNIQUE(scope, level, period)
);
CREATE TABLE IF NOT EXISTS conversation_originals (
    session_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    role TEXT NOT NULL,
    sender_name TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    message_text TEXT NOT NULL,
    PRIMARY KEY(session_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_conversation_originals_time ON conversation_originals(session_id, occurred_at);
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    title TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',
    created_at TEXT NOT NULL,
    UNIQUE(scope, content_hash)
);
CREATE TABLE IF NOT EXISTS knowledge_passages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(source_id, ordinal)
);
CREATE TABLE IF NOT EXISTS knowledge_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passage_id INTEGER NOT NULL REFERENCES knowledge_passages(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    reflection TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    evidence_quote TEXT NOT NULL,
    action_id TEXT NOT NULL DEFAULT '',
    previous_reading_id INTEGER REFERENCES knowledge_readings(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_readings_passage ON knowledge_readings(passage_id, id);
"""
