CONVERSATION_SQL = """
CREATE TABLE IF NOT EXISTS chat_memory_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    sender_profile_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    is_group INTEGER NOT NULL DEFAULT 0,
    is_directed INTEGER NOT NULL DEFAULT 0,
    is_quoted INTEGER NOT NULL DEFAULT 0,
    message_text TEXT NOT NULL,
    message_facts TEXT NOT NULL DEFAULT '',
    quote_context TEXT NOT NULL DEFAULT '',
    structured_context TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_memory_sessions (
    session_id TEXT PRIMARY KEY,
    is_group INTEGER NOT NULL DEFAULT 0,
    last_processed_row_id INTEGER NOT NULL DEFAULT 0,
    pending_since TEXT NOT NULL DEFAULT '',
    last_message_at TEXT NOT NULL DEFAULT '',
    last_completed_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_memory_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_key TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    first_row_id INTEGER NOT NULL,
    last_row_id INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    attempt_count INTEGER NOT NULL DEFAULT 1,
    summary_id INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_chat_memory_messages_session_row
    ON chat_memory_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_chat_memory_batches_session_status
    ON chat_memory_batches(session_id, status, id);
"""
