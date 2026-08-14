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
    _IMAGE_SUBJECT_KINDS = frozenset(
        ("character", "person", "object", "environment", "unknown")
    )
    _IMAGE_PERSON_SUBJECT_KINDS = frozenset(("character", "person"))

    @staticmethod
    def _media_image_subject_kind(payload: dict[str, Any]) -> str:
        kind = clean_director_text(payload.get("subject_kind"), 24).lower()
        if kind in StageReelMixin._IMAGE_SUBJECT_KINDS:
            return kind
        return "unknown"

    @classmethod
    def _validate_media_image_route_payload(
        cls, *, identity_route: str, payload: dict[str, Any]
    ) -> None:
        subject_kind = cls._media_image_subject_kind(payload)
        if (
            identity_route == cls._IMAGE_IDENTITY_NONE
            and subject_kind in cls._IMAGE_PERSON_SUBJECT_KINDS
        ):
            raise MediaPromptExtractionError(
                "图片路线裁定为无人物，但画面提取返回了人物主体"
            )

    def _media_image_prompt_from_payload(
        self,
        payload: dict[str, Any],
        *,
        identity_route: str = "",
    ) -> str:
        self._validate_media_image_route_payload(
            identity_route=identity_route, payload=payload
        )
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
        hair = clean_director_text(payload.get("hair"))
        makeup = clean_director_text(payload.get("makeup"), 120)
        nails = clean_director_text(payload.get("nails"), 120)
        appearance_style = clean_director_text(payload.get("appearance_style"), 80)
        body_presentation = clean_director_text(
            payload.get("body_presentation"), 140
        )
        outfit_visibility = clean_director_text(payload.get("outfit_visibility"), 100)
        outfit_logic = clean_director_text(payload.get("outfit_logic"), 220)
        action = clean_director_text(payload.get("action"))
        weather = clean_director_text(payload.get("weather_vibe"))
        mood = clean_director_text(payload.get("mood"))
        render_style = clean_director_text(payload.get("render_style"), 80)
        constraints = clean_director_text(payload.get("constraints"), 220)
        continuity_constraints = clean_director_text(
            payload.get("continuity_constraints"), 220
        )
        if not any((subject, scene, composition, frame_logic, action)):
            raise MediaPromptExtractionError("图片智能提取没有返回有效画面字段")
        tags = [
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
            f"发型：{hair}" if hair else "",
            f"妆容：{makeup}" if makeup else "",
            f"美甲：{nails}" if nails else "",
            f"造型风格：{appearance_style}" if appearance_style else "",
            f"体貌呈现：{body_presentation}" if body_presentation else "",
            f"穿搭可见性：{outfit_visibility}" if outfit_visibility else "",
            f"穿搭逻辑：{outfit_logic}" if outfit_logic else "",
            action,
            weather,
            mood,
            render_style,
            constraints,
            (
                f"连续性约束：{continuity_constraints}"
                if continuity_constraints
                else ""
            ),
        ]
        prompt = "，".join(item for item in tags if item)
        if not prompt:
            raise MediaPromptExtractionError("图片智能提取结果为空")
        return prompt

    @staticmethod
    def _media_image_preserve_user_request(
        original_prompt: str, directed_prompt: str
    ) -> str:
        """将用户原始画面要求作为不可丢失的硬约束保留在导演结果前。"""
        original = clean_director_text(original_prompt, 600)
        directed = str(directed_prompt or "").strip()
        if not original:
            return directed
        if not directed or directed == original:
            return original or directed
        return (
            f"用户明确要求（最高优先级）：{original}；"
            f"导演构图补充（不得覆盖用户要求）：{directed}"
        )

    @staticmethod
    def _media_image_identity_route(payload: dict[str, Any]) -> str:
        route = clean_director_text(payload.get("identity_route"), 24)
        if route in StageReelMixin._IMAGE_IDENTITY_ROUTES:
            return route
        return StageReelMixin._IMAGE_IDENTITY_UNKNOWN

    @staticmethod
    def _media_image_contains_character(payload: dict[str, Any]) -> bool:
        return (
            StageReelMixin._media_image_identity_route(payload)
            == StageReelMixin._IMAGE_IDENTITY_CHARACTER
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
            f"当前生活上下文（只作身份判断背景）：\n{context}\n\n"
            f"最近对话场景锚点：\n{recent_context}\n\n"
            f"生成类型：{'参考图再创作' if reference else '文生图'}\n"
            f"输出模式：{'保持原文直出，只返回路线判断' if judge_only else '先裁定路线，画面提取另行处理'}\n"
            f"原始画面要求：{original_prompt}"
        )
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="图片路线裁定")

    def _media_image_director_prompt(
        self,
        *,
        context: str,
        recent_context: str,
        original_prompt: str,
        reference: bool,
        persona: str = "",
        appearance_profile: str = "",
    ) -> str:
        fixed = f"""你是角色生活图片导演。一次完成身份路线裁定和可见画面提取，不扩写不可见事实。
身份字段说明：
- identity_route 只能是角色本人、独立主体、无人物或不确定。
- contains_character 只有画面主体是当前角色本人时才为 true。
- needs_character_reference 只有当前角色本人入镜且需要用已配置参考图保持身份连续时才为 true。
画面字段说明：
- subject 写画面主体；subject_kind 写 character、person、object、environment 或 unknown，其中 character 只表示当前角色本人，其他人物写 person；scene 写具体地点和环境；scene_type 写家里、室内公共场所、室外或未知。
- composition 写最终构图；visible_scope 写半身、全身、手部特写、环境空镜、静物或未知。
- frame_logic 写取景依据，说明哪些身体范围、物品或环境进入画面。
- outfit 只写画面中可见的穿搭；outfit_visibility 写穿搭可见范围。
- hair 只写画面中可见的发型；人物不是当前角色、原始要求另有指定或画面看不见头发时，不得套用生活上下文里的当前发型。
- makeup 和 nails 只写画面实际可见的妆容与美甲；优先保持当前生活外观，脸部或手部不在取景范围时留空，不得为了填写字段改变构图。
- appearance_style 写人物当前造型的简短审美风格；不要与 render_style 的摄影或画面风格混同。
- body_presentation 只在当前角色本人入镜、实际可见范围能呈现身体轮廓、且给出了稳定体貌时填写。它只说明本轮服装、姿势或景别如何自然呈现既有比例；不得新增身体特征，不得刻意突出局部，也不得把遮挡、远景或宽松衣物改成贴身展示。其他情况留空。
- render_style 写用户明确要求的画面风格；没有明确要求时留空。
- constraints 只写用户原始要求中的限制；不要把模型自行补充的审美偏好伪装成用户要求。
- continuity_constraints 只写身份参考图、当前外观、场景关系和跨画面需要保持不变的事实；没有可靠连续性依据时留空。
- subject_kind 必须与 identity_route 一致：角色本人写 character，其他人物写 person，无人物画面不得写 character 或 person。
- 优先级是原始画面要求 > 真实参考图 > 当前生活外观。只有当前角色本人入镜、原始要求没有另行指定且对应部分可见时，才用生活上下文补足穿搭、发型和简短风格；不得补充实际取景范围外的鞋袜、配饰、妆容或指甲细节。
- appearance_profile 只提取当前人设明确写出的跨场景稳定体貌，排除五官、发型、服装、姿势、镜头和临时状态；没有明确内容时留空。该字段只供系统缓存，不代替本轮 body_presentation。
- 输出字段尽量短，最终会被拼成图片提示词。
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"identity_route":"不确定","contains_character":false,"needs_character_reference":false,"appearance_profile":"","subject":"","subject_kind":"unknown","scene":"","scene_type":"","temperature_feel":"","weather_condition":"","composition":"","visible_scope":"","frame_logic":"","lighting":"","outfit":"","hair":"","makeup":"","nails":"","appearance_style":"","body_presentation":"","outfit_visibility":"","outfit_logic":"","action":"","weather_vibe":"","mood":"","render_style":"","constraints":"","continuity_constraints":""}}"""
        appearance_context = (
            "\n\n当前角色稳定体貌（仅在身份路线为当前角色本人且可见时使用；"
            "只能如实呈现，不得推断或强化）：\n"
            f"{appearance_profile}"
            if appearance_profile
            else ""
        )
        persona_context = (
            "\n\n当前角色人设（只提取明确稳定体貌和判断身份，不得补写）：\n"
            f"{persona}"
            if persona and not appearance_profile
            else ""
        )
        dynamic = (
            f"当前生活上下文（低于最近对话，只作生活背景）：\n{context}\n\n"
            f"最近对话场景锚点（优先级高于生活背景）：\n{recent_context}\n\n"
            f"生成类型：{'参考图再创作' if reference else '文生图'}\n"
            f"原始画面要求（最终画面需求，必须回应）：{original_prompt}"
            f"{appearance_context}"
            f"{persona_context}"
        )
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="图片导演裁定")

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

    async def _direct_life_image_prompt(
        self, event: Any, original_prompt: str, *, reference: bool = False
    ) -> str:
        result = await self._direct_life_image_payload(
            event, original_prompt, reference=reference
        )
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
            emotion_context = await self._media_director_emotion_context_text(
                day, event=event
            )
            if emotion_context:
                context = f"{context}\n{emotion_context}"
            recent_context = await self._media_director_recent_context_text(event)
            if judge_only:
                route_payload = await self._media_director_call(
                    self._media_image_route_prompt(
                        context=context,
                        recent_context=recent_context,
                        original_prompt=original_prompt,
                        reference=reference,
                        judge_only=True,
                    ),
                    provider_id=self._media_image_director_provider_id(),
                )
                identity_route = self._media_image_identity_route(route_payload)
                contains_character = self._media_image_contains_character(route_payload)
                needs_character_reference = self._media_image_needs_character_reference(
                    route_payload
                )
                logger.debug(
                    f"{LOG_PREFIX} 图片导演裁定：保持原文；身份路线={identity_route}；主角入镜={'是' if contains_character else '否'}；角色参考图={'是' if needs_character_reference else '否'}"
                )
                return DirectedImagePrompt(
                    prompt=original_prompt,
                    identity_route=identity_route,
                    contains_character=contains_character,
                    needs_character_reference=needs_character_reference,
                )
            persona = ""
            appearance_profile = ""
            appearance_context_getter = getattr(
                self, "_character_appearance_context", None
            )
            if callable(appearance_context_getter):
                persona, appearance_profile = await appearance_context_getter(
                    event, schedule_extract=False
                )
            payload = await self._media_director_call(
                self._media_image_director_prompt(
                    context=context,
                    recent_context=recent_context,
                    original_prompt=original_prompt,
                    reference=reference,
                    persona=persona,
                    appearance_profile=appearance_profile,
                ),
                provider_id=self._media_image_director_provider_id(),
            )
            identity_route = self._media_image_identity_route(payload)
            contains_character = self._media_image_contains_character(payload)
            needs_character_reference = self._media_image_needs_character_reference(
                payload
            )
            extracted_subject_kind = self._media_image_subject_kind(payload)
            extracted_character = extracted_subject_kind == "character"
            # 允许“不确定”路线由画面提取补充，但前置的明确路线不能被第二轮改写。
            if extracted_character and identity_route == self._IMAGE_IDENTITY_UNKNOWN:
                identity_route = self._IMAGE_IDENTITY_CHARACTER
                contains_character = True
                needs_character_reference = True
                logger.debug(
                    f"{LOG_PREFIX} 图片智能提取确认当前角色本人，已补充角色参考图需求"
                )
            elif extracted_character and identity_route == self._IMAGE_IDENTITY_CHARACTER:
                contains_character = True
            elif extracted_character and identity_route != self._IMAGE_IDENTITY_CHARACTER:
                logger.debug(
                    f"{LOG_PREFIX} 图片身份路线保持{identity_route}；画面提取为角色设定主体，不使用当前角色参考图"
                )
            if contains_character and persona:
                remember_profile = getattr(
                    self, "_remember_character_appearance_profile", None
                )
                if callable(remember_profile):
                    remember_profile(persona, payload.get("appearance_profile"))
            prompt = self._media_image_prompt_from_payload(
                payload,
                identity_route=identity_route,
            )
            prompt = self._media_image_preserve_user_request(original_prompt, prompt)
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
生成类型：{"参考图再创作" if reference else "文生图"}
{CORE_JSON_OUTPUT_RULES}
JSON 字段：
{{"prompt":""}}"""
        dynamic = f"需要改写的图片提示词：{original_prompt}"
        try:
            image_config = getattr(
                getattr(self, "config", None), "image_generation", None
            )
            provider_id = str(
                getattr(image_config, "prompt_rewrite_provider", "") or ""
            ).strip()
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
