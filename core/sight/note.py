from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..life.tools import extract_json_from_text
from ..prompts import cache_friendly_prompt
from ..runtime.markers import LOG_PREFIX
from .clip import SightInsight
from .digest import content_details
from .provider import get_sight_provider, sight_provider_id
from .sample import sight_cache_dir


NOTE_TRANSCRIPT_CHARS = 20000
NOTE_FRAME_LIMIT = 24
NOTE_SEGMENT_LIMIT = 2000
NOTE_CHECKPOINT_DIR = "professional_notes"
PROFESSIONAL_CLOSING_TITLE = "总结与参考建议"
PROFESSIONAL_NOTE_CACHE_SCHEMA = "professional_structured_note"
PROFESSIONAL_NOTE_NEEDS_EVIDENCE = "没有可用音频转写或画面证据，无法生成专业总结"

CONTENT_MARKER_RE = re.compile(
    r"\*?Content-(?:\[(\d{1,2}):(\d{2})\]|(\d{1,2}):(\d{2}))\*?"
)
SCREENSHOT_MARKER_RE = re.compile(
    r"\*?Screenshot-(?:\[(\d{1,2}):(\d{2})\]|(\d{1,2}):(\d{2}))\*?"
)
HEADING_LINE_RE = re.compile(r"^(\s{0,3}#{1,6}\s*)(.*)$")
TIME_LABEL_RE = re.compile(r"(?<![^\W_])\d{1,2}:\d{2}(?::\d{2})?(?![^\W_])")
PROFESSIONAL_ROLE_SCHEMA: tuple[tuple[str, str, str], ...] = (
    ("overview", "背景概述", "背景、主题、对象与上下文"),
    ("core", "核心论点", "核心问题、主要判断与论证方向"),
    ("fact", "关键事实", "事件、观点、案例与可确认结论"),
    ("data", "数据支撑", "数字、统计、比例、金额与实验结果"),
    ("analysis", "分析与影响", "原因、比较、影响与逻辑关系"),
    ("risk", "争议与风险", "争议、不确定性、限制与潜在风险"),
    ("suggestion", "总结与参考建议", "全片总结、观看后的启发与参考意见"),
    ("other", "其他", "无法归类但确有保留价值的内容"),
)
PROFESSIONAL_ROLE_TITLES = {
    role: title for role, title, _ in PROFESSIONAL_ROLE_SCHEMA if role != "other"
}
PROFESSIONAL_ROLE_ENUM = "/".join(role for role, _, _ in PROFESSIONAL_ROLE_SCHEMA)
PROFESSIONAL_ROLE_SCHEMA_TEXT = "\n".join(
    f"  - {role}：{title}；{description}"
    for role, title, description in PROFESSIONAL_ROLE_SCHEMA
)


BASE_NOTE_PROMPT = """\
你是专业的视频总结助手，负责把视频转录和可确认的画面整理成清晰、完整、可复核的中文总结。

仅返回 JSON 对象本体，不要代码块、解释或额外字段：
{
  "sections": [
    {
      "role": "overview/core/fact/data/analysis/risk/suggestion/other，可空",
      "start": "00:00",
      "end": "00:25",
      "title": "简短、具体的章节标题",
      "paragraphs": ["背景或核心判断，可空"],
      "bullets": ["事实、依据、比较、影响或建议，可空"],
      "quotes": ["关键原话或结论，可空"]
    }
  ]
}

要求：
- 使用中文；专有名词、技术术语、品牌和人名可保留英文。
- role 从 JSON 中列出的枚举选择，没有合适类型时使用 other 或留空；role 只用于组织，不作为标题。
- start/end 使用 mm:ss 或 hh:mm:ss，覆盖章节使用的素材范围，start 不得晚于 end。
- “总结与参考建议”是针对全片的收束内容，不对应单一时间点；该 section 的 start/end 必须留空字符串。
- title 应简短、具体并反映实际议题，通常不超过 20 个汉字，不使用 role 名称或“视频内容、相关介绍”等通用标题。
- paragraphs 承载概述或判断，bullets 承载事实、数据、案例、比较、影响或建议，quotes 只保留素材中的关键原话；没有依据的字段留空。
- paragraphs 和 bullets 可用 `**...**` 少量标记核心概念、关键数据和结论；每项通常标记 1-2 处，不要加粗整句或全部文字。
- sections 根据素材主题自然划分，不逐句复述，也不把有足够内容的独立主题强行合并；最终按素材时间组织。
- 只依据转录和可确认画面归纳。作者观点写明“视频认为”或“作者主张”，不得补充素材外事实或把推测写成结论。
- 关键画面只辅助确认内容，不编造声音、前后剧情或画面外信息。
- 彻底省略广告、推广口播、填充词、寒暄和无关内容；当前时间窗只有这些内容时返回空的 sections，不解释省略原因。
- 保留重要人物、数字、案例、条件、结论和建议；数学公式使用 LaTeX。
"""


NOTE_STYLES: dict[str, str] = {
    "concise": (
        "**简洁模式**：仅提取核心观点和关键结论，每个章节用简短的要点概括。"
        "省略细节和举例，只保留最重要的信息。整体控制在 5-8 个要点以内。"
    ),
    "detailed": (
        "**详细模式**：完整记录视频内容，每个部分都包含详细讨论。"
        "保留重要的例子、数据和论证过程。每个板块内使用列表和引用块组织信息，"
        "需要尽可能多地记录视频内容。"
    ),
    "professional": (
        "**专业模式**：提供完整、正式的结构化总结。根据素材自然划分具体章节，重点整理背景、核心观点、"
        "事实与数据、依据或机制、比较与影响、结论与建议，只使用素材实际包含的部分。"
        "每节先用简洁段落说明主要内容，再用“**短标签**：具体说明”的要点展开事实、案例和关系；"
        "短标签两侧的 `**` 必须保留，以便在成品中形成重点色，必要时保留一条关键原话。"
        "标题应具体反映实际议题，不使用“概述、核心内容、相关介绍”等通用标题；保留人物、数字、案例、条件和结论。"
        "内容适合展开时，通常使用 1 个简洁段落、2-5 个要点和至多 1 条引用；这是排版倾向，实际数量由素材决定。"
        "最后一个 section 固定为 role=suggestion、title=“总结与参考建议”：paragraphs 概括全片核心内容，"
        "bullets 给出看完整体内容后的参考建议。这里的建议面向读者理解、判断或后续关注，不要求写成行动清单，"
        "也不要求由视频作者明确提出；可以基于素材归纳，但要明确写成参考意见，不得冒充作者原话、引入新事实或替代专业判断。"
    ),
}


def professional_note_prompt_key(style: str = "professional") -> str:
    style_key = _note_style_key(style)
    prompt = "\n".join(
        (BASE_NOTE_PROMPT, NOTE_STYLES.get(style_key, NOTE_STYLES["professional"]))
    )
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class SightNote:
    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def compose(
        self, insight: SightInsight, *, style: str = "professional"
    ) -> str:
        started = time.monotonic()
        style_key = _note_style_key(style)
        unavailable = professional_note_unavailable_reason(insight, style=style_key)
        if unavailable:
            raise SightNoteError(unavailable)

        (
            composer,
            call_llm,
            provider,
            provider_id,
            max_chars,
        ) = await self._composition_dependencies()
        segments = _transcript_segments(insight)
        chunks = _evidence_chunks(segments, max_chars=max_chars)
        checkpoint_path = _note_checkpoint_path(
            self.runtime,
            insight,
            provider_id=provider_id,
            style=style_key,
        )
        checkpoint_key = _note_checkpoint_key(insight, segments, style=style_key)
        checkpoint = await asyncio.to_thread(_load_note_checkpoint, checkpoint_path)
        partials = _checkpoint_partials(
            checkpoint,
            checkpoint_key=checkpoint_key,
            provider_id=provider_id,
            total_chunks=len(chunks),
        )
        model_calls = 0
        format_retries = 0
        input_chars = 0
        output_chars = 0
        for index in range(len(partials), len(chunks)):
            chunk = chunks[index]
            prompt = self._prompt(
                insight,
                style=style,
                max_transcript_chars=max_chars,
                segments=chunk,
                chunk_index=index,
                chunk_total=len(chunks),
            )
            payload: dict[str, Any] | None = None
            failure_detail = ""
            for attempt in range(2):
                current_prompt = prompt
                if attempt:
                    format_retries += 1
                    current_prompt = (
                        f"{prompt}\n\n上次返回格式无效。请重新生成，且只输出包含 "
                        '"sections" 数组的 JSON 对象；不要输出解释或代码块。'
                    )
                model_calls += 1
                input_chars += len(current_prompt)
                try:
                    raw = await self._call_summary_model(
                        composer,
                        call_llm,
                        provider,
                        provider_id,
                        current_prompt,
                    )
                except SightNoteError as exc:
                    failure_detail = failure_detail or str(exc)
                    if attempt == 0:
                        continue
                    raise SightNoteError(failure_detail) from exc
                output_chars += len(raw)
                candidate = extract_json_from_text(raw)
                if _valid_note_payload(candidate):
                    payload = candidate
                    break
                failure_detail = "总结模型返回的 JSON 无法解析"
            if payload is None:
                raise SightNoteError(failure_detail or "总结模型返回的 JSON 无法解析")
            payload = _payload_with_window_defaults(payload, chunk)
            partials.append(payload)
            await asyncio.to_thread(
                _save_note_checkpoint,
                checkpoint_path,
                _note_checkpoint_payload(
                    checkpoint_key,
                    provider_id,
                    total_chunks=len(chunks),
                    partials=partials,
                ),
            )

        sections = _merge_payload_sections(insight, partials, style=style_key)
        payload: dict[str, Any] = {"sections": sections}
        markdown = _payload_markdown(insight, payload, style=style_key)
        if not markdown:
            raise SightNoteError("总结模型返回的 JSON 缺少有效总结内容")
        result = self.normalize(
            insight,
            markdown,
            include_frames=style_key != "professional",
        )
        metadata = dict(insight.metadata or {})
        metrics = dict(metadata.get("sight_metrics") or {})
        metrics["professional_note_seconds"] = round(time.monotonic() - started, 3)
        metrics["professional_input_chars"] = input_chars
        metrics["professional_output_chars"] = output_chars
        metrics["professional_model_calls"] = model_calls
        metrics["professional_format_retries"] = format_retries
        metrics["professional_chunk_count"] = len(chunks)
        metrics["professional_empty_chunk_count"] = sum(
            1
            for item in partials
            if not _payload_sections(insight, item, style=style_key)
        )
        metrics["professional_section_count"] = len(sections)
        metadata["sight_metrics"] = metrics
        insight.metadata = metadata
        await asyncio.to_thread(_remove_note_checkpoint, checkpoint_path)
        return result

    async def _composition_dependencies(self) -> tuple[Any, Any, Any, str, int]:
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
        max_chars = max(
            2000,
            int(
                getattr(settings, "note_max_transcript_chars", NOTE_TRANSCRIPT_CHARS)
                or NOTE_TRANSCRIPT_CHARS
            ),
        )
        return composer, call_llm, provider, provider_id, max_chars

    @staticmethod
    async def _call_summary_model(
        composer: Any,
        call_llm: Any,
        provider: Any,
        provider_id: str,
        prompt: str,
    ) -> str:
        session_id = f"daily_life_sight_note_{uuid.uuid4().hex[:8]}"
        try:
            if provider_id:
                try:
                    text = await call_llm(
                        provider,
                        prompt,
                        session_id,
                        empty_retries=0,
                        primary_provider_id=provider_id,
                    )
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
        return raw

    @staticmethod
    def normalize(
        insight: SightInsight,
        markdown: str,
        *,
        include_frames: bool = True,
    ) -> str:
        return normalize_note_markdown(
            insight,
            markdown,
            include_frames=include_frames,
        )

    @staticmethod
    def _prompt(
        insight: SightInsight,
        *,
        style: str,
        max_transcript_chars: int,
        segments: list[dict[str, Any]] | None = None,
        chunk_index: int = 0,
        chunk_total: int = 1,
    ) -> str:
        metadata = dict(insight.metadata or {})
        title, author = _title_parts(metadata, getattr(insight.clip, "name", ""))
        duration = _compact(metadata.get("duration") or "", 40)
        resolved_segments = (
            list(segments) if segments is not None else _transcript_segments(insight)
        )
        segment_text = _segment_text(
            resolved_segments,
            " ".join(str(insight.transcript or "").split()),
            limit=max_transcript_chars,
        )
        window = _segment_window(resolved_segments)
        frame_text = _frame_text_for_window(insight, window)
        style_key = _note_style_key(style)
        fixed = "\n".join(
            (BASE_NOTE_PROMPT, NOTE_STYLES.get(style_key, NOTE_STYLES["professional"]))
        )
        task = ""
        if chunk_total > 1:
            task = (
                f"这是连续时间窗 {chunk_index + 1}/{chunk_total}；只整理本窗口，"
                "保留本窗口全部有效内容；若本窗口只有应省略内容，返回空的 sections。\n"
            )
        dynamic = (
            f"{task}视频标题：\n{title}\n\n"
            f"视频作者：\n{author or '未获取'}\n\n"
            f"视频标签：\n{_tags_text(metadata)}\n\n"
            f"视频时长：\n{duration or '未知'}\n\n"
            "视频分段（格式：开始时间-结束时间 - 内容）：\n\n"
            "---\n"
            f"{segment_text}\n"
            "---\n\n"
            f"关键画面辅助证据：\n{frame_text}\n\n"
            "请直接输出符合格式要求的 JSON 对象。"
        )
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="视频内容")


def _valid_note_payload(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("sections"), list)


def _transcript_segments(insight: SightInsight) -> list[dict[str, Any]]:
    metadata = dict(getattr(insight, "metadata", None) or {})
    values = metadata.get("transcript_segments")
    result: list[dict[str, Any]] = []
    if isinstance(values, list):
        for item in values[:NOTE_SEGMENT_LIMIT]:
            if not isinstance(item, dict):
                continue
            text = " ".join(str(item.get("text") or "").split())
            if not text:
                continue
            start = _float_second(item.get("start"))
            end = max(start, _float_second(item.get("end")))
            result.append({"start": start, "end": end, "text": text})
    if result:
        return sorted(result, key=lambda item: (item["start"], item["end"]))
    transcript = " ".join(str(getattr(insight, "transcript", "") or "").split())
    if not transcript:
        return []
    first_label = _heading_time_label(transcript)
    start = float(_label_seconds(first_label) or 0.0)
    duration = _duration_seconds(metadata.get("duration"))
    end = max(start, duration)
    return [{"start": start, "end": end, "text": transcript}]


def _evidence_chunks(
    segments: list[dict[str, Any]], *, max_chars: int
) -> list[list[dict[str, Any]]]:
    if not segments:
        return [[]]
    limit = max(2000, int(max_chars or NOTE_TRANSCRIPT_CHARS))
    expanded: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "")
        if len(text) <= limit:
            expanded.append(segment)
            continue
        start = _float_second(segment.get("start"))
        end = max(start, _float_second(segment.get("end")))
        pieces = [text[index : index + limit] for index in range(0, len(text), limit)]
        span = max(0.0, end - start)
        for index, piece in enumerate(pieces):
            piece_start = start + span * index / len(pieces)
            piece_end = start + span * (index + 1) / len(pieces)
            expanded.append({"start": piece_start, "end": piece_end, "text": piece})

    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    used = 0
    for segment in expanded:
        size = len(str(segment.get("text") or "")) + 32
        if current and used + size > limit:
            chunks.append(current)
            current = []
            used = 0
        current.append(segment)
        used += size
    if current:
        chunks.append(current)
    return chunks or [[]]


def _segment_window(
    segments: list[dict[str, Any]],
) -> tuple[float, float] | None:
    if not segments:
        return None
    return (
        min(_float_second(item.get("start")) for item in segments),
        max(_float_second(item.get("end")) for item in segments),
    )


def _frame_text_for_window(
    insight: SightInsight, window: tuple[float, float] | None
) -> str:
    frames = _frame_assets(insight)
    notes: list[str] = []
    if frames:
        for frame in frames:
            second = _float_second(frame.get("second"))
            if window and not (window[0] - 15 <= second <= window[1] + 15):
                continue
            note = _compact(frame.get("note"), 300)
            if note:
                notes.append(f"{_format_time(second)}：{note}")
    if not notes and window is None:
        notes = [str(item or "").strip() for item in insight.frame_notes]
    return _frame_text(notes)


def _payload_with_window_defaults(
    payload: dict[str, Any], segments: list[dict[str, Any]]
) -> dict[str, Any]:
    result = dict(payload)
    values = [
        dict(item)
        for item in _as_list(payload.get("sections"))
        if isinstance(item, dict)
    ]
    if not values:
        return result
    window = _segment_window(segments)
    if window is None:
        result["sections"] = values
        return result
    start, end = window
    span = max(0.0, end - start)
    for index, item in enumerate(values):
        inferred_time_window = (
            "start" not in item
            and "end" not in item
            and any(
                key in item
                for key in (
                    "time",
                    "timestamp",
                    "paragraphs",
                    "bullets",
                    "claims",
                    "evidence",
                    "quotes",
                    "uncertainties",
                )
            )
        )
        if inferred_time_window:
            item["_coverage_start"] = _format_time(start + span * index / len(values))
            item["_coverage_end"] = _format_time(
                start + span * (index + 1) / len(values)
            )
            continue
        if not _payload_time(item.get("start") or item.get("time")):
            item["start"] = _format_time(start + span * index / len(values))
        if not _payload_time(item.get("end")):
            item["end"] = _format_time(start + span * (index + 1) / len(values))
    result["sections"] = values
    return result


def _merge_payload_sections(
    insight: SightInsight,
    payloads: list[dict[str, Any]],
    *,
    style: str,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for payload in payloads:
        sections.extend(_payload_sections(insight, payload, style=style))
    merged = _merge_sections([], sections, style=style)
    if _note_style_key(style) == "professional":
        return _ensure_professional_closing_section(merged)
    return merged


def _merge_sections(
    current: list[dict[str, Any]],
    additions: list[dict[str, Any]],
    *,
    style: str = "",
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in [*current, *additions]:
        if not _section_has_content(section):
            continue
        section = _normalize_section_range(section)
        content = _section_content(section)
        key = re.sub(r"\W+", "", f"{section.get('title', '')}{content}").lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(section)
    result.sort(key=lambda item: (_section_bounds(item) or (float("inf"), 0))[0])
    result = _coalesce_same_start_sections(result)
    if not (bool(style) and _note_style_key(style) == "professional"):
        for index, section in enumerate(result[:-1]):
            bounds = _section_bounds(section)
            next_bounds = _section_bounds(result[index + 1])
            if not bounds or not next_bounds:
                continue
            if bounds[1] > next_bounds[0] >= bounds[0]:
                section["end"] = _format_time(next_bounds[0])
    return result


def _ensure_professional_closing_section(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values = [dict(section) for section in sections if _section_has_content(section)]
    if not values:
        return []

    closing: list[dict[str, Any]] = []
    regular: list[dict[str, Any]] = []
    last_index = len(values) - 1
    for index, section in enumerate(values):
        title = _field_text(section.get("title"), 80)
        title_is_closing = "总结" in title and "建议" in title
        last_suggestion = (
            index == last_index and _section_role(section.get("role")) == "suggestion"
        )
        if title_is_closing or last_suggestion:
            closing.append(section)
        else:
            regular.append(section)

    final_section = (
        _merge_professional_closing_sections(closing)
        if closing
        else _professional_closing_fallback(regular)
    )
    return [*regular, final_section] if final_section else regular


def _merge_professional_closing_sections(
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(
        sections,
        key=lambda item: (_section_bounds(item) or (float("inf"), 0))[0],
    )
    paragraphs = _unique_section_values(ordered, "paragraphs", limit=3, char_limit=700)
    bullets = _unique_section_values(ordered, "bullets", limit=8, char_limit=280)
    quotes = _unique_section_values(ordered, "quotes", limit=2, char_limit=320)
    result = {
        "start": "",
        "end": "",
        "title": PROFESSIONAL_CLOSING_TITLE,
        "role": "suggestion",
        "time": "",
        "paragraphs": paragraphs,
        "bullets": bullets,
        "claims": [],
        "evidence": [],
        "quotes": quotes,
        "uncertainties": [],
    }
    result["content"] = _structured_section_content(result)
    return result


def _unique_section_values(
    sections: list[dict[str, Any]],
    field: str,
    *,
    limit: int,
    char_limit: int,
) -> list[str]:
    result: list[str] = []
    for section in sections:
        for value in _text_values(section.get(field), limit=limit, char_limit=char_limit):
            if value not in result:
                result.append(value)
            if len(result) >= limit:
                return result
    return result


def _professional_closing_fallback(
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    titles = [
        title
        for section in sections
        if (title := _field_text(section.get("title"), 40))
        and title not in {"概述", "其他"}
    ][:4]
    focus = "、".join(titles)
    paragraph = (
        f"视频主要围绕{focus}展开，相关事实、观点和影响已在上文分节整理。"
        if focus
        else "视频的主要内容、相关事实和观点已在上文分节整理。"
    )
    result = {
        "start": "",
        "end": "",
        "title": PROFESSIONAL_CLOSING_TITLE,
        "role": "suggestion",
        "time": "",
        "paragraphs": [paragraph],
        "bullets": [
            "**阅读重点**：结合视频中的事实、数据、案例和观点理解主要结论。",
            "**参考建议**：区分作者观点与已确认事实；涉及重要判断时，可结合原始内容和可靠来源进一步核实。",
        ],
        "claims": [],
        "evidence": [],
        "quotes": [],
        "uncertainties": [],
    }
    result["content"] = _structured_section_content(result)
    return result


def _coalesce_same_start_sections(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for section in sections:
        bounds = _section_bounds(section)
        previous = result[-1] if result else None
        previous_bounds = _section_bounds(previous) if previous else None
        if (
            not previous
            or not bounds
            or not previous_bounds
            or abs(bounds[0] - previous_bounds[0]) > 1
            or not _sections_share_topic(previous, section)
        ):
            result.append(section)
            continue
        previous_content = _section_content(previous)
        section_content = _section_content(section)
        subtitle = _field_text(section.get("title"), 80)
        previous_title = _field_text(previous.get("title"), 80)
        addition = section_content
        if subtitle and subtitle != previous_title:
            addition = f"### {subtitle}\n\n{section_content}"
        previous["content"] = _cleanup_note_text(
            "\n\n".join(item for item in (previous_content, addition) if item)
        )
        previous["end"] = _format_time(max(previous_bounds[1], bounds[1]))
    return result


def _sections_share_topic(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_title = re.sub(r"\W+", "", _field_text(first.get("title"), 80)).lower()
    second_title = re.sub(r"\W+", "", _field_text(second.get("title"), 80)).lower()
    if first_title and second_title:
        return first_title == second_title
    first_role = _section_role(first.get("role"))
    second_role = _section_role(second.get("role"))
    return first_role != "other" and first_role == second_role


def _normalize_section_range(section: dict[str, Any]) -> dict[str, Any]:
    result = dict(section)
    start_label = _payload_time(result.get("start") or result.get("time"))
    end_label = _payload_time(result.get("end")) or start_label
    start = _label_seconds(start_label)
    end = _label_seconds(end_label)
    if start is not None and end is not None and end < start:
        start, end = end, start
    if start is not None:
        result["start"] = _format_time(start)
        result["time"] = result["start"]
    if end is not None:
        result["end"] = _format_time(end)
    return result


def _section_bounds(section: dict[str, Any]) -> tuple[float, float] | None:
    start_label = _payload_time(
        section.get("_coverage_start") or section.get("start") or section.get("time")
    )
    end_label = (
        _payload_time(section.get("_coverage_end") or section.get("end")) or start_label
    )
    start = _label_seconds(start_label)
    end = _label_seconds(end_label)
    if start is None or end is None or end < start:
        return None
    return start, end


def _duration_seconds(value: Any) -> float:
    if isinstance(value, str) and ":" in value:
        return float(_label_seconds(value) or 0.0)
    return _float_second(value)


def _note_checkpoint_key(
    insight: SightInsight,
    segments: list[dict[str, Any]],
    *,
    style: str,
) -> str:
    metadata = dict(getattr(insight, "metadata", None) or {})
    payload = {
        "clip": getattr(getattr(insight, "clip", None), "key", ""),
        "style": style,
        "prompt_key": professional_note_prompt_key(style),
        "title": metadata.get("title"),
        "author": metadata.get("author") or metadata.get("uploader"),
        "segments": segments,
        "frames": list(getattr(insight, "frame_notes", None) or []),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _note_checkpoint_path(
    runtime: Any,
    insight: SightInsight,
    *,
    provider_id: str,
    style: str,
) -> Path:
    clip_key = str(getattr(getattr(insight, "clip", None), "key", "") or "unknown")
    digest = hashlib.sha256(
        f"{clip_key}|{provider_id}|{style}".encode("utf-8", errors="ignore")
    ).hexdigest()[:32]
    return (
        sight_cache_dir(getattr(runtime, "data_path", None))
        / NOTE_CHECKPOINT_DIR
        / f"{digest}.json"
    )


def _load_note_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _checkpoint_partials(
    payload: dict[str, Any],
    *,
    checkpoint_key: str,
    provider_id: str,
    total_chunks: int,
) -> list[dict[str, Any]]:
    if (
        payload.get("checkpoint_key") != checkpoint_key
        or str(payload.get("provider_id") or "") != provider_id
        or int(payload.get("total_chunks") or 0) != total_chunks
    ):
        return []
    values = [
        item for item in list(payload.get("partials") or []) if isinstance(item, dict)
    ]
    return values[:total_chunks]


def _note_checkpoint_payload(
    checkpoint_key: str,
    provider_id: str,
    *,
    total_chunks: int,
    partials: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "checkpoint_key": checkpoint_key,
        "provider_id": provider_id,
        "total_chunks": total_chunks,
        "next_chunk": len(partials),
        "partials": partials,
        "updated_at": int(time.time()),
    }


def _save_note_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _remove_note_checkpoint(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug(f"{LOG_PREFIX} 视频专业总结断点清理失败：{exc}")


class SightNoteError(RuntimeError):
    pass


def professional_note_unavailable_reason(
    insight: SightInsight | None, *, style: str = "professional"
) -> str:
    if _note_style_key(style) != "professional":
        return ""
    if _has_transcript_content(insight) or bool(
        list(getattr(insight, "frame_notes", None) or [])
    ):
        return ""
    return PROFESSIONAL_NOTE_NEEDS_EVIDENCE


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


def _payload_markdown(
    insight: SightInsight, payload: Any, *, style: str = "professional"
) -> str:
    if not isinstance(payload, dict):
        return ""
    metadata = dict(getattr(insight, "metadata", None) or {})
    title, author = _title_parts(
        metadata, getattr(getattr(insight, "clip", None), "name", "")
    )
    sections = _payload_sections(insight, payload, style=style)
    if not sections:
        return ""
    if _note_style_key(style) == "professional":
        sections = _ensure_professional_closing_section(sections)
    lines = [_h1_title(title, author), ""]
    for section in sections:
        rendered = _render_payload_section(section, style=style)
        if rendered:
            if lines[-1] != "":
                lines.append("")
            lines.extend(rendered)
    return "\n".join(lines).strip()


def _payload_sections(
    insight: SightInsight, payload: dict[str, Any], *, style: str = "professional"
) -> list[dict[str, Any]]:
    del style
    sections = [
        _normalize_payload_section(item) for item in _as_list(payload.get("sections"))
    ]
    sections = [section for section in sections if _section_has_content(section)]
    if "sections" in payload:
        return sections

    summary = _field_text(payload.get("summary"), 700)
    bullets = _text_values(
        payload.get("bullets") or payload.get("points") or payload.get("details"),
        limit=10,
        char_limit=280,
    )
    if not bullets:
        bullets = content_details(getattr(insight, "details", None), limit=8)
    if not summary:
        summary = " ".join(str(getattr(insight, "summary", "") or "").split())[:700]
    if not summary and not bullets:
        return []
    return [
        {
            "start": "",
            "end": "",
            "title": "概述",
            "content": "\n\n".join(
                [*([summary] if summary else []), *[f"- {item}" for item in bullets]]
            ),
            "role": "other",
            "time": "",
            "paragraphs": [summary] if summary else [],
            "bullets": bullets,
            "claims": [],
            "evidence": [],
            "quotes": [],
            "uncertainties": [],
        }
    ]


def _normalize_payload_section(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {
            "start": _payload_time(value),
            "end": _payload_time(value),
            "title": "概述",
            "content": _markdown_content(value),
            "role": "other",
            "time": _payload_time(value),
            "paragraphs": [_field_text(value, 700)],
            "bullets": [],
            "claims": [],
            "evidence": [],
            "quotes": [],
            "uncertainties": [],
        }
    if not isinstance(value, dict):
        return {}
    raw_title = _field_text(
        value.get("title") or value.get("heading") or value.get("name"), 80
    )
    raw_time = (
        value.get("time") or value.get("timestamp") or value.get("start") or raw_title
    )
    start_label = _payload_time(value.get("start") or raw_time)
    end_label = _payload_time(value.get("end")) or start_label
    role = _section_role(value.get("role"))
    title = (
        _strip_title_time(raw_title)
        if raw_title
        else PROFESSIONAL_ROLE_TITLES.get(role, "") or "概述"
    )
    normalized = {
        "_coverage_start": _payload_time(value.get("_coverage_start")),
        "_coverage_end": _payload_time(value.get("_coverage_end")),
        "start": start_label,
        "end": end_label,
        "title": title,
        "role": role,
        "time": start_label,
        "paragraphs": _text_values(
            value.get("paragraphs")
            or value.get("paragraph")
            or value.get("content")
            or value.get("summary"),
            limit=5,
            char_limit=700,
        ),
        "bullets": _text_values(
            value.get("bullets")
            or value.get("points")
            or value.get("items")
            or value.get("details"),
            limit=12,
            char_limit=280,
        ),
        "claims": _text_values(
            value.get("claims") or value.get("claim"), limit=10, char_limit=320
        ),
        "evidence": _text_values(
            value.get("evidence") or value.get("sources"),
            limit=12,
            char_limit=360,
        ),
        "quotes": _text_values(
            value.get("quotes") or value.get("quote"), limit=4, char_limit=320
        ),
        "uncertainties": _text_values(
            value.get("uncertainties")
            or value.get("uncertainty")
            or value.get("limitations"),
            limit=6,
            char_limit=320,
        ),
    }
    content = _markdown_content(value.get("content"))
    normalized["content"] = content or _structured_section_content(normalized)
    return normalized


def _render_payload_section(
    section: dict[str, Any], *, style: str = "professional"
) -> list[str]:
    title = _section_render_title(section, style=style)
    is_professional_closing = (
        _note_style_key(style) == "professional"
        and _section_role(section.get("role")) == "suggestion"
        and title == PROFESSIONAL_CLOSING_TITLE
    )
    start_label = (
        ""
        if is_professional_closing
        else _payload_time(section.get("start") or section.get("time"))
    )
    lines = [f"## {title}", ""]
    if start_label:
        lines.extend([f"⏱ {start_label}", ""])
    content = _section_content(section)
    if content:
        lines.extend(content.splitlines())
        return lines
    for paragraph in _text_values(section.get("paragraphs"), limit=5, char_limit=700):
        lines.extend([paragraph, ""])
    for bullet in _text_values(section.get("bullets"), limit=12, char_limit=280):
        lines.append(f"- {bullet}")
    if section.get("bullets"):
        lines.append("")
    for claim in _text_values(section.get("claims"), limit=10, char_limit=320):
        lines.append(f"- **视频主张**：{claim}")
    if section.get("claims"):
        lines.append("")
    for evidence in _text_values(section.get("evidence"), limit=12, char_limit=360):
        lines.append(f"- **证据**：{evidence}")
    if section.get("evidence"):
        lines.append("")
    for quote in _text_values(section.get("quotes"), limit=4, char_limit=320):
        lines.append(f"> {quote}")
    if section.get("quotes"):
        lines.append("")
    for uncertainty in _text_values(
        section.get("uncertainties"), limit=6, char_limit=320
    ):
        lines.append(f"- **不确定性**：{uncertainty}")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _section_render_title(
    section: dict[str, Any], *, style: str = "professional"
) -> str:
    title = _field_text(section.get("title"), 80) or "概述"
    return title


def _section_role(value: Any) -> str:
    text = _field_text(value, 80).lower()
    return text if text in PROFESSIONAL_ROLE_TITLES or text == "other" else "other"


def _section_has_content(section: dict[str, Any]) -> bool:
    if not isinstance(section, dict):
        return False
    return bool(
        section.get("content")
        or section.get("paragraphs")
        or section.get("bullets")
        or section.get("claims")
        or section.get("evidence")
        or section.get("quotes")
        or section.get("uncertainties")
    )


def _section_content(section: dict[str, Any]) -> str:
    content = _markdown_content(section.get("content"))
    return content or _structured_section_content(section)


def _structured_section_content(section: dict[str, Any]) -> str:
    lines: list[str] = []
    for paragraph in _text_values(
        section.get("paragraphs"), limit=100, char_limit=10000
    ):
        if lines:
            lines.append("")
        lines.append(paragraph)
    for bullet in _text_values(section.get("bullets"), limit=100, char_limit=10000):
        lines.append(f"- {_emphasize_bullet_label(bullet)}")
    for claim in _text_values(section.get("claims"), limit=100, char_limit=10000):
        lines.append(f"- **视频主张**：{claim}")
    for evidence in _text_values(section.get("evidence"), limit=100, char_limit=10000):
        lines.append(f"- **证据**：{evidence}")
    for quote in _text_values(section.get("quotes"), limit=100, char_limit=10000):
        lines.append(f"> {quote}")
    for uncertainty in _text_values(
        section.get("uncertainties"), limit=100, char_limit=10000
    ):
        lines.append(f"- **不确定性**：{uncertainty}")
    return _cleanup_note_text("\n".join(lines))


def _emphasize_bullet_label(value: str) -> str:
    text = str(value or "").strip()
    candidates = [
        (position, separator)
        for separator in ("：", ":")
        if (position := text.find(separator)) > 0
    ]
    if not candidates:
        return text
    position, separator = min(candidates, key=lambda item: item[0])
    label = text[:position].strip()
    detail = text[position + len(separator) :].strip()
    if (
        not detail
        or len(label) > 18
        or label.isdigit()
        or any(mark in label for mark in "，。！？；\n")
        or (separator == ":" and detail.startswith("//"))
        or (label.startswith("**") and label.endswith("**"))
    ):
        return text
    return f"**{label}**：{detail}"


def _markdown_content(value: Any) -> str:
    if isinstance(value, list):
        value = "\n".join(str(item or "") for item in value)
    text = _strip_fence(str(value or "").strip())
    if not text:
        return ""
    text = _strip_html_comments(text)
    text = re.sub(r"^\s*#{1,2}\s+.*$", "", text, flags=re.MULTILINE)
    return _cleanup_note_text(text)


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
            item = (
                item.get("text")
                or item.get("content")
                or item.get("summary")
                or item.get("title")
                or ""
            )
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


def normalize_note_markdown(
    insight: SightInsight,
    markdown: str,
    *,
    include_frames: bool = True,
) -> str:
    value = _sanitize_note_markdown(markdown)
    value = _normalize_marker_annotations(value)
    metadata = dict(getattr(insight, "metadata", None) or {})
    title, author = _title_parts(
        metadata, getattr(getattr(insight, "clip", None), "name", "")
    )
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
    if include_frames:
        value = _ensure_frame_references(insight, value)
    return _cleanup_note_text(value)


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


def _segment_text(value: Any, transcript: str, *, limit: int) -> str:
    segments: list[str] = []
    used = 0
    if isinstance(value, list):
        for item in value[:NOTE_SEGMENT_LIMIT]:
            if not isinstance(item, dict):
                continue
            text = " ".join(str(item.get("text") or "").split())
            if not text:
                continue
            start = _format_time(item.get("start"))
            end = _format_time(item.get("end"))
            line = f"{start}-{end} - {text}"
            if segments and used + len(line) > max(1, int(limit or 1)):
                break
            segments.append(line)
            used += len(line)
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
    for note in list(getattr(insight, "frame_notes", None) or [])[
        : max(1, int(limit or 8))
    ]:
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
    values = [
        f"- {item}" for item in notes[:NOTE_FRAME_LIMIT] if str(item or "").strip()
    ]
    return "\n".join(values) if values else "（没有可确认的画面时间线）"


def _frame_assets(insight: SightInsight) -> list[dict[str, Any]]:
    return _normalize_frame_assets(
        dict(getattr(insight, "metadata", None) or {}).get("frames")
    )


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


def _heading_time_label(line: str) -> str:
    match = TIME_LABEL_RE.search(str(line or ""))
    return match.group(0) if match else ""


def _line_time_label(line: str) -> str:
    return _heading_time_label(str(line or "").strip())


def _nearest_frame(frames: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    if not frames:
        return None
    target = _label_seconds(label)
    if target is None:
        return frames[0]
    best = min(
        frames, key=lambda frame: abs(float(frame.get("second") or 0.0) - target)
    )
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
