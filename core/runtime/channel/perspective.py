"""随媒体任务保留说话人、收话人与画面人物，供交付补话延续同一视角。"""

from __future__ import annotations

from ...addresses import habitual_address

import copy
import json
from typing import Any

from astrbot.api import logger

from ...sources.events import event_attr, event_call, iter_event_sources
from ...sources.platforms import parse_unified_origin

MEDIA_REPLY_CONTEXT_ATTR = "_daily_life_media_reply_context"
MEDIA_REPLY_PERSPECTIVE_RULE = (
    "用当前角色本人的第一人称视角承接对话，依据请求中保留的身份对应关系，"
    "分清画面里的自己、当前聊天对象和其他人物。画面设定本身不证明现实中已经共同经历。"
)


def friend_reference_options(host: Any) -> list[dict[str, str]]:
    image = getattr(getattr(host, "media", None), "image", None)
    reader = getattr(image, "friend_reference_options", None)
    if not callable(reader):
        return []
    try:
        profiles = reader() or []
    except Exception as exc:
        logger.debug(f"[日常生活] 媒体参考档案读取暂未完成：{type(exc).__name__}")
        return []
    return [
        {
            "profile_id": str(item["profile_id"]).strip(),
            "display_name": str(item.get("display_name") or item["profile_id"]).strip(),
        }
        for item in profiles
        if isinstance(item, dict) and str(item.get("profile_id") or "").strip()
    ]


async def resolve_group_participants(
    host: Any, event: Any, participants: list[str]
) -> list[str]:
    """保留显式选择；省略时仅采用能唯一对应私聊发送者的参考档案。"""
    if participants:
        return list(participants)
    origin = event_attr(event, "unified_msg_origin")
    parts = origin.split(":", 2)
    if len(parts) != 3 or parts[1] != "FriendMessage":
        return []
    if event_call(event, "get_group_id"):
        return []
    platform, target_id = parse_unified_origin(origin)
    sender_id = event_call(event, "get_sender_id") or target_id
    if not sender_id:
        return []
    get_relationship = getattr(getattr(host, "archive", None), "get_relationship", None)
    matches = set()
    for item in friend_reference_options(host):
        profile_id = item["profile_id"]
        is_sender = profile_id == sender_id
        if callable(get_relationship):
            try:
                record = await get_relationship(profile_id)
            except Exception as exc:
                logger.debug(f"[日常生活] 合影好友身份暂未确认：{type(exc).__name__}")
                continue
            if record is not None:
                if str(record.platform or "") not in {"", platform}:
                    continue
                if str(record.user_id or ""):
                    is_sender = str(record.user_id) == sender_id
        if is_sender:
            matches.add(profile_id)
    return list(matches) if len(matches) == 1 else []


def group_participants_required(host: Any, *, media: str = "image") -> str:
    return json.dumps(
        {
            "status": "needs_parameters",
            "media": media,
            "required_parameters": ["participants"],
            "message": "本次合影需要选择一位好友，请补齐 participants；尚未开始生成。",
            "available_participants": friend_reference_options(host),
        },
        ensure_ascii=False,
    )


def media_reply_context(event: Any) -> dict[str, Any]:
    for source in iter_event_sources(event):
        value = getattr(source, MEDIA_REPLY_CONTEXT_ATTR, None)
        if isinstance(value, dict):
            return copy.deepcopy(value)
    return {}


def restore_media_reply_context(event: Any, value: Any) -> None:
    if event is not None:
        setattr(
            event,
            MEDIA_REPLY_CONTEXT_ATTR,
            copy.deepcopy(value) if isinstance(value, dict) else {},
        )


async def bind_media_reply_context(
    host: Any,
    event: Any,
    subject_route: str,
    participants: list[str] | tuple[str, ...] = (),
    *,
    source_request: str | None = None,
) -> dict[str, Any]:
    """只读取已知身份，不根据昵称、画面提示词或聊天范围猜测人物归属。"""
    origin = event_attr(event, "unified_msg_origin")
    platform, target_id = parse_unified_origin(origin)
    sender_id = event_call(event, "get_sender_id")
    # 群会话 ID 是投递地址，不是这次说话人的身份。
    if not sender_id and ":FriendMessage:" in origin:
        sender_id = target_id
    addressee = {"id": sender_id, "name": event_call(event, "get_sender_name")}
    persona = ""
    get_persona = getattr(host, "get_persona_text", None)
    if callable(get_persona):
        try:
            persona = str(await get_persona(origin) or "").strip()
        except Exception as exc:
            logger.debug(f"[日常生活] 媒体身份快照暂未取得人设：{type(exc).__name__}")
    names = {
        item["profile_id"]: item["display_name"]
        for item in friend_reference_options(host)
    }
    people = []
    route = str(subject_route or "free")
    if route in {"current_character", "group"}:
        people.append(
            {"画面身份": "人物 A（当前角色本人）", "对话身份": "说话人（我）"}
        )
    get_relationship = getattr(getattr(host, "archive", None), "get_relationship", None)
    for profile_id in dict.fromkeys(
        str(value).strip()
        for value in participants
        if route == "group" and str(value).strip()
    ):
        name = names.get(profile_id, "")
        subjective_name = ""
        is_addressee = bool(sender_id and profile_id == sender_id)
        if callable(get_relationship):
            try:
                record = await get_relationship(profile_id)
                if record is not None:
                    subjective_name = habitual_address(record)
                    name = str(
                        name or record.name or record.alias or profile_id
                    )
                    if str(record.platform or "") not in {"", platform}:
                        is_addressee = False
                    elif str(record.user_id or ""):
                        is_addressee = bool(
                            sender_id and str(record.user_id) == sender_id
                        )
            except Exception as exc:
                logger.debug(
                    f"[日常生活] 媒体人物关系读取暂未完成：{type(exc).__name__}"
                )
        people.append(
            {
                "画面身份": "人物 B（合影好友）",
                "档案 ID": profile_id,
                "姓名或称呼": name,
                "习惯称呼": subjective_name,
                "对话身份": "当前聊天对象（你）"
                if is_addressee
                else "其他人物（未确认为当前聊天对象）",
            }
        )
    if source_request is None:
        reader = getattr(host, "_event_current_image_request_text", None)
        source_request = (
            reader(event) if callable(reader) else event_attr(event, "message_str")
        )
    value = {
        "角色人设": persona,
        "当前聊天对象": addressee,
        "用户原话": str(source_request or ""),
        "画面路线": route,
        "画面人物对应": people,
        "角色是否在画面中": (
            "是"
            if route in {"current_character", "group"}
            else "否"
            if route == "scene"
            else "未确认"
        ),
    }
    restore_media_reply_context(event, value)
    return value


def media_reply_context_text(event: Any, persona: str = "") -> str:
    value = media_reply_context(event)
    # 人设是身份依据，不能只截取开头的口吻描述而丢失后面的身份和关系。
    value["角色人设"] = value.get("角色人设") or str(persona or "").strip()
    if not value["角色人设"]:
        value["角色人设"] = "沿用当前角色口吻。"
    return "对话与画面身份：\n" + json.dumps(value, ensure_ascii=False)
