"""与分享插件协调独立主动联系；可选能力，不读取对方的内部存储。"""

from __future__ import annotations

from astrbot.api import logger


async def coordinate(runtime, scope, *, owner="", action="check", rhythm=None, selected=()):
    reader = getattr(getattr(runtime, "context", None), "get_all_stars", None)
    if not callable(reader):
        return {"ready": True, "retry_after": 0}
    try:
        for metadata in reader() or []:
            if (getattr(metadata, "root_dir_name", "") != "astrbot_plugin_daily_share"
                    and getattr(metadata, "name", "") != "astrbot_plugin_daily_share"):
                continue
            plugin = getattr(metadata, "star_cls", None)
            method = getattr(plugin, "coordinate_autonomous_contact", None)
            if not getattr(metadata, "activated", False) or not callable(method):
                return {"ready": True, "retry_after": 0}
            if rhythm is None:
                from .contact import contact_rhythm
                from ..clock import now
                rhythm = await contact_rhythm(runtime, scope, now(), shared=False)
            # 公共发布不带入私聊时间；仅绑定身份的主动联系使用所属会话时间。
            state = {} if scope in {"global", "qzone_broadcast", "bilibili_broadcast"} else {
                "last_received_at": rhythm.get("last_received_at", 0)}
            result = await method(scope, owner=owner, action=action, rhythm=state, selected=list(selected))
            if isinstance(result, dict) and isinstance(result.get("ready"), bool):
                return result
            raise ValueError("invalid contact coordination result")
        return {"ready": True, "retry_after": 0}
    except Exception as exc:
        logger.debug(f"[生活协作] 主动联系协调暂缓：{type(exc).__name__}")
        return {"ready": False, "retry_after": 60, "reason": "等待主动联系协调恢复"}


async def merge(runtime, scope, rhythm):
    gate = await coordinate(runtime, scope, rhythm=rhythm)
    if gate.get("ready") is False:
        rhythm = {**rhythm, "ready": False,
                  "retry_after": max(rhythm.get("retry_after", 0), gate.get("retry_after", 60))}
    return rhythm
