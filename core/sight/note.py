from __future__ import annotations

import re
import uuid
from typing import Any

from ..life.tools import extract_json_from_text
from ..prompts import cache_friendly_prompt
from .clip import SightInsight
from .digest import content_details
from .model import get_sight_provider, sight_provider_id


NOTE_TRANSCRIPT_CHARS = 20000
NOTE_FRAME_LIMIT = 24
NOTE_SEGMENT_LIMIT = 120
PROFESSIONAL_NOTE_NEEDS_TRANSCRIPT = "没有可用音频转写，已跳过专业总结（画面理解仍会保留）"

CONTENT_MARKER_RE = re.compile(r"\*?Content-(?:\[(\d{1,2}):(\d{2})\]|(\d{1,2}):(\d{2}))\*?")
SCREENSHOT_MARKER_RE = re.compile(r"\*?Screenshot-(?:\[(\d{1,2}):(\d{2})\]|(\d{1,2}):(\d{2}))\*?")
HEADING_LINE_RE = re.compile(r"^(\s{0,3}#{1,6}\s*)(.*)$")
PROFESSIONAL_ROLE_SCHEMA: tuple[tuple[str, str, str], ...] = (
    ("overview", "背景概述", "背景、主题、对象与上下文"),
    ("core", "核心论点", "核心主张、主要判断与论证方向"),
    ("fact", "关键事实", "事件、观点、例子与可确认结论"),
    ("data", "数据支撑", "数字、统计、比例、金额、实验结果等量化依据"),
    ("analysis", "分析与影响", "原因、影响、逻辑关系与延伸分析"),
    ("risk", "争议与风险", "争议、不确定性、限制与潜在风险"),
    ("suggestion", "结论与参考建议", "结论、启发与参考建议"),
    ("other", "其他", "无法归入上述结构但有保留价值的内容"),
)
PROFESSIONAL_ROLES: tuple[tuple[str, str], ...] = tuple(
    (role, title) for role, title, _ in PROFESSIONAL_ROLE_SCHEMA if role != "other"
)
PROFESSIONAL_ROLE_TITLES = dict(PROFESSIONAL_ROLES)
PROFESSIONAL_ROLE_ENUM = "/".join(role for role, _, _ in PROFESSIONAL_ROLE_SCHEMA)
PROFESSIONAL_ROLE_SCHEMA_TEXT = "\n".join(
    f"  - {role}: {title}；{description}" for role, title, description in PROFESSIONAL_ROLE_SCHEMA
)


BASE_NOTE_PROMPT = """\
你是一个专业的视频总结结构化提取器，负责把视频转录、画面线索和已有要点整理成可渲染的数据。

语言要求：
- 使用中文撰写，专有名词、技术术语、品牌名称和人名可保留英文。

输出 JSON 对象：
{
  "sections": [
    {
      "title": "板块标题",
      "role": "{PROFESSIONAL_ROLE_ENUM}，可空",
      "time": "mm:ss 或 hh:mm:ss，可空",
      "paragraphs": ["段落文本"],
      "bullets": ["要点文本"],
      "quotes": ["原话或名言，可空"]
    }
  ]
}

字段说明：
- sections 按视频内容顺序排列。
- role 用于专业模式的章节归类；非专业模式可留空。专业 role 语义：
{PROFESSIONAL_ROLE_SCHEMA_TEXT}
- time 填该板块对应的视频起点；没有明确时间可留空。
- paragraphs、bullets、quotes 只写可确认内容。
- 素材不足时省略没有依据的 role，不要为了凑齐板块编造内容。
- 渲染时 title 会成为 `##` 章节标题，bullets 会成为列表，quotes 会成为引用块。
- 字段文本可以使用 `**加粗**` 突出关键词，但不要输出整篇 Markdown。
- 视频中提及的数学公式保留为 LaTeX 字符串。
""".replace("{PROFESSIONAL_ROLE_ENUM}", PROFESSIONAL_ROLE_ENUM).replace(
    "{PROFESSIONAL_ROLE_SCHEMA_TEXT}", PROFESSIONAL_ROLE_SCHEMA_TEXT
)


NOTE_STYLES: dict[str, str] = {
    "concise": (
        "**简洁模式**：输出 2-4 个 sections，仅提取核心观点和关键结论。"
        "每个 section 控制在 1 个短段落或 2-3 个要点。"
    ),
    "detailed": (
        "**详细模式**：输出较完整的 sections，保留重要例子、数据和论证过程。"
        "每个 section 可同时包含段落、要点和引用。"
    ),
    "professional": (
        f"**专业模式**：提供深度结构化分析，按 role 组织 {PROFESSIONAL_ROLE_ENUM}。"
        "每个 section 内优先使用 bullets 拆分事实、示例、判断、影响和参考建议；"
        "有原话、名言或关键表述时放入 quotes；可用 **加粗** 突出核心概念、数据和判断。"
        "画面线索只作为证据来源，语言正式、逻辑清晰，保留可复核的事实、证据和推理链。"
    ),
}


class SightNote:
    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def compose(self, insight: SightInsight, *, style: str = "professional") -> str:
        style_key = _note_style_key(style)
        unavailable = professional_note_unavailable_reason(insight, style=style_key)
        if unavailable:
            raise SightNoteError(unavailable)

        composer = getattr(self.runtime, "composer", None)
        if composer is None:
            raise SightNoteError("总结模型不可用：缺少文本生成组件")
        call_llm = getattr(composer, "_call_llm_text", None)
        if not callable(call_llm):
            raise SightNoteError("总结模型不可用：缺少文本生成接口")

        settings = getattr(getattr(self.runtime, "config", None), "sight", None)
        provider_id = sight_provider_id(self.runtime, "summary_provider")
        provider = await get_sight_provider(self.runtime, "summary_provider")
        if not provider:
            raise SightNoteError("总结模型不可用：没有可用提供商")

        max_chars = max(2000, int(getattr(settings, "note_max_transcript_chars", NOTE_TRANSCRIPT_CHARS) or NOTE_TRANSCRIPT_CHARS))
        prompt = self._prompt(insight, style=style, max_transcript_chars=max_chars)
        session_id = f"daily_life_sight_note_{uuid.uuid4().hex[:8]}"
        try:
            if provider_id:
                try:
                    text = await call_llm(provider, prompt, session_id, empty_retries=0, primary_provider_id=provider_id)
                except TypeError:
                    text = await call_llm(provider, prompt, session_id, empty_retries=0)
            else:
                text = await call_llm(provider, prompt, session_id, empty_retries=0)
        except Exception as exc:
            raise SightNoteError(f"总结模型调用失败：{exc}") from exc
        finally:
            cleanup = getattr(composer, "_cleanup_conversation", None)
            if callable(cleanup):
                await cleanup(session_id)
        raw = str(text or "").strip()
        if not raw:
            raise SightNoteError("总结模型返回空内容")
        payload = extract_json_from_text(raw)
        if not isinstance(payload, dict):
            raise SightNoteError("总结模型返回的 JSON 无法解析")
        markdown = _payload_markdown(insight, payload, style=style_key)
        if not markdown:
            raise SightNoteError("总结模型返回的 JSON 缺少有效总结内容")
        return self.normalize(insight, markdown)

    @staticmethod
    def normalize(insight: SightInsight, markdown: str) -> str:
        return normalize_note_markdown(insight, markdown)

    @staticmethod
    def _prompt(insight: SightInsight, *, style: str, max_transcript_chars: int) -> str:
        metadata = dict(insight.metadata or {})
        title, author = _title_parts(metadata, getattr(insight.clip, "name", ""))
        duration = _compact(metadata.get("duration") or "", 40)
        transcript = _excerpt(" ".join(str(insight.transcript or "").split()), max_transcript_chars)
        segments = _segment_text(metadata.get("transcript_segments"), transcript)
        frame_text = _frame_text(insight.frame_notes)
        details = "\n".join(f"- {item}" for item in content_details(insight.details, limit=8)) or "（没有额外要点）"
        style_key = _note_style_key(style)
        fixed = "\n".join((BASE_NOTE_PROMPT, NOTE_STYLES.get(style_key, NOTE_STYLES["professional"])))
        dynamic = (
            f"首行标题：{_h1_title(title, author)}\n"
            f"视频标题：{title}\n"
            f"作者名：{author or '（未获取到）'}\n"
            f"视频标签：{_tags_text(metadata)}\n"
            f"视频时长：{duration or '未知'}\n\n"
            "视频分段（格式：开始时间 - 内容）：\n\n"
            "---\n"
            f"{segments}\n"
            "---\n\n"
            f"完整转写：\n{transcript or '（没有可用转写）'}\n\n"
            f"画面线索：\n{frame_text}\n\n"
            f"已有要点：\n{details}\n\n"
            "请直接输出符合 schema 的 JSON 对象。"
        )
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="视频内容")

class SightNoteError(RuntimeError):
    pass


def professional_note_unavailable_reason(insight: SightInsight | None, *, style: str = "professional") -> str:
    if _note_style_key(style) != "professional":
        return ""
    if _has_transcript_content(insight):
        return ""
    return PROFESSIONAL_NOTE_NEEDS_TRANSCRIPT


def _has_transcript_content(insight: SightInsight | None) -> bool:
    if insight is None:
        return False
    transcript = " ".join(str(getattr(insight, "transcript", "") or "").split())
    if transcript:
        return True
    metadata = dict(getattr(insight, "metadata", None) or {})
    segments = metadata.get("transcript_segments")
    if not isinstance(segments, list):
        return False
    for segment in segments:
        if isinstance(segment, dict) and str(segment.get("text") or "").strip():
            return True
    return False


def _compact(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit or 1))]


def _title_parts(metadata: dict[str, Any], fallback_title: str = "") -> tuple[str, str]:
    title = _compact(metadata.get("title") or fallback_title or "视频总结", 80)
    author = _compact(
        metadata.get("author")
        or metadata.get("uploader")
        or metadata.get("owner")
        or metadata.get("owner_name")
        or "",
        60,
    )
    return title, author


def _h1_title(title: str, author: str = "") -> str:
    title = _compact(title or "视频总结", 80)
    author = _compact(author, 60)
    return f"# {title} - {author}" if author else f"# {title}"


def _note_style_key(style: str) -> str:
    key = str(style or "").strip().lower()
    aliases = {
        "简洁": "concise",
        "简洁模式": "concise",
        "详细": "detailed",
        "详细模式": "detailed",
        "专业": "professional",
        "专业模式": "professional",
    }
    return aliases.get(key, key if key in NOTE_STYLES else "professional")


def _payload_markdown(insight: SightInsight, payload: Any, *, style: str = "professional") -> str:
    if not isinstance(payload, dict):
        return ""
    metadata = dict(getattr(insight, "metadata", None) or {})
    title, author = _title_parts(metadata, getattr(getattr(insight, "clip", None), "name", ""))
    sections = _payload_sections(insight, payload, style=style)
    if not sections:
        return ""
    lines = [_h1_title(title, author), ""]
    for section in sections:
        rendered = _render_payload_section(section, style=style)
        if rendered:
            if lines[-1] != "":
                lines.append("")
            lines.extend(rendered)
    return "\n".join(lines).strip()


def _payload_sections(insight: SightInsight, payload: dict[str, Any], *, style: str = "professional") -> list[dict[str, Any]]:
    style_key = _note_style_key(style)
    sections = [_normalize_payload_section(item) for item in _as_list(payload.get("sections"))]
    sections = [section for section in sections if _section_has_content(section)]
    if style_key == "professional":
        professional = _professional_payload_sections(insight, payload, sections)
        if professional:
            return professional[:12]
    if sections:
        return sections[:12]

    summary = _field_text(payload.get("summary"), 700)
    bullets = _text_values(payload.get("bullets") or payload.get("points") or payload.get("details"), limit=10, char_limit=280)
    if not bullets:
        bullets = content_details(getattr(insight, "details", None), limit=8)
    if not summary:
        summary = " ".join(str(getattr(insight, "summary", "") or "").split())[:700]
    if not summary and not bullets:
        return []
    return [
        {
            "title": "概述",
            "role": "other",
            "time": "",
            "paragraphs": [summary] if summary else [],
            "bullets": bullets,
            "quotes": [],
        }
    ]


def _normalize_payload_section(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {
            "title": "概述",
            "role": "other",
            "time": _payload_time(value),
            "paragraphs": [_field_text(value, 700)],
            "bullets": [],
            "quotes": [],
        }
    if not isinstance(value, dict):
        return {}
    raw_title = _field_text(value.get("title") or value.get("heading") or value.get("name"), 80)
    raw_time = value.get("time") or value.get("timestamp") or value.get("start") or raw_title
    time_label = _payload_time(raw_time)
    title = _strip_title_time(raw_title) or "概述"
    role = _section_role(value.get("role"))
    return {
        "title": title,
        "role": role,
        "time": time_label,
        "paragraphs": _text_values(
            value.get("paragraphs") or value.get("paragraph") or value.get("content") or value.get("summary"),
            limit=5,
            char_limit=700,
        ),
        "bullets": _text_values(
            value.get("bullets") or value.get("points") or value.get("items") or value.get("details"),
            limit=12,
            char_limit=280,
        ),
        "quotes": _text_values(value.get("quotes") or value.get("quote"), limit=4, char_limit=320),
    }


def _render_payload_section(section: dict[str, Any], *, style: str = "professional") -> list[str]:
    title = _section_render_title(section, style=style)
    time_label = _payload_time(section.get("time"))
    heading = f"## {title}"
    if time_label and not _has_timestamp(title):
        heading = f"## {time_label} {title}"
    lines = [heading, ""]
    for paragraph in _text_values(section.get("paragraphs"), limit=5, char_limit=700):
        lines.extend([paragraph, ""])
    for bullet in _text_values(section.get("bullets"), limit=12, char_limit=280):
        lines.append(f"- {bullet}")
    if section.get("bullets"):
        lines.append("")
    for quote in _text_values(section.get("quotes"), limit=4, char_limit=320):
        lines.append(f"> {quote}")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _section_render_title(section: dict[str, Any], *, style: str = "professional") -> str:
    title = _field_text(section.get("title"), 80) or "概述"
    if _note_style_key(style) != "professional":
        return title
    role = _section_role(section.get("role"))
    preferred = PROFESSIONAL_ROLE_TITLES.get(role, "")
    return preferred or title


def _section_role(value: Any) -> str:
    text = _field_text(value, 80).lower()
    return text if text in PROFESSIONAL_ROLE_TITLES or text == "other" else "other"


def _professional_payload_sections(
    insight: SightInsight,
    payload: dict[str, Any],
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {role: [] for role, _ in PROFESSIONAL_ROLES}
    unclassified_sections: list[dict[str, Any]] = []
    timed_sections: list[dict[str, Any]] = []
    for section in sections:
        role = _section_role(section.get("role"))
        if role in buckets:
            section["role"] = role
            buckets[role].append(section)
        elif _payload_time(section.get("time")):
            section["role"] = "other"
            timed_sections.append(section)
        else:
            unclassified_sections.append(section)

    for section in unclassified_sections:
        role = _infer_professional_role(section, buckets, has_payload_summary=bool(payload.get("summary")))
        section["role"] = role
        buckets[role].append(section)

    summary = _field_text(payload.get("summary"), 700) or _field_text(getattr(insight, "summary", ""), 700)
    details = content_details(getattr(insight, "details", None), limit=8)
    frame_notes = _text_values(getattr(insight, "frame_notes", None), limit=6, char_limit=220)

    if summary and not buckets["overview"]:
        buckets["overview"].append(_section("overview", paragraphs=[summary]))
    if not buckets["core"]:
        thesis_bullets = _text_values(payload.get("bullets") or payload.get("points"), limit=4, char_limit=260)
        if not thesis_bullets:
            thesis_bullets = _unique_texts(details[:3] or ([summary] if summary else []), limit=4)
        if thesis_bullets:
            buckets["core"].append(_section("core", bullets=thesis_bullets))
    if not buckets["fact"]:
        fact_bullets = _unique_texts(details, limit=10)
        if not fact_bullets:
            fact_bullets = _unique_texts(frame_notes, limit=6)
        if fact_bullets:
            buckets["fact"].append(_section("fact", bullets=fact_bullets))
    if not buckets["data"]:
        data_bullets = _text_values(
            payload.get("data")
            or payload.get("data_points")
            or payload.get("statistics")
            or payload.get("metrics")
            or payload.get("numbers"),
            limit=8,
            char_limit=280,
        )
        if data_bullets:
            buckets["data"].append(_section("data", bullets=data_bullets))
    if not buckets["analysis"]:
        analysis_bullets = _text_values(
            payload.get("analysis") or payload.get("impacts") or payload.get("impact"),
            limit=5,
            char_limit=300,
        )
        if analysis_bullets:
            buckets["analysis"].append(_section("analysis", bullets=analysis_bullets))
    if not buckets["risk"]:
        risk_bullets = _text_values(payload.get("risks") or payload.get("risk"), limit=5, char_limit=300)
        if risk_bullets:
            buckets["risk"].append(_section("risk", bullets=risk_bullets))
    if not buckets["suggestion"]:
        suggestion = _field_text(
            payload.get("conclusion") or payload.get("reference_suggestion") or payload.get("recommendation"),
            500,
        )
        suggestion_bullets = _text_values(
            payload.get("conclusions")
            or payload.get("reference_suggestions")
            or payload.get("recommendations")
            or payload.get("suggestions"),
            limit=5,
            char_limit=280,
        )
        if suggestion:
            suggestion_bullets.insert(0, suggestion)
        if suggestion_bullets:
            buckets["suggestion"].append(_section("suggestion", bullets=suggestion_bullets))
    result: list[dict[str, Any]] = []
    for role, title in PROFESSIONAL_ROLES:
        merged = _merge_professional_bucket(role, title, buckets[role])
        if _section_has_content(merged):
            result.append(merged)
    result.extend(timed_sections)
    return result


def _infer_professional_role(
    section: dict[str, Any],
    buckets: dict[str, list[dict[str, Any]]],
    *,
    has_payload_summary: bool,
) -> str:
    if not buckets["overview"] and (section.get("paragraphs") or not has_payload_summary):
        return "overview"
    if section.get("bullets") and not buckets["core"]:
        return "core"
    if (section.get("quotes") or section.get("time")) and not buckets["fact"]:
        return "fact"
    if not buckets["overview"]:
        return "overview"
    if not buckets["core"]:
        return "core"
    if not buckets["analysis"]:
        return "analysis"
    return "fact"


def _merge_professional_bucket(role: str, title: str, sections: list[dict[str, Any]]) -> dict[str, Any]:
    paragraphs: list[str] = []
    bullets: list[str] = []
    quotes: list[str] = []
    time_label = ""
    for section in sections:
        if not time_label:
            time_label = _payload_time(section.get("time"))
        paragraphs.extend(_text_values(section.get("paragraphs"), limit=5, char_limit=700))
        bullets.extend(_text_values(section.get("bullets"), limit=12, char_limit=280))
        quotes.extend(_text_values(section.get("quotes"), limit=4, char_limit=320))
    return {
        "title": title,
        "role": role,
        "time": time_label,
        "paragraphs": _unique_texts(paragraphs, limit=5),
        "bullets": _unique_texts(bullets, limit=12),
        "quotes": _unique_texts(quotes, limit=4),
    }


def _section(
    role: str,
    *,
    paragraphs: list[str] | None = None,
    bullets: list[str] | None = None,
    quotes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "title": PROFESSIONAL_ROLE_TITLES.get(role, "概述"),
        "role": role,
        "time": "",
        "paragraphs": _unique_texts(paragraphs or [], limit=5),
        "bullets": _unique_texts(bullets or [], limit=12),
        "quotes": _unique_texts(quotes or [], limit=4),
    }


def _unique_texts(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _field_text(value, 700)
        if text and text not in result:
            result.append(text)
        if len(result) >= max(1, int(limit or 1)):
            break
    return result


def _section_has_content(section: dict[str, Any]) -> bool:
    if not isinstance(section, dict):
        return False
    return bool(section.get("paragraphs") or section.get("bullets") or section.get("quotes"))


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _text_values(value: Any, *, limit: int, char_limit: int) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            item = item.get("text") or item.get("content") or item.get("summary") or item.get("title") or ""
        text = _field_text(item, char_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= max(1, int(limit or 1)):
            break
    return result


def _field_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    text = text.strip().strip("#").strip()
    text = re.sub(r"^(?:[-*+]|\d+[.)、])\s+", "", text)
    return _cleanup_note_text(text)[: max(1, int(limit or 1))].strip()


def _payload_time(value: Any) -> str:
    if isinstance(value, (int, float)):
        return _format_time(value)
    text = str(value or "").strip().strip("`")
    if not text:
        return ""
    label = _heading_time_label(text)
    if not label and text.isdigit():
        label = _format_time(int(text))
    if not label:
        return ""
    seconds = _label_seconds(label)
    return _format_time(seconds) if seconds is not None else label


def _strip_title_time(title: str) -> str:
    value = str(title or "").strip()
    label = _heading_time_label(value)
    if label:
        value = value.replace(label, "", 1).strip()
    value = re.sub(r"^[`：:：\-\s]+", "", value)
    return value or "概述"


def normalize_note_markdown(insight: SightInsight, markdown: str) -> str:
    value = _sanitize_note_markdown(markdown)
    value = _normalize_marker_annotations(value)
    metadata = dict(getattr(insight, "metadata", None) or {})
    title, author = _title_parts(metadata, getattr(getattr(insight, "clip", None), "name", ""))
    expected = _h1_title(title, author)
    if not value:
        return expected
    lines = value.splitlines()
    first = next((index for index, line in enumerate(lines) if line.strip()), 0)
    if lines[first].lstrip().startswith("# "):
        lines[first] = expected
    else:
        lines.insert(first, expected)
    value = "\n".join(lines).strip()
    value = _ensure_timeline_section(insight, value)
    return _cleanup_note_text(_ensure_frame_references(insight, value))


def _excerpt(text: str, limit: int) -> str:
    text = str(text or "").strip()
    limit = max(100, int(limit or NOTE_TRANSCRIPT_CHARS))
    if len(text) <= limit:
        return text
    head = max(1, limit * 2 // 3)
    tail = max(1, limit - head)
    return f"{text[:head]}\n\n……（中间内容过长，已省略）……\n\n{text[-tail:]}"


def _strip_fence(text: str) -> str:
    value = str(text or "").strip()
    if not value.startswith("```"):
        return value
    lines = value.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _sanitize_note_markdown(markdown: str) -> str:
    value = _strip_fence(str(markdown or "").strip())
    if not value:
        return value
    value = _strip_html_comments(value)
    value = _unwrap_fenced_blocks(value)
    lines = [_clean_note_line(line) for line in value.splitlines()]
    lines = [line for line in lines if line is not None]
    return _cleanup_note_text("\n".join(lines))


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", str(text or ""), flags=re.DOTALL)


def _unwrap_fenced_blocks(text: str) -> str:
    output: list[str] = []
    in_fence = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        output.append(line.rstrip() if in_fence else line)
    return "\n".join(output).strip()


def _clean_note_line(line: str) -> str | None:
    value = str(line or "").rstrip()
    compact = value.strip()
    if not compact:
        return ""
    artifact_prefixes = (
        "可用关键帧",
        "可引用关键帧",
    )
    if compact.lstrip("-*0123456789.、)） ").startswith(artifact_prefixes):
        return None
    return value


def _cleanup_note_text(text: str) -> str:
    value = str(text or "")
    value = _strip_html_comments(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" ?[（(]\s*[)）]", "", value)
    value = re.sub(r" ?[【\[]\s*[]】]", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


def _normalize_marker_annotations(markdown: str) -> str:
    value = str(markdown or "").strip()
    if not value:
        return value

    def _replace_marker(match: re.Match[str]) -> str:
        minute = match.group(1) or match.group(3) or "00"
        second = match.group(2) or match.group(4) or "00"
        return f"{minute}:{second}"

    lines: list[str] = []
    for line in value.splitlines():
        match = HEADING_LINE_RE.match(line)
        if match:
            prefix, body = match.groups()
            marker_time = _first_marker_time(body)
            body = CONTENT_MARKER_RE.sub("", body)
            body = SCREENSHOT_MARKER_RE.sub("", body)
            body = " ".join(body.split())
            if marker_time and not _has_timestamp(body):
                body = f"{marker_time} {body}".strip()
            lines.append(f"{prefix}{body}".rstrip())
            continue
        marker_time = _first_marker_time(line)
        line = CONTENT_MARKER_RE.sub("", line)
        line = SCREENSHOT_MARKER_RE.sub("", line)
        line = _cleanup_note_text(line)
        if marker_time and line and not _has_timestamp(line):
            line = _prepend_time_label(line, marker_time)
        lines.append(line.rstrip())
    return _cleanup_note_text("\n".join(lines))


def _prepend_time_label(line: str, label: str) -> str:
    value = str(line or "").strip()
    label = str(label or "").strip()
    if not value or not label:
        return value
    bullet = re.match(r"^(\s*(?:[-*+]|\d+[.)、])\s+)(.*)$", value)
    if bullet:
        return f"{bullet.group(1)}`{label}` {bullet.group(2).strip()}".rstrip()
    quote = re.match(r"^(\s*>\s*)(.*)$", value)
    if quote:
        return f"{quote.group(1)}{label} {quote.group(2).strip()}".rstrip()
    return f"{label} {value}".rstrip()


def _first_marker_time(text: str) -> str:
    value = str(text or "")
    for pattern in (CONTENT_MARKER_RE, SCREENSHOT_MARKER_RE):
        match = pattern.search(value)
        if not match:
            continue
        minute = match.group(1) or match.group(3) or "00"
        second = match.group(2) or match.group(4) or "00"
        return f"{minute}:{second}"
    return ""


def _tags_text(metadata: dict[str, Any]) -> str:
    value = metadata.get("tags") or metadata.get("tag") or ""
    if isinstance(value, (list, tuple, set)):
        text = "、".join(_compact(item, 40) for item in value if _compact(item, 40))
        return text or "无"
    return _compact(value, 200) or "无"


def _segment_text(value: Any, transcript: str) -> str:
    segments: list[str] = []
    if isinstance(value, list):
        for item in value[:NOTE_SEGMENT_LIMIT]:
            if not isinstance(item, dict):
                continue
            text = _compact(item.get("text"), 500)
            if not text:
                continue
            segments.append(f"{_format_time(item.get('start'))} - {text}")
    if segments:
        return "\n".join(segments)
    transcript = str(transcript or "").strip()
    if transcript:
        return f"00:00 - {transcript}"
    return "00:00 - （没有可用转写）"


def _timeline_items(insight: SightInsight, *, limit: int = 8) -> list[str]:
    metadata = dict(getattr(insight, "metadata", None) or {})
    values = metadata.get("transcript_segments")
    items: list[str] = []
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, dict):
                continue
            text = _compact(item.get("text"), 90)
            if not text:
                continue
            items.append(f"- `{_format_time(item.get('start'))}` {text}")
            if len(items) >= max(1, int(limit or 8)):
                break
    if items:
        return items
    for note in list(getattr(insight, "frame_notes", None) or [])[: max(1, int(limit or 8))]:
        text = str(note or "").strip()
        if text:
            items.append(f"- {text}")
    return items


def _has_timestamp(text: str) -> bool:
    value = str(text or "")
    for index, char in enumerate(value):
        if char != ":":
            continue
        left = value[max(0, index - 2) : index]
        right = value[index + 1 : index + 3]
        if len(left) == 2 and len(right) == 2 and left.isdigit() and right.isdigit():
            return True
    return False


def _ensure_timeline_section(insight: SightInsight, markdown: str) -> str:
    value = str(markdown or "").strip()
    if _has_timestamp(value):
        return value
    items = _timeline_items(insight)
    if not items:
        return value
    return "\n\n".join((value, "## 时间线", "\n".join(items))).strip()


def _ensure_frame_references(insight: SightInsight, markdown: str) -> str:
    frames = _frame_assets(insight)
    if not frames:
        return str(markdown or "").strip()
    value = _remove_frame_image_lines(str(markdown or "").strip(), frames)
    inserted: set[str] = set()
    output: list[str] = []
    for line in value.splitlines():
        output.append(line)
        label = _line_time_label(line)
        if not label:
            continue
        frame = _nearest_frame(frames, label)
        if not frame:
            continue
        path = str(frame.get("path") or "")
        image = _frame_markdown(frame)
        if image and path not in inserted:
            output.extend(["", image])
            inserted.add(path)
    if inserted:
        return "\n".join(output).strip()
    return value


def _remove_frame_image_lines(markdown: str, frames: list[dict[str, Any]]) -> str:
    tokens = _frame_path_tokens(frames)
    if not tokens:
        return str(markdown or "").strip()
    output: list[str] = []
    for line in str(markdown or "").splitlines():
        if _is_frame_image_line(line, tokens):
            continue
        output.append(line)
    return "\n".join(output).strip()


def _frame_path_tokens(frames: list[dict[str, Any]]) -> set[str]:
    tokens: set[str] = set()
    for frame in frames:
        path = str(frame.get("path") or "").strip()
        if not path:
            continue
        tokens.add(path)
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name:
            tokens.add(name)
    return tokens


def _is_frame_image_line(line: str, tokens: set[str]) -> bool:
    value = str(line or "").strip()
    if not value.startswith("![") or "](" not in value:
        return False
    return any(token and token in value for token in tokens)


def _frame_text(notes: list[str]) -> str:
    values = [f"- {item}" for item in notes[:NOTE_FRAME_LIMIT] if str(item or "").strip()]
    return "\n".join(values) if values else "（没有可确认的画面时间线）"


def _frame_assets(insight: SightInsight) -> list[dict[str, Any]]:
    return _normalize_frame_assets(dict(getattr(insight, "metadata", None) or {}).get("frames"))


def _normalize_frame_assets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        label = str(item.get("label") or "").strip()
        second = _float_second(item.get("second"))
        if not label:
            label = _format_time(second)
        result.append(
            {
                "path": path,
                "label": label,
                "second": second,
                "note": _compact(item.get("note"), 160),
            }
        )
    result.sort(key=lambda item: float(item.get("second") or 0.0))
    return result


def _frame_markdown(frame: dict[str, Any]) -> str:
    path = str(frame.get("path") or "").strip()
    if not path:
        return ""
    label = str(frame.get("label") or _format_time(frame.get("second"))).strip()
    return f"![{label} 关键帧]({path})"


def _time_label_at(text: str, colon_index: int) -> str:
    if colon_index <= 0 or colon_index >= len(text) - 1:
        return ""

    left_start = colon_index
    while left_start > 0 and text[left_start - 1].isdigit():
        left_start -= 1
    left = text[left_start:colon_index]
    if len(left) not in (1, 2):
        return ""
    if left_start > 0 and text[left_start - 1].isalnum():
        return ""

    right_start = colon_index + 1
    right_end = right_start
    while right_end < len(text) and text[right_end].isdigit():
        right_end += 1
    right = text[right_start:right_end]
    if len(right) != 2:
        return ""
    if right_end < len(text) and text[right_end].isalnum():
        return ""

    if right_end < len(text) and text[right_end] == ":":
        third_start = right_end + 1
        third_end = third_start
        while third_end < len(text) and text[third_end].isdigit():
            third_end += 1
        third = text[third_start:third_end]
        if len(third) != 2:
            return ""
        if third_end < len(text) and text[third_end].isalnum():
            return ""
        return f"{left}:{right}:{third}"

    return f"{left}:{right}"


def _heading_time_label(line: str) -> str:
    text = str(line or "")
    for index, char in enumerate(text):
        if char != ":":
            continue
        label = _time_label_at(text, index)
        if label:
            return label
    return ""


def _line_time_label(line: str) -> str:
    return _heading_time_label(str(line or "").strip())


def _nearest_frame(frames: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    if not frames:
        return None
    target = _label_seconds(label)
    if target is None:
        return frames[0]
    best = min(frames, key=lambda frame: abs(float(frame.get("second") or 0.0) - target))
    return best if abs(float(best.get("second") or 0.0) - target) <= 45 else None


def _label_seconds(label: str) -> float | None:
    parts = str(label or "").strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (TypeError, ValueError):
        return None
    return None


def _float_second(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _format_time(value: Any) -> str:
    try:
        total = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
