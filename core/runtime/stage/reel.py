from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from ...life.tools import extract_json_from_text
from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX
from .error import MediaPromptExtractionError
from .lens import clean_director_text


@dataclass(frozen=True, slots=True)
class DirectedImagePrompt:
    prompt: str
    identity_route: str = "不确定"
    contains_character: bool = False
    needs_character_reference: bool = False


class StageReelMixin:
    _IMAGE_IDENTITY_CHARACTER = "角色本人"
    _IMAGE_IDENTITY_NONE = "无人物"
    _IMAGE_IDENTITY_UNKNOWN = "不确定"
    _IMAGE_IDENTITY_ROUTES = frozenset(("角色本人", "独立主体", "无人物", "不确定"))
    _IMAGE_SUBJECT_KINDS = frozenset(("character", "person", "object", "environment", "unknown"))
    _IMAGE_PERSON_SUBJECT_KINDS = frozenset(("character", "person"))

    @staticmethod
    def _media_image_visual_identity_prompt(visual_context: str) -> str:
        parts: list[str] = []
        for line in str(visual_context or "").splitlines():
            text = clean_director_text(line, 220)
            if not text:
                continue
            if "：" in text:
                _, value = text.split("：", 1)
                text = value.strip() or text
            parts.append(text)
        if not parts:
            return ""
        return "画面主体是当前角色本人，保持角色外貌身份一致，" + "，".join(parts)

    @staticmethod
    def _media_image_subject_kind(payload: dict[str, Any]) -> str:
        kind = clean_director_text(payload.get("subject_kind"), 24).lower()
        if kind in StageReelMixin._IMAGE_SUBJECT_KINDS:
            return kind
        return "unknown"

    @classmethod
    def _validate_media_image_route_payload(cls, *, identity_route: str, payload: dict[str, Any]) -> None:
        subject_kind = cls._media_image_subject_kind(payload)
        if identity_route == cls._IMAGE_IDENTITY_NONE and subject_kind in cls._IMAGE_PERSON_SUBJECT_KINDS:
            raise MediaPromptExtractionError("图片路线裁定为无人物，但画面提取返回了人物主体")

    def _media_image_prompt_from_payload(
        self,
        payload: dict[str, Any],
        *,
        visual_context: str = "",
        identity_route: str = "",
    ) -> str:
        self._validate_media_image_route_payload(identity_route=identity_route, payload=payload)
        subject = clean_director_text(payload.get("subject"))
        scene = clean_director_text(payload.get("scene"))
        scene_type = clean_director_text(payload.get("scene_type"))
        temperature = clean_director_text(payload.get("temperature_feel"))
        weather_condition = clean_director_text(payload.get("weather_condition"))
        composition = clean_director_text(payload.get("composition"))
        frame_logic = clean_director_text(payload.get("frame_logic"), 220)
        visible_scope = clean_director_text(payload.get("visible_scope"), 80)
        lighting = clean_director_text(payload.get("lighting"))
        outfit = clean_director_text(payload.get("outfit"))
        outfit_visibility = clean_director_text(payload.get("outfit_visibility"), 100)
        outfit_logic = clean_director_text(payload.get("outfit_logic"), 220)
        action = clean_director_text(payload.get("action"))
        weather = clean_director_text(payload.get("weather_vibe"))
        mood = clean_director_text(payload.get("mood"))
        render_style = clean_director_text(payload.get("render_style"), 80)
        constraints = clean_director_text(payload.get("constraints"), 220)
        if not any((subject, scene, composition, frame_logic, action)):
            raise MediaPromptExtractionError("图片智能提取没有返回有效画面字段")
        tags = [
            self._media_image_visual_identity_prompt(visual_context),
            subject,
            scene,
            composition,
            f"可见范围：{visible_scope}" if visible_scope else "",
            f"取景逻辑：{frame_logic}" if frame_logic else "",
            f"场景类型：{scene_type}" if scene_type else "",
            f"温感：{temperature}" if temperature else "",
            f"天气：{weather_condition}" if weather_condition else "",
            lighting,
            outfit,
            f"穿搭可见性：{outfit_visibility}" if outfit_visibility else "",
            f"穿搭逻辑：{outfit_logic}" if outfit_logic else "",
            action,
            weather,
            mood,
            render_style,
            constraints,
        ]
        prompt = "，".join(item for item in tags if item)
        if not prompt:
            raise MediaPromptExtractionError("图片智能提取结果为空")
        return prompt

    @staticmethod
    def _media_image_identity_route(payload: dict[str, Any]) -> str:
        route = clean_director_text(payload.get("identity_route"), 24)
        if route in StageReelMixin._IMAGE_IDENTITY_ROUTES:
            return route
        return StageReelMixin._IMAGE_IDENTITY_UNKNOWN

    @staticmethod
    def _media_image_contains_character(payload: dict[str, Any]) -> bool:
        return (
            StageReelMixin._media_image_identity_route(payload) == StageReelMixin._IMAGE_IDENTITY_CHARACTER
            and payload.get("contains_character") is True
        )

    @staticmethod
    def _media_image_needs_character_reference(payload: dict[str, Any]) -> bool:
        return (
            StageReelMixin._media_image_contains_character(payload)
            and payload.get("needs_character_reference") is True
        )

    def _media_image_route_prompt(
        self,
        *,
        context: str,
        recent_context: str,
        visual_context: str,
        original_prompt: str,
        reference: bool,
        judge_only: bool,
    ) -> str:
        fixed = f"""你是图片生成路线裁定器。只判断原始画面要求和当前角色的身份关系，不改写画面，只返回路线判断。
裁定字段：
- identity_route：角色本人、独立主体、无人物、不确定。
- contains_character：只有画面主体是当前角色本人时才为 true。
- needs_character_reference：只有需要用已配置角色参考图保持身份连续时才为 true。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"identity_route":"不确定","contains_character":false,"needs_character_reference":false}}"""
        dynamic = (
            f"角色视觉设定摘要（仅用于识别当前角色本人）：\n{visual_context or '无'}\n\n"
            f"当前生活上下文（只作身份判断背景）：\n{context}\n\n"
            f"最近对话场景锚点：\n{recent_context}\n\n"
            f"生成类型：{'参考图再创作' if reference else '文生图'}\n"
            f"输出模式：{'保持原文直出，只返回路线判断' if judge_only else '先裁定路线，画面提取另行处理'}\n"
            f"原始画面要求：{original_prompt}"
        )
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="图片路线裁定")

    def _media_image_extraction_prompt(
        self,
        *,
        context: str,
        recent_context: str,
        original_prompt: str,
        reference: bool,
        identity_route: str,
    ) -> str:
        fixed = f"""你是角色生活图片的画面提取器。只把画面要求整理成可见画面字段，不判断角色参考图。
画面字段说明：
- subject 写画面主体；subject_kind 写 character、person、object、environment 或 unknown，其中 character 只表示当前角色本人，其他人物写 person；scene 写具体地点和环境；scene_type 写家里、室内公共场所、室外或未知。
- composition 写最终构图；visible_scope 写半身、全身、手部特写、环境空镜、静物或未知。
- frame_logic 写取景依据，说明哪些身体范围、物品或环境进入画面。
- outfit 只写画面中可见的穿搭；outfit_visibility 写穿搭可见范围。
- render_style 写用户明确要求的画面风格；没有明确要求时留空。
- 输出字段尽量短，最终会被拼成图片提示词。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"subject":"","subject_kind":"unknown","scene":"","scene_type":"","temperature_feel":"","weather_condition":"","composition":"","visible_scope":"","frame_logic":"","lighting":"","outfit":"","outfit_visibility":"","outfit_logic":"","action":"","weather_vibe":"","mood":"","render_style":"","constraints":""}}"""
        dynamic = (
            f"当前生活上下文（低于最近对话，只作生活背景）：\n{context}\n\n"
            f"最近对话场景锚点（优先级高于生活背景）：\n{recent_context}\n\n"
            f"身份路线裁定：{identity_route}\n"
            f"生成类型：{'参考图再创作' if reference else '文生图'}\n"
            f"原始画面要求（最终画面需求，必须回应）：{original_prompt}"
        )
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="图片画面提取")

    @staticmethod
    def _strip_media_code_fence(text: str) -> str:
        raw = str(text or "").strip()
        if not raw.startswith("```"):
            return raw
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def _media_rewrite_prompt_from_payload(self, payload: dict[str, Any]) -> str:
        for key in ("prompt", "image_prompt", "rewritten_prompt", "result", "text"):
            value = clean_director_text(payload.get(key), 600)
            if value:
                return value
        try:
            return self._media_image_prompt_from_payload(payload)
        except MediaPromptExtractionError:
            return ""

    def _media_rewrite_prompt_from_response(self, response: Any) -> str:
        if isinstance(response, dict):
            return self._media_rewrite_prompt_from_payload(response)
        raw = self._strip_media_code_fence(str(response or ""))
        if not raw:
            return ""
        payload = extract_json_from_text(raw)
        if isinstance(payload, dict):
            return self._media_rewrite_prompt_from_payload(payload)
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(raw)
            except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                return self._media_rewrite_prompt_from_payload(parsed)
            if isinstance(parsed, str):
                return clean_director_text(parsed, 600)
        if raw.startswith(("{", "[")) and raw.endswith(("}", "]")):
            return ""
        return clean_director_text(raw, 600)

    async def _direct_life_image_prompt(self, event: Any, original_prompt: str, *, reference: bool = False) -> str:
        result = await self._direct_life_image_payload(event, original_prompt, reference=reference)
        return result.prompt

    async def _direct_life_image_payload(
        self,
        event: Any,
        original_prompt: str,
        *,
        reference: bool = False,
        judge_only: bool = False,
    ) -> DirectedImagePrompt:
        original_prompt = str(original_prompt or "").strip()
        if not original_prompt:
            return DirectedImagePrompt("")
        try:
            day, now, using_extended_night = await self._media_director_current_day()
            context = self._media_director_context_text(day, now, using_extended_night)
            emotion_context = await self._media_director_emotion_context_text(day, event=event)
            if emotion_context:
                context = f"{context}\n{emotion_context}"
            recent_context = await self._media_director_recent_context_text(event)
            visual_context_getter = getattr(self, "_media_director_visual_context_text", None)
            visual_context = (
                await visual_context_getter(event)
                if callable(visual_context_getter)
                else ""
            )
            route_payload = await self._media_director_call(
                self._media_image_route_prompt(
                    context=context,
                    recent_context=recent_context,
                    visual_context=visual_context,
                    original_prompt=original_prompt,
                    reference=reference,
                    judge_only=judge_only,
                )
            )
            identity_route = self._media_image_identity_route(route_payload)
            contains_character = self._media_image_contains_character(route_payload)
            needs_character_reference = self._media_image_needs_character_reference(route_payload)
            if judge_only:
                logger.debug(
                    f"{LOG_PREFIX} 图片导演裁定：保持原文；身份路线={identity_route}；主角入镜={'是' if contains_character else '否'}；角色参考图={'是' if needs_character_reference else '否'}"
                )
                return DirectedImagePrompt(
                    prompt=original_prompt,
                    identity_route=identity_route,
                    contains_character=contains_character,
                    needs_character_reference=needs_character_reference,
                )
            payload = await self._media_director_call(
                self._media_image_extraction_prompt(
                    context=context,
                    recent_context=recent_context,
                    original_prompt=original_prompt,
                    reference=reference,
                    identity_route=identity_route,
                )
            )
            extracted_subject_kind = self._media_image_subject_kind(payload)
            extracted_character = extracted_subject_kind == "character"
            if extracted_character:
                identity_route = self._IMAGE_IDENTITY_CHARACTER
                contains_character = True
                needs_character_reference = True
                logger.debug(f"{LOG_PREFIX} 图片智能提取确认当前角色本人，已补充角色参考图需求")
            character_visual_context = (
                visual_context
                if identity_route == self._IMAGE_IDENTITY_CHARACTER
                else ""
            )
            prompt = self._media_image_prompt_from_payload(
                payload,
                visual_context=character_visual_context,
                identity_route=identity_route,
            )
            logger.debug(f"{LOG_PREFIX} 图片智能提取：{prompt[:180]}")
            return DirectedImagePrompt(
                prompt=prompt,
                identity_route=identity_route,
                contains_character=contains_character,
                needs_character_reference=needs_character_reference,
            )
        except Exception as exc:
            if judge_only:
                logger.debug(f"{LOG_PREFIX} 图片导演裁定失败：{exc}")
                raise MediaPromptExtractionError(f"图片导演裁定失败：{exc}") from exc
            logger.debug(f"{LOG_PREFIX} 图片智能提取失败：{exc}")
            raise MediaPromptExtractionError(f"图片智能提取失败：{exc}") from exc

    async def _rewrite_life_image_prompt_for_policy_retry(
        self,
        event: Any,
        original_prompt: str,
        *,
        reference: bool = False,
    ) -> str:
        original_prompt = str(original_prompt or "").strip()
        if not original_prompt:
            return ""
        fixed = f"""你是角色生活图片的提示词润色助手。图片接口拒绝了当前提示词，请在尽量少改原文的前提下，润色成更容易通过图片接口的中文画面提示词。
要求：
- 原文已经明确的主体、场景、构图、镜头、动作、穿搭、光线、天气、时间和氛围必须保留。
- 只微调可能让接口误判的措辞，优先用更中性的生活化描述替换，不要整体重写。
- 不新增人物、关系、剧情、姿势、服装、地点或风格；不要把原画面改成另一张图。
- 不要额外加入说教式安全词、负面词或审查说明。
- 只在 prompt 字段输出润色后的图片提示词，不要解释。
生成类型：{'参考图再创作' if reference else '文生图'}
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"prompt":""}}"""
        dynamic = f"需要改写的图片提示词：{original_prompt}"
        try:
            image_config = getattr(getattr(self, "config", None), "image_generation", None)
            provider_id = str(getattr(image_config, "prompt_rewrite_provider", "") or "").strip()
            text = await self._media_director_text_call(
                cache_friendly_prompt(fixed, dynamic, dynamic_title="图片轻量润色"),
                provider_id=provider_id,
            )
            value = self._media_rewrite_prompt_from_response(text)
            if value:
                return value
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 图片轻量润色失败：{exc}")
            raise MediaPromptExtractionError(f"图片轻量润色失败：{exc}") from exc
        raise MediaPromptExtractionError("图片轻量润色结果为空")
