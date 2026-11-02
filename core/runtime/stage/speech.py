from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from astrbot.api import logger

from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX
from .error import MediaPromptExtractionError
from .lens import clean_director_text


class StageVoiceComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    config: Any
    _media_director_call: Any
    _media_director_context_text: Any
    _media_director_current_day: Any
    _media_director_emotion_context_text: Any
    _media_director_recent_context_text: Any


@dataclass(frozen=True, slots=True)
class VideoPromptPlan:
    first_frame: str
    video_prompt: str


class StageVoiceComponent:
    def __init__(self, context: StageVoiceComponentContext) -> None:
        self.context = context

    def _media_video_plan_from_payload(
        self, payload: dict[str, Any]
    ) -> VideoPromptPlan:
        image = clean_director_text(payload.get("image"), 260)
        camera = clean_director_text(payload.get("camera"), 220)
        motion = clean_director_text(payload.get("motion"), 260)
        sound = clean_director_text(payload.get("sound"), 240)
        continuity = clean_director_text(payload.get("continuity"), 240)
        if not image:
            raise MediaPromptExtractionError("视频智能提取没有返回 image 字段")
        first_frame_parts = [f"画面：{image}"]
        if continuity:
            first_frame_parts.append(f"连续性：{continuity}")
        parts = [*first_frame_parts]
        for label, value in (
            ("镜头", camera),
            ("动态", motion),
            ("声音", sound),
        ):
            if value:
                parts.append(f"{label}：{value}")
        return VideoPromptPlan(
            first_frame="。".join(first_frame_parts) + "。",
            video_prompt="。".join(parts) + "。",
        )


    async def _direct_life_video_plan(
        self, event: Any, original_prompt: str
    ) -> VideoPromptPlan:
        original_prompt = str(original_prompt or "").strip()
        if not original_prompt:
            return VideoPromptPlan("", "")
        try:
            day, now, using_extended_night = await self.context._media_director_current_day()
            context = self.context._media_director_context_text(day, now, using_extended_night)
            emotion_context = await self.context._media_director_emotion_context_text(
                day, event=event
            )
            if emotion_context:
                context = f"{context}\n{emotion_context}"
            recent_context = await self.context._media_director_recent_context_text(event)
            fixed = f"""你是生活感短视频导演兼声音设计师。请依据原始画面要求和当前生活上下文，整理成可直接用于图片与视频生成的结构化方案。
只输出 JSON 对象，不要解释。字段定义：
- image：视频开始时的一张静态画面，写清主体、场景、造型和构图。
- continuity：已有参考对象的身份、外观和场景连续性；没有时留空。
- camera：镜头构图与运动；motion：按时间推进的动作和变化。
- sound：符合场景的声音与用户要求的台词；没有明确内容时留空。
视频时长由接口参数单独控制，描述字段只写画面内容。台词、口型与声音保持自然一致。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"image":"","continuity":"","camera":"","motion":"","sound":""}}"""
            dynamic = (
                f"当前生活上下文（低于最近对话，只作生活背景）：\n{context}\n\n"
                f"最近对话场景锚点（优先级高于生活背景）：\n{recent_context}\n\n"
                f"原始视频要求（最终视频需求，必须回应）：{original_prompt}"
            )
            payload = await self.context._media_director_call(
                cache_friendly_prompt(fixed, dynamic, dynamic_title="视频生成请求"),
                provider_id=self.context.config.video_generation.prompt_provider,
            )
            plan = self._media_video_plan_from_payload(payload)
            logger.debug(f"{LOG_PREFIX} 视频智能提取：{plan.video_prompt[:180]}")
            return plan
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 视频智能提取失败：{exc}")
            raise MediaPromptExtractionError(f"视频智能提取失败：{exc}") from exc

    async def _direct_life_video_prompt(self, event: Any, original_prompt: str) -> str:
        plan = await self._direct_life_video_plan(event, original_prompt)
        return plan.video_prompt
