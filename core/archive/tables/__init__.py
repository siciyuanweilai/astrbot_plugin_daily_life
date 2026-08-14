from .chronicle import EXPERIENCE_SQL
from .mind import COGNITION_INDEX_SQL, COGNITION_SQL
from .conversation import CONVERSATION_SQL
from .cycle import WEEKLY_SQL
from .living import DOMAIN_INDEX_SQL, DOMAIN_SQL
from .indexes import INDEX_SQL
from .kernel import CORE_SQL
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
    COGNITION_SQL,
    DOMAIN_SQL,
    INDEX_SQL,
    COGNITION_INDEX_SQL,
    DOMAIN_INDEX_SQL,
)


__all__ = [
    "CORE_SQL",
    "DAILY_SQL",
    "WEEKLY_SQL",
    "COMMITMENT_SQL",
    "WORLD_SQL",
    "AWARENESS_SQL",
    "REVIEW_SQL",
    "EXPERIENCE_SQL",
    "CONVERSATION_SQL",
    "COGNITION_SQL",
    "DOMAIN_SQL",
    "DOMAIN_INDEX_SQL",
    "COGNITION_INDEX_SQL",
    "INDEX_SQL",
    "DROP_SCHEMA_SQL",
    "SCHEMA_GROUPS",
]
