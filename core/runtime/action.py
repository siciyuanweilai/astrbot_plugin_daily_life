from __future__ import annotations

import datetime
import hashlib
from typing import Any

from astrbot.api import logger

from ..clock import now as life_now


class RuntimeActionReceiptMixin:
    """将已确认的工具结果提交给当前生活动作。"""

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
    ) -> Any:
        """记录当前日程动作的外部执行回执。

        Args:
            event: 原始消息事件或可提供会话范围的对象。
            action_type: 已确认动作类型。
            status: confirmed、simulated、failed 或 cancelled。
            evidence: 可展示的执行证据。
            source: 回执来源类别。
            source_id: 来源的稳定编号。
            artifact_path: 生成媒体的本地路径或可追溯地址。
            action_id: 可选的精确动作编号。

        Returns:
            匹配到的结算结果；当前没有对应计划动作时返回空。
        """

        composer = getattr(self, "composer", None)
        archive = getattr(self, "archive", None)
        if composer is None or archive is None:
            return None
        now_getter = getattr(self, "_runtime_now", None)
        now = now_getter() if callable(now_getter) else life_now()
        resolver = getattr(self, "resolve_injection_target", None)
        if not callable(resolver):
            return None
        date_str, _ = await resolver(now)
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
    ) -> Any:
        """在发送前登记已生成媒体，供重启后的投递恢复使用。

        外部生图、生视频请求没有通用的幂等恢复协议，因此只持久化已经
        取得的产物，避免重启时重复调用付费接口。
        """

        archive = getattr(self, "archive", None)
        enqueue = getattr(archive, "enqueue_durable_task", None)
        kind = str(media_kind or "").strip().lower()
        normalized_scope = str(scope or "").strip()
        normalized_artifacts = [
            str(item or "").strip()
            for item in artifacts
            if str(item or "").strip()
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
            return await enqueue(
                f"media_delivery:{digest}",
                "media_delivery",
                {
                    "scope": normalized_scope,
                    "media_kind": kind,
                    "artifacts": normalized_artifacts,
                    "action_type": str(action_type or "").strip(),
                    "evidence": str(evidence or "").strip()[:500],
                    "created_at": datetime.datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                },
                priority=90,
                max_attempts=3,
            )
        except Exception as exc:
            logger.warning(f"[日常生活] 媒体投递任务登记失败：{exc}")
            return None

    async def finalize_durable_media_delivery(
        self, task: Any, *, outcome: str, detail: str = ""
    ) -> bool:
        """标记当前请求已经完成或取消了已登记的媒体投递。"""

        task_id = int(getattr(task, "id", 0) or 0)
        finalizer = getattr(getattr(self, "archive", None), "finalize_durable_task", None)
        if task_id <= 0 or not callable(finalizer):
            return False
        try:
            return await finalizer(
                task_id,
                {
                    "delivery": str(outcome or "sent").strip(),
                    "detail": str(detail or "").strip()[:500],
                    "completed_at": datetime.datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                },
            )
        except Exception as exc:
            logger.warning(f"[日常生活] 媒体投递任务收束失败：{exc}")
            return False

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
            chain = self.video_message_chain(artifact)
        await self.context.send_message(scope, chain)
        recorder = getattr(self, "record_current_life_action_receipt", None)
        action_type = str(payload.get("action_type") or "").strip()
        if callable(recorder) and action_type:
            await recorder(
                None,
                action_type,
                evidence=str(payload.get("evidence") or "媒体恢复投递成功"),
                source="media_delivery_recovery",
                artifact_path=artifacts[0],
            )
        logger.info("[日常生活] 已恢复投递重启前生成的媒体产物")
        return {
            "delivery": "recovered",
            "scope": scope,
            "media_kind": media_kind,
            "artifacts": artifacts,
        }


__all__ = ["RuntimeActionReceiptMixin"]
