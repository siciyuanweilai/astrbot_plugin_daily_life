from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from ...config.options import IMAGE_ASPECT_RATIOS
from ...life.appearance import format_current_appearance_context
from ...life.wardrobe import (
    normalize_outfit_decision,
    normalize_outfit_scene_category,
    normalize_outfit_style_pool,
    outfit_scene_category_label,
    outfit_style_pool_label,
    style_pool_for_scene_category,
)
from ...media.base import GROUP_IDENTITY_CONTINUITY_RULE
from ...media.picture.routes import (
    image_provider_label,
    requested_image_provider,
)
from ...paths import runtime_data_root
from ..locks import operation_lock
from ..markers import LOG_PREFIX


@dataclass(slots=True)
class ImageGenerationPlan:
    prompt: str
    direct_prompt: bool
    aspect_ratio: str
    generation_mode: str
    current_character: bool
    group_request: bool
    participant_ids: list[str]
    resolution: str
    provider: str


@dataclass(frozen=True, slots=True)
class ImageGenerationExecution:
    generated: Any = None
    directed_prompt: str = ""
    error: str = ""
    generation_mode: str = ""


class RuntimeImageMediaMixin:
    _CURRENT_APPEARANCE_PROMPT_MARKER = "当前生活状态权威造型快照"

    @staticmethod
    def _current_character_identity_profiles(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        profile = " ".join(str(value.get("current_character") or "").strip().split())[
            :600
        ]
        return {"current_character": profile} if profile else {}

    async def _resolve_life_identity_profiles(
        self, event: Any, subject_route: str
    ) -> dict[str, str]:
        route = self._normalize_image_subject_route(subject_route)
        if route not in {"current_character", "group"}:
            return {}
        profiles: dict[str, str] = {}
        resolver = getattr(self, "_character_appearance_profile", None)
        if callable(resolver):
            try:
                current_profile = " ".join(
                    str(await resolver(event) or "").strip().split()
                )[:600]
            except Exception:
                current_profile = ""
            if current_profile:
                profiles["current_character"] = current_profile
        return profiles

    async def _current_life_appearance_snapshot(self, subject_route: str) -> str:
        route = self._normalize_image_subject_route(subject_route)
        if route not in {"current_character", "group"}:
            return ""
        resolver = getattr(self, "_media_director_current_day", None)
        if not callable(resolver):
            return ""
        try:
            day, _now, _using_extended_night = await resolver()
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 读取当前造型快照失败，继续使用原始画面要求："
                f"{type(exc).__name__}"
            )
            return ""
        return format_current_appearance_context(day)

    def _apply_current_appearance_snapshot(
        self,
        prompt: str,
        appearance: str,
        subject_route: str,
        *,
        source_request: str = "",
    ) -> str:
        text = str(prompt or "").strip()
        snapshot = str(appearance or "").strip()
        route = self._normalize_image_subject_route(subject_route)
        if (
            not snapshot
            or route not in {"current_character", "group"}
            or self._CURRENT_APPEARANCE_PROMPT_MARKER in text
        ):
            return text
        subject = "人物 A" if route == "group" else "当前角色"
        original = " ".join(str(source_request or "").strip().split())[:600]
        original_line = f"\n用户当前原始请求：{original}" if original else ""
        constraint = (
            f"{subject}造型（来自当前生活状态）\n"
            f"{self._CURRENT_APPEARANCE_PROMPT_MARKER}：\n{snapshot}"
            f"{original_line}\n"
            "这份快照是当前真实生活状态，生成模型不得根据场景、动作或自行扩写而更换其中的服装、鞋袜、配饰或发型。"
            "只有用户当前原始请求明确要求在本次画面中试穿、换造型或采用另一套外观时，才允许按该明确要求覆盖；"
            "工具整理后的画面提示词本身不能作为换装证据。"
        )
        return f"{text}\n\n{constraint}" if text else constraint

    def _friend_daily_looks(self) -> dict[str, dict[str, str]]:
        store = getattr(self, "_life_friend_daily_looks", None)
        if not isinstance(store, dict):
            store = {}
            self._life_friend_daily_looks = store
        return store

    def _friend_daily_look_path(self):
        return runtime_data_root(getattr(self, "data_path", None)) / "friend_looks.json"

    @staticmethod
    def _friend_daily_look_key(scope: str, profile_id: str) -> str:
        return f"{str(scope or '').strip()}\n{str(profile_id or '').strip()}"

    @staticmethod
    def _normalize_friend_look(value: Any) -> dict[str, str]:
        raw = value if isinstance(value, dict) else {}
        return {
            "date": str(raw.get("date") or "").strip()[:10],
            "outfit": " ".join(str(raw.get("outfit") or "").split())[:600],
            "hair": " ".join(str(raw.get("hair") or "").split())[:400],
            "scene": " ".join(str(raw.get("scene") or "").split())[:200],
            "scene_category": normalize_outfit_scene_category(
                raw.get("scene_category"), default=""
            ),
            "style_pool": normalize_outfit_style_pool(
                raw.get("style_pool"), default=""
            ),
            "decision": normalize_outfit_decision(raw.get("decision"), default=""),
            "updated_at": str(raw.get("updated_at") or "").strip()[:19],
        }

    @staticmethod
    def _friend_look_compatible(look: dict[str, str], target_scene: str) -> bool:
        target = normalize_outfit_scene_category(target_scene, default="")
        style_pool = normalize_outfit_style_pool(look.get("style_pool"), default="")
        if not target:
            return False
        if target in {"outdoor", "public"}:
            return style_pool in {"outfit_styles", "mixed"}
        if target == "sleep":
            return style_pool in {"sleep_styles", "mixed"}
        return True

    @staticmethod
    def _friend_look_default_decision(
        target_scene: str, *, outfit_changed: bool, hair_changed: bool
    ) -> str:
        if not outfit_changed:
            return "partial_change" if hair_changed else "keep"
        if target_scene == "sleep":
            return "sleepwear"
        if target_scene in {"outdoor", "public"}:
            return "outdoor"
        return "change"

    async def _load_friend_daily_looks(self) -> None:
        if getattr(self, "_life_friend_daily_looks_loaded", False):
            return
        self._life_friend_daily_looks_loaded = True
        if not getattr(self, "data_path", None):
            return
        path = self._friend_daily_look_path()

        def read() -> dict[str, Any]:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                return {}
            return value if isinstance(value, dict) else {}

        loaded = await asyncio.to_thread(read)
        today = time.strftime("%Y-%m-%d")
        store = self._friend_daily_looks()
        for key, value in loaded.items():
            look = self._normalize_friend_look(value)
            if look["date"] == today and (look["outfit"] or look["hair"]):
                store[str(key)] = look

    async def _persist_friend_daily_looks(self) -> None:
        if not getattr(self, "data_path", None):
            return
        path = self._friend_daily_look_path()
        payload = dict(self._friend_daily_looks())

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(path)

        lock = getattr(self, "_life_friend_daily_looks_write_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._life_friend_daily_looks_write_lock = lock
        async with lock:
            await asyncio.to_thread(write)

    def _current_friend_daily_look(self, scope: str, profile_id: str) -> dict[str, str]:
        key = self._friend_daily_look_key(scope, profile_id)
        look = self._normalize_friend_look(self._friend_daily_looks().get(key))
        if look["date"] != time.strftime("%Y-%m-%d"):
            self._friend_daily_looks().pop(key, None)
            return {}
        return look if look["outfit"] or look["hair"] else {}

    async def _resolve_friend_daily_look(
        self,
        event: Any,
        profile_id: str,
        *,
        outfit: str = "",
        hair: str = "",
        scene: str = "",
    ) -> dict[str, str]:
        look, _, _ = await self._prepare_friend_daily_look(
            event,
            profile_id,
            outfit=outfit,
            hair=hair,
            scene=scene,
        )
        return look

    async def _prepare_friend_daily_look(
        self,
        event: Any,
        profile_id: str,
        *,
        outfit: str = "",
        hair: str = "",
        scene: str = "",
        scene_category: str = "",
        style_pool: str = "",
        decision: str = "",
    ) -> tuple[dict[str, str], str, list[str]]:
        await self._load_friend_daily_looks()
        current = self._current_friend_daily_look(
            self._event_session_id(event), profile_id
        )
        provided = self._normalize_friend_look(
            {
                "date": time.strftime("%Y-%m-%d"),
                "outfit": outfit,
                "hair": hair,
                "scene": scene,
                "scene_category": scene_category,
                "style_pool": style_pool,
                "decision": decision,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        target_scene = provided["scene_category"]
        if not target_scene:
            missing = ["friend_scene_category"]
            if not current:
                for field, value in (
                    ("friend_outfit", provided["outfit"]),
                    ("friend_hair", provided["hair"]),
                ):
                    if not value:
                        missing.append(field)
            return current, "缺少场景", missing

        if provided["outfit"] and not provided["style_pool"]:
            provided["style_pool"] = style_pool_for_scene_category(target_scene)

        outfit_changed = bool(
            provided["outfit"] and provided["outfit"] != current.get("outfit", "")
        )
        hair_changed = bool(
            provided["hair"] and provided["hair"] != current.get("hair", "")
        )
        pool_changed = bool(
            provided["style_pool"]
            and provided["style_pool"] != current.get("style_pool", "")
        )
        requested_decision = provided["decision"]
        final_outfit = provided["outfit"] or current.get("outfit", "")
        final_hair = provided["hair"] or current.get("hair", "")
        final_pool = provided["style_pool"] or current.get("style_pool", "")
        final_decision = requested_decision or self._friend_look_default_decision(
            target_scene,
            outfit_changed=outfit_changed,
            hair_changed=hair_changed,
        )

        missing: list[str] = []
        if not current:
            for field, value in (
                ("friend_outfit", final_outfit),
                ("friend_hair", final_hair),
            ):
                if not value:
                    missing.append(field)
        else:
            if (
                final_decision in {"change", "sleepwear", "outdoor"}
                and not outfit_changed
            ):
                missing.append("friend_outfit")
            if not outfit_changed and not self._friend_look_compatible(
                current, target_scene
            ):
                if "friend_outfit" not in missing:
                    missing.append("friend_outfit")

        candidate = self._normalize_friend_look(
            {
                "date": provided["date"],
                "outfit": final_outfit,
                "hair": final_hair,
                "scene": provided["scene"] or current.get("scene", ""),
                "scene_category": target_scene,
                "style_pool": final_pool,
                "decision": final_decision,
                "updated_at": provided["updated_at"],
            }
        )
        if not missing and not self._friend_look_compatible(candidate, target_scene):
            if not outfit_changed:
                missing.append("friend_outfit")
        if missing:
            return (
                current or candidate,
                "场景需要更新" if current else "本轮新建",
                missing,
            )

        changed = bool(outfit_changed or hair_changed or pool_changed)
        if not current:
            source = "本轮新建"
        elif changed:
            source = "本轮更新"
        elif current.get("scene_category") != target_scene:
            source = "场景沿用"
        else:
            source = "当天沿用"
        look = candidate
        return look, source, missing

    @staticmethod
    def _friend_look_should_persist(source: str) -> bool:
        return str(source or "").strip() in {
            "本轮新建",
            "本轮更新",
            "场景沿用",
        }

    @staticmethod
    def _friend_look_parameters_result(missing: list[str]) -> str:
        return json.dumps(
            {
                "status": "needs_parameters",
                "media": "group_look",
                "required_parameters": list(missing),
                "response_stance": (
                    "不要发送最终文字；补齐人物 B 的结构化造型参数后，重新调用当前工具"
                ),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _friend_look_required_parameters(source: str, missing: list[str]) -> list[str]:
        del source
        return list(missing)

    def _friend_profile_display_name(self, profile_id: str) -> str:
        target = str(profile_id or "").strip()
        image_service = getattr(getattr(self, "media", None), "image", None)
        resolver = getattr(image_service, "friend_reference_options", None)
        if callable(resolver):
            try:
                options = resolver()
            except Exception:
                options = []
            for item in options or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("profile_id") or "").strip() != target:
                    continue
                return str(item.get("display_name") or target).strip() or target
        return target or "好友"

    def _log_friend_daily_look(
        self, profile_id: str, look: dict[str, str], source: str
    ) -> None:
        logger.debug(
            f"{LOG_PREFIX} 好友合影造型：好友={self._friend_profile_display_name(profile_id)}；"
            f"来源={source or '本轮'}；场景={outfit_scene_category_label(look.get('scene_category'))}；"
            f"风格池={outfit_style_pool_label(look.get('style_pool'))}；"
            f"决定={look.get('decision') or '继续沿用'}；"
            f"穿搭长度={len(str(look.get('outfit') or ''))}；"
            f"发型长度={len(str(look.get('hair') or ''))}"
        )

    async def _remember_friend_daily_look(
        self, scope: str, profile_id: str, look: dict[str, str]
    ) -> None:
        normalized = self._normalize_friend_look(look)
        if not normalized["outfit"] and not normalized["hair"]:
            return
        normalized["date"] = time.strftime("%Y-%m-%d")
        normalized["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._friend_daily_looks()[self._friend_daily_look_key(scope, profile_id)] = (
            normalized
        )
        await self._persist_friend_daily_looks()

    @staticmethod
    def _friend_look_prompt(prompt: str, look: dict[str, str]) -> str:
        text = str(prompt or "").strip()
        outfit = str(look.get("outfit") or "").strip()
        hair = str(look.get("hair") or "").strip()
        if not outfit and not hair:
            rule = (
                "人物 B 的参考图只用于确认身份和稳定外观，不锁定本轮服装、配饰或发型。"
                "画面要求已明确人物 B 造型时严格遵循；未明确时再根据当前场景选择独立完整的穿搭与协调发型，"
                "不要复制人物 A 的造型。"
            )
        else:
            parts = []
            if outfit:
                parts.append(f"穿搭：{outfit}")
            if hair:
                parts.append(f"发型：{hair}")
            scene_label = outfit_scene_category_label(look.get("scene_category"))
            pool_label = outfit_style_pool_label(look.get("style_pool"))
            rule = (
                "人物 B 本轮结构化造型（最高优先级）："
                + "；".join(parts)
                + f"；当前场景：{scene_label}；服装属性：{pool_label}"
                + "。好友参考图只用于确认身份和稳定外观，不代表本轮服装、配饰或发型；"
                "参考图造型与上述内容冲突时，必须以上述结构化造型为准。"
            )
        return f"{text}\n{rule}" if text else rule

    @staticmethod
    def _image_delivery_result(action: str) -> str:
        return json.dumps(
            {
                "status": "sent",
                "media": "image",
                "action": action,
                "response_stance": "图片已经真实发送；按当前关系和聊天语境自然递给对方看，也可以不补文字",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _image_prompt_aspect_ratio(text: str) -> str:
        compact = "".join(str(text or "").replace("：", ":").split())
        if not compact:
            return ""
        for index, char in enumerate(compact):
            if char != ":":
                continue
            left = index - 1
            while left >= 0 and compact[left].isdigit():
                left -= 1
            right = index + 1
            while right < len(compact) and compact[right].isdigit():
                right += 1
            if left == index - 1 or right == index + 1:
                continue
            try:
                ratio = f"{int(compact[left + 1 : index])}:{int(compact[index + 1 : right])}"
            except ValueError:
                continue
            if ratio in IMAGE_ASPECT_RATIOS:
                return ratio
        return ""

    @staticmethod
    def _image_prompt_resolution(text: str) -> str:
        """从图片请求中提取最后一个独立的 1K/2K/4K 分辨率标记。"""
        matches = re.findall(
            r"(?<![A-Za-z0-9])([124])\s*[Kk](?![A-Za-z0-9])",
            str(text or ""),
        )
        return f"{matches[-1]}K" if matches else ""

    @staticmethod
    def _image_generation_error_needs_rewrite(exc: Exception) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            text = str(current).lower()
            if "content_policy_violation" in text or "policy_violation" in text:
                return True
            current = getattr(current, "__cause__", None) or getattr(
                current, "__context__", None
            )
        return False

    @staticmethod
    def _image_tool_failure_text(action: str, error: str) -> str:
        detail = str(error or "").strip()
        if not detail:
            return f"{action}失败，已记录失败原因。"
        if len(detail) > 500:
            detail = detail[:500].rstrip() + "..."
        return f"{action}失败：{detail}"

    def _image_policy_rewrite_reason(self, exc: Exception) -> str:
        reason = self._image_policy_rejection_detail(exc)
        if len(reason) > 1000:
            return reason[:1000].rstrip() + "..."
        return reason or type(exc).__name__

    @staticmethod
    def _image_policy_rejection_detail(exc: Exception) -> str:
        current: BaseException | None = exc
        seen: set[int] = set()
        fallback = ""
        http_detail = ""
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            detail = " ".join(str(current or "").strip().split())
            if detail:
                if not fallback:
                    fallback = detail
                marker = detail.find("HTTP ")
                if marker >= 0:
                    http_detail = detail[marker:]
            current = getattr(current, "__cause__", None) or getattr(
                current, "__context__", None
            )
        return http_detail or fallback

    async def _image_operation_with_policy_retry(
        self,
        event: Any,
        prompt: str,
        operation: Callable[[str], Awaitable[Any]],
        *,
        reference: bool = False,
    ) -> Any:
        prompt = str(prompt or "").strip()
        try:
            return await operation(prompt)
        except Exception as exc:
            if not self._image_generation_error_needs_rewrite(exc):
                raise
            rewrite = getattr(self, "_rewrite_life_image_prompt_for_policy_retry", None)
            if not callable(rewrite):
                raise
            reason = self._image_policy_rewrite_reason(exc)
            logger.info(f"{LOG_PREFIX} 图片触发安全拒绝，尝试轻量润色后重试")
            logger.debug(f"{LOG_PREFIX} 图片安全拒绝详情：{reason}")
            try:
                rewritten_prompt = str(
                    await rewrite(event, prompt, reference=reference) or ""
                ).strip()
            except Exception:
                raise RuntimeError("图片触发安全拒绝，轻量润色失败。") from exc
            if not rewritten_prompt or rewritten_prompt == prompt:
                raise RuntimeError(
                    "图片触发安全拒绝，轻量润色没有返回可用的新提示词。"
                ) from exc
            logger.debug(
                f"{LOG_PREFIX} 图片轻量润色完成：提示词长度={len(rewritten_prompt)}"
            )
            try:
                return await operation(rewritten_prompt)
            except Exception as retry_exc:
                raise RuntimeError(
                    f"图片轻量润色后重试仍失败：{self._media_error_summary(retry_exc)}"
                ) from exc

    async def _generate_life_image_with_policy_retry(
        self,
        event: Any,
        prompt: str,
        aspect_ratio: str = "",
        resolution: str = "",
        protocol: str = "",
        *,
        identity_profile: str = "",
    ) -> Any:
        resolution = str(
            resolution or ""
        ).strip().upper() or self._image_prompt_resolution(prompt)

        async def generate(safe_prompt: str) -> Any:
            options = {}
            if aspect_ratio:
                options["aspect_ratio"] = aspect_ratio
            if resolution:
                options["resolution"] = resolution
            if protocol:
                options["protocol"] = protocol
            if identity_profile:
                options["identity_profile"] = identity_profile
            return await self.media.image.generate_image(safe_prompt, **options)

        async with self.runtime_service_lease():
            return await self._image_operation_with_policy_retry(
                event,
                prompt,
                generate,
            )

    async def _edit_life_image_with_policy_retry(
        self,
        event: Any,
        prompt: str,
        reference_image: str,
        aspect_ratio: str = "",
        resolution: str = "",
        *,
        preserve_reference_ratio: bool = True,
        protocol: str = "",
        identity_profile: str = "",
    ) -> Any:
        resolution = str(
            resolution or ""
        ).strip().upper() or self._image_prompt_resolution(prompt)

        async def edit(safe_prompt: str) -> Any:
            options = {"preserve_reference_ratio": preserve_reference_ratio}
            if aspect_ratio:
                options["aspect_ratio"] = aspect_ratio
            if resolution:
                options["resolution"] = resolution
            if protocol:
                options["protocol"] = protocol
            if identity_profile:
                options["identity_profile"] = identity_profile
            return await self.media.image.edit_image(
                safe_prompt,
                reference_image,
                **options,
            )

        async with self.runtime_service_lease():
            return await self._image_operation_with_policy_retry(
                event,
                prompt,
                edit,
                reference=True,
            )

    async def _directed_life_image_result(
        self,
        event: Any,
        prompt: str,
        *,
        direct_prompt: bool = False,
        reference: bool = False,
    ) -> tuple[str, bool, bool]:
        if direct_prompt:
            direct_text = str(prompt or "").strip()
            try:
                result = await self._direct_life_image_payload(
                    event,
                    direct_text,
                    reference=reference,
                    judge_only=True,
                )
                return (
                    direct_text,
                    getattr(result, "contains_character", False) is True,
                    getattr(result, "needs_character_reference", False) is True,
                )
            except Exception:
                return direct_text, False, False
        result = await self._direct_life_image_payload(
            event, prompt, reference=reference
        )
        directed_prompt = str(getattr(result, "prompt", "") or "").strip()
        contains_character = getattr(result, "contains_character", False) is True
        needs_character_reference = (
            getattr(result, "needs_character_reference", False) is True
        )
        return directed_prompt, contains_character, needs_character_reference

    def note_media_source_event(self, event: Any) -> None:
        scope = self._event_session_id(event)
        if not scope or self._proactive_is_self_message(event):
            return
        reader = getattr(self, "_event_user_history_text", None)
        text = str(
            reader(event)
            if callable(reader)
            else getattr(event, "message_str", "") or ""
        ).strip()
        if not text:
            return
        store = getattr(self, "_life_media_source_events", None)
        if not isinstance(store, dict):
            store = {}
            self._life_media_source_events = store
        store[scope] = {
            "text": text,
            "message_id": self._event_message_id(event),
            "timestamp": time.monotonic(),
        }

    @staticmethod
    def _image_prompt_detail_score(text: str) -> int:
        raw = str(text or "")
        compact = "".join(raw.split())
        if not compact:
            return 0
        separators = sum(
            1 for char in raw if char in "，,、；;。.!！?？：:\n\r（）()[]【】"
        )
        line_count = sum(1 for line in raw.splitlines() if line.strip())
        return len(compact) + separators * 8 + max(line_count - 1, 0) * 12

    @staticmethod
    def _normalize_image_subject_route(subject_route: str) -> str:
        route = str(subject_route or "").strip().casefold()
        return (
            route
            if route in {"current_character", "group", "scene", "object", "free"}
            else "free"
        )

    @staticmethod
    def _normalize_image_participants(participants: Any) -> list[str]:
        values = [participants] if isinstance(participants, str) else participants
        if not isinstance(values, (list, tuple, set)):
            return []
        result = []
        for value in values:
            profile_id = str(value or "").strip()
            if profile_id and profile_id not in result:
                result.append(profile_id)
        return result[:2]

    def friend_reference_injection_context(self, event: Any = None) -> str:
        image_service = getattr(getattr(self, "media", None), "image", None)
        can_edit = getattr(image_service, "can_edit_image", None)
        current_reference = getattr(
            image_service, "first_character_reference_image", None
        )
        try:
            if not callable(can_edit) or not can_edit():
                return ""
            if not callable(current_reference) or not current_reference():
                return ""
        except Exception:
            return ""
        resolver = getattr(image_service, "friend_reference_options", None)
        if not callable(resolver):
            return ""
        try:
            options = resolver()
        except Exception:
            return ""
        lines = []
        scope = self._event_session_id(event) if event is not None else ""
        for item in options or []:
            if not isinstance(item, dict):
                continue
            profile_id = str(item.get("profile_id") or "").strip()
            display_name = str(item.get("display_name") or profile_id).strip()
            if profile_id:
                line = f"- {display_name}：{profile_id}"
                look = (
                    self._current_friend_daily_look(scope, profile_id) if scope else {}
                )
                if look:
                    details = "；".join(
                        part
                        for part in (
                            f"穿搭={look.get('outfit')}" if look.get("outfit") else "",
                            f"发型={look.get('hair')}" if look.get("hair") else "",
                            (
                                "当前场景="
                                + outfit_scene_category_label(
                                    look.get("scene_category")
                                )
                                if look.get("scene_category")
                                else ""
                            ),
                            (
                                "服装属性="
                                + outfit_style_pool_label(look.get("style_pool"))
                                if look.get("style_pool")
                                else ""
                            ),
                        )
                        if part
                    )
                    line += f"；当天当前造型：{details}"
                lines.append(line)
        if not lines:
            return ""
        return (
            "\n\n## 可用于合影的好友参考档案\n"
            + "\n".join(lines)
            + "\n用户要求当前角色与其中一位好友合影时，单张调用 life_image_generate；"
            "明确要求一组独立合影照片时调用 life_photo_suite_generate。"
            "明确要求与该好友拍同框视频时调用 life_video_generate，"
            "subject_route 填 group，participants 只填写上方对应的关系档案 ID。"
            "每次新画面都填写 friend_scene_category；好友已有当天造型且服装属性适合当前场景时才继续沿用。"
            "没有当天造型时，必须同时填写人物 B 的完整穿搭和发型。"
            "本轮换装时填写 friend_outfit；服装属性和造型决定由插件根据场景推导；"
            "改变发型时填写 friend_hair，不能只把人物 B 的新造型写进 prompt。"
            f"合影画面要求中把当前角色写为人物 A、好友写为人物 B，分别描述两人的服装、发型、体态和外观呈现。{GROUP_IDENTITY_CONTINUITY_RULE}不要根据姓名或昵称猜测性别。"
        )

    def _event_current_image_request_text(self, event: Any) -> str:
        reader = getattr(self, "_event_user_history_text", None)
        return str(
            reader(event)
            if callable(reader)
            else getattr(event, "message_str", "") or ""
        ).strip()

    def _event_image_prompt_text(self, event: Any) -> str:
        text = self._event_current_image_request_text(event)
        scope = self._event_session_id(event)
        store = getattr(self, "_life_media_source_events", None)
        cached = store.get(scope) if isinstance(store, dict) else None
        if isinstance(cached, dict):
            cached_text = str(cached.get("text") or "").strip()
            cached_age = time.monotonic() - float(cached.get("timestamp") or 0)
            if cached_text and cached_age <= 10 * 60:
                if self._image_prompt_detail_score(
                    cached_text
                ) > self._image_prompt_detail_score(text):
                    return cached_text
        return text

    def _resolve_image_prompt(self, event: Any, prompt: str) -> tuple[str, bool, str]:
        tool_prompt = str(prompt or "").strip()
        event_prompt = self._event_image_prompt_text(event)
        aspect_ratio = self._image_prompt_aspect_ratio(
            event_prompt
        ) or self._image_prompt_aspect_ratio(tool_prompt)
        event_score = self._image_prompt_detail_score(event_prompt)
        tool_score = self._image_prompt_detail_score(tool_prompt)
        event_length = len("".join(event_prompt.split()))
        separators = sum(
            1 for char in event_prompt if char in "，,、；;。.!！?？：:\n\r（）()[]【】"
        )
        reverse_resolver = getattr(self, "_last_reverse_prompt_for_scope", None)
        last_reverse_prompt = str(
            reverse_resolver(event) if callable(reverse_resolver) else ""
        ).strip()
        if tool_prompt and last_reverse_prompt and tool_prompt == last_reverse_prompt:
            return tool_prompt, True, aspect_ratio
        if (
            tool_prompt
            and event_prompt
            and tool_score >= max(event_score + 32, int(event_score * 1.8))
        ):
            if event_length < 18 and separators <= 1:
                return tool_prompt, False, aspect_ratio
            return tool_prompt, True, aspect_ratio
        direct_enough = (
            event_length >= 48
            or (event_length >= 18 and separators >= 2)
            or (
                event_length >= 28
                and event_score >= max(tool_score + 18, int(tool_score * 1.4))
            )
        )
        if event_prompt and direct_enough:
            return event_prompt, True, aspect_ratio
        return tool_prompt or event_prompt, False, aspect_ratio

    @staticmethod
    def _image_generation_mode_label(direct_prompt: bool) -> str:
        return "保持原文" if direct_prompt else "智能提取"

    def _resolve_last_reverse_image_prompt(self, event: Any) -> tuple[str, str]:
        resolver = getattr(self, "_last_reverse_prompt_for_scope", None)
        if not callable(resolver):
            return "", ""
        prompt = str(resolver(event) or "").strip()
        return prompt, self._image_prompt_aspect_ratio(prompt)

    async def _resolve_last_reverse_image_prompt_async(
        self, event: Any
    ) -> tuple[str, str]:
        resolver = getattr(self, "_last_reverse_prompt_record_for_scope", None)
        if callable(resolver):
            record = await resolver(event)
            prompt = (
                str(record.get("prompt") or "").strip()
                if isinstance(record, dict)
                else ""
            )
            if prompt:
                return prompt, self._image_prompt_aspect_ratio(prompt)
        return self._resolve_last_reverse_image_prompt(event)

    def _image_edit_available(self) -> bool:
        image_service = getattr(getattr(self, "media", None), "image", None)
        if image_service is None or not callable(
            getattr(image_service, "edit_image", None)
        ):
            return False
        checker = getattr(image_service, "can_edit_image", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return True

    def _life_character_reference_image(self) -> str:
        image_service = getattr(getattr(self, "media", None), "image", None)
        if image_service is None:
            return ""
        checker = getattr(image_service, "can_edit_image", None)
        if callable(checker):
            try:
                if not checker():
                    return ""
            except Exception:
                return ""
        resolver = getattr(image_service, "first_character_reference_image", None)
        if not callable(resolver):
            return ""
        try:
            return str(resolver() or "").strip()
        except Exception:
            return ""

    async def generate_life_image_asset(
        self,
        event: Any,
        prompt: str,
        aspect_ratio: str = "",
        resolution: str = "",
        *,
        contains_character: bool = False,
        preserve_reference_ratio: bool = False,
        trusted_identity: bool = False,
        protocol: str = "",
        identity_profile: str | None = None,
    ) -> Any:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("没有收到图片提示词。")
        aspect_ratio = str(
            aspect_ratio or ""
        ).strip() or self._image_prompt_aspect_ratio(prompt)
        resolution = str(
            resolution or ""
        ).strip().upper() or self._image_prompt_resolution(prompt)
        if trusted_identity and contains_character:
            directed_prompt = prompt
            character_reference = self._life_character_reference_image()
        else:
            (
                directed_prompt,
                detected_character,
                needs_character_reference,
            ) = await self._directed_life_image_result(
                event,
                prompt,
                direct_prompt=True,
            )
            if contains_character and not detected_character:
                detected_character = True
            contains_character = detected_character
            character_reference = (
                self._life_character_reference_image()
                if needs_character_reference
                else ""
            )
        if contains_character:
            if identity_profile is None:
                identity_profiles = await self._resolve_life_identity_profiles(
                    event, "current_character"
                )
                identity_profile = identity_profiles.get("current_character", "")
            identity_profile = " ".join(str(identity_profile or "").strip().split())[
                :600
            ]
        else:
            identity_profile = ""
        if character_reference:
            reason = (
                "已确认角色本人入镜"
                if trusted_identity and contains_character
                else "图片导演判定需要角色参考图"
            )
            logger.debug(f"{LOG_PREFIX} {reason}，已自动切换到图生图。")
            return await self._edit_life_image_with_policy_retry(
                event,
                directed_prompt,
                character_reference,
                aspect_ratio,
                resolution,
                preserve_reference_ratio=preserve_reference_ratio,
                protocol=protocol,
                identity_profile=identity_profile,
            )
        return await self._generate_life_image_with_policy_retry(
            event,
            directed_prompt,
            aspect_ratio,
            resolution,
            protocol,
            identity_profile=identity_profile,
        )

    async def _prepare_image_generation_plan(
        self,
        event: Any,
        prompt: str,
        *,
        use_last_reverse_prompt: bool,
        subject_route: str,
        participants: list[str] | None,
        resolution: str,
        provider: str,
        current_appearance: str = "",
    ) -> ImageGenerationPlan:
        provider = requested_image_provider(provider)
        if provider:
            logger.debug(
                f"{LOG_PREFIX} 图片协议指定：{image_provider_label(provider)}；"
                "模式=图片生成"
            )
        resolution = str(resolution or "").strip().upper()
        event_prompt = self._event_image_prompt_text(event)
        if not resolution:
            resolution = self._image_prompt_resolution(event_prompt)
        participant_ids = self._normalize_image_participants(participants)
        if use_last_reverse_prompt:
            (
                resolved_prompt,
                aspect_ratio,
            ) = await self._resolve_last_reverse_image_prompt_async(event)
            if not resolution:
                resolution = self._image_prompt_resolution(resolved_prompt)
            return ImageGenerationPlan(
                prompt=resolved_prompt,
                direct_prompt=True,
                aspect_ratio=aspect_ratio,
                generation_mode="上一条反推",
                current_character=False,
                group_request=False,
                participant_ids=participant_ids,
                resolution=resolution,
                provider=provider,
            )

        route = self._normalize_image_subject_route(subject_route)
        tool_prompt = str(prompt or "").strip()
        appearance = str(current_appearance or "").strip()
        tool_prompt = self._apply_current_appearance_snapshot(
            tool_prompt,
            appearance,
            route,
            source_request=self._event_current_image_request_text(event),
        )
        if not resolution:
            resolution = self._image_prompt_resolution(tool_prompt)
        current_character = route == "current_character"
        group_request = route == "group"
        if (group_request or current_character) and tool_prompt:
            resolved_prompt = tool_prompt
            direct_prompt = True
            aspect_ratio = self._image_prompt_aspect_ratio(
                event_prompt
            ) or self._image_prompt_aspect_ratio(tool_prompt)
            generation_mode = "好友合影" if group_request else "当前角色本人"
        else:
            resolved_prompt, direct_prompt, aspect_ratio = self._resolve_image_prompt(
                event, tool_prompt
            )
            generation_mode = self._image_generation_mode_label(direct_prompt)
        return ImageGenerationPlan(
            prompt=resolved_prompt,
            direct_prompt=direct_prompt,
            aspect_ratio=aspect_ratio,
            generation_mode=generation_mode,
            current_character=current_character,
            group_request=group_request,
            participant_ids=participant_ids,
            resolution=resolution,
            provider=provider,
        )

    async def _execute_image_generation_plan(
        self,
        event: Any,
        plan: ImageGenerationPlan,
    ) -> ImageGenerationExecution:
        if plan.group_request:
            if len(plan.participant_ids) != 1:
                return ImageGenerationExecution(
                    error="请明确选择一位已配置参考图的好友再生成合影。"
                )
            generator = getattr(
                getattr(getattr(self, "media", None), "image", None),
                "generate_group_image",
                None,
            )
            if not callable(generator):
                return ImageGenerationExecution(error="当前图片接口不支持好友合影。")
            group_options = {}
            if plan.resolution:
                group_options["resolution"] = plan.resolution
            if plan.provider:
                group_options["protocol"] = plan.provider
            identity_profiles = await self._resolve_life_identity_profiles(
                event, "group"
            )
            if identity_profiles:
                group_options["identity_profiles"] = identity_profiles
            generated = await generator(
                plan.prompt,
                plan.participant_ids,
                plan.aspect_ratio,
                **group_options,
            )
            return ImageGenerationExecution(generated, plan.prompt)

        if plan.direct_prompt:
            generated = await self.generate_life_image_asset(
                event,
                plan.prompt,
                plan.aspect_ratio,
                plan.resolution,
                contains_character=plan.current_character,
                trusted_identity=plan.current_character,
                protocol=plan.provider,
            )
            return ImageGenerationExecution(generated, plan.prompt)

        (
            directed_prompt,
            contains_character,
            needs_character_reference,
        ) = await self._directed_life_image_result(
            event,
            plan.prompt,
            direct_prompt=False,
        )
        character_reference = (
            self._life_character_reference_image() if needs_character_reference else ""
        )
        identity_profile = ""
        if contains_character:
            identity_profiles = await self._resolve_life_identity_profiles(
                event, "current_character"
            )
            identity_profile = identity_profiles.get("current_character", "")
        if character_reference:
            logger.debug(
                f"{LOG_PREFIX} 图片导演判定需要角色参考图，已自动切换到图生图。"
            )
            generated = await self._edit_life_image_with_policy_retry(
                event,
                directed_prompt,
                character_reference,
                plan.aspect_ratio,
                plan.resolution,
                preserve_reference_ratio=False,
                protocol=plan.provider,
                identity_profile=identity_profile,
            )
        else:
            generated = await self._generate_life_image_with_policy_retry(
                event,
                directed_prompt,
                plan.aspect_ratio,
                plan.resolution,
                plan.provider,
                identity_profile=identity_profile,
            )
        return ImageGenerationExecution(generated, directed_prompt)

    async def life_image_generate(
        self,
        event: Any,
        prompt: str,
        *,
        use_last_reverse_prompt: bool = False,
        subject_route: str = "free",
        participants: list[str] | None = None,
        friend_outfit: str = "",
        friend_hair: str = "",
        friend_scene_category: str = "",
        friend_style_pool: str = "",
        friend_outfit_decision: str = "",
        current_outfit_change: bool = False,
        current_outfit_instruction: str = "",
        resolution: str = "",
        provider: str = "",
    ) -> str | None:
        route = self._normalize_image_subject_route(subject_route)
        current_appearance = ""
        if current_outfit_change:
            if use_last_reverse_prompt:
                return "真实换装不能与上一条反推提示词同时使用。"
            if route not in {"current_character", "group"}:
                return (
                    "真实换装生图请将 subject_route 设为 current_character 或 group。"
                )
            instruction = str(current_outfit_instruction or "").strip()
            if not instruction:
                instruction = self._event_current_image_request_text(event)
            if not instruction:
                return "没有收到当前角色的换装要求。"

            current_time = self._runtime_now()
            target_date, _ = await self.resolve_injection_target(current_time)
            target_period = self._get_curr_period(current_time)
            previous_appearance = ""
            archive = getattr(self, "archive", None)
            get_day = getattr(archive, "get_day", None)
            if callable(get_day):
                previous_day = await get_day(target_date)
                previous_appearance = format_current_appearance_context(previous_day)
            async with operation_lock(self, f"outfit:{target_date}"):
                updated_day = await self.composer.update_outfit(
                    target_date,
                    target_period,
                    current_time=current_time,
                    instruction=instruction,
                )
            if updated_day is None:
                logger.warning(f"{LOG_PREFIX} 当前角色穿搭更新失败，已取消图片生成。")
                return "这次换装状态没有更新成功，已取消图片生成。"

            current_appearance = format_current_appearance_context(updated_day)
            if not current_appearance:
                logger.warning(
                    f"{LOG_PREFIX} 当前角色穿搭更新后没有可用造型，已取消图片生成。"
                )
                return "这次换装没有生成可用造型，已取消图片生成。"
            if previous_appearance and current_appearance == previous_appearance:
                logger.warning(
                    f"{LOG_PREFIX} 明确换装请求没有产生状态变化，已取消图片生成。"
                )
                return "这次没有产生新的穿搭变化，已取消图片生成。"
            visual_prompt = str(prompt or "").strip()
            if not visual_prompt:
                visual_prompt = "当前角色完成换装后的自然生活照。"
            prompt = visual_prompt
            await self.mark_page_status_changed("outfit_update")

        if not current_appearance:
            current_appearance = await self._current_life_appearance_snapshot(route)

        plan = await self._prepare_image_generation_plan(
            event,
            prompt,
            use_last_reverse_prompt=use_last_reverse_prompt,
            subject_route=route,
            participants=participants,
            resolution=resolution,
            provider=provider,
            current_appearance=current_appearance,
        )
        if not plan.prompt:
            return "没有收到图片提示词。"
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送图片。"
        friend_look: dict[str, str] = {}
        friend_look_persist = False
        if plan.group_request and len(plan.participant_ids) == 1:
            friend_look, look_source, missing = await self._prepare_friend_daily_look(
                event,
                plan.participant_ids[0],
                outfit=friend_outfit,
                hair=friend_hair,
                scene=plan.prompt,
                scene_category=friend_scene_category,
                style_pool=friend_style_pool,
                decision=friend_outfit_decision,
            )
            if missing:
                required = self._friend_look_required_parameters(look_source, missing)
                logger.debug(
                    f"{LOG_PREFIX} 好友合影暂未生成：缺少好友当天穿搭或发型；"
                    f"参数={','.join(missing)}"
                )
                return self._friend_look_parameters_result(required)
            self._log_friend_daily_look(
                plan.participant_ids[0], friend_look, look_source
            )
            friend_look_persist = self._friend_look_should_persist(look_source)
            plan.prompt = self._friend_look_prompt(plan.prompt, friend_look)
            plan.prompt += await self._build_person_fact_injection_context(event)
        started_at = time.monotonic()
        try:
            execution = await self._execute_image_generation_plan(event, plan)
            if execution.error:
                return execution.error
            generated = execution.generated
            directed_prompt = execution.directed_prompt
            logger.debug(
                f"{LOG_PREFIX} 图片生成模式：{plan.generation_mode}；长度：{len(directed_prompt)}"
            )
            delivery_task = await self.stage_durable_media_delivery(
                scope,
                "image",
                [str(generated.path)],
                action_type="photo",
                evidence="图片已生成，等待投递确认",
            )
            if not await self.send_message_if_not_recalled(
                scope,
                self.image_message_chain(generated.path),
                source_event=event,
            ):
                await self.finalize_durable_media_delivery(
                    delivery_task,
                    outcome="cancelled",
                    detail="原消息已撤回，取消图片投递",
                )
                return "原消息已撤回，已取消图片发送。"
            self.note_structured_bot_message(
                scope, "[图片已发送]", source_event=event, media="图片"
            )
            self._remember_life_image_for_scope(scope, generated.path)
            if (
                plan.group_request
                and len(plan.participant_ids) == 1
                and friend_look
                and friend_look_persist
            ):
                await self._remember_friend_daily_look(
                    scope, plan.participant_ids[0], friend_look
                )
            self.note_life_media_sent(event, "图片")
            receipt_recorder = getattr(self, "record_current_life_action_receipt", None)
            if callable(receipt_recorder):
                await receipt_recorder(
                    event,
                    "photo",
                    evidence="图片已生成并成功发送",
                    source="image_delivery",
                    artifact_path=str(generated.path),
                )
            await self.finalize_durable_media_delivery(
                delivery_task,
                outcome="sent",
                detail="图片已发送",
            )
            summary = await self._media_result_summary(generated.path, started_at)
            logger.info(f"{LOG_PREFIX} 图片已发送：{summary}")
            return self._image_delivery_result("generate")
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 图片生成或发送失败：{error}")
            return self._image_tool_failure_text("图片生成", error)

    async def _execute_image_edit(
        self,
        event: Any,
        prompt: str,
        reference: str,
        participant_ids: list[str],
        aspect_ratio: str,
        resolution: str,
        provider: str,
        *,
        direct_prompt: bool,
    ) -> ImageGenerationExecution:
        if participant_ids:
            if len(participant_ids) != 1:
                return ImageGenerationExecution(
                    error="请明确选择一位已配置参考图的好友再生成合影。"
                )
            generator = getattr(
                getattr(getattr(self, "media", None), "image", None),
                "generate_group_image",
                None,
            )
            if not callable(generator):
                return ImageGenerationExecution(error="当前图片接口不支持好友合影。")
            group_options = {}
            if resolution:
                group_options["resolution"] = resolution
            if provider:
                group_options["protocol"] = provider
            identity_profiles = await self._resolve_life_identity_profiles(
                event, "group"
            )
            if identity_profiles:
                group_options["identity_profiles"] = identity_profiles
            generated = await generator(
                prompt,
                participant_ids,
                aspect_ratio,
                scene_reference=reference,
                **group_options,
            )
            return ImageGenerationExecution(
                generated=generated,
                directed_prompt=prompt,
                generation_mode="好友合影参考图再创作",
            )

        (
            directed_prompt,
            contains_character,
            needs_character_reference,
        ) = await self._directed_life_image_result(
            event,
            prompt,
            direct_prompt=direct_prompt,
            reference=True,
        )
        if needs_character_reference:
            logger.debug(
                f"{LOG_PREFIX} 图片身份路线为当前角色本人；本次继续使用用户参考图编辑。"
            )
        identity_profile = ""
        if contains_character:
            identity_profiles = await self._resolve_life_identity_profiles(
                event, "current_character"
            )
            identity_profile = identity_profiles.get("current_character", "")
        generated = await self._edit_life_image_with_policy_retry(
            event,
            directed_prompt,
            reference,
            aspect_ratio,
            resolution,
            preserve_reference_ratio=not bool(aspect_ratio),
            protocol=provider,
            identity_profile=identity_profile,
        )
        return ImageGenerationExecution(
            generated=generated,
            directed_prompt=directed_prompt,
            generation_mode=self._image_generation_mode_label(direct_prompt),
        )

    async def _deliver_edited_life_image(
        self,
        event: Any,
        scope: str,
        generated: Any,
        started_at: float,
    ) -> str:
        delivery_task = await self.stage_durable_media_delivery(
            scope,
            "image",
            [str(generated.path)],
            action_type="photo",
            evidence="参考图图片已生成，等待投递确认",
        )
        if not await self.send_message_if_not_recalled(
            scope,
            self.image_message_chain(generated.path),
            source_event=event,
        ):
            await self.finalize_durable_media_delivery(
                delivery_task,
                outcome="cancelled",
                detail="原消息已撤回，取消参考图图片投递",
            )
            return "原消息已撤回，已取消图片发送。"
        self.note_structured_bot_message(
            scope, "[图片已发送]", source_event=event, media="图片"
        )
        self._remember_life_image_for_scope(scope, generated.path)
        self.note_life_media_sent(event, "图片")
        receipt_recorder = getattr(self, "record_current_life_action_receipt", None)
        if callable(receipt_recorder):
            await receipt_recorder(
                event,
                "photo",
                evidence="参考图图片已生成并成功发送",
                source="image_delivery",
                artifact_path=str(generated.path),
            )
        await self.finalize_durable_media_delivery(
            delivery_task,
            outcome="sent",
            detail="参考图图片已发送",
        )
        summary = await self._media_result_summary(generated.path, started_at)
        logger.info(f"{LOG_PREFIX} 参考图生成结果已发送：{summary}")
        return self._image_delivery_result("edit")

    async def edit_life_image(
        self,
        event: Any,
        prompt: str,
        reference_image: str = "",
        *,
        continue_last_result: bool = False,
        generate_without_reference: bool = False,
        participants: list[str] | None = None,
        resolution: str = "",
        provider: str = "",
    ) -> str | None:
        provider = requested_image_provider(provider)
        if provider:
            logger.debug(
                f"{LOG_PREFIX} 图片协议指定：{image_provider_label(provider)}；"
                "模式=图片编辑"
            )
        resolution = str(resolution or "").strip().upper()
        participant_ids = self._normalize_image_participants(participants)
        group_request = bool(participant_ids)
        raw_prompt = str(prompt or "").strip()
        if not resolution:
            resolution = self._image_prompt_resolution(
                self._event_image_prompt_text(event)
            ) or self._image_prompt_resolution(raw_prompt)
        prompt, direct_prompt, aspect_ratio = self._resolve_image_prompt(
            event, raw_prompt
        )
        if not prompt:
            return "没有收到图片编辑提示词。"
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送图片。"
        reference = await self._resolve_life_image_reference_async(
            event,
            reference_image,
            allow_last_generated=True,
            prefer_last_generated=bool(continue_last_result),
        )
        if not reference:
            logger.debug(
                f"{LOG_PREFIX} 图片编辑参考不可用：当前会话没有可继续修改的图片"
            )
            if (
                generate_without_reference
                and not continue_last_result
                and not str(reference_image or "").strip()
            ):
                logger.debug(
                    f"{LOG_PREFIX} 图片编辑未找到参考图，已按工具参数改走文生图。"
                )
                return await self.life_image_generate(
                    event,
                    prompt,
                    subject_route="group" if group_request else "free",
                    participants=participant_ids,
                    resolution=resolution,
                )
            if continue_last_result or str(reference_image or "").strip():
                return "当前会话没有可继续修改的图片，请重新发送或引用原图。"
            return "请先发送或引用一张要参考的图片。"
        started_at = time.monotonic()
        try:
            execution = await self._execute_image_edit(
                event,
                prompt,
                reference,
                participant_ids,
                aspect_ratio,
                resolution,
                provider,
                direct_prompt=direct_prompt,
            )
            if execution.error:
                return execution.error
            logger.debug(
                f"{LOG_PREFIX} 图片生成模式：{execution.generation_mode}；"
                f"长度：{len(execution.directed_prompt)}"
            )
            return await self._deliver_edited_life_image(
                event,
                scope,
                execution.generated,
                started_at,
            )
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 参考图生成或发送失败：{error}")
            return self._image_tool_failure_text("参考图生成", error)
