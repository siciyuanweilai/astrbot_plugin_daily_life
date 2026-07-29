from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX
from .error import MediaPromptExtractionError
from .lens import clean_director_text


class StageVoiceMixin:
    def _media_video_prompt_from_payload(self, payload: dict[str, Any]) -> str:
        image = clean_director_text(payload.get("image"), 260)
        camera = clean_director_text(payload.get("camera"), 220)
        motion = clean_director_text(payload.get("motion"), 260)
        sound = clean_director_text(payload.get("sound"), 240)
        continuity = clean_director_text(payload.get("continuity"), 240)
        if not image:
            raise MediaPromptExtractionError("视频智能提取没有返回 image 字段")
        parts = [f"画面：{image}"]
        for label, value in (
            ("连续性", continuity),
            ("镜头", camera),
            ("动态", motion),
            ("声音", sound),
        ):
            if value:
                parts.append(f"{label}：{value}")
        return "。".join(parts) + "。"

    async def _direct_life_video_prompt(self, event: Any, original_prompt: str) -> str:
        original_prompt = str(original_prompt or "").strip()
        if not original_prompt:
            return ""
        try:
            day, now, using_extended_night = await self._media_director_current_day()
            context = self._media_director_context_text(day, now, using_extended_night)
            emotion_context = await self._media_director_emotion_context_text(
                day, event=event
            )
            if emotion_context:
                context = f"{context}\n{emotion_context}"
            recent_context = await self._media_director_recent_context_text(event)
            fixed = f"""你是生活感短视频导演兼声音设计师。请根据画面要求和当前生活上下文，生成视频提示词的画面、动态和声音设计。
要求：
- 只输出 JSON 对象，不要解释。
- image 写一段稳定画面描述，保持真实生活片段，不要写成广告大片。
- continuity 只在使用首帧、参考图或已有角色身份时填写，保持原图主体、人物身份、可见造型、场景关系和构图连续；没有连续性对象时留空。
- camera 和 motion 根据剧情、用户要求和画面内容决定；需要定镜、快速运动、转身、遮挡或较大动作时可以直接采用，不套固定镜头清单。没有独立镜头或动态要求时对应字段可留空。
- sound 根据剧情决定；可以写环境声、动作声、人物台词、音乐，也可以留空。不要为了字段完整强行添加背景声或人声。
- 如果 sound 有人物台词，motion 要包含嘴唇轻微自然开合并与说话节奏同步；如果 sound 没有台词，人物不要出现明显说话口型。
- 只有用户要求、原始剧情或人物互动确实需要时才写台词；写清说话者、语气和台词内容，不要朗读聊天回复、说明文字或提示词。
- 旁白、画外音和解说只有用户明确要求或剧情确实需要时才使用。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"image":"","continuity":"","camera":"","motion":"","sound":""}}"""
            dynamic = (
                f"当前生活上下文（低于最近对话，只作生活背景）：\n{context}\n\n"
                f"最近对话场景锚点（优先级高于生活背景）：\n{recent_context}\n\n"
                f"原始视频要求（最终视频需求，必须回应）：{original_prompt}"
            )
            payload = await self._media_director_call(
                cache_friendly_prompt(fixed, dynamic, dynamic_title="视频生成请求")
            )
            prompt = self._media_video_prompt_from_payload(payload)
            logger.debug(f"{LOG_PREFIX} 视频智能提取：{prompt[:180]}")
            return prompt
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 视频智能提取失败：{exc}")
            raise MediaPromptExtractionError(f"视频智能提取失败：{exc}") from exc
