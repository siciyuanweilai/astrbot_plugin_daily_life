"""构造旧版本数据库记录，仅用于验证升级后的只读兼容。"""

import json

from core.models import (
    BehaviorPatternRecord, FocusSlotRecord, FocusTargetRecord,
    SessionMidSummaryRecord, TemporaryExpressionStateRecord,
)

_TABLES = {
    BehaviorPatternRecord: "behavior_patterns",
    SessionMidSummaryRecord: "session_mid_summaries",
    TemporaryExpressionStateRecord: "temporary_expression_states",
    FocusSlotRecord: "focus_slots",
    FocusTargetRecord: "focus_targets",
}


async def seed_legacy_record(archive, record):
    table = _TABLES[type(record)]

    def write():
        columns = {row[1] for row in archive._conn.execute(f"PRAGMA table_info({table})")}
        values = {
            key: "\n".join(value) if key == "participants" else
            json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            for key, value in record.as_dict().items()
            if key in columns and not (key in {"id", "created_at", "updated_at"} and not value)
        }
        keys = list(values)
        archive._conn.execute(
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
            [values[key] for key in keys],
        )
        archive._conn.commit()

    await archive._run_db(write)
