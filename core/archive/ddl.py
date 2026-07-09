from .tables import (
    ARCHIVE_VERSION,
    AWARENESS_SQL,
    COMMITMENT_SQL,
    CORE_SQL,
    DAILY_SQL,
    DROP_SCHEMA_SQL,
    EXPERIENCE_SQL,
    INDEX_SQL,
    REVIEW_SQL,
    SCHEMA_GROUPS,
    WEEKLY_SQL,
    WORLD_SQL,
)


def iter_schema_sql() -> tuple[str, ...]:
    return SCHEMA_GROUPS


__all__ = [
    "ARCHIVE_VERSION",
    "CORE_SQL",
    "DAILY_SQL",
    "WEEKLY_SQL",
    "COMMITMENT_SQL",
    "WORLD_SQL",
    "AWARENESS_SQL",
    "REVIEW_SQL",
    "EXPERIENCE_SQL",
    "INDEX_SQL",
    "DROP_SCHEMA_SQL",
    "SCHEMA_GROUPS",
    "iter_schema_sql",
]
