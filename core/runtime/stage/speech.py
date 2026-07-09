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
        if not continuity:
            raise MediaPromptExtractionError("视频智能提取没有返回 continuity 字段")
        if not camera:
            raise MediaPromptExtractionError("视频智能提取没有返回 camera 字段")
        if not motion:
            raise MediaPromptExtractionError("视频智能提取没有返回 motion 字段")
        if not sound:
            raise MediaPromptExtractionError("视频智能提取没有返回 sound 字段")
        return f"画面：{image}。连续性：{continuity}。镜头：{camera}。动态：{motion}。声音：{sound}。"

    async def _direct_life_video_prompt(self, event: Any, original_prompt: str) -> str:
        original_prompt = str(original_prompt or "").strip()
        if not original_prompt:
            return ""
        try:
            day, now, using_extended_night = await self._media_director_current_day()
            context = self._media_director_context_text(day, now, using_extended_night)
            emotion_context = await self._media_director_emotion_context_text(day, event=event)
            if emotion_context:
                context = f"{context}\n{emotion_context}"
            recent_context = await self._media_director_recent_context_text(event)
            fixed = f"""你是生活感短视频导演兼声音设计师。请根据画面要求和当前生活上下文，生成视频提示词的画面、动态和声音设计。
要求：
- 只输出 JSON 对象，不要解释。
- image 写一段稳定画面描述，保持真实生活片段，不要写成广告大片。
- continuity 写主体连续性约束；如果会使用上一张生活图或参考图作为首帧，必须保持原图主体、人物身份、五官、发型、服装、场景、光线、构图重心、景别、主体位置和人物关系，不新增人物、物体、剧情动作或夸张表演。
- camera 写景别、机位、构图重心、镜头运动和节奏；优先从轻微推近、缓慢横移、少量手持呼吸感或自然定镜中选择，保持生活感，不要破坏原图景别、人物比例和主体位置。
- motion 写人物或物体细微动作、光影或天气氛围变化，要和 camera 自然衔接；不要把镜头设计全塞进 motion，也不要默认写静止无变化。
- sound 必须包含真实生活声层次，默认考虑环境声、动作声和一层很轻的氛围背景声；只有画面明显不适合时才排除背景声或人声。
- 如果 sound 有人物台词，motion 要包含嘴唇轻微自然开合并与说话节奏同步；如果 sound 没有台词，人物不要出现明显说话口型。
- 如果画面有人物，优先判断是否适合让人物根据文案内容自然开口说话；台词应口语化、生活化、短句表达，可提炼成一句符合人物状态和情绪的话。
- 人物台词只作为短促的画面内声音元素，不能从头说到尾；其余时间保留环境声、动作声和轻柔背景声。
- sound 中如果包含台词，要写清说话者、语气和台词内容，但不要固定成某一种声线，也不要把聊天回复、说明文字或提示词念出来。
- 当同时包含人声和背景声时，环境声、动作声与背景声要自然融合，人物说话清晰可听但不过分贴耳，背景声持续存在但不喧宾夺主。
- 禁止旁白、画外音、解说或朗读提示词。
- 如果没有明确人物或说话必要，不要强行让人物开口。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"image":"","continuity":"","camera":"","motion":"","sound":""}}"""
            dynamic = (
                f"当前生活上下文（低于最近对话，只作生活背景）：\n{context}\n\n"
                f"最近对话场景锚点（优先级高于生活背景）：\n{recent_context}\n\n"
                f"原始视频要求（最终视频需求，必须回应）：{original_prompt}"
            )
            payload = await self._media_director_call(cache_friendly_prompt(fixed, dynamic, dynamic_title="视频生成请求"))
            prompt = self._media_video_prompt_from_payload(payload)
            logger.debug(f"{LOG_PREFIX} 视频智能提取：{prompt[:180]}")
            return prompt
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 视频智能提取失败：{exc}")
            raise MediaPromptExtractionError(f"视频智能提取失败：{exc}") from exc
