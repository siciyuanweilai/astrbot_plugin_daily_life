import datetime
import random
from typing import Any, Iterable

from ..labels import (
    action_label,
    atmosphere_label,
    bot_watch_state_label,
    scene_type_label,
    understanding_label,
    visibility_label,
)


PLACE_ANCHORS = [
    {"name": "家", "type": "home", "hint": "最稳定的生活锚点，适合清晨、夜晚和休息片段"},
    {"name": "家附近街区", "type": "neighborhood", "hint": "日常散步、便利店、咖啡和临时小事的活动范围"},
    {"name": "常去咖啡店", "type": "cafe", "hint": "适合写手帐、见朋友、短暂停留"},
    {"name": "常去书店", "type": "bookstore", "hint": "适合阅读、买书、安静消磨时间"},
]

PLACE_EXPLORATIONS = [
    ("便利店", "shop", "临时补给、买饮料或甜点"),
    ("社区公园", "park", "散步、晒太阳、放空"),
    ("街角面包店", "bakery", "早餐、点心和香气记忆"),
    ("小型超市", "shop", "采购生活用品"),
    ("地铁站附近", "transit", "通勤、等人、临时碰面"),
    ("新开的甜品店", "dessert", "朋友邀约和下午茶"),
    ("电影院", "cinema", "约电影或消磨夜晚"),
    ("商场中庭", "mall", "逛街、吃饭、等朋友"),
    ("火锅店", "restaurant", "热闹聚餐"),
    ("展览馆", "gallery", "看展、拍照和聊天"),
    ("图书馆", "library", "学习、查资料和安静工作"),
    ("自习室", "study", "专注任务"),
    ("共享办公区", "work", "处理项目"),
    ("文具店", "shop", "补充手帐和学习用品"),
    ("健身房", "fitness", "运动和恢复精力"),
    ("河边步道", "walk", "散步、慢跑和吹风"),
    ("羽毛球馆", "sport", "轻运动和朋友局"),
    ("舞蹈教室", "sport", "练习和出汗"),
    ("安静茶馆", "tea", "放松、聊天和避开吵闹"),
    ("花店", "flower", "买花、散心和制造小记忆"),
    ("家附近小餐馆", "restaurant", "不想折腾时吃饭"),
    ("香薰小店", "shop", "买居家物件"),
]


def compact_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    return text[:limit]


def _as_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _field(item: Any, key: str, default: Any = "") -> Any:
    if hasattr(item, key):
        return getattr(item, key)
    if isinstance(item, dict):
        return item.get(key, default)
    return default


def _text_fields(item: Any, *keys: str) -> str:
    chunks = [compact_text(_field(item, key), 200) for key in keys]
    for note in _as_list(_field(item, "notes", [])):
        chunks.append(compact_text(_field(note, "content") or note, 120))
    for point in _as_list(_field(item, "memory_points", [])):
        chunks.append(compact_text(_field(point, "content") or point, 120))
    for person in _as_list(_field(item, "people", [])):
        chunks.append(compact_text(person, 40))
    return " ".join(chunk for chunk in chunks if chunk)


def _query_terms(values: Iterable[Any]) -> list[str]:
    separators = " \n\t,.;:!?，。；：！？、/|()（）[]【】<>《》\"'“”‘’的和与跟在去到"
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        text = compact_text(value, 220)
        if not text:
            continue
        candidates = [text] if len(text) <= 24 else []
        scratch = text
        for sep in separators:
            scratch = scratch.replace(sep, " ")
        candidates.extend(scratch.split())
        for candidate in candidates:
            term = compact_text(candidate, 40)
            if len(term) < 2 or term in seen:
                continue
            seen.add(term)
            result.append(term)
    return result[:80]


def _score_item(item: Any, terms: list[str], index: int, kind: str) -> float:
    blob = _text_fields(
        item,
        "name",
        "id",
        "alias",
        "persona_hint",
        "hint",
        "type",
        "summary",
        "brief",
        "long_summary",
        "place",
        "source",
    )
    score = max(0.0, 1.2 - index * 0.08)
    for term in terms:
        if term and term in blob:
            score += 1.0 + min(len(term), 8) * 0.08
    if kind == "relationships":
        score += min(int(_field(item, "interactions", 0) or 0), 30) * 0.03
        score += len(_as_list(_field(item, "memory_points", []))) * 0.35
    elif kind == "places":
        score += min(int(_field(item, "visits", 0) or 0), 20) * 0.04
    elif kind == "events" and compact_text(_field(item, "importance")).lower() in {"high", "important", "重要"}:
        score += 1.0
    return score


def _select_items(items: list[Any], terms: list[str], limit: int, kind: str) -> list[Any]:
    if limit <= 0:
        return []
    ranked = [
        (_score_item(item, terms, index, kind), index, item)
        for index, item in enumerate(list(items or []))
    ]
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    return [item for _, _, item in ranked[:limit]]


def select_relevant_world(
    relationships: list[Any],
    places: list[Any],
    events: list[Any],
    summaries: list[Any] | None = None,
    hints: Iterable[Any] | None = None,
    relationship_limit: int = 6,
    place_limit: int = 8,
    event_limit: int = 8,
    summary_limit: int = 6,
) -> dict[str, list[Any]]:
    terms = _query_terms(hints or [])
    return {
        "relationships": _select_items(relationships, terms, relationship_limit, "relationships"),
        "places": _select_items(places, terms, place_limit, "places"),
        "events": _select_items(events, terms, event_limit, "events"),
        "summaries": _select_items(summaries or [], terms, summary_limit, "summaries"),
    }


def normalize_place_names(raw_places: Any) -> list[str]:
    if not raw_places:
        return []
    if isinstance(raw_places, (str, dict)):
        raw_places = [raw_places]
    names: list[str] = []
    try:
        iterator = iter(raw_places)
    except TypeError:
        return []

    for item in iterator:
        name = compact_text(_field(item, "name") if isinstance(item, dict) else item, 40)
        if name and name not in names:
            names.append(name)
    return names[:8]


def normalize_event_items(date_str: str, raw_events: Any, source: str = "daily") -> list[dict[str, Any]]:
    if not raw_events:
        return []
    if isinstance(raw_events, (str, dict)):
        raw_events = [raw_events]

    result: list[dict[str, Any]] = []
    try:
        iterator = iter(raw_events)
    except TypeError:
        return result

    for item in iterator:
        if isinstance(item, dict):
            summary = compact_text(item.get("summary"), 160)
            place = compact_text(item.get("place"), 40)
            people = item.get("people", [])
            if isinstance(people, str):
                people = [people]
            if not isinstance(people, list):
                people = []
            people = [compact_text(person, 40) for person in people if compact_text(person, 40)]
            importance = compact_text(item.get("importance") or "normal", 20)
        else:
            summary = compact_text(item, 160)
            place = ""
            people = []
            importance = "normal"

        if summary:
            result.append(
                {
                    "date": date_str,
                    "summary": summary,
                    "people": people[:6],
                    "place": place,
                    "importance": importance,
                    "source": source,
                }
            )
    return result[:12]


def choose_place_candidates(
    saved_places: list[dict[str, Any]],
    date_value: datetime.date,
    schedule_intent: str = "",
    weather_condition: str = "",
    limit: int = 8,
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(place: dict[str, Any], source: str) -> None:
        name = compact_text(_field(place, "name"), 40)
        if not name or name in seen:
            return
        seen.add(name)
        candidates.append(
            {
                "name": name,
                "type": compact_text(_field(place, "type") or "place", 20),
                "hint": compact_text(_field(place, "hint") or _field(place, "source") or "", 80),
                "source": source,
            }
        )

    for anchor in PLACE_ANCHORS:
        add(anchor, "anchor")

    for place in sorted(
        list(saved_places or []),
        key=lambda item: (str(_field(item, "last_seen")), int(_field(item, "visits", 0) or 0)),
        reverse=True,
    )[:4]:
        add(place, "memory")

    pool = PLACE_EXPLORATIONS
    rng = random.Random(f"{date_value.isoformat()}:{schedule_intent}:{weather_condition}")
    for name, kind, hint in rng.sample(pool, k=min(len(pool), max(0, limit - len(candidates) + 2))):
        add({"name": name, "type": kind, "hint": hint}, "random")
        if len(candidates) >= limit:
            break
    return candidates[:limit]


def format_relationships(relationships: Iterable[dict[str, Any]], limit: int = 6) -> str:
    lines = []
    for profile in list(relationships or [])[:limit]:
        name = compact_text(_field(profile, "name") or _field(profile, "id") or "用户", 40)
        notes = _field(profile, "notes", [])
        note = ""
        if isinstance(notes, list) and notes:
            latest = notes[-1]
            note = compact_text(_field(latest, "content") or latest, 80)
        points = _as_list(_field(profile, "memory_points", []))
        point = ""
        if points:
            latest_point = points[-1]
            point = compact_text(_field(latest_point, "content") or latest_point, 100)
        persona = compact_text(_field(profile, "persona_hint"), 90)
        subjective_name = compact_text(_field(profile, "subjective_name"), 40)
        subjective_tags = [
            compact_text(item, 32)
            for item in _as_list(_field(profile, "subjective_tags", []))
            if compact_text(item, 32)
        ]
        relationship_story = compact_text(_field(profile, "relationship_story"), 100)
        count = _field(profile, "interactions", 0)
        details = []
        if persona:
            details.append(f"人设线索：{persona}")
        if subjective_name:
            details.append(f"主观称呼：{subjective_name}")
        if subjective_tags:
            details.append("短标签：" + "、".join(subjective_tags[:4]))
        if relationship_story:
            details.append(f"关系叙事：{relationship_story}")
        if point:
            details.append(f"记忆点：{point}")
        if note:
            details.append(f"最近：{note}")
        suffix = "；" + "；".join(details) if details else ""
        lines.append(f"- {name}：互动 {count} 次{suffix}")
    return "\n".join(lines)


def format_places(places: Iterable[dict[str, Any]], limit: int = 8) -> str:
    lines = []
    for place in list(places or [])[:limit]:
        name = compact_text(_field(place, "name"), 40)
        if not name:
            continue
        visits = _field(place, "visits", 0)
        hint = compact_text(_field(place, "hint") or _field(place, "source"), 70)
        suffix = f"；{hint}" if hint else ""
        lines.append(f"- {name}：出现 {visits} 次{suffix}")
    return "\n".join(lines)


def format_events(events: Iterable[dict[str, Any]], limit: int = 8) -> str:
    lines = []
    for event in list(events or [])[:limit]:
        summary = compact_text(_field(event, "summary"), 120)
        if not summary:
            continue
        date = compact_text(_field(event, "date"), 20)
        place = compact_text(_field(event, "place"), 40)
        place_text = f" @ {place}" if place else ""
        lines.append(f"- {date}{place_text}：{summary}")
    return "\n".join(lines)


def format_chat_summaries(summaries: Iterable[dict[str, Any]], limit: int = 6) -> str:
    lines = []
    for summary in list(summaries or [])[:limit]:
        brief = compact_text(_field(summary, "brief") or _field(summary, "long_summary"), 120)
        if not brief:
            continue
        date = compact_text(_field(summary, "date"), 20)
        people = [compact_text(item, 30) for item in _as_list(_field(summary, "people", []))]
        extras = []
        if people:
            extras.append("人物：" + "、".join(people[:4]))
        suffix = "；" + "；".join(extras) if extras else ""
        lines.append(f"- {date}：{brief}{suffix}")
    return "\n".join(lines)


def format_candidates(candidates: Iterable[dict[str, Any]], limit: int = 8) -> str:
    lines = []
    for place in list(candidates or [])[:limit]:
        name = compact_text(_field(place, "name"), 40)
        if not name:
            continue
        hint = compact_text(_field(place, "hint"), 70)
        source = compact_text(_field(place, "source"), 20)
        detail = f"；{hint}" if hint else ""
        lines.append(f"- {name} ({source}){detail}")
    return "\n".join(lines)


def format_world_prompt(
    relationships: list[dict[str, Any]],
    places: list[dict[str, Any]],
    events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    summaries: list[dict[str, Any]] | None = None,
) -> str:
    sections = []
    rel_text = format_relationships(relationships)
    if rel_text:
        sections.append(
            "## 关系档案\n"
            "称谓边界：人设线索优先；若某人没有明确人设线索，关系叙事或最近印象里零散出现的他/她不能当作性别依据。\n"
            + rel_text
        )
    summary_text = format_chat_summaries(summaries or [])
    if summary_text:
        sections.append("## 会话摘要\n" + summary_text)
    place_text = format_places(places)
    if place_text:
        sections.append("## 已沉淀地点\n" + place_text)
    event_text = format_events(events)
    if event_text:
        sections.append("## 近期事件记忆\n" + event_text)
    candidate_text = format_candidates(candidates)
    if candidate_text:
        sections.append(
            "## 今日地点候选\n"
            "优先用生活锚点保持连续性，也可以挑选随机探索地点制造新鲜感；不要每天都去同一个地方。\n"
            + candidate_text
        )
    return "\n\n".join(sections)


def format_hidden_world_context(
    relationships: list[dict[str, Any]],
    places: list[dict[str, Any]],
    events: list[dict[str, Any]],
    summaries: list[dict[str, Any]] | None = None,
) -> str:
    sections = []
    rel_text = format_relationships(relationships, limit=3)
    if rel_text:
        sections.append(
            "[HiddenRelationships]\n"
            "称谓边界：人设线索优先；没有明确人设线索时，不要把旧叙事里的他/她当成性别依据。\n"
            + rel_text
        )
    summary_text = format_chat_summaries(summaries or [], limit=3)
    if summary_text:
        sections.append("[HiddenChatMemory]\n" + summary_text)
    place_text = format_places(places, limit=5)
    if place_text:
        sections.append("[HiddenPlaces]\n" + place_text)
    event_text = format_events(events, limit=5)
    if event_text:
        sections.append("[HiddenEvents]\n" + event_text)
    return "\n".join(sections)


def format_hidden_group_awareness(
    environments: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    visibility: list[dict[str, Any]] | None = None,
) -> str:
    sections: list[str] = []
    env_lines = []
    for item in list(environments or [])[:3]:
        group = compact_text(_field(item, "group_name") or _field(item, "group_id") or "当前群聊", 32)
        topic = compact_text(_field(item, "topic") or _field(item, "summary"), 70)
        atmosphere = atmosphere_label(compact_text(_field(item, "atmosphere"), 24)) or "未知氛围"
        watch = bot_watch_state_label(compact_text(_field(item, "bot_watch_state"), 24)) or "未定"
        desire = compact_text(_field(item, "participation_desire"), 8)
        complexity = compact_text(_field(item, "complexity_score"), 8)
        confidence = compact_text(_field(item, "understanding_confidence"), 8)
        deep = "需深析" if _field(item, "deep_analysis_needed") else "轻量判断"
        env_lines.append(
            f"- {group}: {atmosphere}/{watch}/{deep}; "
            f"参与欲{desire or 0}, 复杂度{complexity or 0}, 理解{confidence or 0}; "
            f"{topic or '暂无话题'}"
        )
    if env_lines:
        sections.append("[HiddenGroupAwareness]\n" + "\n".join(env_lines))

    visible_lines = []
    for item in list(visibility or [])[:3]:
        sender = compact_text(_field(item, "sender_name") or _field(item, "sender_profile_id"), 24)
        level = visibility_label(compact_text(_field(item, "visibility"), 24)) or "已留意"
        score = compact_text(_field(item, "attention_level"), 8)
        freshness = compact_text(_field(item, "freshness"), 24)
        psychological = compact_text(_field(item, "psychological_freshness"), 8)
        reactivated = compact_text(_field(item, "reactivated_from_id"), 12)
        reason = compact_text(_field(item, "reason"), 70)
        freshness_text = freshness or "新鲜度未定"
        if psychological:
            freshness_text += f"/心理{psychological}"
        if reactivated and reactivated != "0":
            freshness_text += f"/激活#{reactivated}"
        visible_lines.append(
            f"- {sender or '未知'}: {level}, 注意{score or 0}, {freshness_text}; {reason or '无说明'}"
        )
    if visible_lines:
        sections.append("[HiddenMessageAttention]\n" + "\n".join(visible_lines))

    decision_lines = []
    for item in list(decisions or [])[:3]:
        action = action_label(compact_text(_field(item, "action"), 32)) or "未定"
        scene = scene_type_label(compact_text(_field(item, "scene_type"), 32)) or "场景未定"
        understanding = understanding_label(compact_text(_field(item, "understanding"), 32)) or "理解未定"
        monologue = compact_text(_field(item, "inner_monologue"), 80)
        strategy = compact_text(_field(item, "reply_strategy"), 80)
        reason = compact_text(_field(item, "reason"), 80)
        tail = "；".join(part for part in (reason, monologue, strategy) if part)
        decision_lines.append(f"- {action} [{scene}/{understanding}]: {tail or '无说明'}")
    if decision_lines:
        sections.append("[HiddenActionJudgement]\n" + "\n".join(decision_lines))

    return "\n".join(sections)


def format_world_display(
    relationships: list[dict[str, Any]],
    places: list[dict[str, Any]],
    events: list[dict[str, Any]],
    summaries: list[dict[str, Any]] | None = None,
    environments: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    visibility: list[dict[str, Any]] | None = None,
) -> str:
    parts = ["🌐 日常生活世界"]
    rel_text = format_relationships(relationships, limit=8)
    parts.append("\n👥 关系档案\n" + (rel_text or "暂无"))
    summary_text = format_chat_summaries(summaries or [], limit=8)
    parts.append("\n💬 会话摘要\n" + (summary_text or "暂无"))
    env_lines = []
    for item in list(environments or [])[:5]:
        group = compact_text(_field(item, "group_name") or _field(item, "group_id") or "未命名群聊", 40)
        topic = compact_text(_field(item, "topic") or _field(item, "summary"), 80)
        atmosphere = atmosphere_label(compact_text(_field(item, "atmosphere"), 20))
        watch = bot_watch_state_label(compact_text(_field(item, "bot_watch_state"), 24))
        desire = compact_text(_field(item, "participation_desire"), 8)
        complexity = compact_text(_field(item, "complexity_score"), 8)
        confidence = compact_text(_field(item, "understanding_confidence"), 8)
        meta = f"参与{desire or 0} · 复杂{complexity or 0} · 理解{confidence or 0}"
        env_lines.append(f"- {group}：{atmosphere or '未知氛围'} · {watch or '未定'} · {meta}；{topic or '暂无话题'}")
    parts.append("\n🛰️ 群聊环境\n" + ("\n".join(env_lines) if env_lines else "暂无"))
    decision_lines = []
    for item in list(decisions or [])[:5]:
        action = compact_text(_field(item, "action"), 32)
        reason = compact_text(_field(item, "reason"), 100)
        scene = scene_type_label(compact_text(_field(item, "scene_type"), 32))
        understanding = compact_text(_field(item, "understanding"), 32)
        strategy = compact_text(_field(item, "reply_strategy"), 80)
        meta = " · ".join(part for part in (scene, understanding_label(understanding)) if part)
        suffix = f"；策略：{strategy}" if strategy else ""
        decision_lines.append(f"- {action_label(action) or '未定'} [{meta or '场景未定'}]：{reason or '无原因'}{suffix}")
    parts.append("\n🧭 动作裁定\n" + ("\n".join(decision_lines) if decision_lines else "暂无"))
    visibility_lines = []
    for item in list(visibility or [])[:5]:
        sender = compact_text(_field(item, "sender_name") or _field(item, "sender_profile_id"), 40)
        level = compact_text(_field(item, "visibility"), 24)
        score = compact_text(_field(item, "attention_level"), 8)
        reason = compact_text(_field(item, "reason"), 100)
        freshness = compact_text(_field(item, "freshness"), 24)
        psychological = compact_text(_field(item, "psychological_freshness"), 8)
        reactivated = compact_text(_field(item, "reactivated_from_id"), 12)
        freshness_parts = []
        if freshness:
            freshness_parts.append(freshness)
        if psychological:
            freshness_parts.append(f"心理{psychological}")
        if reactivated and reactivated != "0":
            freshness_parts.append(f"激活#{reactivated}")
        freshness_suffix = f" · {' · '.join(freshness_parts)}" if freshness_parts else ""
        visibility_lines.append(
            f"- {sender or '未知'}：{visibility_label(level) or '已留意'} · 注意{score or 0}"
            f"{freshness_suffix}；{reason or '无说明'}"
        )
    parts.append("\n👀 消息留意\n" + ("\n".join(visibility_lines) if visibility_lines else "暂无"))
    place_text = format_places(places, limit=10)
    parts.append("\n📍 地点\n" + (place_text or "暂无"))
    event_text = format_events(events, limit=10)
    parts.append("\n🧠 事件记忆\n" + (event_text or "暂无"))
    return "\n".join(parts)
