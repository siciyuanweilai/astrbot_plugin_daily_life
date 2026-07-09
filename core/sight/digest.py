from __future__ import annotations

from typing import Any

from ..life.tools import extract_json_from_text
from ..prompts import cache_friendly_prompt
from .clip import SightClip, SightInsight


VIDEO_ANSWER_BOUNDARY_RULE = (
    "后续回答规则：如果用户只是问视频讲什么、画面是什么或发生了什么，直接基于以上视频理解回答；"
    "不要因为字幕、水印、标题或画面线索再调用联网搜索。"
    "只有用户明确追问出处、原视频、作者、链接、背景核验或站外资料时，才考虑搜索。"
)

DETAIL_SKIP_PREFIXES = (
    "完整文字来源：",
    "文字内容预览：",
    "画面内容来源：",
    "笔记摘要来源：",
    "标题：",
)
DETAIL_STRIP_PREFIXES = ("音频主线：",)


def frame_prompt(index: int, total: int, clip: SightClip, label: str = "") -> str:
    fixed = (
        "请从第一人称生活聊天视角理解这个视频抽样画面。\n"
        "只描述画面里能直接看到的事实、文字、人物动作、场景和氛围。\n"
        "不要编造声音、前后剧情或看不见的信息。\n"
        '只输出 JSON：{"summary":"这一帧的可见内容，12-50字","details":["可见细节1","可见细节2"]}'
    )
    dynamic = f"抽样帧：第 {index}/{total} 个\n时间点：{label or '未知'}"
    return cache_friendly_prompt(fixed, dynamic, dynamic_title="画面帧")


def _clean_list(value: Any, limit: int = 6) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: list[str] = []
    for item in values:
        text = " ".join(str(item or "").split())
        if text and text not in result:
            result.append(text[:120])
        if len(result) >= limit:
            break
    return result


def frame_note_from_text(text: str) -> str:
    payload = extract_json_from_text(text)
    if isinstance(payload, dict):
        summary = " ".join(str(payload.get("summary") or "").split())
        details = _clean_list(payload.get("details"), limit=3)
        if summary and details:
            return f"{summary}（{'；'.join(details)}）"
        if summary:
            return summary
        if details:
            return "；".join(details)
    return " ".join(str(text or "").split())[:160]


def insight_from_notes(
    clip: SightClip,
    frame_notes: list[str],
    *,
    transcript: str = "",
    transcript_source: str = "",
    note: str = "",
    note_source: str = "",
    note_details: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    source_note: str = "",
    error: str = "",
) -> SightInsight:
    notes = _clean_list(frame_notes, limit=6)
    brief_details = _clean_list(note_details or [], limit=5)
    transcript = " ".join(str(transcript or "").split())[:8000]
    note = str(note or "").strip()[:8000]
    metadata = dict(metadata or {})
    title = " ".join(str(metadata.get("title") or "").split())
    if note:
        summary = " ".join(note.split())[:220]
        status = "ready"
    elif transcript:
        prefix = f"{title}：" if title else ""
        summary = f"{prefix}{transcript[:220]}"
        status = "ready"
    elif notes:
        summary = "；".join(notes[:3])[:220]
        status = "ready"
    else:
        summary = "已收到视频，但暂时没有可确认的内容信息。"
        status = "failed"
    details = list(brief_details) + list(notes)
    if note and note != summary:
        details.insert(0, note[:160])
    if title:
        details.insert(0, f"标题：{title[:120]}")
    return SightInsight(
        clip=clip,
        summary=summary,
        details=_clean_list(details, limit=8),
        frame_notes=notes,
        transcript=transcript,
        transcript_source=transcript_source,
        note=note,
        note_source=note_source,
        metadata=metadata,
        source_note=source_note,
        status=status,
        error=error,
    )


def tool_result_text(insight: SightInsight) -> str:
    if insight.status == "failed":
        detail = insight.error or insight.summary or "没有拿到可确认的视频内容"
        return f"视频理解失败：{detail}"
    details = "；".join(content_details(insight.details, limit=3))
    if details and details != insight.summary:
        return f"视频理解完成：{insight.summary}\n内容要点：{details}\n{VIDEO_ANSWER_BOUNDARY_RULE}"
    return f"视频理解完成：{insight.summary}\n{VIDEO_ANSWER_BOUNDARY_RULE}"


def content_details(values: list[str], *, limit: int = 3) -> list[str]:
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if not text:
            continue
        if any(text.startswith(prefix) for prefix in DETAIL_SKIP_PREFIXES):
            continue
        for prefix in DETAIL_STRIP_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result
