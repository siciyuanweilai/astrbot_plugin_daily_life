from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX
from .error import MediaPromptExtractionError
from .lens import clean_director_text


class StageReelMixin:
    def _media_image_prompt_from_payload(self, payload: dict[str, Any]) -> str:
        subject = clean_director_text(payload.get("subject"))
        scene = clean_director_text(payload.get("scene"))
        scene_type = clean_director_text(payload.get("scene_type"))
        temperature = clean_director_text(payload.get("temperature_feel"))
        weather_condition = clean_director_text(payload.get("weather_condition"))
        composition = clean_director_text(payload.get("composition"))
        frame_logic = clean_director_text(payload.get("frame_logic"), 220)
        lighting = clean_director_text(payload.get("lighting"))
        outfit = clean_director_text(payload.get("outfit"))
        outfit_logic = clean_director_text(payload.get("outfit_logic"), 220)
        action = clean_director_text(payload.get("action"))
        weather = clean_director_text(payload.get("weather_vibe"))
        mood = clean_director_text(payload.get("mood"))
        constraints = clean_director_text(payload.get("constraints"), 220)
        if not any((subject, scene, composition, frame_logic, action)):
            raise MediaPromptExtractionError("图片智能提取没有返回有效画面字段")
        tags = [
            "写实生活照",
            subject,
            scene,
            composition,
            f"取景逻辑：{frame_logic}" if frame_logic else "",
            f"场景类型：{scene_type}" if scene_type else "",
            f"温感：{temperature}" if temperature else "",
            f"天气：{weather_condition}" if weather_condition else "",
            lighting,
            outfit,
            f"穿搭逻辑：{outfit_logic}" if outfit_logic else "",
            action,
            weather,
            mood,
            constraints,
        ]
        prompt = "，".join(item for item in tags if item)
        if not prompt:
            raise MediaPromptExtractionError("图片智能提取结果为空")
        return prompt

    async def _direct_life_image_prompt(self, event: Any, original_prompt: str, *, reference: bool = False) -> str:
        original_prompt = str(original_prompt or "").strip()
        if not original_prompt:
            return ""
        try:
            day, now, using_extended_night = await self._media_director_current_day()
            context = self._media_director_context_text(day, now, using_extended_night)
            recent_context = await self._media_director_recent_context_text(event)
            fixed = f"""你是角色生活图片的画面导演。请把用户或模型给出的画面要求，整理成适合图片生成的中文提示词要素。
要求：
- 只根据画面要求和当前生活上下文补足可见细节，不要编造无依据的新人物、新剧情或夸张姿势。
- 根据当前活动、地点、天气、时间、心情和穿搭选择自然构图；可以是自拍感、半身生活照、环境中景、远景、手部特写、静物或空镜。
- 先判断场景类型是家里、室内公共场所、室外还是未知，再结合天气、温度、动作和构图决定可见细节。
- composition 写最终构图；frame_logic 用一句话说明为什么这样取景，以及哪些身体范围、物品或环境会进入画面。
- outfit 只写画面里能看见的主角穿搭；外套、鞋袜、脚部状态必须符合地点、温度、动作和构图范围，不可见部位不要写进画面词。
- outfit_logic 用一句话说明穿搭判断，尤其说明室内外、外套层次、鞋袜或赤脚是否真的会入镜。
- 今日穿搭只属于主角本人；不要把主角穿搭套给其他人，其他人缺少依据时保持概括。
- 如果画面不需要角色本人，不要强行加入人物。
- 视频截图、电影海报、插画感、营销海报、过度磨皮和夸张特效都不要默认加入。
- 输出字段尽量短，最终会被拼成图片提示词。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"subject":"","scene":"","scene_type":"","temperature_feel":"","weather_condition":"","composition":"","frame_logic":"","lighting":"","outfit":"","outfit_logic":"","action":"","weather_vibe":"","mood":"","constraints":""}}"""
            dynamic = (
                f"当前生活上下文（低于最近对话，只作生活背景）：\n{context}\n\n"
                f"最近对话场景锚点（优先级高于生活背景，最近明确说过的当前状态必须覆盖日程和旧回复）：\n{recent_context}\n\n"
                f"生成类型：{'参考图再创作' if reference else '文生图'}\n"
                f"原始画面要求（最终画面需求，必须回应）：{original_prompt}"
            )
            payload = await self._media_director_call(cache_friendly_prompt(fixed, dynamic, dynamic_title="媒体生成请求"))
            prompt = self._media_image_prompt_from_payload(payload)
            logger.debug(f"{LOG_PREFIX} 图片智能提取：{prompt[:180]}")
            return prompt
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 图片智能提取失败：{exc}")
            raise MediaPromptExtractionError(f"图片智能提取失败：{exc}") from exc
