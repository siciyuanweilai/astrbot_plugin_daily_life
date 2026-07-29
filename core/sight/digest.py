from __future__ import annotations

from typing import Any

from ..life.tools import extract_json_from_text
from ..prompts import cache_friendly_prompt
from ..outcome import ToolResultText
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
        "请提取这个视频抽样画面中的可复核视觉证据。\n"
        "只描述画面里能直接看到的事实、屏幕文字、人物动作、场景、图表和状态变化。\n"
        "不要编造声音、前后剧情或看不见的信息。\n"
        '只输出 JSON：{"summary":"这一帧的可见内容，12-50字","details":["可见细节1","可见细节2"]}'
    )
    dynamic = f"抽样帧：第 {index}/{total} 个\n时间点：{label or '未知'}"
    return cache_friendly_prompt(fixed, dynamic, dynamic_title="画面帧")


def batch_frame_prompt(frames: list[tuple[int, str]], clip: SightClip) -> str:
    fixed = (
        "请按输入顺序分析这一组视频关键画面，提取可复核视觉证据。\n"
        "逐帧记录直接可见的事实、屏幕文字、人物动作、场景、图表和相对上一帧的变化。\n"
        "不要编造声音、镜头之间没有证据的剧情或看不见的信息。\n"
        "frames 必须使用给出的 index，一张画面对应一项；没有有效信息时仍保留该 index 并简要说明。\n"
        '只输出 JSON：{"frames":[{"index":1,"summary":"可见内容，12-60字",'
        '"details":["可见细节1","可见细节2"]}]}'
    )
    timeline = "\n".join(
        f"- index={index}；时间点={label or '未知'}" for index, label in frames
    )
    subject = str(clip.name or clip.metadata.get("title") or "").strip()
    dynamic = f"视频：{subject or '未命名视频'}\n关键画面清单：\n{timeline}"
    return cache_friendly_prompt(fixed, dynamic, dynamic_title="关键画面")


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


def batch_frame_notes_from_text(text: str) -> dict[int, str]:
    payload = extract_json_from_text(text)
    values = payload.get("frames") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return {}
    result: dict[int, str] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index") or 0)
        except (TypeError, ValueError):
            continue
        if index <= 0:
            continue
        summary = " ".join(str(item.get("summary") or "").split())
        details = _clean_list(item.get("details"), limit=3)
        if summary and details:
            result[index] = f"{summary}（{'；'.join(details)}）"
        elif summary:
            result[index] = summary
        elif details:
            result[index] = "；".join(details)
    return result


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
    notes = _clean_list(frame_notes, limit=24)
    brief_details = _clean_list(note_details or [], limit=5)
    transcript = " ".join(str(transcript or "").split())[:60000]
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
        return ToolResultText(
            f"视频理解失败：{detail}", status="failed", media="video_understanding"
        )
    details = "；".join(content_details(insight.details, limit=3))
    if details and details != insight.summary:
        text = f"视频理解完成：{insight.summary}\n内容要点：{details}\n{VIDEO_ANSWER_BOUNDARY_RULE}"
    else:
        text = f"视频理解完成：{insight.summary}\n{VIDEO_ANSWER_BOUNDARY_RULE}"
    return ToolResultText(text, status="ok", media="video_understanding")


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
