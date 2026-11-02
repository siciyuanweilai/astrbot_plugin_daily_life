"""根据已有消息身份修复旧关系名称，不解释名称的语义。"""
from __future__ import annotations

import sqlite3


def restore_observed_relationship_names(conn: sqlite3.Connection) -> int:
    marker = "relationship_observed_names_v1"
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        if conn.execute("SELECT 1 FROM meta WHERE key=?", (marker,)).fetchone():
            return 0
        rows = conn.execute("""
            SELECT * FROM (
                SELECT r.id, m.sender_name, ROW_NUMBER() OVER (
                    PARTITION BY r.id ORDER BY m.occurred_at DESC, m.id DESC
                ) AS position
                FROM relationships r JOIN chat_memory_messages m
                  ON m.sender_profile_id=r.id AND m.user_id=r.user_id AND m.platform=r.platform
                WHERE m.role='user' AND m.is_quoted=0 AND m.sender_name<>''
                  AND m.sender_name<>m.user_id AND r.user_id<>'' AND r.platform<>''
                  AND m.platform<>'bilibili'
            ) WHERE position=1
        """).fetchall()
        count = 0
        for row in rows:
            count += conn.execute(
                "UPDATE relationships SET name=?,alias=? WHERE id=? AND (name<>? OR alias<>?)",
                (row["sender_name"], row["sender_name"], row["id"], row["sender_name"], row["sender_name"]),
            ).rowcount
        conn.execute("INSERT INTO meta(key,value) VALUES(?,?)", (marker, str(count)))
    return count
