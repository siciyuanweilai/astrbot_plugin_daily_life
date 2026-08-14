from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ...media.base import GROUP_IDENTITY_CONTINUITY_RULE
from ...paths import expand_path, path_is_file
from ...prompts import CORE_MEDIA_REPLY_RULES, cache_friendly_prompt
from ..delivery import BackgroundTextMode
from ..markers import LOG_PREFIX


@dataclass(frozen=True, slots=True)
class LifeVideoRequest:
    scope: str
    prompt: str
    event: Any
    request_id: str
    direct_prompt: bool
    subject_route: str
    participants: tuple[str, ...]
    identity_profiles: dict[str, str]
    person_fact_context: str
    current_appearance: str
    source_request: str
    friend_look: dict[str, str]
    friend_look_persist: bool
    continue_last_result: bool
    initial_reference_image: str


@dataclass(frozen=True, slots=True)
class LifeVideoExecution:
    generated: Any
    route: str
    participants: tuple[str, ...]
    friend_look: dict[str, str]
    friend_look_persist: bool
    first_frame: str
    reference_image: str


class RuntimeVideoMediaMixin:
    @staticmethod
    def _video_prompt_duration_seconds(text: str) -> int:
        compact = str(text or "").strip()
        if not compact:
            return 0
        seconds: list[int] = []
        index = 0
        while index < len(compact):
            number, end = RuntimeVideoMediaMixin._video_read_number(compact, index)
            if end <= index:
                index += 1
                continue
            cursor = RuntimeVideoMediaMixin._video_skip_spaces(compact, end)
            if cursor < len(compact) and compact[cursor] in "-–—~到至":
                right_start = RuntimeVideoMediaMixin._video_skip_spaces(
                    compact, cursor + 1
                )
                right, right_end = RuntimeVideoMediaMixin._video_read_number(
                    compact, right_start
                )
                unit_at = RuntimeVideoMediaMixin._video_skip_spaces(compact, right_end)
                if (
                    right_end > right_start
                    and RuntimeVideoMediaMixin._video_has_duration_unit(
                        compact, unit_at
                    )
                ):
                    seconds.append(RuntimeVideoMediaMixin._video_clamp_seconds(right))
                    index = unit_at + 1
                    continue
            if RuntimeVideoMediaMixin._video_has_duration_unit(compact, cursor):
                seconds.append(RuntimeVideoMediaMixin._video_clamp_seconds(number))
                index = cursor + 1
                continue
            index = end
        return max(seconds) if seconds else 0

    @staticmethod
    def _video_read_number(text: str, start: int) -> tuple[float, int]:
        if start >= len(text) or not text[start].isdigit():
            return 0.0, start
        end = start
        dot_seen = False
        while end < len(text):
            char = text[end]
            if char.isdigit():
                end += 1
                continue
            if (
                char == "."
                and not dot_seen
                and end + 1 < len(text)
                and text[end + 1].isdigit()
            ):
                dot_seen = True
                end += 1
                continue
            break
        try:
            return float(text[start:end]), end
        except ValueError:
            return 0.0, start

    @staticmethod
    def _video_skip_spaces(text: str, start: int) -> int:
        while start < len(text) and text[start].isspace():
            start += 1
        return start

    @staticmethod
    def _video_has_duration_unit(text: str, start: int) -> bool:
        if start >= len(text):
            return False
        if text.startswith("秒钟", start) or text[start] == "秒":
            return True
        if text[start] in {"s", "S"}:
            next_index = start + 1
            return (
                next_index >= len(text)
                or not text[next_index].isascii()
                or not text[next_index].isalnum()
            )
        return False

    @staticmethod
    def _video_clamp_seconds(value: float) -> int:
        if value <= 0:
            return 0
        seconds = int(value) if value.is_integer() else int(value) + 1
        return max(1, min(15, seconds))

    def _event_video_prompt_text(self, event: Any) -> str:
        resolver = getattr(self, "_event_image_prompt_text", None)
        text = str(resolver(event) if callable(resolver) else "").strip()
        if not text:
            reader = getattr(self, "_event_user_history_text", None)
            text = str(
                reader(event)
                if callable(reader)
                else getattr(event, "message_str", "") or ""
            ).strip()
        return text

    def _resolve_video_prompt(self, event: Any, prompt: str) -> tuple[str, bool]:
        tool_prompt = str(prompt or "").strip()
        event_prompt = self._event_video_prompt_text(event)
        if not event_prompt:
            return tool_prompt, False
        event_score = self._image_prompt_detail_score(event_prompt)
        tool_score = self._image_prompt_detail_score(tool_prompt)
        line_count = sum(1 for line in event_prompt.splitlines() if line.strip())
        event_length = len("".join(event_prompt.split()))
        if event_length >= 80 and (
            line_count >= 3
            or event_score >= max(tool_score + 80, int(tool_score * 1.45))
        ):
            return event_prompt, True
        return tool_prompt or event_prompt, False

    async def generate_life_video_asset(
        self,
        event: Any,
        prompt: str,
        reference_image: str = "",
        *,
        direct_prompt: bool = False,
        group_video: bool = False,
    ) -> Any:
        async with self.runtime_service_lease():
            return await self._generate_life_video_asset_locked(
                event,
                prompt,
                reference_image,
                direct_prompt=direct_prompt,
                group_video=group_video,
            )

    async def _generate_life_video_asset_locked(
        self,
        event: Any,
        prompt: str,
        reference_image: str = "",
        *,
        direct_prompt: bool = False,
        group_video: bool = False,
    ) -> Any:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("没有收到视频提示词。")
        reference_image = str(reference_image or "").strip()
        if reference_image:
            try:
                image_bytes = await self._load_life_video_reference_image(
                    reference_image
                )
            except Exception as exc:
                raise RuntimeError(
                    f"当前消息图片不可用，无法作为视频首帧：{self._media_error_summary(exc)}"
                ) from exc
            reference_aspect_ratio = self._life_video_reference_aspect_ratio(
                image_bytes
            )
        else:
            reference_aspect_ratio = ""
            first_frame = await self._generate_life_video_first_frame("", prompt, event)
            image_bytes = await self._load_life_video_reference_image(first_frame)
        directed_prompt = (
            prompt
            if direct_prompt
            else await self._direct_life_video_prompt(event, prompt)
        )
        if group_video:
            directed_prompt = (
                f"{directed_prompt}\n这是两位既定人物同框的视频。"
                f"{GROUP_IDENTITY_CONTINUITY_RULE}"
                "视频中的本轮服装、配饰和发型以首帧已经呈现的双方造型为准，保持前后连续。"
                "具体站位、距离、互动、遮挡、动作幅度、转身和镜头运动服从当前剧情与镜头设计。"
            )
        aspect_ratio = self._image_prompt_aspect_ratio(prompt) or reference_aspect_ratio
        duration = self._video_prompt_duration_seconds(prompt)
        if aspect_ratio or duration:
            kwargs: dict[str, Any] = {}
            if aspect_ratio:
                kwargs["aspect_ratio"] = aspect_ratio
            if duration:
                kwargs["duration"] = duration
            return await self.media.video.generate_video(
                directed_prompt,
                image_bytes=image_bytes,
                **kwargs,
            )
        return await self.media.video.generate_video(
            directed_prompt, image_bytes=image_bytes
        )

    async def life_video_generate(
        self,
        event: Any,
        prompt: str,
        *,
        subject_route: str = "free",
        participants: list[str] | None = None,
        friend_outfit: str = "",
        friend_hair: str = "",
        friend_scene_category: str = "",
        friend_style_pool: str = "",
        friend_outfit_decision: str = "",
        continue_last_result: bool = False,
    ) -> str:
        prompt, direct_prompt = self._resolve_video_prompt(event, prompt)
        if not prompt:
            return "没有收到视频提示词。"
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送视频。"
        route = self._normalize_image_subject_route(subject_route)
        participant_ids = self._normalize_image_participants(participants)
        if route == "group" and len(participant_ids) > 1:
            return "当前合影视频只能选择一位好友。"
        initial_reference_image = await self._resolve_life_image_reference_async(
            event,
            allow_last_generated=bool(continue_last_result),
            prefer_last_generated=bool(continue_last_result),
        )
        friend_look: dict[str, str] = {}
        friend_look_persist = False
        if route == "group" and not initial_reference_image:
            if len(participant_ids) != 1:
                return "没有现成合影首帧，请选择一位已配置参考图的好友。"
            friend_look, look_source, missing = await self._prepare_friend_daily_look(
                event,
                participant_ids[0],
                outfit=friend_outfit,
                hair=friend_hair,
                scene=prompt,
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
            self._log_friend_daily_look(participant_ids[0], friend_look, look_source)
            friend_look_persist = self._friend_look_should_persist(look_source)
        current_appearance = ""
        if not initial_reference_image:
            current_appearance = await self._current_life_appearance_snapshot(route)
        source_request = self._event_current_image_request_text(event)
        if current_appearance and route in {"current_character", "group"}:
            prompt = await self._align_current_appearance_scene_prompt(
                prompt,
                source_request,
                route,
            )
        request_id = self._register_life_video_request(scope, prompt, event)
        request = LifeVideoRequest(
            scope=scope,
            prompt=prompt,
            event=event,
            request_id=request_id,
            direct_prompt=direct_prompt,
            subject_route=route,
            participants=tuple(participant_ids),
            identity_profiles=await self._resolve_life_identity_profiles(event, route),
            person_fact_context=(
                await self._build_person_fact_injection_context(event)
                if route == "group" and participant_ids
                else ""
            ),
            current_appearance=current_appearance,
            source_request=source_request,
            friend_look=dict(friend_look),
            friend_look_persist=friend_look_persist,
            continue_last_result=bool(continue_last_result),
            initial_reference_image=str(initial_reference_image or "").strip(),
        )
        self._schedule_background_task(
            self._life_video_generate_background(request),
            label="生活视频生成",
            key=f"life_video:{scope}:{prompt[:80]}",
        )
        return json.dumps(
            {
                "status": "pending",
                "media": "video",
                "response_timing": "after_delivery",
                "response_stance": "当前不再发送文字；真实视频发送后再按聊天语境自然补一句",
            },
            ensure_ascii=False,
        )

    async def _life_video_generate_background(self, request: LifeVideoRequest) -> None:
        started_at = time.monotonic()
        try:
            execution = await self._execute_life_video_request(request)
            await self._deliver_life_video_execution(request, execution, started_at)
        except asyncio.CancelledError:
            self.cancel_tool_reaction(request.event, "life_video_generate")
            raise
        except Exception as exc:
            error = self._media_error_summary(exc)
            logger.warning(f"{LOG_PREFIX} 视频生成或发送失败：{error}")
            self._update_life_video_request(
                request.request_id,
                video_status="failed",
                failure_reason=error,
            )
            await self._send_life_video_failure_notice(
                request.scope,
                request.prompt,
                error,
                request.event,
                request_id=request.request_id,
            )
            await self.finish_tool_reaction(
                request.event, "life_video_generate", success=False
            )
        finally:
            self._finish_life_video_request(request.request_id)

    async def _execute_life_video_request(
        self, request: LifeVideoRequest
    ) -> LifeVideoExecution:
        route = self._normalize_image_subject_route(request.subject_route)
        participant_ids = tuple(
            self._normalize_image_participants(request.participants)
        )
        reference_image = request.initial_reference_image
        if not reference_image:
            reference_image = await self._resolve_life_image_reference_async(
                request.event,
                allow_last_generated=request.continue_last_result,
                prefer_last_generated=request.continue_last_result,
            )
        first_frame = ""
        generation_prompt = self._apply_current_appearance_snapshot(
            request.prompt,
            request.current_appearance,
            route,
            source_request=request.source_request,
        )
        generation_prompt += request.person_fact_context
        if not reference_image:
            if route == "group":
                if len(participant_ids) != 1:
                    raise ValueError("没有现成合影首帧，请选择一位已配置参考图的好友。")
                first_frame = await self._generate_life_group_video_first_frame(
                    generation_prompt,
                    request.event,
                    list(participant_ids),
                    friend_look=request.friend_look,
                    identity_profiles=request.identity_profiles,
                )
            else:
                first_frame = await self._generate_life_video_first_frame(
                    request.scope,
                    generation_prompt,
                    request.event,
                    identity_profiles=request.identity_profiles,
                )
            self._update_life_video_request(
                request.request_id, first_frame_path=first_frame
            )
        generated = await self.generate_life_video_asset(
            request.event,
            generation_prompt,
            reference_image or first_frame,
            direct_prompt=request.direct_prompt,
            group_video=route == "group",
        )
        return LifeVideoExecution(
            generated=generated,
            route=route,
            participants=participant_ids,
            friend_look=request.friend_look,
            friend_look_persist=request.friend_look_persist,
            first_frame=first_frame,
            reference_image=reference_image,
        )

    async def _deliver_life_video_execution(
        self,
        request: LifeVideoRequest,
        execution: LifeVideoExecution,
        started_at: float,
    ) -> None:
        generated_url = str(getattr(execution.generated, "url", "") or "").strip()
        delivery_task = await self.stage_durable_media_delivery(
            request.scope,
            "video",
            [generated_url],
            action_type="video",
            evidence="视频已生成，等待投递确认",
        )
        if not await self.send_message_if_not_recalled(
            request.scope,
            self.video_message_chain(generated_url),
            source_event=request.event,
        ):
            await self.finalize_durable_media_delivery(
                delivery_task,
                outcome="cancelled",
                detail="原消息已撤回，取消视频投递",
            )
            self._update_life_video_request(
                request.request_id, video_status="cancelled"
            )
            self.cancel_tool_reaction(request.event, "life_video_generate")
            return
        self._update_life_video_request(request.request_id, video_status="sent")
        summary = await self._media_result_summary(generated_url, started_at)
        logger.info(f"{LOG_PREFIX} 视频已发送：{summary}")
        self.note_structured_bot_message(
            request.scope,
            f"[视频已发送：{summary}]",
            source_event=request.event,
            media="视频",
        )
        self._remember_life_image_for_scope(
            request.scope, execution.first_frame or execution.reference_image
        )
        if (
            execution.route == "group"
            and len(execution.participants) == 1
            and execution.friend_look
            and execution.friend_look_persist
        ):
            await self._remember_friend_daily_look(
                request.scope,
                execution.participants[0],
                execution.friend_look,
            )
        self.note_life_media_sent(request.event or request.scope, "视频")
        receipt_recorder = getattr(self, "record_current_life_action_receipt", None)
        if callable(receipt_recorder):
            await receipt_recorder(
                request.event,
                "video",
                evidence="视频已生成并成功发送",
                source="video_delivery",
                artifact_path=generated_url,
            )
        await self.finalize_durable_media_delivery(
            delivery_task,
            outcome="sent",
            detail="视频已发送",
        )
        await self._send_life_video_followup(
            request.scope,
            request.prompt,
            summary,
            request.event,
            request.request_id,
        )
        await self.finish_tool_reaction(
            request.event, "life_video_generate", success=True
        )

    def _update_life_video_request(self, request_id: str, **values: str) -> None:
        marker = self._life_video_requests().get(request_id)
        if isinstance(marker, dict):
            marker.update(values)

    async def _generate_life_video_first_frame(
        self,
        scope: str,
        prompt: str,
        event: Any,
        *,
        identity_profiles: dict[str, str] | None = None,
    ) -> str:
        image_service = getattr(getattr(self, "media", None), "image", None)
        if image_service is None:
            raise RuntimeError("未配置图片生成服务，无法自动生成视频首帧。")
        directed = await self._direct_life_image_payload(event, prompt, reference=False)
        image_prompt = str(getattr(directed, "prompt", "") or "").strip()
        if not image_prompt:
            raise RuntimeError("视频首帧图片提示词为空。")
        aspect_ratio = self._image_prompt_aspect_ratio(
            prompt
        ) or self._image_prompt_aspect_ratio(image_prompt)
        character_reference = (
            self._life_character_reference_image()
            if getattr(directed, "needs_character_reference", False) is True
            else ""
        )
        identity_profile = ""
        if isinstance(identity_profiles, dict):
            identity_profile = " ".join(
                str(identity_profiles.get("current_character") or "").split()
            )[:600]
        if (
            not identity_profile
            and getattr(directed, "contains_character", False) is True
        ):
            resolved_profiles = await self._resolve_life_identity_profiles(
                event, "current_character"
            )
            identity_profile = resolved_profiles.get("current_character", "")
        if character_reference:
            logger.debug(
                f"{LOG_PREFIX} 视频首帧判定需要角色参考图，已自动切换到图生图。"
            )
            generated = await self._edit_life_image_with_policy_retry(
                event,
                image_prompt,
                character_reference,
                aspect_ratio,
                preserve_reference_ratio=False,
                identity_profile=identity_profile,
            )
        else:
            generate_image = getattr(image_service, "generate_image", None)
            if not callable(generate_image):
                raise RuntimeError("未配置图片生成服务，无法自动生成视频首帧。")
            generated = await self._generate_life_image_with_policy_retry(
                event,
                image_prompt,
                aspect_ratio,
                identity_profile=identity_profile,
            )
        path = str(getattr(generated, "path", "") or "").strip()
        if not path:
            raise RuntimeError("首帧图片生成完成但没有返回图片路径。")
        logger.debug(f"{LOG_PREFIX} 视频首帧图片已生成：{Path(path).name}")
        return path

    async def _generate_life_group_video_first_frame(
        self,
        prompt: str,
        event: Any,
        participant_ids: list[str],
        *,
        friend_look: dict[str, str] | None = None,
        identity_profiles: dict[str, str] | None = None,
    ) -> str:
        image_service = getattr(getattr(self, "media", None), "image", None)
        generator = getattr(image_service, "generate_group_image", None)
        if not callable(generator):
            raise RuntimeError("当前图片接口不支持好友合影，无法生成合影视频首帧。")
        directed = await self._direct_life_image_payload(event, prompt, reference=False)
        image_prompt = str(getattr(directed, "prompt", "") or "").strip()
        if not image_prompt:
            raise RuntimeError("合影视频首帧图片提示词为空。")
        image_prompt = self._friend_look_prompt(image_prompt, friend_look or {})
        image_prompt = (
            f"{image_prompt}\n双人视频首帧：{GROUP_IDENTITY_CONTINUITY_RULE}"
            "具体站位、距离、互动、遮挡与构图服从当前剧情和镜头设计。"
        )
        aspect_ratio = self._image_prompt_aspect_ratio(
            prompt
        ) or self._image_prompt_aspect_ratio(image_prompt)
        options = {"reference_context": "合影首帧"}
        current_profiles = self._current_character_identity_profiles(identity_profiles)
        if current_profiles:
            options["identity_profiles"] = current_profiles
        generated = await generator(
            image_prompt, participant_ids, aspect_ratio, **options
        )
        path = str(getattr(generated, "path", "") or "").strip()
        if not path:
            raise RuntimeError("合影首帧生成完成但没有返回图片路径。")
        logger.debug(f"{LOG_PREFIX} 合影视频首帧图片已生成：{Path(path).name}")
        return path

    def _life_video_requests(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_life_video_pending_requests", None)
        if not isinstance(store, dict):
            store = {}
            self._life_video_pending_requests = store
        return store

    def _register_life_video_request(self, scope: str, prompt: str, event: Any) -> str:
        request_id = uuid.uuid4().hex
        marker = {
            "id": request_id,
            "scope": str(scope or "").strip(),
            "prompt": str(prompt or "").strip(),
            "created_at": time.monotonic(),
            "llm_final_seen": False,
            "first_frame_path": "",
            "video_status": "pending",
            "failure_reason": "",
            "fallback_photo_sent": False,
            "failure_reply_sent": False,
        }
        self._life_video_requests()[request_id] = marker
        for source in self._event_sources(event):
            setattr(source, "_daily_life_video_request_id", request_id)
        return request_id

    def _life_video_request_from_event(self, event: Any) -> dict[str, Any] | None:
        requests = self._life_video_requests()
        for source in self._event_sources(event):
            request_id = str(
                getattr(source, "_daily_life_video_request_id", "") or ""
            ).strip()
            if request_id and request_id in requests:
                return requests[request_id]
        scope = self._event_session_id(event)
        if not scope:
            return None
        candidates = [
            item
            for item in requests.values()
            if str(item.get("scope") or "") == scope
            and time.monotonic() - float(item.get("created_at") or 0) <= 20
        ]
        return candidates[-1] if candidates else None

    def _finish_life_video_request(self, request_id: str) -> None:
        request_id = str(request_id or "").strip()
        if request_id:
            self._life_video_requests().pop(request_id, None)

    async def _send_life_video_failure_notice(
        self,
        scope: str,
        prompt: str,
        error: str,
        event: Any,
        *,
        request_id: str = "",
    ) -> bool:
        marker = self._life_video_requests().get(str(request_id or "").strip())
        photo_path = ""
        if isinstance(marker, dict):
            candidate = str(marker.get("first_frame_path") or "").strip()
            if candidate and await asyncio.to_thread(path_is_file, candidate):
                photo_path = candidate

        # 先发送可用图片，再生成补充文字，让文字描述已经呈现的兜底结果，
        # 避免在图片发送前提前说明。
        photo_sent = False
        if photo_path:
            try:
                photo_sent = await self.send_message_if_not_recalled(
                    scope,
                    self.image_message_chain(photo_path),
                    source_event=event,
                )
                if photo_sent:
                    self.note_structured_bot_message(
                        scope,
                        "[视频失败图片已发送]",
                        source_event=event,
                        media="图片",
                    )
                    self._remember_life_image_for_scope(scope, photo_path)
                    self.note_life_media_sent(event or scope, "图片")
                    logger.debug(
                        f"{LOG_PREFIX} 视频失败，图片已发送：{Path(photo_path).name}"
                    )
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} 视频失败，图片发送失败：{self._media_error_summary(exc)}"
                )

        text = await self._generate_life_video_failure_text(
            event, prompt, error, photo_sent=photo_sent
        )
        if not text:
            text = self._life_video_failure_fallback_text(photo_sent)
        try:
            text_sent = await self._send_life_video_text(
                scope, text, source_event=event, source="video_failure"
            )
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} 视频失败补话发送失败：{self._media_error_summary(exc)}"
            )
            text_sent = False
        if text_sent:
            try:
                await self._append_assistant_history(scope, text)
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 视频失败补话历史记录失败：{self._media_error_summary(exc)}"
                )
            if isinstance(marker, dict):
                marker["failure_reply_sent"] = True
        if isinstance(marker, dict):
            marker["fallback_photo_sent"] = photo_sent
            if photo_path and not photo_sent:
                logger.warning(f"{LOG_PREFIX} 视频失败，图片不可用或发送失败")
        logger.info(
            f"{LOG_PREFIX} 视频失败交付完成：文字={'已发送' if text_sent else '未发送'}；"
            f"图片={'已发送' if photo_sent else '未发送'}"
        )
        return text_sent or photo_sent

    @staticmethod
    def _life_video_failure_fallback_text(photo_sent: bool) -> str:
        if photo_sent:
            return "视频没拍成，先把这张照片发你看。"
        return "刚才没发出去，这次视频没有拍成。"

    async def _life_video_reply_model(self, event: Any) -> tuple[Any, Any, str] | None:
        get_provider = getattr(self, "get_text_provider", None)
        call_llm = getattr(self, "call_text_model", None)
        if not callable(get_provider) or not callable(call_llm):
            return None
        provider = await get_provider("")
        if provider is None:
            return None
        persona_getter = getattr(self, "get_persona_text", None)
        persona = ""
        if callable(persona_getter):
            try:
                persona = str(
                    await persona_getter(self._event_session_id(event)) or ""
                ).strip()
            except TypeError:
                try:
                    persona = str(await persona_getter() or "").strip()
                except Exception as exc:
                    logger.debug(
                        f"{LOG_PREFIX} 视频补话未取得角色资料："
                        f"{self._media_error_summary(exc)}"
                    )
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 视频补话未取得角色资料："
                    f"{self._media_error_summary(exc)}"
                )
        return provider, call_llm, persona

    def hold_life_video_final_text(self, event: Any) -> bool:
        marker = self._life_video_request_from_event(event)
        if not marker:
            return False
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not self._is_llm_result_object(result):
            return False
        marker["llm_final_seen"] = True
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            chain.clear()
        logger.debug(
            f"{LOG_PREFIX} 已拦截视频生成完成前的文字回复，等待视频发送成功后再补话。"
        )
        return True

    async def _send_life_video_followup(
        self,
        scope: str,
        prompt: str,
        summary: str,
        event: Any,
        request_id: str,
    ) -> bool:
        text = await self._generate_life_video_followup_text(event, prompt, summary)
        if not text:
            return False
        text_sent = await self._send_life_video_text(
            scope, text, source_event=event, source="video_followup"
        )
        if text_sent:
            await self._append_assistant_history(scope, text)
        return text_sent

    async def _generate_life_video_followup_text(
        self, event: Any, prompt: str, summary: str
    ) -> str:
        try:
            reply_model = await self._life_video_reply_model(event)
            if reply_model is None:
                return ""
            provider, call_llm, persona = reply_model
            fixed = """你正在给刚刚交付完成的一段生活短视频补一句自然回复。
这是成品交付后的回应，不是过程播报。
严格只输出一个 JSON 对象，不要使用 Markdown 代码块，也不要输出解释：
{"reply_text":"真正发送给用户的一句中文短回复"}
JSON 只能包含 reply_text；{CORE_MEDIA_REPLY_RULES}
回复要像角色本人顺手接话，可以轻轻评价画面氛围、人物状态或镜头感，也可以自然回应用户拍视频的请求。
保持生活化语气，不展开解释，不描述内部流程或生成过程。""".replace(
                "{CORE_MEDIA_REPLY_RULES}", CORE_MEDIA_REPLY_RULES
            )
            dynamic = (
                f"角色口吻参考：{persona[:800] if persona else '按当前对话中的角色口吻自然回复。'}\n"
                f"用户的视频要求：{str(prompt or '').strip()}\n"
                f"视频发送结果：{str(summary or '').strip()}"
            )
            prompt_text = cache_friendly_prompt(
                fixed, dynamic, dynamic_title="已发送视频"
            )
            text = await call_llm(
                provider,
                prompt_text,
                f"daily_life_video_followup_{uuid.uuid4().hex[:8]}",
                empty_retries=0,
                primary_provider_id="",
            )
            reply_text = self._parse_life_video_reply_text(text)
            if not reply_text:
                logger.debug(f"{LOG_PREFIX} 视频发送后补话生成失败：返回内容不符合协议")
            return reply_text
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 视频发送后补话生成失败：{self._media_error_summary(exc)}"
            )
            return ""

    async def _generate_life_video_failure_text(
        self,
        event: Any,
        prompt: str,
        error: str,
        *,
        photo_sent: bool = False,
    ) -> str:
        try:
            reply_model = await self._life_video_reply_model(event)
            if reply_model is None:
                logger.debug(
                    f"{LOG_PREFIX} 视频失败补话生成失败：没有可用模型；使用本地兜底"
                )
                return ""
            provider, call_llm, persona = reply_model
            detail = " ".join(str(error or "").split())
            if len(detail) > 500:
                detail = detail[:500].rstrip() + "..."
            fixed = """你正在给一次生活短视频生成失败补一句自然回复。
这是面向用户的聊天回复，不是日志，也不是技术报告。
严格只输出一个 JSON 对象，不要使用 Markdown 代码块，也不要输出解释：
{"reply_text":"真正发送给用户的一句中文短回复"}
JSON 只能包含 reply_text；{CORE_MEDIA_REPLY_RULES}
语气要像当前角色本人顺手说明情况，可以轻轻带一点歉意或吐槽。
本次没有登记自动重试任务，不得承诺晚点、稍后或之后会自行重试、补发或再联系。""".replace(
                "{CORE_MEDIA_REPLY_RULES}", CORE_MEDIA_REPLY_RULES
            )
            dynamic = (
                f"角色口吻参考：{persona[:800] if persona else '按当前对话中的角色口吻自然回复。'}\n"
                f"用户的视频要求：{str(prompt or '').strip()}\n"
                f"内部失败原因（只用于理解，不要复述）：{detail or '未知'}\n"
                f"失败图片状态：{'已经发送一张图片' if photo_sent else '没有可发送的图片'}"
            )
            prompt_text = cache_friendly_prompt(
                fixed, dynamic, dynamic_title="视频未发送"
            )
            text = await call_llm(
                provider,
                prompt_text,
                f"daily_life_video_failure_{uuid.uuid4().hex[:8]}",
                empty_retries=0,
                primary_provider_id="",
            )
            reply_text = self._parse_life_video_reply_text(text)
            if not reply_text:
                logger.debug(
                    f"{LOG_PREFIX} 视频失败补话生成失败：返回内容不符合协议；使用本地兜底"
                )
            return reply_text
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 视频失败补话生成失败：{self._media_error_summary(exc)}；"
                "使用本地兜底"
            )
            return ""

    @staticmethod
    def _parse_life_video_reply_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""

        if text.startswith("```"):
            lines = text.splitlines()
            fence = lines[0].strip().lower() if lines else ""
            if len(lines) < 3 or fence not in {"```", "```json"}:
                return ""
            if lines[-1].strip() != "```":
                return ""
            text = "\n".join(lines[1:-1]).strip()

        if not text.startswith("{") or not text.endswith("}"):
            return ""
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return ""
        if not isinstance(payload, dict) or set(payload) != {"reply_text"}:
            return ""
        reply_text = payload.get("reply_text")
        if not isinstance(reply_text, str):
            return ""
        return reply_text.strip()

    async def _send_life_video_text(
        self,
        scope: str,
        text: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
        source: str = "video",
    ) -> bool:
        text = str(text or "").strip()
        if not scope or not text:
            return False
        return await self.send_background_text(
            scope,
            text,
            mode=BackgroundTextMode.EXPRESSIVE,
            source_event=source_event,
            source_message_id=source_message_id,
            source=source,
        )

    async def _load_life_video_reference_image(self, reference_image: str) -> bytes:
        reference_image = str(reference_image or "").strip()
        if not reference_image:
            raise ValueError("缺少视频首帧参考图")
        load_image = getattr(
            getattr(self.media, "image", None), "_load_reference_image", None
        )
        if callable(load_image):
            image_bytes, _ = await load_image(reference_image)
            return image_bytes
        path = await asyncio.to_thread(expand_path, reference_image)
        if not await asyncio.to_thread(path_is_file, path):
            raise FileNotFoundError(f"参考图片不存在：{reference_image}")
        return await asyncio.to_thread(path.read_bytes)

    def _life_video_reference_aspect_ratio(self, image_bytes: bytes) -> str:
        resolver = getattr(
            getattr(self.media, "image", None), "_reference_image_aspect_ratio", None
        )
        if not callable(resolver):
            return ""
        try:
            return str(resolver(image_bytes) or "").strip()
        except Exception:
            return ""
