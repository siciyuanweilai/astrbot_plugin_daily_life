from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StorageTableGroup:
    key: str
    label: str
    tables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StorageCategory:
    key: str
    label: str
    description: str
    tables: tuple[str, ...]
    clear_order: tuple[str, ...]
    default_keep_days: int = 0
    auto_cleanup: bool = False
    groups: tuple[StorageTableGroup, ...] = ()


DAILY_TABLES = (
    "days",
    "timelines",
    "outfit_history",
    "day_meta",
    "states",
    "state_logs",
    "day_places",
    "day_events",
    "day_event_people",
)
DAILY_CLEAR_ORDER = (
    "day_event_people",
    "day_events",
    "day_places",
    "state_logs",
    "states",
    "day_meta",
    "outfit_history",
    "timelines",
    "days",
)

RELATIONSHIP_TABLES = (
    "relationships",
    "relationship_notes",
    "relationship_points",
    "relationship_contacts",
)
RELATIONSHIP_CLEAR_ORDER = (
    "relationship_points",
    "relationship_notes",
    "relationship_contacts",
    "relationships",
)

WORLD_TABLES = (
    "places",
    "events",
    "event_people",
    "preferences",
    "life_events",
)
WORLD_CLEAR_ORDER = (
    "event_people",
    "events",
    "life_events",
    "preferences",
    "places",
)

CONVERSATION_TABLES = (
    "chat_memory_messages",
    "chat_memory_sessions",
    "chat_memory_batches",
    "chat_summaries",
    "chat_summary_people",
    "group_environments",
    "message_visibility",
    "action_decisions",
    "session_mid_summaries",
)
CONVERSATION_CLEAR_ORDER = (
    "chat_memory_batches",
    "chat_memory_sessions",
    "chat_memory_messages",
    "chat_summary_people",
    "chat_summaries",
    "action_decisions",
    "message_visibility",
    "group_environments",
    "session_mid_summaries",
)

EXPERIENCE_TABLES = (
    "life_episodes",
    "life_episode_people",
    "life_episode_places",
    "memory_evidence",
    "behavior_feedback",
    "emotion_arcs",
    "physiological_rhythm_logs",
    "reply_effects",
    "life_decisions",
    "memory_corrections",
    "behavior_patterns",
    "behavior_scenes",
    "focus_slots",
    "focus_targets",
    "life_terms",
    "memory_boundaries",
    "memory_maintenance",
)
EXPERIENCE_CLEAR_ORDER = (
    "memory_decision_links",
    "life_episode_places",
    "life_episode_people",
    "life_episodes",
    "memory_evidence",
    "behavior_feedback",
    "emotion_arcs",
    "physiological_rhythm_logs",
    "reply_effects",
    "life_decisions",
    "memory_corrections",
    "behavior_scenes",
    "behavior_patterns",
    "focus_slots",
    "focus_targets",
    "life_terms",
    "memory_boundaries",
    "memory_maintenance",
)

EXPRESSION_TABLES = (
    "expression_profiles",
    "expression_reviews",
    "temporary_expression_states",
    "expression_intents",
    "emoji_assets",
    "reverse_prompts",
)
EXPRESSION_CLEAR_ORDER = (
    "emoji_assets",
    "expression_intents",
    "temporary_expression_states",
    "expression_reviews",
    "expression_profiles",
    "reverse_prompts",
)

MEDIA_TABLES = (
    "video_insights",
    "video_insight_details",
    "video_insight_frames",
)
MEDIA_CLEAR_ORDER = (
    "video_insight_frames",
    "video_insight_details",
    "video_insights",
)

LONG_TERM_TABLES = (
    "long_term_memories",
    "memory_episode_clusters",
    "memory_episode_cluster_items",
    "memory_entities",
    "memory_entity_links",
    "memory_conflicts",
    "memory_decision_links",
)
LONG_TERM_CLEAR_ORDER = (
    "memory_decision_links",
    "memory_conflicts",
    "memory_entity_links",
    "memory_episode_cluster_items",
    "memory_entities",
    "memory_episode_clusters",
    "long_term_memories",
)

PLANNING_TABLES = (
    "week_plans",
    "week_goals",
    "week_hints",
    "week_suggestions",
    "commitments",
    "commitment_people",
    "day_commitments",
)
PLANNING_CLEAR_ORDER = (
    "week_suggestions",
    "week_hints",
    "week_goals",
    "week_plans",
    "day_commitments",
    "commitment_people",
    "commitments",
)

REVIEW_TABLES = (
    "daily_reviews",
    "daily_review_points",
    "review_preferences",
)
REVIEW_CLEAR_ORDER = (
    "review_preferences",
    "daily_review_points",
    "daily_reviews",
)


STORAGE_CATEGORIES: dict[str, StorageCategory] = {
    "daily": StorageCategory(
        key="daily",
        label="日常记录",
        description="当天日程、时间轴、穿搭、状态、地点和当日事件。",
        tables=DAILY_TABLES,
        clear_order=DAILY_CLEAR_ORDER,
        default_keep_days=30,
        auto_cleanup=True,
    ),
    "relationships": StorageCategory(
        key="relationships",
        label="关系档案",
        description="联系人、群友、关系备注、关系要点和可触达入口。",
        tables=RELATIONSHIP_TABLES,
        clear_order=RELATIONSHIP_CLEAR_ORDER,
        default_keep_days=0,
        auto_cleanup=True,
    ),
    "world": StorageCategory(
        key="world",
        label="世界线索",
        description="地点、世界事件、长期偏好和生活事件。",
        tables=WORLD_TABLES,
        clear_order=WORLD_CLEAR_ORDER,
        default_keep_days=0,
        auto_cleanup=True,
        groups=(
            StorageTableGroup("places", "地点档案", ("places",)),
            StorageTableGroup("events", "事件线索", ("events", "event_people", "life_events")),
            StorageTableGroup("preferences", "长期偏好", ("preferences",)),
        ),
    ),
    "conversation": StorageCategory(
        key="conversation",
        label="聊天留意",
        description="聊天摘要、群聊氛围、可见性判断、行动裁定和会话中段摘要。",
        tables=CONVERSATION_TABLES,
        clear_order=CONVERSATION_CLEAR_ORDER,
        default_keep_days=180,
        auto_cleanup=True,
        groups=(
            StorageTableGroup("summaries", "聊天摘要", ("chat_summaries", "chat_summary_people", "session_mid_summaries")),
            StorageTableGroup("group_awareness", "群聊感知", ("group_environments", "message_visibility", "action_decisions")),
        ),
    ),
    "experience": StorageCategory(
        key="experience",
        label="体验沉淀",
        description="生活片段、证据链、行为反馈、情绪节律、决策、关注目标和记忆边界。",
        tables=EXPERIENCE_TABLES,
        clear_order=EXPERIENCE_CLEAR_ORDER,
        default_keep_days=0,
        auto_cleanup=True,
        groups=(
            StorageTableGroup("episodes", "生活片段", ("life_episodes", "life_episode_people", "life_episode_places")),
            StorageTableGroup("signals", "证据与反馈", ("memory_evidence", "behavior_feedback", "reply_effects")),
            StorageTableGroup("rhythm", "情绪节律", ("emotion_arcs", "physiological_rhythm_logs")),
            StorageTableGroup("decisions", "决策与修正", ("life_decisions", "memory_corrections")),
            StorageTableGroup("patterns", "行为模式", ("behavior_patterns", "behavior_scenes")),
            StorageTableGroup("focus", "关注边界", ("focus_slots", "focus_targets", "life_terms", "memory_boundaries", "memory_maintenance")),
        ),
    ),
    "expression": StorageCategory(
        key="expression",
        label="表达素材",
        description="表达习惯、表达复核、临时表达状态、发表情意图、表情素材和反推提示。",
        tables=EXPRESSION_TABLES,
        clear_order=EXPRESSION_CLEAR_ORDER,
        default_keep_days=0,
        auto_cleanup=True,
        groups=(
            StorageTableGroup("profiles", "表达习惯", ("expression_profiles", "expression_reviews", "temporary_expression_states")),
            StorageTableGroup("emoji", "表情素材", ("expression_intents", "emoji_assets")),
            StorageTableGroup("reverse", "反推提示", ("reverse_prompts",)),
        ),
    ),
    "media": StorageCategory(
        key="media",
        label="媒体理解",
        description="视频理解缓存、精简摘要、分段细节和关键帧记录。",
        tables=MEDIA_TABLES,
        clear_order=MEDIA_CLEAR_ORDER,
        default_keep_days=30,
        auto_cleanup=True,
    ),
    "longterm": StorageCategory(
        key="longterm",
        label="长期记忆",
        description="长期记忆条目、片段聚类、实体索引、冲突记录和决策关联。",
        tables=LONG_TERM_TABLES,
        clear_order=LONG_TERM_CLEAR_ORDER,
        default_keep_days=0,
        auto_cleanup=True,
        groups=(
            StorageTableGroup("memories", "记忆条目", ("long_term_memories",)),
            StorageTableGroup("clusters", "片段聚类", ("memory_episode_clusters", "memory_episode_cluster_items")),
            StorageTableGroup("entities", "实体索引", ("memory_entities", "memory_entity_links")),
            StorageTableGroup("quality", "冲突关联", ("memory_conflicts", "memory_decision_links")),
        ),
    ),
    "planning": StorageCategory(
        key="planning",
        label="计划安排",
        description="周计划、周目标、建议活动、未来承诺和每日约定引用。",
        tables=PLANNING_TABLES,
        clear_order=PLANNING_CLEAR_ORDER,
        default_keep_days=180,
        auto_cleanup=True,
    ),
    "review": StorageCategory(
        key="review",
        label="日常复盘",
        description="每日复盘、复盘要点和复盘引用到的偏好。",
        tables=REVIEW_TABLES,
        clear_order=REVIEW_CLEAR_ORDER,
        default_keep_days=120,
        auto_cleanup=True,
    ),
}


CATEGORY_LABELS = {category.label: key for key, category in STORAGE_CATEGORIES.items()}


def normalize_storage_category(value: str) -> str:
    text = str(value or "").strip()
    if text in STORAGE_CATEGORIES:
        return text
    return CATEGORY_LABELS.get(text, "")


def iter_owned_storage_tables() -> tuple[str, ...]:
    return tuple(table for category in STORAGE_CATEGORIES.values() for table in category.tables)


def validate_storage_categories(schema_tables: set[str], *, ignored_tables: set[str] | None = None) -> None:
    ignored = set(ignored_tables or set())
    owned = iter_owned_storage_tables()
    duplicate_tables = sorted({table for table in owned if owned.count(table) > 1})
    missing_tables = sorted(set(schema_tables) - ignored - set(owned))
    unknown_tables = sorted(set(owned) - set(schema_tables))
    if duplicate_tables or missing_tables or unknown_tables:
        problems = []
        if duplicate_tables:
            problems.append(f"重复归属：{', '.join(duplicate_tables)}")
        if missing_tables:
            problems.append(f"未分类：{', '.join(missing_tables)}")
        if unknown_tables:
            problems.append(f"未知表：{', '.join(unknown_tables)}")
        raise ValueError("存储分类定义不完整：" + "；".join(problems))


__all__ = [
    "StorageCategory",
    "StorageTableGroup",
    "STORAGE_CATEGORIES",
    "normalize_storage_category",
    "iter_owned_storage_tables",
    "validate_storage_categories",
]
