"""Resolve GIF options from the user's request, independently of tool guesses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from ...config.options import IMAGE_ASPECT_RATIOS
from ...prompts import cache_friendly_prompt
from ..markers import LOG_PREFIX


class GifRequestError(ValueError):
    """No generation should be submitted when request interpretation fails."""


@dataclass(frozen=True, slots=True)
class GifRequest:
    prompt: str
    frame_count: int = 9
    aspect_ratio: str = "1:1"
    frame_duration_ms: int = 200


def _explicit_value(payload: dict, key: str, source: str, default: Any) -> Any:
    option = payload[key]
    if option is None:
        return default
    if not isinstance(option, dict) or set(option) != {"value", "evidence"}:
        raise GifRequestError("GIF 参数解析不完整，本次未提交生成。")
    evidence = option["evidence"]
    if not isinstance(evidence, str) or not evidence.strip() or evidence not in source:
        raise GifRequestError("GIF 参数缺少用户原话依据，本次未提交生成。")
    return option["value"]


def parse_gif_request(payload: Any, source: str) -> GifRequest:
    required = {"visual_prompt", "frame_count", "aspect_ratio", "frame_duration_ms"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise GifRequestError("GIF 请求解析不完整，本次未提交生成。")
    prompt = payload["visual_prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise GifRequestError("GIF 画面要求解析为空，本次未提交生成。")
    count = _explicit_value(payload, "frame_count", source, 9)
    ratio = _explicit_value(payload, "aspect_ratio", source, "1:1")
    duration = _explicit_value(payload, "frame_duration_ms", source, 200)
    if type(count) is not int or count < 2:
        raise GifRequestError("GIF 帧数须为至少 2 的整数，本次未提交生成。")
    if not isinstance(ratio, str) or ratio not in IMAGE_ASPECT_RATIOS:
        raise GifRequestError("GIF 画面比例不受支持，本次未提交生成。")
    if type(duration) is not int or not 20 <= duration <= 2000:
        raise GifRequestError("GIF 单帧时长须在 20 至 2000 毫秒之间，本次未提交生成。")
    return GifRequest(prompt.strip(), count, ratio, duration)


async def resolve_gif_request(host: Any, event: Any, prompt: str) -> GifRequest:
    source = host._event_current_image_request_text(event)
    # Options and creative drafting have different provenance. Tool arguments and
    # storyboards are never accepted as evidence of an explicit user preference.
    fixed = """将 GIF 请求整理为一个 JSON 对象：
{"visual_prompt":"完整画面与连续动作描述", "frame_count":null, "aspect_ratio":null, "frame_duration_ms":null}
三个输出参数只从 user_request 中用户明确要求的语义提取；未指定就返回 null，默认值由程序决定。
明确要求时返回 {"value":规范值,"evidence":"user_request 中对应要求的连续原文"}。
帧数和每帧毫秒数为整数；比例为支持列表中的字符串。场景中物体数量不等于帧数。
visual_draft 仅提供画面素材，不提供输出参数依据。visual_prompt 保留主体、身份、外观、场景和动作，
将帧数、比例、帧时长移到独立参数中；分镜只保留动作顺序，不将草稿的分镜数量当作帧数。
输出参数不得从草稿、参考图形状或创作偏好补全。只输出上述四个字段。"""
    dynamic = json.dumps(
        {
            "user_request": source,
            "visual_draft": prompt,
            "supported_aspect_ratios": IMAGE_ASPECT_RATIOS,
        },
        ensure_ascii=False,
    )
    try:
        payload = await host._media_director_call(
            cache_friendly_prompt(fixed, dynamic, dynamic_title="GIF 请求数据"),
            provider_id=host._media_image_director_provider_id(),
        )
        request = parse_gif_request(payload, source)
    except GifRequestError:
        raise
    except Exception as exc:
        # Provider errors may contain prompts or credentials; do not echo them.
        raise GifRequestError("GIF 请求解析暂时失败，本次未提交生成。") from exc
    logger.debug(
        f"{LOG_PREFIX} GIF 请求参数已确认：帧数={request.frame_count}；"
        f"比例={request.aspect_ratio}；单帧毫秒={request.frame_duration_ms}；"
        f"用户指定字段={','.join(key for key in ('frame_count', 'aspect_ratio', 'frame_duration_ms') if payload[key] is not None) or '无'}"
    )
    return request
