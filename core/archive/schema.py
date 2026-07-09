import sqlite3

from .ddl import ARCHIVE_VERSION, DROP_SCHEMA_SQL, iter_schema_sql


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _current_archive_version(conn: sqlite3.Connection, tables: set[str]) -> str:
    if "meta" not in tables:
        return ""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'archive_version'"
    ).fetchone()
    return str(row["value"] if row else "")


def init_schema(conn: sqlite3.Connection) -> None:
    tables = _existing_tables(conn)
    if tables and _current_archive_version(conn, tables) != str(ARCHIVE_VERSION):
        drop_schema(conn)

    for script in iter_schema_sql():
        conn.executescript(script)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("archive_version", str(ARCHIVE_VERSION)),
    )
    conn.commit()


def drop_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DROP_SCHEMA_SQL)
    conn.commit()
