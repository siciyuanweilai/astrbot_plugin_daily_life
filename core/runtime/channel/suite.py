from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ...life.people import MEDIA_PERSON_TEXT_PATHS
from ...media.base import GROUP_IDENTITY_CONTINUITY_RULE, image_mime_and_ext
from ...media.picture.routes import image_provider_label, requested_image_provider
from ...outcome import ToolResultText
from ...paths import path_is_file, runtime_data_root
from ...prompts import CORE_MEDIA_REPLY_RULES, cache_friendly_prompt
from ..delivery import BackgroundTextMode
from ..markers import LOG_PREFIX

PHOTO_SUITE_DEFAULT_COUNT = 3
PHOTO_SUITE_MIN_COUNT = 2
PHOTO_SUITE_MAX_COUNT = 6
PHOTO_SUITE_GENERATION_LIMIT = 2
PHOTO_SUITE_SLOT_ATTEMPTS = 2

_PERSON_FALLBACK_SHOTS = (
    ("环境同行", "稍远景生活抓拍，人物自然融入完整环境，画面留有呼吸感"),
    ("侧面抓拍", "侧面视角，人物正在完成一个与场景相符的生活动作"),
    ("神态近景", "自然近景，重点呈现眼神、表情和柔和的面部光线"),
    ("半身回望", "半身视角，人物自然回望镜头，神态松弛而非刻意摆拍"),
    ("低机位动态", "轻微低机位视角，人物动作舒展，背景保持自然纵深"),
    ("穿搭细节", "聚焦服装层次、配饰与手部姿态，人物身份仍清晰可辨"),
)

_GENERIC_FALLBACK_SHOTS = (
    ("环境建立", "稍远景完整交代主体所处环境、空间层次和整体氛围"),
    ("情境关系", "通过附近物件、光线与使用状态呈现主体所处情境"),
    ("质感近景", "近景呈现主体纹理、材质、光泽和细微使用痕迹"),
    ("主体正面", "平视正面构图，完整呈现主体外观与关键特征"),
    ("轻微俯拍", "轻微俯拍视角，清楚呈现主体布局及周围元素关系"),
    ("局部叙事", "选择主体与环境接触的局部，形成有前后联系的细节画面"),
)


class RuntimePhotoSuiteMediaMixin:
    """规划、生成并交付画面连续的独立生活套图。"""

    @staticmethod
    def _photo_suite_count(value: Any) -> int:
        try:
            count = int(float(str(value or PHOTO_SUITE_DEFAULT_COUNT).strip()))
        except (TypeError, ValueError):
            count = PHOTO_SUITE_DEFAULT_COUNT
        return max(PHOTO_SUITE_MIN_COUNT, min(count, PHOTO_SUITE_MAX_COUNT))

    def _photo_suite_planning_timeout_seconds(self) -> int:
        settings = getattr(getattr(self, "config", None), "image_generation", None)
        try:
            timeout = int(
                getattr(settings, "photo_suite_planning_timeout_seconds", 45) or 45
            )
        except (TypeError, ValueError):
            timeout = 45
        return max(10, min(timeout, 120))

    @staticmethod
    def _photo_suite_provider_label(provider: Any) -> str:
        config = getattr(provider, "provider_config", None)
        config = config if isinstance(config, dict) else {}
        provider_id = str(config.get("id") or "").strip()
        meta_getter = getattr(provider, "meta", None)
        if callable(meta_getter):
            try:
                provider_id = str(
                    getattr(meta_getter(), "id", "") or provider_id
                ).strip()
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 读取套图模型元信息失败，改用配置标识："
                    f"{type(exc).__name__}"
                )
        model = str(
            getattr(provider, "model_name", "") or config.get("model") or ""
        ).strip()
        if provider_id and model and provider_id != model:
            return f"{provider_id}/{model}"
        return model or provider_id or type(provider).__name__

    @staticmethod
    def _photo_suite_retry_indexes(value: Any, count: int) -> list[int]:
        values = [value] if isinstance(value, (str, int, float)) else value
        if not isinstance(values, (list, tuple, set)):
            return []
        result: list[int] = []
        for item in values:
            try:
                index = int(float(str(item).strip()))
            except (TypeError, ValueError):
                continue
            if 1 <= index <= count and index not in result:
                result.append(index)
        return sorted(result)

    @staticmethod
    def _photo_suite_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _photo_suite_requests(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_life_photo_suite_pending_requests", None)
        if not isinstance(store, dict):
            store = {}
            self._life_photo_suite_pending_requests = store
        return store

    def _photo_suite_last_tasks(self) -> dict[str, str]:
        store = getattr(self, "_life_photo_suite_last_tasks", None)
        if not isinstance(store, dict):
            store = {}
            self._life_photo_suite_last_tasks = store
        return store

    def _photo_suite_generation_gate(self) -> asyncio.Semaphore:
        gate = getattr(self, "_life_photo_suite_generation_semaphore", None)
        if not isinstance(gate, asyncio.Semaphore):
            gate = asyncio.Semaphore(PHOTO_SUITE_GENERATION_LIMIT)
            self._life_photo_suite_generation_semaphore = gate
        return gate

    def _photo_suite_register_request(
        self, scope: str, prompt: str, event: Any, task_dir: Path
    ) -> str:
        request_id = uuid.uuid4().hex
        marker = {
            "id": request_id,
            "scope": str(scope or "").strip(),
            "prompt": str(prompt or "").strip(),
            "created_at": time.monotonic(),
            "task_dir": str(task_dir),
            "llm_final_seen": False,
            "status": "pending",
        }
        self._photo_suite_requests()[request_id] = marker
        for source in self._event_sources(event):
            setattr(source, "_daily_life_photo_suite_request_id", request_id)
        return request_id

    def _photo_suite_request_from_event(self, event: Any) -> dict[str, Any] | None:
        requests = self._photo_suite_requests()
        for source in self._event_sources(event):
            request_id = str(
                getattr(source, "_daily_life_photo_suite_request_id", "") or ""
            ).strip()
            if request_id and request_id in requests:
                return requests[request_id]
        scope = self._event_session_id(event)
        candidates = [
            item
            for item in requests.values()
            if str(item.get("scope") or "") == scope
            and time.monotonic() - float(item.get("created_at") or 0) <= 20
        ]
        return candidates[-1] if candidates else None

    def _photo_suite_finish_request(self, request_id: str) -> None:
        request_id = str(request_id or "").strip()
        if request_id:
            self._photo_suite_requests().pop(request_id, None)

    def hold_life_photo_suite_final_text(self, event: Any) -> bool:
        marker = self._photo_suite_request_from_event(event)
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
        logger.debug(f"{LOG_PREFIX} 已拦截套图交付前的文字回复，等待图片发送后再补话。")
        return True

    def _photo_suite_root(self) -> Path:
        return (
            runtime_data_root(getattr(self, "data_path", None))
            / "generated"
            / "images"
            / "suites"
        )

    async def life_photo_suite_generate(
        self,
        event: Any,
        prompt: str = "",
        *,
        count: int = PHOTO_SUITE_DEFAULT_COUNT,
        reference_image: str = "",
        continue_last_result: bool = False,
        subject_route: str = "free",
        participants: list[str] | None = None,
        friend_outfit: str = "",
        friend_hair: str = "",
        friend_scene_category: str = "",
        friend_style_pool: str = "",
        friend_outfit_decision: str = "",
        retry_indexes: list[int] | None = None,
        resolution: str = "",
        provider: str = "",
    ) -> str:
        provider = requested_image_provider(provider)
        if provider:
            logger.debug(
                f"{LOG_PREFIX} 图片协议指定：{image_provider_label(provider)}；模式=套图"
            )
        scope = self._event_session_id(event)
        if not scope:
            return "当前会话不可发送套图。"
        participant_ids = self._normalize_image_participants(participants)
        route = self._normalize_image_subject_route(subject_route)
        retry_values = list(retry_indexes or [])
        resolution = str(resolution or "").strip().upper()
        if not resolution:
            resolution = self._image_prompt_resolution(
                self._event_image_prompt_text(event)
            ) or self._image_prompt_resolution(prompt)

        manifest: dict[str, Any] | None = None
        manifest_path: Path | None = None
        initial_reference_image = ""
        initial_continue_last_result = False
        is_retry = bool(retry_values)
        if is_retry:
            manifest_path, manifest = await self._photo_suite_latest_manifest(scope)
            if manifest is None or manifest_path is None:
                return "当前会话没有可重试的上一组照片。"
            count = self._photo_suite_count(manifest.get("count"))
            retry_values = self._photo_suite_retry_indexes(retry_values, count)
            if not retry_values:
                return f"重试序号应在 1 到 {count} 之间。"
            task_dir = manifest_path.parent
            prompt = str(manifest.get("prompt") or "").strip()
            route = self._normalize_image_subject_route(
                str(manifest.get("subject_route") or "free")
            )
            participant_ids = self._normalize_image_participants(
                manifest.get("participants")
            )
            provider = requested_image_provider(manifest.get("protocol")) or provider
        else:
            count = self._photo_suite_count(count)
            prompt, _, aspect_ratio = self._resolve_image_prompt(event, prompt)
            if not prompt:
                return "没有收到套图画面要求。"
            if route == "group" and len(participant_ids) != 1:
                return "请明确选择一位已配置参考图的好友再生成合影套图。"
            friend_look: dict[str, str] = {}
            friend_look_persist = False
            if route == "group":
                (
                    friend_look,
                    look_source,
                    missing,
                ) = await self._prepare_friend_daily_look(
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
                    required = self._friend_look_required_parameters(
                        look_source, missing
                    )
                    logger.debug(
                        f"{LOG_PREFIX} 好友合影暂未生成：缺少好友当天穿搭或发型；"
                        f"参数={','.join(missing)}"
                    )
                    return self._friend_look_parameters_result(required)
                self._log_friend_daily_look(
                    participant_ids[0], friend_look, look_source
                )
                friend_look_persist = self._friend_look_should_persist(look_source)
            current_appearance = ""
            if not reference_image and not continue_last_result:
                current_appearance = await self._current_life_appearance_snapshot(route)
            source_request = self._event_current_image_request_text(event)
            if current_appearance and route in {"current_character", "group"}:
                prompt = await self._align_current_appearance_scene_prompt(
                    prompt,
                    source_request,
                    route,
                )
            task_id = uuid.uuid4().hex
            task_dir = self._photo_suite_root() / task_id
            manifest_path = task_dir / "manifest.json"
            await asyncio.to_thread(task_dir.mkdir, parents=True, exist_ok=True)
            initial_reference_image = str(reference_image or "").strip()
            initial_continue_last_result = bool(continue_last_result)
            created_at = self._photo_suite_now()
            manifest = {
                "id": task_id,
                "scope": scope,
                "created_at": created_at,
                "updated_at": created_at,
                "prompt": prompt,
                "count": count,
                "aspect_ratio": aspect_ratio or "",
                "resolution": resolution,
                "subject_route": route,
                "protocol": provider,
                "participants": participant_ids,
                "identity_profiles": await self._resolve_life_identity_profiles(
                    event, route
                ),
                "current_appearance": current_appearance,
                "source_request": source_request,
                "friend_look": friend_look,
                "friend_look_persist": friend_look_persist,
                "reference_path": "",
                "status": "pending",
                "shots": [],
            }
            await self._photo_suite_write_manifest(manifest_path, manifest)
            self._photo_suite_last_tasks()[scope] = str(manifest_path)

        assert manifest is not None and manifest_path is not None
        request_id = self._photo_suite_register_request(scope, prompt, event, task_dir)
        scheduled = self._schedule_background_task(
            self._photo_suite_generate_background(
                scope,
                event,
                request_id,
                manifest_path,
                retry_indexes=retry_values,
                initial_reference_image=initial_reference_image,
                continue_last_result=initial_continue_last_result,
            ),
            label="生活套图生成",
            key=f"photo_suite:{scope}:{manifest.get('id') or task_dir.name}",
        )
        if not scheduled:
            self._photo_suite_finish_request(request_id)
            return ToolResultText(
                "这组照片已经在准备了。",
                status="pending",
                media="photo_suite",
            )
        return json.dumps(
            {
                "status": "pending",
                "media": "photo_suite",
                "response_timing": "after_delivery",
                "response_stance": "当前不再发送文字；整组照片真实发送后再按聊天语境自然补一句",
            },
            ensure_ascii=False,
        )

    async def _photo_suite_prepare_generation(
        self,
        event: Any,
        manifest_path: Path,
        marker: Any,
        retry_indexes: list[int] | None,
        initial_reference_image: str,
        continue_last_result: bool,
    ) -> tuple[dict[str, Any], int, list[int]]:
        manifest = await self._photo_suite_read_manifest(manifest_path)
        if manifest is None:
            raise RuntimeError("套图任务记录不可用")
        count = self._photo_suite_count(manifest.get("count"))
        if not manifest.get("shots"):
            reference = await self._resolve_life_image_reference_async(
                event,
                initial_reference_image,
                allow_last_generated=bool(continue_last_result),
                prefer_last_generated=bool(continue_last_result),
            )
            manifest["reference_path"] = await self._photo_suite_stabilize_reference(
                reference, manifest_path.parent
            )
            planning_prompt = str(manifest.get("prompt") or "")
            planning_prompt = self._apply_current_appearance_snapshot(
                planning_prompt,
                str(manifest.get("current_appearance") or ""),
                str(manifest.get("subject_route") or "free"),
                source_request=str(manifest.get("source_request") or ""),
            )
            if (
                self._normalize_image_subject_route(manifest.get("subject_route"))
                == "group"
            ):
                planning_prompt = self._friend_look_prompt(
                    planning_prompt,
                    self._normalize_friend_look(manifest.get("friend_look")),
                )
            shots = await self._photo_suite_plan(
                event,
                planning_prompt,
                count,
                subject_route=str(manifest.get("subject_route") or "free"),
                participants=list(manifest.get("participants") or []),
            )
            manifest["shots"] = [
                {
                    "index": index,
                    "title": shot["title"],
                    "prompt": shot["prompt"],
                    "status": "pending",
                    "path": "",
                    "attempts": 0,
                    "error": "",
                }
                for index, shot in enumerate(shots, start=1)
            ]
        indexes = self._photo_suite_retry_indexes(retry_indexes, count) or list(
            range(1, count + 1)
        )
        if isinstance(marker, dict):
            marker["status"] = "generating"
        manifest["status"] = "generating"
        for shot in manifest.get("shots") or []:
            if int(shot.get("index") or 0) in indexes:
                shot["status"] = "generating"
                shot["error"] = ""
        await self._photo_suite_write_manifest(manifest_path, manifest)
        return manifest, count, indexes

    async def _photo_suite_run_generation_slots(
        self,
        event: Any,
        manifest_path: Path,
        manifest: dict[str, Any],
        indexes: list[int],
    ) -> None:
        lock = asyncio.Lock()
        gate = self._photo_suite_generation_gate()

        async def run(index: int) -> None:
            async with gate:
                await self._photo_suite_generate_slot(
                    event, manifest_path, manifest, index, lock
                )

        await asyncio.gather(*(run(index) for index in indexes))

    async def _photo_suite_available_shots(
        self,
        manifest: dict[str, Any],
        *,
        indexes: list[int] | None = None,
        generated_only: bool = False,
    ) -> list[dict[str, Any]]:
        available: list[dict[str, Any]] = []
        for shot in manifest.get("shots") or []:
            index = int(shot.get("index") or 0)
            path = str(shot.get("path") or "").strip()
            if indexes is not None and index not in indexes:
                continue
            if generated_only and str(shot.get("status") or "") != "generated":
                continue
            if path and await asyncio.to_thread(path_is_file, path):
                available.append(shot)
        return available

    async def _photo_suite_finalize_manifest(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
        indexes: list[int],
        count: int,
        sent_indexes: set[int],
    ) -> tuple[int, list[int], int]:
        for shot in manifest.get("shots") or []:
            index = int(shot.get("index") or 0)
            if index in sent_indexes:
                shot["status"] = "sent"
            elif index in indexes and not str(shot.get("path") or "").strip():
                shot["status"] = "failed"
        total_available = len(await self._photo_suite_available_shots(manifest))
        failed_indexes = [
            int(shot.get("index") or 0)
            for shot in manifest.get("shots") or []
            if not str(shot.get("path") or "").strip()
        ]
        sent_total = sum(
            1
            for shot in manifest.get("shots") or []
            if str(shot.get("status") or "") == "sent"
        )
        manifest["status"] = (
            "completed"
            if sent_total >= count
            else "partial"
            if total_available or sent_indexes
            else "failed"
        )
        await self._photo_suite_write_manifest(manifest_path, manifest)
        return total_available, failed_indexes, sent_total

    async def _photo_suite_report_delivery(
        self,
        scope: str,
        event: Any,
        manifest: dict[str, Any],
        indexes: list[int],
        sent_indexes: set[int],
        total_available: int,
        count: int,
    ) -> bool:
        if not sent_indexes:
            logger.warning(f"{LOG_PREFIX} 套图本次没有可发送的图片。")
            return False
        last_shot = max(
            (
                shot
                for shot in manifest.get("shots") or []
                if int(shot.get("index") or 0) in sent_indexes
            ),
            key=lambda item: int(item.get("index") or 0),
        )
        self.note_structured_bot_message(
            scope,
            f"[套图已发送：成功 {len(sent_indexes)}/{len(indexes)}]",
            source_event=event,
            media="图片",
        )
        self._remember_life_image_for_scope(scope, last_shot.get("path"))
        participants = self._normalize_image_participants(manifest.get("participants"))
        friend_look = self._normalize_friend_look(manifest.get("friend_look"))
        if (
            self._normalize_image_subject_route(manifest.get("subject_route"))
            == "group"
            and len(participants) == 1
            and (friend_look["outfit"] or friend_look["hair"])
            and bool(manifest.get("friend_look_persist", True))
        ):
            await self._remember_friend_daily_look(scope, participants[0], friend_look)
        self.note_life_media_sent(event or scope, "图片")
        receipt_recorder = getattr(self, "record_current_life_action_receipt", None)
        if callable(receipt_recorder):
            await receipt_recorder(
                event,
                "photo",
                evidence=f"套图已生成并成功发送 {len(sent_indexes)} 张",
                source="photo_suite_delivery",
                artifact_path=str(last_shot.get("path") or ""),
            )
        logger.info(
            f"{LOG_PREFIX} 套图已发送：本次={len(sent_indexes)}/{len(indexes)}；"
            f"整组可用={total_available}/{count}"
        )
        return True

    async def _photo_suite_handle_generation_failure(
        self,
        scope: str,
        event: Any,
        marker: Any,
        manifest_path: Path,
        retry_indexes: list[int] | None,
        error: str,
    ) -> None:
        logger.warning(f"{LOG_PREFIX} 套图生成或发送失败：{error}")
        if isinstance(marker, dict):
            marker["status"] = "failed"
        failed_manifest = await self._photo_suite_read_manifest(manifest_path)
        if failed_manifest is not None:
            available = len(await self._photo_suite_available_shots(failed_manifest))
            failed_manifest["status"] = "partial" if available else "failed"
            failed_manifest["failure_reason"] = error
            await self._photo_suite_write_manifest(manifest_path, failed_manifest)
        await self._photo_suite_send_followup(
            scope,
            event,
            prompt="",
            sent_count=0,
            requested_count=0,
            total_count=0,
            failed_indexes=[],
            is_retry=bool(retry_indexes),
            error=error,
        )
        await self.finish_tool_reaction(
            event, "life_photo_suite_generate", success=False
        )

    async def _photo_suite_generate_background(
        self,
        scope: str,
        event: Any,
        request_id: str,
        manifest_path: Path,
        *,
        retry_indexes: list[int] | None = None,
        initial_reference_image: str = "",
        continue_last_result: bool = False,
    ) -> None:
        marker = self._photo_suite_requests().get(request_id)
        try:
            manifest, count, indexes = await self._photo_suite_prepare_generation(
                event,
                manifest_path,
                marker,
                retry_indexes,
                initial_reference_image,
                continue_last_result,
            )
            await self._photo_suite_run_generation_slots(
                event, manifest_path, manifest, indexes
            )
            successful = await self._photo_suite_available_shots(
                manifest, indexes=indexes, generated_only=True
            )
            delivery_task = await self.stage_durable_media_delivery(
                scope,
                "images",
                [str(shot.get("path") or "") for shot in successful],
                action_type="photo",
                evidence=f"套图已生成 {len(successful)} 张，等待投递确认",
            )
            sent_indexes = await self._photo_suite_send_images(scope, event, successful)
            (
                total_available,
                failed_indexes,
                _sent_total,
            ) = await self._photo_suite_finalize_manifest(
                manifest_path,
                manifest,
                indexes,
                count,
                sent_indexes,
            )
            delivery_success = await self._photo_suite_report_delivery(
                scope,
                event,
                manifest,
                indexes,
                sent_indexes,
                total_available,
                count,
            )
            await self.finalize_durable_media_delivery(
                delivery_task,
                outcome="sent" if delivery_success else "cancelled",
                detail=(
                    f"套图已发送 {len(sent_indexes)} 张"
                    if delivery_success
                    else "本次没有可发送的套图，取消投递"
                ),
            )

            if isinstance(marker, dict):
                marker["status"] = manifest["status"]
            await self._photo_suite_send_followup(
                scope,
                event,
                prompt=str(manifest.get("prompt") or ""),
                sent_count=len(sent_indexes),
                requested_count=len(indexes),
                total_count=count,
                failed_indexes=failed_indexes,
                is_retry=bool(retry_indexes),
            )
            await self.finish_tool_reaction(
                event,
                "life_photo_suite_generate",
                success=delivery_success,
            )
        except asyncio.CancelledError:
            self.cancel_tool_reaction(event, "life_photo_suite_generate")
            manifest = await self._photo_suite_read_manifest(manifest_path)
            if manifest is not None:
                manifest["status"] = "cancelled"
                await self._photo_suite_write_manifest(manifest_path, manifest)
            raise
        except Exception as exc:
            error = self._media_error_summary(exc)
            await self._photo_suite_handle_generation_failure(
                scope,
                event,
                marker,
                manifest_path,
                retry_indexes,
                error,
            )
        finally:
            self._photo_suite_finish_request(request_id)

    async def _photo_suite_generate_slot(
        self,
        event: Any,
        manifest_path: Path,
        manifest: dict[str, Any],
        index: int,
        manifest_lock: asyncio.Lock,
    ) -> None:
        shots = manifest.get("shots") or []
        shot = next(
            (item for item in shots if int(item.get("index") or 0) == index), None
        )
        if not isinstance(shot, dict):
            return
        previous_path = str(shot.get("path") or "").strip()
        previous_status = "sent" if previous_path else "pending"
        error = ""
        for _ in range(PHOTO_SUITE_SLOT_ATTEMPTS):
            shot["attempts"] = int(shot.get("attempts") or 0) + 1
            try:
                generated = await self._photo_suite_generate_asset(
                    event, manifest, str(shot.get("prompt") or "")
                )
                source_path = Path(str(getattr(generated, "path", "") or ""))
                if not await asyncio.to_thread(path_is_file, source_path):
                    raise RuntimeError("图片服务没有返回可用文件")
                target = await self._photo_suite_copy_generated(
                    source_path, manifest_path.parent, index
                )
                shot["path"] = str(target)
                shot["status"] = "generated"
                shot["error"] = ""
                break
            except Exception as exc:
                error = self._media_error_summary(exc)
        else:
            if previous_path and await asyncio.to_thread(path_is_file, previous_path):
                shot["path"] = previous_path
                shot["status"] = previous_status
                shot["error"] = f"重新生成失败，保留原图：{error}"
            else:
                shot["path"] = ""
                shot["status"] = "failed"
                shot["error"] = error
            logger.warning(f"{LOG_PREFIX} 套图第 {index} 张生成失败：{error}")
        async with manifest_lock:
            await self._photo_suite_write_manifest(manifest_path, manifest)

    async def _photo_suite_generate_asset(
        self, event: Any, manifest: dict[str, Any], prompt: str
    ) -> Any:
        route = self._normalize_image_subject_route(manifest.get("subject_route"))
        prompt = self._apply_current_appearance_snapshot(
            prompt,
            str(manifest.get("current_appearance") or ""),
            route,
            source_request=str(manifest.get("source_request") or ""),
        )
        participants = self._normalize_image_participants(manifest.get("participants"))
        aspect_ratio = str(manifest.get("aspect_ratio") or "").strip()
        resolution = str(manifest.get("resolution") or "").strip().upper()
        protocol = requested_image_provider(manifest.get("protocol"))
        reference = str(manifest.get("reference_path") or "").strip()
        identity_profiles = self._current_character_identity_profiles(
            manifest.get("identity_profiles")
        )
        current_identity_profile = (
            str(identity_profiles.get("current_character") or "").strip()
            if identity_profiles
            else None
        )
        if route == "group":
            friend_look = self._normalize_friend_look(manifest.get("friend_look"))
            if friend_look["outfit"] or friend_look["hair"]:
                prompt = self._friend_look_prompt(prompt, friend_look)
            generator = getattr(
                getattr(getattr(self, "media", None), "image", None),
                "generate_group_image",
                None,
            )
            if not callable(generator) or len(participants) != 1:
                raise RuntimeError("当前图片接口不支持这组合影")
            group_options = {}
            if resolution:
                group_options["resolution"] = resolution
            if protocol:
                group_options["protocol"] = protocol
            if identity_profiles:
                group_options["identity_profiles"] = identity_profiles
            return await generator(
                prompt,
                participants,
                aspect_ratio,
                scene_reference=reference,
                **group_options,
            )
        if reference:
            return await self._edit_life_image_with_policy_retry(
                event,
                prompt,
                reference,
                aspect_ratio,
                resolution,
                preserve_reference_ratio=not bool(aspect_ratio),
                protocol=protocol,
                identity_profile=current_identity_profile or "",
            )
        return await self.generate_life_image_asset(
            event,
            prompt,
            aspect_ratio,
            resolution,
            contains_character=route == "current_character",
            trusted_identity=route == "current_character",
            protocol=protocol,
            identity_profile=current_identity_profile,
        )

    async def _photo_suite_plan(
        self,
        event: Any,
        prompt: str,
        count: int,
        *,
        subject_route: str = "free",
        participants: list[str] | None = None,
    ) -> list[dict[str, str]]:
        route = self._normalize_image_subject_route(subject_route)
        fallback = self._photo_suite_fallback_plan(prompt, count, route)
        get_provider = getattr(self, "get_text_provider", None)
        call_llm = getattr(self, "call_text_model", None)
        if not callable(get_provider) or not callable(call_llm):
            return fallback
        timeout_seconds = self._photo_suite_planning_timeout_seconds()
        provider_id = self._media_image_director_provider_id()
        provider_label = "当前默认模型"
        started_at = time.monotonic()
        try:
            provider = await get_provider(provider_id)
            if provider is None:
                return fallback
            provider_label = self._photo_suite_provider_label(provider)
            logger.debug(
                f"{LOG_PREFIX} 套图镜头规划开始：模型={provider_label}；"
                f"上限={timeout_seconds}秒；数量={count}"
            )
            fixed = """你是生活照片套图的镜头规划器。严格只输出一个 JSON 对象，不要使用 Markdown 代码块：
{"shots":[{"title":"简短镜头名","prompt":"可独立用于图片生成的完整中文画面提示词"}]}
shots 数量必须与要求一致。每个 prompt 都必须独立完整，不能写“同上”“保持不变”或依赖上一张。
整组固定主体身份或外观、数量、关键特征、地点、时间、光线方向、色彩和画面风格；人物入镜时还必须固定脸部、发型、身形、服装与配饰。只改变景别、机位、构图、姿势、动作与视觉重点。
镜头之间要有明确语义差异，像同一时段连续拍摄的一组照片，不要生成拼图、网格、分镜图或多宫格。"""
            group_rules = (
                "\n这是双人合影套图：人物 A 是当前角色，人物 B 是好友。"
                f"{GROUP_IDENTITY_CONTINUITY_RULE}每个 prompt 都必须独立写清双方身份与各自属性。"
                if route == "group"
                else ""
            )
            composer = getattr(self, "composer", None)
            fact_builder = getattr(composer, "_build_person_fact_context", None)
            auditor = getattr(composer, "_audit_person_payload", None)
            person_facts = (
                await fact_builder(
                    persona=await self.get_persona_text(self._event_session_id(event))
                )
                if callable(fact_builder) and route == "group" and participants
                else None
            )
            person_context = (
                person_facts.format_for_generation() if person_facts is not None else ""
            )
            dynamic = (
                f"需要规划 {count} 张独立照片。\n"
                f"主体路线：{route}{group_rules}\n"
                f"{person_context}\n用户画面要求：{prompt}"
            )
            planner_prompt = cache_friendly_prompt(
                fixed, dynamic, dynamic_title="套图要求"
            )
            raw = await asyncio.wait_for(
                call_llm(
                    provider,
                    planner_prompt,
                    f"daily_life_photo_suite_plan_{uuid.uuid4().hex[:8]}",
                    empty_retries=0,
                    primary_provider_id=provider_id,
                ),
                timeout=timeout_seconds,
            )
            planned = self._photo_suite_parse_plan(raw, count)
            if planned:
                if person_facts is not None and callable(auditor):
                    audit = await auditor(
                        {"shots": planned},
                        context=person_facts,
                        patterns=MEDIA_PERSON_TEXT_PATHS,
                        provider=provider,
                        provider_id=provider_id,
                        subject="合影套图镜头规划",
                    )
                    if audit.unresolved:
                        logger.warning(
                            f"{LOG_PREFIX} 套图镜头规划人物事实未通过："
                            f"模型={provider_label}；耗时={time.monotonic() - started_at:.2f} 秒；"
                            "使用本地镜头方案"
                        )
                        return fallback
                    revised = audit.payload.get("shots")
                    if isinstance(revised, list) and len(revised) == count:
                        planned = revised
                logger.debug(
                    f"{LOG_PREFIX} 套图镜头规划完成：模型={provider_label}；"
                    f"数量={count}；耗时={time.monotonic() - started_at:.2f} 秒"
                )
                return planned
            logger.debug(
                f"{LOG_PREFIX} 套图镜头规划返回无效：模型={provider_label}；"
                f"耗时={time.monotonic() - started_at:.2f} 秒；使用本地镜头方案"
            )
        except TimeoutError:
            logger.debug(
                f"{LOG_PREFIX} 套图镜头规划超时：模型={provider_label}；"
                f"上限={timeout_seconds}秒；耗时={time.monotonic() - started_at:.2f} 秒；"
                "使用本地镜头方案"
            )
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 套图镜头规划失败：模型={provider_label}；"
                f"耗时={time.monotonic() - started_at:.2f} 秒；"
                f"原因={self._media_error_summary(exc)}；使用本地镜头方案"
            )
        return fallback

    @staticmethod
    def _photo_suite_parse_plan(value: Any, count: int) -> list[dict[str, str]]:
        text = str(value or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) < 3 or lines[-1].strip() != "```":
                return []
            text = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, dict) or set(payload) != {"shots"}:
            return []
        shots = payload.get("shots")
        if not isinstance(shots, list) or len(shots) != count:
            return []
        result: list[dict[str, str]] = []
        for index, item in enumerate(shots, start=1):
            if not isinstance(item, dict):
                return []
            prompt = str(item.get("prompt") or "").strip()
            title = str(item.get("title") or f"镜头 {index}").strip()
            if not prompt:
                return []
            result.append({"title": title[:40], "prompt": prompt})
        return result

    @staticmethod
    def _photo_suite_fallback_plan(
        prompt: str, count: int, subject_route: str = "free"
    ) -> list[dict[str, str]]:
        route = str(subject_route or "free").strip().casefold()
        person_suite = route in {"current_character", "group"}
        shots = _PERSON_FALLBACK_SHOTS if person_suite else _GENERIC_FALLBACK_SHOTS
        continuity = (
            f"{GROUP_IDENTITY_CONTINUITY_RULE}地点、时间、光线方向、色彩和真实生活摄影风格保持一致；"
            if route == "group"
            else "整组保持相同人物身份、人数、脸部、发型、身形、服装、配饰、地点、"
            "时间、光线方向、色彩和真实生活摄影风格；"
            if person_suite
            else "整组保持相同主体外观、数量、关键特征、地点、时间、光线方向、"
            "色彩和真实摄影风格；"
        )
        return [
            {
                "title": title,
                "prompt": (
                    f"{prompt}。{continuity}输出一张独立完整照片，不要拼图或多宫格。"
                    f"本张优先采用原始画面要求中第 {index} 个明确的镜头、机位、姿势或动作变化；"
                    f"若原始要求没有第 {index} 个明确变化，再采用以下通用镜头：{detail}。"
                ),
            }
            for index, (title, detail) in enumerate(shots[:count], start=1)
        ]

    async def _photo_suite_stabilize_reference(
        self, reference: str, task_dir: Path
    ) -> str:
        reference = str(reference or "").strip()
        if not reference:
            return ""
        loader = getattr(
            getattr(getattr(self, "media", None), "image", None),
            "_load_reference_image",
            None,
        )
        if not callable(loader):
            return reference
        image_bytes, _ = await loader(reference)
        _, extension = image_mime_and_ext(image_bytes)
        target = task_dir / f"reference{extension}"
        await asyncio.to_thread(target.write_bytes, image_bytes)
        return str(target)

    @staticmethod
    async def _photo_suite_copy_generated(
        source: Path, task_dir: Path, index: int
    ) -> Path:
        data = await asyncio.to_thread(source.read_bytes)
        _, extension = image_mime_and_ext(data)
        target = task_dir / f"{index:02d}{extension}"
        if source.resolve() != target.resolve():
            await asyncio.to_thread(shutil.copyfile, source, target)
        return target

    async def _photo_suite_send_images(
        self, scope: str, event: Any, shots: list[dict[str, Any]]
    ) -> set[int]:
        ordered = sorted(shots, key=lambda item: int(item.get("index") or 0))
        if not ordered:
            return set()
        paths = [Path(str(item.get("path") or "")) for item in ordered]
        indexes = [int(item.get("index") or 0) for item in ordered]
        try:
            sent = await self.send_message_if_not_recalled(
                scope,
                self.images_message_chain(paths),
                source_event=event,
            )
            return set(indexes) if sent else set()
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 平台不支持整组图片一次发送，改为逐张发送："
                f"{self._media_error_summary(exc)}"
            )
        sent_indexes: set[int] = set()
        for index, path in zip(indexes, paths):
            try:
                if not await self.send_message_if_not_recalled(
                    scope, self.image_message_chain(path), source_event=event
                ):
                    break
                sent_indexes.add(index)
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} 套图第 {index} 张发送失败："
                    f"{self._media_error_summary(exc)}"
                )
        return sent_indexes

    async def _photo_suite_send_followup(
        self,
        scope: str,
        event: Any,
        *,
        prompt: str,
        sent_count: int,
        requested_count: int,
        total_count: int,
        failed_indexes: list[int],
        is_retry: bool,
        error: str = "",
    ) -> bool:
        text = await self._photo_suite_followup_text(
            event,
            prompt=prompt,
            sent_count=sent_count,
            requested_count=requested_count,
            total_count=total_count,
            failed_indexes=failed_indexes,
            is_retry=is_retry,
            error=error,
        )
        if not text:
            if sent_count and is_retry:
                text = "这张重新拍好了。"
            elif sent_count and sent_count >= requested_count:
                text = "这组都拍好了。"
            elif sent_count:
                text = "先把拍好的这几张发给你。"
            else:
                text = "这次没拍出来，整组都没有生成成功。"
        try:
            sent = await self.send_background_text(
                scope,
                text,
                mode=BackgroundTextMode.EXPRESSIVE,
                source_event=event,
                source="photo_suite_followup",
            )
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} 套图补话发送失败：{self._media_error_summary(exc)}"
            )
            return False
        if sent:
            await self._append_assistant_history(scope, text)
        return sent

    async def _photo_suite_followup_text(
        self,
        event: Any,
        *,
        prompt: str,
        sent_count: int,
        requested_count: int,
        total_count: int,
        failed_indexes: list[int],
        is_retry: bool,
        error: str,
    ) -> str:
        get_provider = getattr(self, "get_text_provider", None)
        call_llm = getattr(self, "call_text_model", None)
        if not callable(get_provider) or not callable(call_llm):
            return ""
        try:
            provider = await get_provider("")
            if provider is None:
                return ""
            persona_getter = getattr(self, "get_persona_text", None)
            persona = ""
            if callable(persona_getter):
                persona = str(
                    await persona_getter(self._event_session_id(event)) or ""
                ).strip()
            fixed = """你正在给刚刚交付的一组生活照片补一句自然回复。严格只输出一个 JSON 对象，不要使用 Markdown 代码块：
{"reply_text":"角色真正说出口的一句中文短回复"}
JSON 只能包含 reply_text。{CORE_MEDIA_REPLY_RULES}根据当前对话和实际交付结果自然承接，不预设固定话术或互动动作；只有部分送达时如实表达当前结果，全部失败时自然说明这次没拍成。
全部失败时没有登记自动重试任务，不得承诺晚点、稍后或之后会自行重试、补发或再联系。""".replace(
                "{CORE_MEDIA_REPLY_RULES}", CORE_MEDIA_REPLY_RULES
            )
            dynamic = (
                f"角色口吻参考：{persona[:800] if persona else '按当前对话中的角色口吻自然回复。'}\n"
                f"用户画面要求：{prompt}\n"
                f"本次发送：{sent_count}/{requested_count}；整组数量：{total_count}；"
                f"未完成位置：{failed_indexes}；是否重拍：{'是' if is_retry else '否'}；"
                f"内部失败摘要：{error[:300]}"
            )
            raw = await call_llm(
                provider,
                cache_friendly_prompt(fixed, dynamic, dynamic_title="套图交付结果"),
                f"daily_life_photo_suite_followup_{uuid.uuid4().hex[:8]}",
                empty_retries=0,
                primary_provider_id="",
            )
            parser = getattr(self, "_parse_life_video_reply_text", None)
            return str(parser(raw) if callable(parser) else "").strip()
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} 套图补话生成失败：{self._media_error_summary(exc)}；"
                "使用本地兜底"
            )
            return ""

    async def _photo_suite_latest_manifest(
        self, scope: str
    ) -> tuple[Path | None, dict[str, Any] | None]:
        cached = str(self._photo_suite_last_tasks().get(scope) or "").strip()
        if cached:
            path = Path(cached)
            payload = await self._photo_suite_read_manifest(path)
            if payload is not None and str(payload.get("scope") or "") == scope:
                return path, payload

        root = self._photo_suite_root()

        def candidates() -> list[Path]:
            if not root.is_dir():
                return []
            return list(root.glob("*/manifest.json"))

        latest: tuple[str, Path, dict[str, Any]] | None = None
        for path in await asyncio.to_thread(candidates):
            payload = await self._photo_suite_read_manifest(path)
            if payload is not None and str(payload.get("scope") or "") == scope:
                created_at = str(payload.get("created_at") or "")
                candidate = (created_at, path, payload)
                if latest is None or candidate[0] > latest[0]:
                    latest = candidate
        if latest is not None:
            _, path, payload = latest
            self._photo_suite_last_tasks()[scope] = str(path)
            return path, payload
        return None, None

    @staticmethod
    async def _photo_suite_read_manifest(path: Path) -> dict[str, Any] | None:
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            payload = json.loads(text)
        except (OSError, TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    async def _photo_suite_write_manifest(
        self, path: Path, manifest: dict[str, Any]
    ) -> None:
        manifest["updated_at"] = self._photo_suite_now()
        text = json.dumps(manifest, ensure_ascii=False, indent=2)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)

        await asyncio.to_thread(write)
