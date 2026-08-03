from .tables import (
    AWARENESS_SQL,
    COGNITION_INDEX_SQL,
    COGNITION_SQL,
    COMMITMENT_SQL,
    CONVERSATION_SQL,
    CORE_SQL,
    DAILY_SQL,
    DOMAIN_INDEX_SQL,
    DOMAIN_SQL,
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
    "CORE_SQL",
    "DAILY_SQL",
    "DOMAIN_SQL",
    "DOMAIN_INDEX_SQL",
    "WEEKLY_SQL",
    "COMMITMENT_SQL",
    "COGNITION_SQL",
    "COGNITION_INDEX_SQL",
    "CONVERSATION_SQL",
    "WORLD_SQL",
    "AWARENESS_SQL",
    "REVIEW_SQL",
    "EXPERIENCE_SQL",
    "INDEX_SQL",
    "DROP_SCHEMA_SQL",
    "SCHEMA_GROUPS",
    "iter_schema_sql",
]
