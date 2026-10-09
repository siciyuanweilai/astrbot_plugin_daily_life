from __future__ import annotations

import asyncio
import datetime
import hashlib
import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrbot.api import logger

from ..clock import now as life_now
from ..sources.dispatch import send_message_to_scope


class RuntimeActionReceiptMixin:
    """将已确认的工具结果提交给当前生活动作。"""

    _MEDIA_DELIVERY_LEASE_SECONDS = 6 * 60 * 60

    @staticmethod
    def _share_asset_key(task_key: str, kind: str = "image") -> str:
        if not task_key.strip():
            raise ValueError("Sharing media tasks need an owner key")
        return f"share_{kind}:" + hashlib.sha256(task_key.encode()).hexdigest()

    async def get_share_image_task(self, task_key: str) -> dict:
        return await self._get_share_asset_task(task_key, "image")

    async def get_share_video_task(self, task_key: str) -> dict:
        return await self._get_share_asset_task(task_key, "video")

    async def _get_share_asset_task(self, task_key: str, kind: str) -> dict:
        task = await self.archive.get_durable_task(
            self._share_asset_key(task_key, kind)
        )
        if task is None:
            return {"status": "missing", "task_key": task_key}
        if task.status == "completed":
            return {
                "status": "ready",
                "task_key": task_key,
                ("path" if kind == "image" else "url"): str(
                    task.result.get(
                        "artifact_path" if kind == "image" else "artifact_url"
                    )
                    or ""
                ),
            }
        if task.status in {"failed", "cancelled"}:
            return {"status": "failed", "task_key": task_key, "error": task.last_error}
        return {"status": "pending", "task_key": task_key}

    async def generate_share_image_task(
        self,
        event: Any,
        prompt: str,
        *,
        task_key: str,
        contains_character: bool = False,
    ) -> dict:
        return await self._generate_share_asset_task(
            event,
            prompt,
            task_key=task_key,
            kind="image",
            contains_character=contains_character,
        )

    async def generate_share_video_task(
        self, event: Any, prompt: str, *, task_key: str, reference_image: str = ""
    ) -> dict:
        return await self._generate_share_asset_task(
            event,
            prompt,
            task_key=task_key,
            kind="video",
            reference_image=reference_image,
        )

    async def _generate_share_asset_task(
        self,
        event: Any,
        prompt: str,
        *,
        task_key: str,
        kind: str,
        contains_character: bool = False,
        reference_image: str = "",
    ) -> dict:
        """External assets are recovered as assets, never delivered to a chat scope."""
        label = "图片" if kind == "image" else "视频"
        owner = f"share_{kind}:" + uuid.uuid4().hex
        record = await self.archive.enqueue_durable_task(
            self._share_asset_key(task_key, kind),
            f"share_{kind}_generation",
            {"external_owner": "daily_share", "task_key": task_key},
            lease_owner=owner,
            lease_seconds=self._MEDIA_DELIVERY_LEASE_SECONDS,
            max_attempts=10000,
            priority=85,
        )
        if record.lease_owner != owner:
            return await self._get_share_asset_task(task_key, kind)
        progress = {}

        async def accepted(task_id, route, metadata=None):
            from ..media.base import normalize_openai_base_url

            reference = (
                {
                    "api_url": normalize_openai_base_url(route.api_url),
                    "model": route.model,
                    "protocol": route.protocol,
                }
                if kind == "image"
                else {key: route[key] for key in ("endpoint", "model")}
            )
            progress.update(task_id=task_id, route=reference)
            if not await self.archive.update_durable_task_progress(
                record.id, owner, progress
            ):
                raise RuntimeError(f"分享{label}任务已失去租约，不继续提交")

        tracker = getattr(getattr(self.media, kind), "track_async_tasks", None)
        from contextlib import nullcontext

        from ..media.picture.polling import ImageTaskError, ImageTaskFailed
        from ..media.video.errors import VideoTaskError, VideoTaskFailed

        try:
            with tracker(accepted) if callable(tracker) else nullcontext():
                if kind == "image":
                    result = await self.generate_life_image_asset(
                        event,
                        prompt,
                        "",
                        contains_character=contains_character,
                        preserve_reference_ratio=False,
                        trusted_identity=contains_character,
                    )
                else:
                    result = await self.generate_life_video_asset(
                        event, prompt, reference_image
                    )
            path = str(
                getattr(result, "path" if kind == "image" else "url", "") or ""
            ).strip()
            if not path:
                raise RuntimeError(f"分享{label}未返回成品")
            await self.archive.complete_durable_task(
                record.id,
                {"artifact_path" if kind == "image" else "artifact_url": path},
                owner=owner,
            )
        except (ImageTaskFailed, VideoTaskFailed) as exc:
            await self.archive.fail_durable_task(
                record.id, str(exc), owner=owner, permanent=True
            )
        except BaseException as exc:
            if progress.get("task_id"):
                await self.archive.defer_durable_task(
                    record.id,
                    self.archive._cognition_now(),
                    owner=owner,
                    reason=f"继续查询原分享{label}任务",
                    progress=progress,
                )
            else:
                await self.archive.fail_durable_task(
                    record.id,
                    f"{label}请求未获得可恢复编号，停止自动重提：" + type(exc).__name__,
                    owner=owner,
                    permanent=True,
                )
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            if not isinstance(exc, (ImageTaskError, VideoTaskError)) and not progress:
                raise
        return await self._get_share_asset_task(task_key, kind)

    async def resume_share_image_generation(self, task: Any) -> dict:
        return await self._resume_share_asset_generation(task, "image")

    async def resume_share_video_generation(self, task: Any) -> dict:
        return await self._resume_share_asset_generation(task, "video")

    async def _resume_share_asset_generation(self, task: Any, kind: str) -> dict:
        from ..media.picture.polling import ImageTaskError, ImageTaskFailed
        from ..media.video.errors import VideoTaskError, VideoTaskFailed

        failed_error = ImageTaskFailed if kind == "image" else VideoTaskFailed
        pending_error = ImageTaskError if kind == "image" else VideoTaskError
        label = "图片" if kind == "image" else "视频"
        progress = task.result.get("progress", {})
        if not progress.get("task_id") or not isinstance(progress.get("route"), dict):
            raise failed_error(f"外部{label}任务没有原任务编号，禁止重新提交")
        try:
            resume = (
                self.media.image.resume_async_image
                if kind == "image"
                else self.media.video.resume_async_video
            )
            result = await resume(progress["task_id"], progress["route"])
            path = str(
                getattr(result, "path" if kind == "image" else "url", "") or ""
            ).strip()
            if not path:
                raise pending_error(f"原分享{label}任务未返回成品")
            return {"artifact_path" if kind == "image" else "artifact_url": path}
        except (ImageTaskFailed, VideoTaskFailed):
            raise
        except (ImageTaskError, VideoTaskError) as exc:
            return {
                "retry_at": (
                    datetime.datetime.now() + datetime.timedelta(seconds=60)
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "reason": str(exc),
                "progress": progress,
            }

    async def track_image_generation(self, event: Any, work: Any) -> Any:
        """按请求隔离图片任务监听，受理后立即登记查询所需的非敏感信息。"""
        tracker = getattr(getattr(self.media, "image", None), "track_async_tasks", None)
        if not callable(tracker) or not callable(
            getattr(self.archive, "enqueue_durable_task", None)
        ):
            return await work
        pending = []
        event._daily_life_async_image_tasks = pending
        requested_at = (
            getattr(event, "_daily_life_media_requested_at", None) or life_now()
        )
        event._daily_life_media_requested_at = requested_at

        async def accepted(task_id, route, metadata=None):
            from ..media.base import normalize_openai_base_url

            api_url = normalize_openai_base_url(route.api_url)
            digest = hashlib.sha256(f"{api_url}:{task_id}".encode()).hexdigest()[:24]
            owner = f"image:{uuid.uuid4().hex}"
            action_date = str(getattr(event, "_daily_life_action_date", "") or "")
            if getattr(event, "_daily_life_action_id", "") and not action_date:
                action_date, _ = await self.resolve_injection_target(requested_at)
            record = await self.archive.enqueue_durable_task(
                f"image_generation:{digest}",
                "image_generation",
                {
                    "task_id": task_id,
                    "route": {
                        "api_url": api_url,
                        "model": route.model,
                        "protocol": route.protocol,
                    },
                    "scope": self._event_session_id(event),
                    "source_message_id": self._event_message_id(event),
                    "commitment_id": int(
                        getattr(event, "_daily_life_commitment_id", 0) or 0
                    ),
                    "action_id": str(getattr(event, "_daily_life_action_id", "") or ""),
                    "action_date": action_date,
                    "request_text": str(getattr(event, "message_str", "") or "")[:1000],
                    "photo_suite": dict(metadata or {}),
                },
                priority=85,
                max_attempts=3,
                lease_owner=owner,
                lease_seconds=self._MEDIA_DELIVERY_LEASE_SECONDS,
            )
            pending.append(record)

        try:
            with tracker(accepted):
                return await work
        finally:
            retry_at = (
                datetime.datetime.now() + datetime.timedelta(seconds=30)
            ).strftime("%Y-%m-%d %H:%M:%S")
            for task in pending:
                try:
                    marker = getattr(event, "_daily_life_image_request", {})
                    if isinstance(marker, dict) and marker.get("status") == "sent":
                        await self.archive.finalize_durable_task(
                            task.id, {"delivery": "sent"}, owner=task.lease_owner
                        )
                        continue
                    await self.archive.defer_durable_task(
                        task.id,
                        retry_at,
                        owner=task.lease_owner,
                        reason="继续查询已经受理的图片任务，不重新提交",
                    )
                except Exception as exc:
                    logger.warning(
                        f"[日常生活] 图片任务释放租约失败，将由过期恢复处理：{exc}"
                    )

    async def pending_image_generation_result(
        self, event: Any, exc: Exception
    ) -> str | None:
        import json

        from ..media.picture.polling import ImageTaskFailed

        pending = getattr(event, "_daily_life_async_image_tasks", [])
        if not pending:
            return None
        if isinstance(exc, ImageTaskFailed):
            for task in pending:
                await self.archive.fail_durable_task(
                    task.id, str(exc), owner=task.lease_owner, permanent=True
                )
            pending.clear()
            return None
        return json.dumps(
            {"status": "pending", "media": "image", "recovery": "查询原任务"},
            ensure_ascii=False,
        )

    async def resume_durable_image_generation(self, task: Any) -> dict[str, Any]:
        """恢复只查询原任务，再将成品交给媒体投递队列。"""
        from ..media.picture.polling import ImageTaskError, ImageTaskFailed

        payload = task.payload
        progress = task.result.get("progress", {})
        path = str(progress.get("artifact_path") or "")
        try:
            if not path or not Path(path).is_file():
                generated = await self.media.image.resume_async_image(
                    payload["task_id"], payload["route"]
                )
                path = str(generated.path)
                progress = {"artifact_path": path}
                if not await self.archive.update_durable_task_progress(
                    task.id, task.lease_owner, progress
                ):
                    raise RuntimeError("图片恢复任务已失去租约，暂不投递")
        except ImageTaskFailed:
            raise
        except ImageTaskError as exc:
            return {
                "retry_at": (
                    datetime.datetime.now() + datetime.timedelta(seconds=60)
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "reason": str(exc),
                "progress": progress,
            }
        origin = SimpleNamespace(
            _daily_life_action_id=payload.get("action_id", ""),
            _daily_life_action_date=payload.get("action_date", ""),
            _daily_life_photo_suite_slot=payload.get("photo_suite", {}),
        )
        await self._record_recovered_photo_suite_slot(
            payload.get("photo_suite", {}), path, sent=False
        )
        delivery = await self.stage_durable_media_delivery(
            payload["scope"],
            "image",
            [path],
            action_type="photo",
            evidence="原图片异步任务已完成，等待投递确认",
            source_event=origin,
            commitment_id=int(payload.get("commitment_id") or 0),
            source_message_id=payload.get("source_message_id", ""),
            reply_context={
                "media_name": "生活照片",
                "request_text": payload.get("request_text", ""),
                "delivery_text": "图片已恢复并成功送达",
            },
        )
        if delivery is None:
            raise RuntimeError("原图片任务已完成，投递登记失败，保留成品等待恢复")
        if delivery.status == "leased":
            await self.archive.defer_durable_task(
                delivery.id, self.archive._cognition_now(), owner=delivery.lease_owner
            )
        return {"delivery_task_id": delivery.id, "artifact_path": path}

    async def _record_recovered_photo_suite_slot(
        self, metadata: dict[str, Any], path: str, *, sent: bool
    ) -> None:
        if not metadata:
            return
        manifest_path = Path(metadata["manifest_path"])
        manifest = await self._photo_suite_read_manifest(manifest_path)
        if manifest is None:
            return
        for shot in manifest.get("shots", []):
            if int(shot.get("index") or 0) == int(metadata["slot_index"]):
                shot.update(path=path, status="sent" if sent else "generated", error="")
        if sent:
            manifest["status"] = (
                "completed"
                if all(
                    shot.get("status") == "sent" for shot in manifest.get("shots", [])
                )
                else "partial"
            )
        await self._photo_suite_write_manifest(manifest_path, manifest)

    async def record_current_life_action_receipt(
        self,
        event: Any,
        action_type: str,
        *,
        status: str = "confirmed",
        evidence: str = "",
        source: str = "",
        source_id: str = "",
        artifact_path: str = "",
        action_id: str = "",
        action_date: str = "",
    ) -> Any:
        """记录当前日程动作的外部执行回执。

        Args:
            event: 原始消息事件或可提供会话范围的对象。
            action_type: 已确认动作类型。
            status: confirmed、simulated、failed 或 cancelled。simulated 仅用于兼容旧回执，系统不会自动生成。
            evidence: 可展示的执行证据。
            source: 回执来源类别。
            source_id: 来源的稳定编号。
            artifact_path: 生成媒体的本地路径或可追溯地址。
            action_id: 可选的精确动作编号。
            action_date: 动作所属生活日；媒体恢复必须使用登记时的日期。

        Returns:
            匹配到的结算结果；当前没有对应计划动作时返回空。
        """

        composer = getattr(self, "composer", None)
        archive = getattr(self, "archive", None)
        if composer is None or archive is None:
            return None
        now_getter = getattr(self, "_runtime_now", None)
        now = now_getter() if callable(now_getter) else life_now()
        action_id = str(
            action_id or getattr(event, "_daily_life_action_id", "") or ""
        ).strip()
        action_date = str(
            action_date or getattr(event, "_daily_life_action_date", "") or ""
        ).strip()
        # 临时媒体请求没有计划动作编号，不能认领当天任意同类日程。
        if action_type in {"photo", "video"} and not action_id:
            return None
        resolver = getattr(self, "resolve_injection_target", None)
        if not callable(resolver):
            return None
        date_str = action_date
        if not date_str:
            requested_at = getattr(event, "_daily_life_media_requested_at", None)
            date_str, _ = await resolver(requested_at or now)
        day = await archive.get_day(date_str)
        if day is None:
            return None
        receipt = {
            "status": str(status or "confirmed").strip().lower(),
            "evidence": str(evidence or "").strip(),
            "source": str(source or "external_action").strip(),
            "source_id": str(source_id or "").strip(),
            "artifact_path": str(artifact_path or "").strip(),
            "occurred_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if action_id:
            recorder = getattr(composer, "record_life_action_receipt", None)
            outcome = (
                await recorder(day, action_id, receipt, now=now)
                if callable(recorder)
                else None
            )
        else:
            matcher = getattr(composer, "record_matching_life_action_receipt", None)
            outcome = (
                await matcher(day, action_type, receipt, now=now)
                if callable(matcher)
                else None
            )
        if outcome is None:
            return None
        notifier = getattr(self, "mark_page_status_changed", None)
        if callable(notifier):
            await notifier("life_action_receipt")
        if str(status).strip().lower() in {"failed", "cancelled"}:
            refresher = getattr(self, "refresh_state_for_day", None)
            if callable(refresher):
                await refresher(
                    date_str,
                    day,
                    now,
                    source="action_receipt",
                    detail=str(evidence or "动作执行未完成"),
                    force=True,
                    notify_page=False,
                )
        return outcome

    async def stage_durable_media_delivery(
        self,
        scope: str,
        media_kind: str,
        artifacts: list[str] | tuple[str, ...],
        *,
        action_type: str,
        evidence: str,
        commitment_id: int = 0,
        source_message_id: str = "",
        reply_context: dict[str, str] | None = None,
        source_event: Any = None,
    ) -> Any:
        """在发送前登记已生成媒体，供重启后的投递恢复使用。

        产物保留原请求和计划动作归属，恢复发送不会认领其他日程。
        """

        archive = getattr(self, "archive", None)
        enqueue = getattr(archive, "enqueue_durable_task", None)
        kind = str(media_kind or "").strip().lower()
        normalized_scope = str(scope or "").strip()
        normalized_artifacts = [
            str(item or "").strip() for item in artifacts if str(item or "").strip()
        ]
        if (
            not callable(enqueue)
            or not normalized_scope
            or kind not in {"image", "images", "video"}
            or not normalized_artifacts
        ):
            return None
        key_material = "\n".join((normalized_scope, kind, *normalized_artifacts))
        digest = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
        try:
            action_id = str(
                getattr(source_event, "_daily_life_action_id", "") or ""
            ).strip()
            action_date = str(
                getattr(source_event, "_daily_life_action_date", "") or ""
            ).strip()
            if action_id and not action_date:
                requested_at = getattr(
                    source_event, "_daily_life_media_requested_at", None
                )
                action_date, _ = await self.resolve_injection_target(
                    requested_at or life_now()
                )
            owner = str(
                getattr(self, "_durable_task_owner", f"runtime:{id(self)}") or ""
            ).strip()
            delivery = await enqueue(
                f"media_delivery:{digest}",
                "media_delivery",
                {
                    "scope": normalized_scope,
                    "media_kind": kind,
                    "artifacts": normalized_artifacts,
                    "action_type": str(action_type or "").strip(),
                    "action_id": action_id,
                    "action_date": action_date,
                    "photo_suite": dict(
                        getattr(source_event, "_daily_life_photo_suite_slot", {}) or {}
                    ),
                    "evidence": str(evidence or "").strip()[:500],
                    "commitment_id": max(0, int(commitment_id or 0)),
                    "source_message_id": str(source_message_id or "").strip(),
                    "reply_context": {
                        str(key): str(value or "").strip()[:1000]
                        for key, value in dict(reply_context or {}).items()
                        if str(key).strip()
                    },
                    "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
                priority=90,
                max_attempts=3,
                lease_owner=owner,
                lease_seconds=self._MEDIA_DELIVERY_LEASE_SECONDS,
            )
            pending = getattr(source_event, "_daily_life_async_image_tasks", [])
            for task in list(pending):
                metadata = task.payload.get("photo_suite", {})
                if metadata and int(metadata.get("slot_index") or 0) not in getattr(
                    source_event, "_daily_life_photo_suite_ready_indexes", set()
                ):
                    continue
                await archive.finalize_durable_task(
                    task.id, {"delivery_task_id": delivery.id}, owner=task.lease_owner
                )
                pending.remove(task)
            return delivery
        except Exception as exc:
            logger.warning(f"[日常生活] 媒体投递任务登记失败：{exc}")
            return None

    async def finalize_durable_media_delivery(
        self, task: Any, *, outcome: str, detail: str = ""
    ) -> bool:
        """标记当前请求已经完成或取消了已登记的媒体投递。"""

        task_id = int(getattr(task, "id", 0) or 0)
        finalizer = getattr(
            getattr(self, "archive", None), "finalize_durable_task", None
        )
        if task_id <= 0 or not callable(finalizer):
            return False
        try:
            owner = str(getattr(task, "lease_owner", "") or "").strip()
            finalized = await finalizer(
                task_id,
                {
                    "delivery": str(outcome or "sent").strip(),
                    "detail": str(detail or "").strip()[:500],
                    "completed_at": datetime.datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                },
                owner=owner,
            )
            payload = getattr(task, "payload", {})
            payload = payload if isinstance(payload, dict) else {}
            commitment_id = int(payload.get("commitment_id") or 0)
            if finalized and str(outcome or "").strip() == "sent" and commitment_id > 0:
                setter = getattr(
                    getattr(self, "archive", None), "set_commitment_status", None
                )
                if callable(setter):
                    await setter(
                        commitment_id,
                        "done",
                        life_now().isoformat(timespec="seconds"),
                    )
            elif finalized and str(outcome or "").strip() == "sent":
                await self._settle_direct_media_commitment(payload)
            return bool(finalized)
        except Exception as exc:
            logger.warning(f"[日常生活] 媒体投递任务收束失败：{exc}")
            return False

    async def direct_media_was_delivered(
        self, scope: str, media_kind: str, message_ids: list[str]
    ) -> bool:
        getter = getattr(getattr(self, "archive", None), "get_durable_tasks", None)
        if not callable(getter):
            return False
        ids = {
            str(value or "").strip()
            for value in message_ids
            if str(value or "").strip()
        }
        if not ids:
            return False
        tasks = await getter(kind="media_delivery", limit=200)
        expected_kinds = {"image", "images"} if media_kind == "photo" else {media_kind}
        return any(
            task.status == "completed"
            and str(task.result.get("delivery") or "") in {"sent", "recovered"}
            and str(task.payload.get("scope") or "") == scope
            and str(task.payload.get("media_kind") or "") in expected_kinds
            and str(task.payload.get("source_message_id") or "") in ids
            for task in tasks
        )

    async def _settle_direct_media_commitment(self, payload: dict[str, Any]) -> None:
        source_id = str(payload.get("source_message_id") or "").strip()
        scope = str(payload.get("scope") or "").strip()
        media_kind = str(payload.get("media_kind") or "").strip()
        if not source_id or not scope or media_kind not in {"image", "images", "video"}:
            return
        getter = getattr(
            getattr(self, "archive", None), "get_open_commitments_for_message", None
        )
        setter = getattr(getattr(self, "archive", None), "set_commitment_status", None)
        if not callable(getter) or not callable(setter):
            return
        matches = [
            item
            for item in await getter(scope, source_id)
            if item.media_kind == ("video" if media_kind == "video" else "photo")
            and item.owner in {"当前角色", "共同"}
        ]
        if len(matches) == 1:
            await setter(
                matches[0].id, "done", life_now().isoformat(timespec="seconds")
            )

    async def resume_durable_media_delivery(self, task: Any) -> dict[str, Any]:
        """投递重启前已生成但尚未确认发送的媒体产物。"""

        payload = getattr(task, "payload", {})
        payload = payload if isinstance(payload, dict) else {}
        scope = str(payload.get("scope") or "").strip()
        media_kind = str(payload.get("media_kind") or "").strip().lower()
        artifacts = [
            str(item or "").strip()
            for item in payload.get("artifacts", [])
            if str(item or "").strip()
        ]
        if not scope or media_kind not in {"image", "images", "video"} or not artifacts:
            raise ValueError("媒体投递任务缺少会话、类型或产物")
        if media_kind in {"image", "images"}:
            from pathlib import Path

            paths = [Path(item) for item in artifacts]
            if any(not path.is_file() for path in paths):
                raise FileNotFoundError("待恢复的图片产物已不存在")
            chain = (
                self.image_message_chain(paths[0])
                if media_kind == "image"
                else self.images_message_chain(paths)
            )
        else:
            artifact = artifacts[0]
            if not artifact.startswith(("http://", "https://")):
                from pathlib import Path

                if not Path(artifact).is_file():
                    raise FileNotFoundError("待恢复的视频产物已不存在")
            chain = (
                self.video_file_message_chain(artifact)
                if scope.split(":", 1)[0].lower() == "webchat"
                else self.video_message_chain(artifact)
            )
        sent = await send_message_to_scope(self.context, scope, chain)
        if sent is False:
            raise RuntimeError("目标平台尚未就绪，媒体将在后续任务中重试")
        log_outbound = getattr(self, "log_outbound_message_async", None)
        if not callable(log_outbound):
            log_outbound = getattr(self, "log_outbound_message", None)
        if callable(log_outbound):
            result = log_outbound(chain, scope=scope, source="media_recovery")
            if inspect.isawaitable(result):
                await result
        logger.debug("[日常生活] 已恢复投递重启前生成的媒体产物")
        await self._record_recovered_photo_suite_slot(
            payload.get("photo_suite", {}), artifacts[0], sent=True
        )
        recorder = getattr(self, "record_current_life_action_receipt", None)
        action_type = str(payload.get("action_type") or "").strip()
        if (
            callable(recorder)
            and action_type
            and payload.get("action_id")
            and payload.get("action_date")
        ):
            await recorder(
                None,
                action_type,
                evidence=str(payload.get("evidence") or "媒体恢复投递成功"),
                source="media_delivery_recovery",
                artifact_path=artifacts[0],
                action_id=str(payload["action_id"]),
                action_date=str(payload["action_date"]),
            )
        commitment_id = int(payload.get("commitment_id") or 0)
        if commitment_id > 0:
            setter = getattr(
                getattr(self, "archive", None), "set_commitment_status", None
            )
            if callable(setter):
                await setter(
                    commitment_id,
                    "done",
                    life_now().isoformat(timespec="seconds"),
                )
        else:
            await self._settle_direct_media_commitment(payload)
        reply_sent = False
        reply_context = payload.get("reply_context")
        followup = getattr(self, "_send_delivered_media_followup", None)
        if callable(followup) and isinstance(reply_context, dict) and reply_context:
            reply_sent = await followup(
                scope,
                media_name=str(reply_context.get("media_name") or "生活媒体"),
                request_text=str(reply_context.get("request_text") or ""),
                delivery_text=str(
                    reply_context.get("delivery_text") or "媒体已恢复并成功送达"
                ),
                guidance=str(reply_context.get("guidance") or ""),
                source="media_recovery_followup",
            )
        return {
            "delivery": "recovered",
            "scope": scope,
            "media_kind": media_kind,
            "artifacts": artifacts,
            "reply_sent": reply_sent,
        }


__all__ = ["RuntimeActionReceiptMixin"]
