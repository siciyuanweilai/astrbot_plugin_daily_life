import sqlite3
from functools import lru_cache

from .categories import validate_storage_categories
from .ddl import DROP_SCHEMA_SQL, iter_schema_sql
from .migrations import (
    MIGRATIONS,
    SCHEMA_VERSION,
    SchemaMigrationError,
    apply_migrations,
    infer_schema_version,
    read_schema_version,
    validate_migration_registry,
    write_schema_version,
)


class ArchiveSchemaError(RuntimeError):
    """数据库结构无法验证或升级到当前版本。"""


def _schema_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _schema_columns(conn: sqlite3.Connection, table: str) -> frozenset[str]:
    escaped = table.replace('"', '""')
    return frozenset(
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    )


def _schema_indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


@lru_cache(maxsize=1)
def _expected_schema_contract() -> tuple[
    dict[str, frozenset[str]], frozenset[str], frozenset[str]
]:
    expected = sqlite3.connect(":memory:")
    try:
        for script in iter_schema_sql():
            expected.executescript(script)
        tables = {
            table: _schema_columns(expected, table)
            for table in _schema_tables(expected)
        }
        indexes = frozenset(_schema_indexes(expected))
        storage_tables = frozenset(
            str(row[1])
            for row in expected.execute("PRAGMA table_list").fetchall()
            if str(row[0]) == "main"
            and str(row[2]) == "table"
            and not str(row[1]).startswith("sqlite_")
        )
        return tables, indexes, storage_tables
    finally:
        expected.close()


def validate_schema(conn: sqlite3.Connection) -> None:
    expected_tables, expected_indexes, _ = _expected_schema_contract()
    actual_tables = _schema_tables(conn)
    missing_tables = sorted(set(expected_tables) - actual_tables)
    missing_columns = sorted(
        f"{table}.{column}"
        for table, columns in expected_tables.items()
        if table in actual_tables
        for column in columns - _schema_columns(conn, table)
    )
    missing_indexes = sorted(set(expected_indexes) - _schema_indexes(conn))
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

    problems: list[str] = []
    if missing_tables:
        problems.append("缺少数据表：" + ", ".join(missing_tables))
    if missing_columns:
        problems.append("缺少字段：" + ", ".join(missing_columns))
    if missing_indexes:
        problems.append("缺少索引：" + ", ".join(missing_indexes))
    if foreign_key_errors:
        problems.append(f"外键检查失败：{len(foreign_key_errors)} 项")
    if not problems:
        return

    raise ArchiveSchemaError(
        "数据库结构不符合当前定义（" + "；".join(problems) + "）。数据库没有被修改。"
    )


def _validate_storage_category_contract() -> None:
    _, _, storage_tables = _expected_schema_contract()
    try:
        validate_storage_categories(set(storage_tables), ignored_tables={"meta"})
    except ValueError as exc:
        raise ArchiveSchemaError(str(exc)) from exc


def _iter_sql_statements(script: str):
    buffer = ""
    for line in str(script or "").splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                yield statement
    if buffer.strip():
        raise ArchiveSchemaError("数据库建表脚本存在不完整语句")


def _apply_current_schema(conn: sqlite3.Connection) -> None:
    for script in iter_schema_sql():
        for statement in _iter_sql_statements(script):
            conn.execute(statement)


def _create_fresh_schema(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        _apply_current_schema(conn)
        validate_schema(conn)
        write_schema_version(conn, SCHEMA_VERSION)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _upgrade_unversioned_baseline(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        inferred_version = infer_schema_version(conn)
        if inferred_version is None:
            validate_schema(conn)
            raise SchemaMigrationError("数据库未记录结构版本，且结构不符合当前迁移基线")
        write_schema_version(conn, inferred_version)
        if inferred_version < SCHEMA_VERSION:
            apply_migrations(
                conn,
                inferred_version,
                target_version=SCHEMA_VERSION,
                migrations=MIGRATIONS,
            )
        validate_schema(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _upgrade_schema(conn: sqlite3.Connection, current_version: int) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        apply_migrations(
            conn,
            current_version,
            target_version=SCHEMA_VERSION,
            migrations=MIGRATIONS,
        )
        validate_schema(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_schema(conn: sqlite3.Connection) -> None:
    try:
        validate_migration_registry(
            target_version=SCHEMA_VERSION,
            migrations=MIGRATIONS,
        )
    except SchemaMigrationError as exc:
        raise ArchiveSchemaError(str(exc)) from exc
    _validate_storage_category_contract()
    if not _schema_tables(conn):
        _create_fresh_schema(conn)
        return None
    try:
        version = read_schema_version(conn)
        if version is None:
            _upgrade_unversioned_baseline(conn)
        elif version < SCHEMA_VERSION:
            _upgrade_schema(conn, version)
        elif version > SCHEMA_VERSION:
            raise SchemaMigrationError(
                f"数据库结构版本 {version} 高于当前支持版本 {SCHEMA_VERSION}"
            )
        else:
            validate_schema(conn)
    except SchemaMigrationError as exc:
        raise ArchiveSchemaError(str(exc)) from exc


def drop_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DROP_SCHEMA_SQL)
    conn.commit()


__all__ = [
    "ArchiveSchemaError",
    "SCHEMA_VERSION",
    "drop_schema",
    "init_schema",
    "validate_schema",
]
