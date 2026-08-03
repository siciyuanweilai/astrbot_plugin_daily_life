from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping

from ..decisions import normalize_action_decision_dimensions
from .tables.cognition import COGNITION_INDEX_SQL, COGNITION_SQL
from .tables.domains import DOMAIN_INDEX_SQL, DOMAIN_SQL

SCHEMA_VERSION_KEY = "schema_version"
BASELINE_SCHEMA_VERSION = 1
SCHEMA_VERSION = 6
LEGACY_BASELINE_SCHEMA_FINGERPRINT = (
    "9e6243276bf6bd509f6019502e30192310da4197838bd0f7d478f0100f8750a5"
)
BASELINE_SCHEMA_FINGERPRINT = (
    "c4f6c1b47523c4e78f70887f457be7787381efe781862ab628983b255977d485"
)
PREVIOUS_BASELINE_SCHEMA_FINGERPRINT = (
    "993af376991a7d179ccbc4c22d796d9beb2f18c2238a461e973a8596829749c0"
)
PREVIOUS_CURRENT_SCHEMA_FINGERPRINT = (
    "03d44d9dd88b6c381a60f6c72e41fadfd9dbd0edc3239f05ab9fe1653ff91e03"
)
PREVIOUS_V5_SCHEMA_FINGERPRINT = (
    "909f7660043197c3fa12f66bb0eb58d323945f9eefc2e1f3bc68eb39db6b2cc9"
)
CURRENT_SCHEMA_FINGERPRINT = (
    "d23b0eb16fa2075c6dbf92a6b277e2101dc3e7607cf6ef53073cd61d2e8f653a"
)

MigrationStep = Callable[[sqlite3.Connection], None]


def _migrate_timeline_execution_state(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(timelines)").fetchall()
    }
    additions = {
        "execution_state": "TEXT NOT NULL DEFAULT 'planned'",
        "execution_reason": "TEXT NOT NULL DEFAULT ''",
        "execution_evidence": "TEXT NOT NULL DEFAULT ''",
        "execution_updated_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE timelines ADD COLUMN {name} {definition}")


def _migrate_cognition_runtime(conn: sqlite3.Connection) -> None:
    """创建时间化认知和可恢复执行所需的数据表。

    Args:
        conn: 正在迁移的 SQLite 连接。
    """

    emotion_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(emotion_arcs)").fetchall()
    }
    emotion_additions = {
        "layer": "TEXT NOT NULL DEFAULT 'transient'",
        "baseline": "REAL NOT NULL DEFAULT 50",
        "half_life_minutes": "REAL NOT NULL DEFAULT 240",
        "last_decay_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in emotion_additions.items():
        if name not in emotion_columns:
            conn.execute(f"ALTER TABLE emotion_arcs ADD COLUMN {name} {definition}")

    for script in (COGNITION_SQL, COGNITION_INDEX_SQL):
        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                buffer = ""
                if statement:
                    conn.execute(statement)
        if buffer.strip():
            raise ValueError("认知数据表迁移脚本存在不完整语句")


def _migrate_action_receipts(conn: sqlite3.Connection) -> None:
    """创建动作执行回执表和索引。

    Args:
        conn: 正在迁移的 SQLite 连接。
    """

    for script in (COGNITION_SQL, COGNITION_INDEX_SQL):
        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                buffer = ""
                if statement:
                    conn.execute(statement)
        if buffer.strip():
            raise ValueError("动作回执迁移脚本存在不完整语句")


def _migrate_life_domains(conn: sqlite3.Connection) -> None:
    """创建生活领域表，并为地点档案补充可选坐标。"""

    place_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(places)").fetchall()
    }
    additions = {
        "latitude": "REAL",
        "longitude": "REAL",
        "coordinate_source": "TEXT NOT NULL DEFAULT ''",
        "coordinate_updated_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in place_columns:
            conn.execute(f"ALTER TABLE places ADD COLUMN {name} {definition}")

    for script in (DOMAIN_SQL, DOMAIN_INDEX_SQL):
        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                buffer = ""
                if statement:
                    conn.execute(statement)
        if buffer.strip():
            raise ValueError("生活领域迁移脚本存在不完整语句")


def _migrate_action_decision_dimensions(conn: sqlite3.Connection) -> None:
    """为动作裁定增加稳定的类别、来源、阶段和结果字段。"""

    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(action_decisions)").fetchall()
    }
    additions = {
        "decision_category": "TEXT NOT NULL DEFAULT ''",
        "decision_source": "TEXT NOT NULL DEFAULT ''",
        "decision_stage": "TEXT NOT NULL DEFAULT ''",
        "decision_outcome": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE action_decisions ADD COLUMN {name} {definition}"
            )

    rows = conn.execute(
        "SELECT id, action, scene_type, decision_category, decision_source, "
        "decision_stage, decision_outcome FROM action_decisions"
    ).fetchall()
    for row in rows:
        dimensions = normalize_action_decision_dimensions(
            action=row[1],
            scene_type=row[2],
            category=row[3],
            source=row[4],
            stage=row[5],
            outcome=row[6],
        )
        conn.execute(
            "UPDATE action_decisions SET decision_category = ?, decision_source = ?, "
            "decision_stage = ?, decision_outcome = ? WHERE id = ?",
            (
                dimensions["decision_category"],
                dimensions["decision_source"],
                dimensions["decision_stage"],
                dimensions["decision_outcome"],
                row[0],
            ),
        )


# 键是迁移完成后的目标版本；每个步骤只负责从前一版本升级一次。
MIGRATIONS: dict[int, MigrationStep] = {
    2: _migrate_timeline_execution_state,
    3: _migrate_cognition_runtime,
    4: _migrate_action_receipts,
    5: _migrate_life_domains,
    6: _migrate_action_decision_dimensions,
}


class SchemaMigrationError(RuntimeError):
    pass


def schema_fingerprint(conn: sqlite3.Connection) -> str:
    tables: list[list[object]] = []
    table_rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for row in table_rows:
        name = str(row[0])
        escaped = name.replace('"', '""')
        columns = [
            str(column[1])
            for column in conn.execute(f'PRAGMA table_info("{escaped}")').fetchall()
        ]
        tables.append([name, columns])
    indexes = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    payload = json.dumps(
        {"tables": tables, "indexes": indexes},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_baseline_schema(conn: sqlite3.Connection) -> bool:
    return schema_fingerprint(conn) in {
        BASELINE_SCHEMA_FINGERPRINT,
        PREVIOUS_BASELINE_SCHEMA_FINGERPRINT,
        PREVIOUS_CURRENT_SCHEMA_FINGERPRINT,
        PREVIOUS_V5_SCHEMA_FINGERPRINT,
        LEGACY_BASELINE_SCHEMA_FINGERPRINT,
        CURRENT_SCHEMA_FINGERPRINT,
    }


def read_schema_version(conn: sqlite3.Connection) -> int | None:
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        if not table:
            return None
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
        ).fetchone()
    except sqlite3.Error as exc:
        raise SchemaMigrationError(f"无法读取数据库结构版本：{exc}") from exc
    if row is None:
        return None
    raw = str(row[0] or "").strip()
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise SchemaMigrationError(f"数据库结构版本无效：{raw or '空值'}") from exc
    if version < BASELINE_SCHEMA_VERSION:
        raise SchemaMigrationError(
            f"数据库结构版本 {version} 早于迁移基线 {BASELINE_SCHEMA_VERSION}"
        )
    return version


def write_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION_KEY, str(int(version))),
    )


def validate_migration_registry(
    *,
    target_version: int = SCHEMA_VERSION,
    migrations: Mapping[int, MigrationStep] = MIGRATIONS,
) -> None:
    expected = set(range(BASELINE_SCHEMA_VERSION + 1, target_version + 1))
    actual = {int(version) for version in migrations}
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        first = missing[0]
        raise SchemaMigrationError(f"缺少数据库迁移步骤：{first - 1} -> {first}")
    if unexpected:
        raise SchemaMigrationError(
            "数据库迁移表包含超出当前版本的步骤："
            + ", ".join(str(version) for version in unexpected)
        )


def apply_migrations(
    conn: sqlite3.Connection,
    current_version: int,
    *,
    target_version: int = SCHEMA_VERSION,
    migrations: Mapping[int, MigrationStep] = MIGRATIONS,
) -> None:
    if current_version > target_version:
        raise SchemaMigrationError(
            f"数据库结构版本 {current_version} 高于当前支持版本 {target_version}"
        )
    for next_version in range(current_version + 1, target_version + 1):
        migration = migrations.get(next_version)
        if migration is None:
            raise SchemaMigrationError(
                f"缺少数据库迁移步骤：{next_version - 1} -> {next_version}"
            )
        try:
            migration(conn)
        except Exception as exc:
            raise SchemaMigrationError(
                f"数据库迁移失败：{next_version - 1} -> {next_version}：{exc}"
            ) from exc
        write_schema_version(conn, next_version)


__all__ = [
    "BASELINE_SCHEMA_FINGERPRINT",
    "CURRENT_SCHEMA_FINGERPRINT",
    "BASELINE_SCHEMA_VERSION",
    "MIGRATIONS",
    "PREVIOUS_BASELINE_SCHEMA_FINGERPRINT",
    "PREVIOUS_V5_SCHEMA_FINGERPRINT",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "MigrationStep",
    "SchemaMigrationError",
    "apply_migrations",
    "is_baseline_schema",
    "read_schema_version",
    "schema_fingerprint",
    "validate_migration_registry",
    "write_schema_version",
]
