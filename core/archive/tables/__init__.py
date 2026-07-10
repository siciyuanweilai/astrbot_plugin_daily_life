from .chronicle import EXPERIENCE_SQL
from .conversation import CONVERSATION_SQL
from .cycle import WEEKLY_SQL
from .indexes import INDEX_SQL
from .kernel import ARCHIVE_VERSION, CORE_SQL
from .outlook import AWARENESS_SQL
from .purge import DROP_SCHEMA_SQL
from .review import REVIEW_SQL
from .routine import DAILY_SQL
from .vows import COMMITMENT_SQL
from .world import WORLD_SQL


SCHEMA_GROUPS = (
    CORE_SQL,
    DAILY_SQL,
    WEEKLY_SQL,
    COMMITMENT_SQL,
    WORLD_SQL,
    AWARENESS_SQL,
    REVIEW_SQL,
    EXPERIENCE_SQL,
    CONVERSATION_SQL,
    INDEX_SQL,
)


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
    "CONVERSATION_SQL",
    "INDEX_SQL",
    "DROP_SCHEMA_SQL",
    "SCHEMA_GROUPS",
]
