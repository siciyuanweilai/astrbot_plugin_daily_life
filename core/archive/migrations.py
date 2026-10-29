from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping

from ..clock import today as life_today
from .tables.living import DOMAIN_INDEX_SQL, DOMAIN_SQL
from .tables.mind import COGNITION_INDEX_SQL, COGNITION_SQL

SCHEMA_VERSION_KEY = "schema_version"
BASELINE_SCHEMA_VERSION = 1
SCHEMA_VERSION = 32
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
PREVIOUS_V11_SCHEMA_FINGERPRINT = (
    "a1cf402aa1ee09e7ee070240284ad4ffaef9cdadfce4f80acc0453720a026400"
)
PREVIOUS_V12_SCHEMA_FINGERPRINT = (
    "c4f031632aeaf20c37c3a8f36bbd11032075ab933215c0051e06b5b38a252074"
)
PREVIOUS_V14_SCHEMA_FINGERPRINT = (
    "94726281d21d652e331f3310e808ffb1829eead45cdf0504ceef076042293dd4"
)
PREVIOUS_V15_SCHEMA_FINGERPRINT = (
    "d4ae34f6ec613ffacf13dcac7a86a5f419a6f7cbea632ebef45b69fcc4eda498"
)
PREVIOUS_V16_SCHEMA_FINGERPRINT = (
    "b6ec5c00a0b6eff3e54503eb390c3a39f740c2a602d934cc29188730d1f204fe"
)
PREVIOUS_V17_SCHEMA_FINGERPRINT = (
    "2fa6357aa4589b6c1c7322977140312408994ef67d41804a6ab65fac00ca01df"
)
PREVIOUS_V18_SCHEMA_FINGERPRINT = (
    "6fc07333a7aea0ba77a5c8b0fd315bdeee6bbd8b9334df9a8be3dc5d254a7075"
)
PREVIOUS_V19_SCHEMA_FINGERPRINT = "5c85572e593924bac14c74f5e9de4fb3e30f07966678db9301b274b209dda20b"
PREVIOUS_V20_SCHEMA_FINGERPRINT = "43e2b090847b932c95ad312a19ad66a6c109b7283b2959c5dc2e4fc55e61c180"

PREVIOUS_V21_SCHEMA_FINGERPRINT = "1dcab14830e01b324d3bac191506af09e1fc2fc092effb4ab6eb04e4405d4cdf"
PREVIOUS_V22_SCHEMA_FINGERPRINT = "b8f268ce4aba19b956f1a14e4a9b567227a6d011390d62c6ef8a8c2ea716e066"
PREVIOUS_V26_SCHEMA_FINGERPRINT = (
    "a05e55bdfdab44033c105d6c2bb560da7e2beeb50721344f8193815c6ead4ed7"
)
PREVIOUS_V27_SCHEMA_FINGERPRINT = (
    "4cc377da1c900fc3f7c36666665b888512c6203a61b9076d1c9684df7c8c30f2"
)
PREVIOUS_V28_SCHEMA_FINGERPRINT = (
    "6431c08ea940c9eb9c175f009b6cf52d2e9e90282aba0ae643f2fae4ffa98682"
)
PREVIOUS_V29_SCHEMA_FINGERPRINT = "48ee0b885783bac1e045b015f50491ac8d2eb64011580691840be0b83519aee7"

MigrationStep = Callable[[sqlite3.Connection], None]

# 已发布迁移必须使用当时的固定 DDL，不能引用会随当前版本变化的建表常量。
STYLE_CATALOG_V12_SQL = """
CREATE TABLE IF NOT EXISTS style_catalog_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN ('outfit', 'hair')),
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            image_path TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_scope TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT 'user_image',
            source_image_hash TEXT NOT NULL,
            attributes_json TEXT NOT NULL DEFAULT '{}',
            confidence REAL NOT NULL DEFAULT 0,
            preference_score REAL NOT NULL DEFAULT 0,
            feedback_count INTEGER NOT NULL DEFAULT 0,
            seen_count INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            last_used_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_image_hash, kind)
        );
CREATE TABLE IF NOT EXISTS style_catalog_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '',
            sentiment TEXT NOT NULL DEFAULT 'neutral',
            score_delta REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(item_id) REFERENCES style_catalog_items(id) ON DELETE CASCADE
        );
"""

STYLE_CATALOG_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_style_catalog_active
ON style_catalog_items(kind, status, preference_score DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_style_catalog_scope_recent
ON style_catalog_items(source_scope, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_style_catalog_feedback_item
ON style_catalog_feedback(item_id, id DESC);
"""

# 指纹只覆盖表、字段和索引，无法区分仅修改 CHECK 约束的 v12/v13。
# 因此相同指纹选择最早的安全版本，再执行后续幂等迁移完成校准。
PREVIOUS_V30_SCHEMA_FINGERPRINT = "1fe1e4051077cf590c5f041544ea9f660c58bad83ff46759b90083d35eb9cbed"
PREVIOUS_V31_SCHEMA_FINGERPRINT = "c2f8770ba6987c5c89a0da4944a9a2fdbea69ea1be02334b24b844d16ec5b486"
CURRENT_SCHEMA_FINGERPRINT = "7518fd42746c412efc726df5fb035090127c5ed2e19ae1715650d0b88dfeee8a"

KNOWN_SCHEMA_VERSIONS: dict[str, int] = {
    LEGACY_BASELINE_SCHEMA_FINGERPRINT: 1,
    BASELINE_SCHEMA_FINGERPRINT: 1,
    PREVIOUS_BASELINE_SCHEMA_FINGERPRINT: 1,
    PREVIOUS_CURRENT_SCHEMA_FINGERPRINT: 4,
    PREVIOUS_V5_SCHEMA_FINGERPRINT: 5,
    PREVIOUS_V6_SCHEMA_FINGERPRINT: 6,
    PREVIOUS_V8_SCHEMA_FINGERPRINT: 7,
    PREVIOUS_V9_SCHEMA_FINGERPRINT: 9,
    PREVIOUS_V10_SCHEMA_FINGERPRINT: 10,
    PREVIOUS_V11_SCHEMA_FINGERPRINT: 11,
    # v13 旧结构没有聊天记忆重试字段，由版本迁移补齐。
    "86365a73b9bb947feab3191007402f5cf72cfed879215b9416d671c5a33a4eb2": 12,
    PREVIOUS_V12_SCHEMA_FINGERPRINT: 12,
    PREVIOUS_V14_SCHEMA_FINGERPRINT: 14,
    PREVIOUS_V15_SCHEMA_FINGERPRINT: 15,
    PREVIOUS_V16_SCHEMA_FINGERPRINT: 16,
    PREVIOUS_V17_SCHEMA_FINGERPRINT: 17,
    PREVIOUS_V18_SCHEMA_FINGERPRINT: 18,
    PREVIOUS_V19_SCHEMA_FINGERPRINT: 19,
    PREVIOUS_V20_SCHEMA_FINGERPRINT: 20,
    PREVIOUS_V21_SCHEMA_FINGERPRINT: 21,
    PREVIOUS_V22_SCHEMA_FINGERPRINT: 22,
    PREVIOUS_V26_SCHEMA_FINGERPRINT: 23,
    PREVIOUS_V27_SCHEMA_FINGERPRINT: 27,
    PREVIOUS_V28_SCHEMA_FINGERPRINT: 28,
    PREVIOUS_V29_SCHEMA_FINGERPRINT: 29,
    PREVIOUS_V30_SCHEMA_FINGERPRINT: 30,
    PREVIOUS_V31_SCHEMA_FINGERPRINT: 31,
    CURRENT_SCHEMA_FINGERPRINT: 32,
}


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


def _migrate_style_catalog(conn: sqlite3.Connection) -> None:
    """创建视觉衣橱候选、反馈和检索索引。"""

    for script in (STYLE_CATALOG_V12_SQL, STYLE_CATALOG_INDEX_SQL):
        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                buffer = ""
                if statement:
                    conn.execute(statement)
        if buffer.strip():
            raise ValueError("视觉衣橱迁移脚本存在不完整语句")


def _migrate_style_catalog_categories(conn: sqlite3.Connection) -> None:
    """扩展视觉衣橱类别，并完整保留候选编号与反馈记录。"""

    conn.execute("DROP TABLE IF EXISTS style_catalog_feedback_v13")
    conn.execute("DROP TABLE IF EXISTS style_catalog_items_v13")
    conn.execute(
        """
        CREATE TABLE style_catalog_items_v13 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN (
                'outfit', 'top', 'bottom', 'footwear',
                'accessory', 'hair', 'makeup', 'nails'
            )),
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            image_path TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_scope TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT 'user_image',
            source_image_hash TEXT NOT NULL,
            attributes_json TEXT NOT NULL DEFAULT '{}',
            confidence REAL NOT NULL DEFAULT 0,
            preference_score REAL NOT NULL DEFAULT 0,
            feedback_count INTEGER NOT NULL DEFAULT 0,
            seen_count INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            last_used_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_image_hash, kind)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO style_catalog_items_v13(
            id, kind, title, description, image_path, source_url, source_scope,
            source_kind, source_image_hash, attributes_json, confidence,
            preference_score, feedback_count, seen_count, status, last_used_at,
            created_at, updated_at
        )
        SELECT id, kind, title, description, image_path, source_url, source_scope,
               source_kind, source_image_hash, attributes_json, confidence,
               preference_score, feedback_count, seen_count, status, last_used_at,
               created_at, updated_at
        FROM style_catalog_items
        """
    )
    conn.execute(
        """
        CREATE TABLE style_catalog_feedback_v13 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '',
            sentiment TEXT NOT NULL DEFAULT 'neutral',
            score_delta REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(item_id) REFERENCES style_catalog_items_v13(id)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO style_catalog_feedback_v13(
            id, item_id, scope, feedback, sentiment, score_delta, reason, created_at
        )
        SELECT id, item_id, scope, feedback, sentiment, score_delta, reason, created_at
        FROM style_catalog_feedback
        """
    )
    conn.execute("DROP TABLE style_catalog_feedback")
    conn.execute("DROP TABLE style_catalog_items")
    conn.execute("ALTER TABLE style_catalog_items_v13 RENAME TO style_catalog_items")
    conn.execute(
        "ALTER TABLE style_catalog_feedback_v13 RENAME TO style_catalog_feedback"
    )
    buffer = ""
    for line in STYLE_CATALOG_INDEX_SQL.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                conn.execute(statement)
    if buffer.strip():
        raise ValueError("视觉衣橱索引迁移脚本存在不完整语句")


def _migrate_physiological_rhythm_burden_flag(conn: sqlite3.Connection) -> None:
    """为身体节律记录增加显式负荷标记，禁止从自然语言标签反推。"""

    columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(physiological_rhythm_logs)"
        ).fetchall()
    }
    if "body_burden_present" not in columns:
        conn.execute(
            "ALTER TABLE physiological_rhythm_logs "
            "ADD COLUMN body_burden_present INTEGER"
        )


def _migrate_commitment_media_contract(conn: sqlite3.Connection) -> None:
    """为媒体承诺保存明确的承担人和媒体类型，禁止运行时文本猜测。"""

    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(commitments)").fetchall()
    }
    additions = {
        "owner": "TEXT NOT NULL DEFAULT '未定'",
        "media_kind": "TEXT NOT NULL DEFAULT 'none'",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE commitments ADD COLUMN {name} {definition}")


def _migrate_focus_slot_progress(conn: sqlite3.Connection) -> None:
    """为短期目标增加可追踪的进度和终态，避免文本命中即降权。"""

    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(focus_slots)").fetchall()
    }
    additions = {
        "progress": "INTEGER NOT NULL DEFAULT 0",
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "last_evidence": "TEXT NOT NULL DEFAULT ''",
        "last_progress_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE focus_slots ADD COLUMN {name} {definition}")
    conn.execute("DROP INDEX IF EXISTS idx_focus_slots_active")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_focus_slots_active "
        "ON focus_slots(status, expires_at, priority DESC, updated_at DESC)"
    )


def _migrate_timeline_duration(conn: sqlite3.Connection) -> None:
    """保存活动持续时长，区分连续生活与未记录的时间空白。"""

    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(timelines)").fetchall()
    }
    if "duration_minutes" not in columns:
        conn.execute(
            "ALTER TABLE timelines "
            "ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_life_semantic_flags(conn: sqlite3.Connection) -> None:
    """保存模型判定的活动类型和天气风险，不扫描历史描述补猜。"""

    additions = {
        "timelines": ("activity_kind", "TEXT NOT NULL DEFAULT ''"),
        "days": ("weather_is_severe", "INTEGER NOT NULL DEFAULT 0"),
    }
    for table, (name, definition) in additions.items():
        columns = {
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate_timeline_day_offsets(conn: sqlite3.Connection) -> None:
    """根据已保存的生活时间窗口恢复跨午夜日期，并保留动作绑定。"""
    from ..models import (
        TimelineItem,
        normalize_timeline_day_offsets,
        timeline_item_minutes,
    )
    from .timeline import rebind_planned_actions

    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(timelines)")}
    if "day_offset" in columns:
        return
    conn.execute("ALTER TABLE timelines ADD COLUMN day_offset INTEGER NOT NULL DEFAULT 0")
    for (date_str,) in conn.execute("SELECT DISTINCT date FROM timelines").fetchall():
        rows = conn.execute(
            "SELECT sort_order, time, activity, place, place_kind, place_scope, "
            "execution_state, execution_updated_at FROM timelines WHERE date = ? ORDER BY sort_order",
            (date_str,),
        ).fetchall()
        items = [TimelineItem(time=r[1], activity=r[2], place=r[3], place_kind=r[4], place_scope=r[5]) for r in rows]
        meta = dict(conn.execute("SELECT key, value FROM day_meta WHERE date = ?", (date_str,)).fetchall())
        anchor = timeline_item_minutes({"time": meta.get("life_window_start")})
        if anchor is not None:
            for item in items:
                minutes = timeline_item_minutes(item)
                item.day_offset = int(minutes < anchor) if minutes is not None else 0
        else:
            normalize_timeline_day_offsets(items)
        ordered = sorted(zip(rows, items, strict=True), key=lambda pair: timeline_item_minutes(pair[1]) or 0)
        if not any(item.day_offset for item in items):
            continue
        # 临时使用负数位置，避免复合主键冲突。
        conn.execute("UPDATE timelines SET sort_order = -sort_order - 1 WHERE date = ?", (date_str,))
        for index, (row, item) in enumerate(ordered):
            conn.execute(
                "UPDATE timelines SET sort_order = ?, day_offset = ? WHERE date = ? AND sort_order = ?",
                (index, item.day_offset, date_str, -row[0] - 1),
            )
            try:
                observed = datetime.datetime.fromisoformat(row[7])
                planned_date = datetime.date.fromisoformat(date_str) + datetime.timedelta(days=item.day_offset or 0)
            except (TypeError, ValueError):
                continue
            if item.day_offset and row[6] in {"active", "elapsed"} and observed.date() < planned_date:
                conn.execute(
                    "UPDATE timelines SET execution_state = 'planned', execution_reason = '', "
                    "execution_evidence = '', execution_updated_at = '' WHERE date = ? AND sort_order = ?",
                    (date_str, index),
                )
        before = meta.get("planned_life_actions")
        rebind_planned_actions(meta, items, [item for _, item in ordered])
        if meta.get("planned_life_actions") != before:
            conn.execute("UPDATE day_meta SET value = ? WHERE date = ? AND key = 'planned_life_actions'", (meta["planned_life_actions"], date_str))
        conn.execute("UPDATE days SET revision = revision + 1 WHERE date = ?", (date_str,))


def _migrate_continuous_life_entries(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS continuous_life_entries (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, date TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'global', occurred_at TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_continuous_life_entries_date ON continuous_life_entries(date, occurred_at DESC)")


TEXTILE_V21_SQL = """
CREATE TABLE IF NOT EXISTS wardrobe_units (
    unit_id TEXT PRIMARY KEY, ownership TEXT NOT NULL DEFAULT 'candidate',
    condition TEXT NOT NULL DEFAULT 'clean', acquired_at TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS wardrobe_links (
    catalog_id INTEGER NOT NULL REFERENCES style_catalog_items(id) ON DELETE CASCADE,
    unit_id TEXT NOT NULL REFERENCES wardrobe_units(unit_id) ON DELETE CASCADE,
    PRIMARY KEY(catalog_id, unit_id)
);
CREATE TABLE IF NOT EXISTS wardrobe_events (
    event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, occurred_at TEXT NOT NULL,
    source TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS wardrobe_profile (
    name TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS wardrobe_jobs (
    job_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'planned',
    updated_at TEXT NOT NULL, next_at TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '', lease_until TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}', progress_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_wardrobe_jobs_pending ON wardrobe_jobs(status, next_at);
CREATE INDEX IF NOT EXISTS idx_wardrobe_events_time ON wardrobe_events(occurred_at DESC);
"""


def _migrate_wardrobe_life(conn: sqlite3.Connection) -> None:
    buffer = ""
    for line in TEXTILE_V21_SQL.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            conn.execute(buffer)
            buffer = ""


def _migrate_conversation_receipts(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_receipts (
            scope TEXT NOT NULL,
            sent_at REAL NOT NULL,
            PRIMARY KEY(scope, sent_at)
        )
    """)


def _migrate_living_memory_index(conn: sqlite3.Connection) -> None:
    """修复普通 FTS5 表的删除操作，并增加生活实体查询索引。"""
    conn.execute("DROP TRIGGER IF EXISTS long_term_memories_ad")
    conn.execute("DROP TRIGGER IF EXISTS long_term_memories_au")
    conn.execute("""CREATE TRIGGER long_term_memories_ad AFTER DELETE ON long_term_memories
        BEGIN DELETE FROM long_term_memories_fts WHERE rowid=old.id; END""")
    conn.execute("""CREATE TRIGGER long_term_memories_au
        AFTER UPDATE OF title,content,category,status ON long_term_memories
        BEGIN
            DELETE FROM long_term_memories_fts WHERE rowid=old.id;
            INSERT INTO long_term_memories_fts(rowid,title,content,category)
            SELECT new.id,new.title,new.content,new.category WHERE new.status='active';
        END""")
    conn.execute("DELETE FROM long_term_memories_fts")
    conn.execute("""INSERT INTO long_term_memories_fts(rowid,title,content,category)
        SELECT id,title,content,category FROM long_term_memories WHERE status='active'""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_continuous_entities_scope
        ON continuous_life_entries(scope,kind,date,occurred_at DESC)""")


def _migrate_clock_only_active_timelines(conn: sqlite3.Connection) -> None:
    """撤销旧时钟写出的假执行态，保留有结构化自主行动记录的节点。"""
    active_rows = conn.execute(
        "SELECT date, sort_order FROM timelines WHERE execution_state = 'active'"
    ).fetchall()
    if not active_rows:
        return
    current_date = life_today().isoformat()
    changed_dates: set[str] = set()
    for date, index in active_rows:
        stored = conn.execute(
            "SELECT value FROM day_meta WHERE date = ? AND key = 'continuous_execution'",
            (date,),
        ).fetchone()
        try:
            run = json.loads(stored[0]) if stored else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            run = {}
        action = run.get("action") if isinstance(run, dict) else None
        if (
            isinstance(action, dict)
            and run.get("date") == date
            and run.get("status") in {"running", "paused", "ready", "settling"}
            and type(action.get("timeline_index")) is int
            and action["timeline_index"] == index
        ):
            continue
        target = "elapsed" if date < current_date else "planned"
        conn.execute(
            "UPDATE timelines SET execution_state = ?, execution_reason = ?, "
            "execution_evidence = '', execution_updated_at = '' "
            "WHERE date = ? AND sort_order = ?",
            (target, "计划时段已过，尚未收到执行证据" if target == "elapsed" else "尚未收到执行证据", date, index),
        )
        changed_dates.add(date)
        for session_id, metadata_json in conn.execute(
            "SELECT id, metadata_json FROM activity_sessions "
            "WHERE date = ? AND status = 'active'",
            (date,),
        ).fetchall():
            try:
                session_meta = json.loads(metadata_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(session_meta, dict) and session_meta.get("timeline_index") == index:
                conn.execute(
                    "UPDATE activity_sessions SET status = ?, evidence_json = '[]' WHERE id = ?",
                    (target, session_id),
                )
    for date in changed_dates:
        conn.execute("UPDATE days SET revision = revision + 1 WHERE date = ?", (date,))


def _migrate_food_preparation_status(conn: sqlite3.Connection) -> None:
    """把可核实的旧做饭、点餐记录还原为准备阶段，不猜测实际进食。"""
    conn.execute(
        """
        UPDATE meal_records
        SET status = (
            SELECT CASE action_type
                WHEN 'cook' THEN 'prepared'
                WHEN 'order_food' THEN 'ordered'
            END
            FROM life_action_outcomes
            WHERE life_action_outcomes.action_id = meal_records.action_id
              AND life_action_outcomes.status = 'committed'
        )
        WHERE status = 'completed'
          AND EXISTS (
            SELECT 1 FROM life_action_outcomes
            WHERE life_action_outcomes.action_id = meal_records.action_id
              AND life_action_outcomes.status = 'committed'
              AND life_action_outcomes.action_type IN ('cook', 'order_food')
          )
        """
    )


def _migrate_expression_feedback_mirrors(conn: sqlite3.Connection) -> None:
    """表达画像已有同一结论时，移除按每条来源消息重复生成的长期记忆。"""
    rows = conn.execute(
        """
        SELECT mirror.id, MAX(profile.id)
        FROM long_term_memories AS mirror
        JOIN memory_evidence AS evidence
          ON mirror.source_id = CAST(evidence.id AS TEXT)
         AND evidence.target_type = 'expression_profile'
         AND evidence.evidence_type = 'expression_feedback'
         AND mirror.content = evidence.summary
        JOIN long_term_memories AS profile
          ON profile.scope = mirror.scope
         AND profile.source_table = 'expression_profiles'
         AND profile.source_id = evidence.target_id
         AND profile.status = 'active'
         AND instr(profile.content, evidence.summary) > 0
        WHERE mirror.source_table = 'memory_evidence'
          AND mirror.category = 'evidence:expression_feedback'
          AND mirror.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM memory_conflicts AS conflict
              WHERE conflict.memory_id = mirror.id
                 OR conflict.related_memory_id = mirror.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM memory_corrections AS correction
              WHERE correction.target_id = CAST(mirror.id AS TEXT)
                AND correction.target_type IN ('long_term_memory', 'long_term_memories')
          )
        GROUP BY mirror.id
        """
    ).fetchall()
    if not rows:
        return
    cluster_ids = set()
    entity_ids = set()
    for mirror_id, profile_id in rows:
        memory_id = int(mirror_id)
        cluster_ids.update(
            int(row[0])
            for row in conn.execute(
                "SELECT cluster_id FROM memory_episode_cluster_items WHERE memory_id = ?",
                (memory_id,),
            )
        )
        entity_ids.update(
            int(row[0])
            for row in conn.execute(
                "SELECT entity_id FROM memory_entity_links WHERE memory_id = ?",
                (memory_id,),
            )
        )
        for decision_id, influence, weight, created_at in conn.execute(
            "SELECT decision_id, influence, weight, created_at "
            "FROM memory_decision_links WHERE memory_id = ?",
            (memory_id,),
        ):
            conn.execute(
                """
                INSERT INTO memory_decision_links(
                    decision_id, memory_id, influence, weight, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(decision_id, memory_id) DO UPDATE SET
                    weight = MAX(memory_decision_links.weight, excluded.weight)
                """,
                (decision_id, int(profile_id), influence, weight, created_at),
            )
        conn.execute(
            "DELETE FROM memory_vectors "
            "WHERE target_type IN ('long_term_memory', 'long_term_memories') "
            "AND target_id = ?",
            (str(memory_id),),
        )
        conn.execute("DELETE FROM long_term_memories WHERE id = ?", (memory_id,))
    for entity_id in entity_ids:
        conn.execute(
            "DELETE FROM memory_entities WHERE id = ? AND NOT EXISTS "
            "(SELECT 1 FROM memory_entity_links WHERE entity_id = ?)",
            (entity_id, entity_id),
        )
    for cluster_id in cluster_ids:
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_episode_cluster_items WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchone()[0]
        if count:
            conn.execute(
                "UPDATE memory_episode_clusters SET memory_count = ? WHERE id = ?",
                (count, cluster_id),
            )
        else:
            conn.execute(
                "DELETE FROM memory_episode_clusters WHERE id = ?", (cluster_id,)
            )


def _migrate_memory_recollection(conn: sqlite3.Connection) -> None:
    # 固定此版本的 DDL，避免后续模型调整改变历史迁移。
    statements = (
        "CREATE TABLE IF NOT EXISTS memory_source_messages ("
        "summary_id INTEGER NOT NULL, event_key TEXT NOT NULL, session_id TEXT NOT NULL, "
        "message_id TEXT NOT NULL DEFAULT '', role TEXT NOT NULL, "
        "sender_name TEXT NOT NULL DEFAULT '', occurred_at TEXT NOT NULL, "
        "message_text TEXT NOT NULL, PRIMARY KEY(summary_id, event_key), "
        "FOREIGN KEY(summary_id) REFERENCES chat_summaries(id) ON DELETE CASCADE)",
        "CREATE INDEX IF NOT EXISTS idx_memory_source_message "
        "ON memory_source_messages(session_id, message_id)",
        "CREATE TABLE IF NOT EXISTS memory_fact_links (memory_id INTEGER NOT NULL, "
        "fact_id INTEGER NOT NULL, PRIMARY KEY(memory_id, fact_id), "
        "FOREIGN KEY(memory_id) REFERENCES long_term_memories(id) ON DELETE CASCADE, "
        "FOREIGN KEY(fact_id) REFERENCES temporal_facts(id) ON DELETE CASCADE)",
        "CREATE INDEX IF NOT EXISTS idx_memory_fact_link_fact ON memory_fact_links(fact_id)",
    )
    for statement in statements:
        conn.execute(statement)


def _migrate_conversation_arrivals(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_arrivals (
            scope TEXT NOT NULL,
            received_at REAL NOT NULL,
            PRIMARY KEY(scope, received_at)
        )
    """)


def _migrate_commitment_follow_up(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(commitments)")}
    if "follow_up_json" not in columns:
        conn.execute("ALTER TABLE commitments ADD COLUMN follow_up_json TEXT NOT NULL DEFAULT '{}'")


def _migrate_current_storage_boundaries(conn: sqlite3.Connection) -> None:
    """统一版本边界中的历史修复，废弃数据原样归档后清理旧列。"""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(chat_memory_batches)")}
    for name in ("next_attempt_at", "failed_at"):
        if name not in columns:
            conn.execute(f"ALTER TABLE chat_memory_batches ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    from types import SimpleNamespace
    from .garments import GarmentArchiveComponent
    previous_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        GarmentArchiveComponent(SimpleNamespace(_conn=conn))._synchronize_wardrobe_unlocked(repair_legacy=True)
    finally:
        conn.row_factory = previous_row_factory
    retired = ("decision_category", "decision_source", "decision_stage", "decision_outcome")
    present = {str(row[1]) for row in conn.execute("PRAGMA table_info(action_decisions)")}
    fields = [name for name in retired if name in present]
    if fields:
        rows = conn.execute("SELECT id," + ",".join(fields) + " FROM action_decisions").fetchall()
        history = [dict(zip(("id", *fields), row)) for row in rows if any(row[1:])]
        if history:
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (
                "retired_action_decisions_v30", json.dumps(history, ensure_ascii=False),
            ))
        for name in fields:
            conn.execute(f"ALTER TABLE action_decisions DROP COLUMN {name}")


def _migrate_expression_evolution(conn: sqlite3.Connection) -> None:
    """保留已有档案，增加可追溯且可撤回的表达学习。"""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(expression_profiles)")}
    if "learning_json" not in columns:
        conn.execute("ALTER TABLE expression_profiles ADD COLUMN learning_json TEXT NOT NULL DEFAULT '{}'")
    conn.execute("""CREATE TABLE IF NOT EXISTS expression_learning_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES expression_profiles(id) ON DELETE CASCADE,
        event_key TEXT NOT NULL, kind TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'neutral',
        source_scope TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(profile_id, event_key)
    )""")


def _migrate_memory_life(conn: sqlite3.Connection) -> None:
    from .tables.lifeschema import MEMORY_LIFE_V32_SQL
    # 逐条执行以保留外层升级事务。
    for statement in MEMORY_LIFE_V32_SQL.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("INSERT OR IGNORE INTO conversation_originals SELECT session_id,event_key,role,sender_name,occurred_at,message_text FROM memory_source_messages")
    conn.execute("INSERT OR IGNORE INTO conversation_originals SELECT session_id,event_key,role,sender_name,occurred_at,message_text FROM chat_memory_messages WHERE role IN ('user','assistant') AND message_text<>''")


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
    12: _migrate_style_catalog,
    13: _migrate_style_catalog_categories,
    14: _migrate_physiological_rhythm_burden_flag,
    15: _migrate_commitment_media_contract,
    16: _migrate_focus_slot_progress,
    17: _migrate_timeline_duration,
    18: _migrate_life_semantic_flags,
    19: _migrate_timeline_day_offsets,
    20: _migrate_continuous_life_entries,
    21: _migrate_wardrobe_life,
    22: _migrate_conversation_receipts,
    23: _migrate_living_memory_index,
    24: _migrate_clock_only_active_timelines,
    25: _migrate_food_preparation_status,
    26: _migrate_expression_feedback_mirrors,
    27: _migrate_memory_recollection,
    28: _migrate_conversation_arrivals,
    29: _migrate_commitment_follow_up,
    30: _migrate_current_storage_boundaries,
    31: _migrate_expression_evolution,
    32: _migrate_memory_life,
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


def infer_schema_version(conn: sqlite3.Connection) -> int | None:
    """根据已发布结构指纹推断无版本数据库的最早安全版本。"""

    return KNOWN_SCHEMA_VERSIONS.get(schema_fingerprint(conn))


def is_baseline_schema(conn: sqlite3.Connection) -> bool:
    return infer_schema_version(conn) is not None


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
    "KNOWN_SCHEMA_VERSIONS",
    "BASELINE_SCHEMA_VERSION",
    "MIGRATIONS",
    "PREVIOUS_BASELINE_SCHEMA_FINGERPRINT",
    "PREVIOUS_V5_SCHEMA_FINGERPRINT",
    "PREVIOUS_V6_SCHEMA_FINGERPRINT",
    "PREVIOUS_V8_SCHEMA_FINGERPRINT",
    "PREVIOUS_V9_SCHEMA_FINGERPRINT",
    "PREVIOUS_V10_SCHEMA_FINGERPRINT",
    "PREVIOUS_V11_SCHEMA_FINGERPRINT",
    "PREVIOUS_V12_SCHEMA_FINGERPRINT",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "MigrationStep",
    "SchemaMigrationError",
    "apply_migrations",
    "infer_schema_version",
    "is_baseline_schema",
    "read_schema_version",
    "schema_fingerprint",
    "validate_migration_registry",
    "write_schema_version",
]
