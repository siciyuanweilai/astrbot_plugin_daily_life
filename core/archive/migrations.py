from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping

from .tables.cognition import COGNITION_INDEX_SQL, COGNITION_SQL
from .tables.domains import DOMAIN_INDEX_SQL, DOMAIN_SQL

SCHEMA_VERSION_KEY = "schema_version"
BASELINE_SCHEMA_VERSION = 1
SCHEMA_VERSION = 11
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
PREVIOUS_V6_SCHEMA_FINGERPRINT = (
    "d23b0eb16fa2075c6dbf92a6b277e2101dc3e7607cf6ef53073cd61d2e8f653a"
)
PREVIOUS_V8_SCHEMA_FINGERPRINT = (
    "62d201bcf9bc94f896bc1a30c014c18c0dac6ec11c11adfedf8e09cba429f140"
)
PREVIOUS_V9_SCHEMA_FINGERPRINT = (
    "5648fde30660641f6ef5582ac778a1449b489923673d9e4f655aa76e1b88dbbd"
)
PREVIOUS_V10_SCHEMA_FINGERPRINT = (
    "188abaade1aace99b29cae4322db76a02dd738f77a284fea50e480ead88081f3"
)
CURRENT_SCHEMA_FINGERPRINT = (
    "a1cf402aa1ee09e7ee070240284ad4ffaef9cdadfce4f80acc0453720a026400"
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
    """保留历史兼容列，不再对动作裁定分类或回填。"""

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
            conn.execute(f"ALTER TABLE action_decisions ADD COLUMN {name} {definition}")


def _migrate_day_revisions(conn: sqlite3.Connection) -> None:
    """为每日生活聚合增加乐观并发版本号。"""

    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(days)").fetchall()
    }
    if "revision" not in columns:
        conn.execute("ALTER TABLE days ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")


def _migrate_activity_session_status_semantics(conn: sqlite3.Connection) -> None:
    """恢复旧版活动会话中被合并的不同终态。

    Args:
        conn: 当前正在迁移的 SQLite 连接。
    """

    rows = conn.execute(
        """
        SELECT id, action_id, date, status, metadata_json
        FROM activity_sessions
        WHERE source = 'daily_plan' AND status = 'failed'
        """
    ).fetchall()
    for row in rows:
        session_id = int(row[0])
        action_id = str(row[1] or "").strip()
        date_text = str(row[2] or "").strip()
        corrected_status = ""

        receipt = conn.execute(
            """
            SELECT status
            FROM life_action_receipts
            WHERE action_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (action_id,),
        ).fetchone()
        if receipt is not None:
            corrected_status = {
                "confirmed": "completed",
                "simulated": "completed",
                "failed": "failed",
                "cancelled": "cancelled",
            }.get(str(receipt[0] or "").strip().lower(), "")

        if not corrected_status:
            outcome = conn.execute(
                """
                SELECT status
                FROM life_action_outcomes
                WHERE action_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (action_id,),
            ).fetchone()
            if outcome is not None:
                corrected_status = {
                    "committed": "completed",
                    "confirmed": "completed",
                    "simulated": "completed",
                    "failed": "failed",
                    "cancelled": "cancelled",
                    "expired": "expired",
                }.get(str(outcome[0] or "").strip().lower(), "")

        if not corrected_status:
            try:
                metadata = json.loads(str(row[4] or "{}"))
                timeline_index = int(metadata.get("timeline_index"))
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                timeline_index = -1
            if timeline_index >= 0:
                timeline = conn.execute(
                    """
                    SELECT execution_state
                    FROM timelines
                    WHERE date = ? AND sort_order = ?
                    LIMIT 1
                    """,
                    (date_text, timeline_index),
                ).fetchone()
                if timeline is not None:
                    corrected_status = {
                        "completed": "completed",
                        "expired": "expired",
                        "skipped": "skipped",
                        "cancelled": "cancelled",
                    }.get(str(timeline[0] or "").strip().lower(), "")

        if corrected_status and corrected_status != str(row[3] or "").strip():
            conn.execute(
                "UPDATE activity_sessions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (corrected_status, session_id),
            )


def _migrate_commitment_source_message_id(conn: sqlite3.Connection) -> None:
    """为承诺补充稳定的来源消息标识。"""

    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(commitments)").fetchall()
    }
    if "source_message_id" not in columns:
        conn.execute(
            "ALTER TABLE commitments "
            "ADD COLUMN source_message_id TEXT NOT NULL DEFAULT ''"
        )


def _migrate_timeline_location_facts(conn: sqlite3.Connection) -> None:
    """为时间轴补齐地点、坐标和交通事实，避免刷新后丢失当前活动状态。"""

    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(timelines)").fetchall()
    }
    additions = {
        "place": "TEXT NOT NULL DEFAULT ''",
        "place_kind": "TEXT NOT NULL DEFAULT 'none'",
        "place_scope": "TEXT NOT NULL DEFAULT 'local'",
        "place_city": "TEXT NOT NULL DEFAULT ''",
        "place_hint": "TEXT NOT NULL DEFAULT ''",
        "travel_mode": "TEXT NOT NULL DEFAULT ''",
        "place_address": "TEXT NOT NULL DEFAULT ''",
        "place_latitude": "REAL",
        "place_longitude": "REAL",
        "place_coordinate_source": "TEXT NOT NULL DEFAULT ''",
        "travel_origin": "TEXT NOT NULL DEFAULT ''",
        "travel_provider": "TEXT NOT NULL DEFAULT ''",
        "travel_minutes": "INTEGER NOT NULL DEFAULT 0",
        "travel_distance_meters": "REAL NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE timelines ADD COLUMN {name} {definition}")


def _migrate_travel_detail(conn: sqlite3.Connection) -> None:
    """保存地图返回的公交、地铁或混合换乘摘要。"""

    targets = {
        "timelines": "TEXT NOT NULL DEFAULT ''",
        "route_cache": "TEXT NOT NULL DEFAULT ''",
    }
    for table, definition in targets.items():
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "travel_detail" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN travel_detail {definition}")


# 键是迁移完成后的目标版本；每个步骤只负责从前一版本升级一次。
MIGRATIONS: dict[int, MigrationStep] = {
    2: _migrate_timeline_execution_state,
    3: _migrate_cognition_runtime,
    4: _migrate_action_receipts,
    5: _migrate_life_domains,
    6: _migrate_action_decision_dimensions,
    7: _migrate_day_revisions,
    8: _migrate_activity_session_status_semantics,
    9: _migrate_commitment_source_message_id,
    10: _migrate_timeline_location_facts,
    11: _migrate_travel_detail,
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
        PREVIOUS_V6_SCHEMA_FINGERPRINT,
        PREVIOUS_V8_SCHEMA_FINGERPRINT,
        PREVIOUS_V9_SCHEMA_FINGERPRINT,
        PREVIOUS_V10_SCHEMA_FINGERPRINT,
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
    "PREVIOUS_V6_SCHEMA_FINGERPRINT",
    "PREVIOUS_V8_SCHEMA_FINGERPRINT",
    "PREVIOUS_V9_SCHEMA_FINGERPRINT",
    "PREVIOUS_V10_SCHEMA_FINGERPRINT",
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
