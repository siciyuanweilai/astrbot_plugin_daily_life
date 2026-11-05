"""Read bot identity from the matching platform, excluding adapter placeholders."""
from __future__ import annotations

from typing import Any

from .events import iter_event_sources, safe_invoke
from .platforms import (
    WEIXIN_OC_TYPE,
    event_platform_names,
    get_platform_client,
    get_platform_id,
    get_platform_type,
    is_weixin_oc_event,
    iter_platform_instances,
    parse_unified_origin,
)

_QQ_OFFICIAL_TYPES = {"qq_official", "qq_official_webhook"}
_PLACEHOLDER_IDS = {"unknown_selfid", *_QQ_OFFICIAL_TYPES}


def bot_id_value(value: Any) -> str:
    if callable(value) or not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    identity = str(value or "").strip()
    return "" if identity.lower() in _PLACEHOLDER_IDS else identity


def get_qq_official_client(
    context: Any = None, *, event: Any = None, scope: str = ""
) -> Any:
    """Resolve only the event's official QQ client or the exact UMO instance."""
    sources = [source for source in iter_event_sources(event)
               if not getattr(source, "is_proactive_synthetic", False)]
    scope = scope or next((str(getattr(source, "unified_msg_origin", "") or "")
                           for source in sources if getattr(source, "unified_msg_origin", "")), "")
    platform_id, _ = parse_unified_origin(scope)
    for source in sources:
        names = {name.strip().lower() for name in event_platform_names(source)}
        if not names:
            continue
        if not names & _QQ_OFFICIAL_TYPES:
            return None
        event_id, _ = parse_unified_origin(getattr(source, "unified_msg_origin", ""))
        event_id = event_id or str(safe_invoke(getattr(source, "get_platform_id", None)) or "")
        if platform_id and event_id and platform_id != event_id:
            return None
        bot = getattr(source, "bot", None)
        if bot is not None:
            return bot
        platform = getattr(source, "platform", None)
        if get_platform_type(platform).lower() in _QQ_OFFICIAL_TYPES:
            bot = get_platform_client(platform)
            if bot is not None:
                return bot
    if not platform_id:
        return None
    matches = [instance for instance in iter_platform_instances(context)
               if get_platform_id(instance) == platform_id]
    if len(matches) != 1 or get_platform_type(matches[0]).lower() not in _QQ_OFFICIAL_TYPES:
        return None
    return get_platform_client(matches[0])


def qq_official_profile_value(value: Any) -> tuple[str, str, str]:
    """Accept the SDK Robot object and /users/@me's dictionary response."""
    def field(key: str) -> str:
        raw = value.get(key) if isinstance(value, dict) else getattr(value, key, None)
        if callable(raw) or not isinstance(raw, (str, int)) or isinstance(raw, bool):
            return ""
        return str(raw or "").strip()

    return (
        field("username") or field("name"),
        bot_id_value(field("id")),
        field("avatar"),
    )


def qq_official_profile(bot: Any) -> tuple[str, str, str]:
    # botpy.Client.robot raises before a websocket session exists (including
    # webhook clients). The caller can use api.me() asynchronously if needed.
    try:
        return qq_official_profile_value(getattr(bot, "robot", None))
    except Exception:
        return "", "", ""


def get_weixin_oc_instance(
    context: Any = None, *, event: Any = None, scope: str = ""
) -> Any:
    """The Weixin login account belongs to the adapter, not its HTTP client."""
    sources = [source for source in iter_event_sources(event)
               if not getattr(source, "is_proactive_synthetic", False)]
    scope = scope or next((str(getattr(source, "unified_msg_origin", "") or "")
                           for source in sources if getattr(source, "unified_msg_origin", "")), "")
    platform_id, _ = parse_unified_origin(scope)
    for source in sources:
        names = event_platform_names(source)
        if names and not is_weixin_oc_event(source):
            return None
        event_id, _ = parse_unified_origin(getattr(source, "unified_msg_origin", ""))
        if platform_id and event_id and platform_id != event_id:
            return None
        platform = getattr(source, "platform", None)
        if get_platform_type(platform).lower() == WEIXIN_OC_TYPE:
            if platform_id and get_platform_id(platform) != platform_id:
                return None
            return platform
    if not platform_id:
        return None
    matches = [instance for instance in iter_platform_instances(context)
               if get_platform_id(instance) == platform_id]
    if len(matches) != 1 or get_platform_type(matches[0]).lower() != WEIXIN_OC_TYPE:
        return None
    return matches[0]


def event_bot_id(event: Any, *, context: Any = None, scope: str = "") -> str:
    """Read a genuine event ID, then the matching client's cached login ID."""
    weixin = get_weixin_oc_instance(context, event=event, scope=scope)
    if weixin is not None:
        identity = bot_id_value(getattr(weixin, "account_id", None))
        if identity:
            return identity
    for source in iter_event_sources(event):
        if getattr(source, "is_proactive_synthetic", False):
            continue
        # AstrBot's Weixin adapter puts its configured connection label in
        # self_id. It is neither a nickname nor an account ID.
        aliases = set()
        if weixin is not None or is_weixin_oc_event(source):
            aliases = {
                WEIXIN_OC_TYPE,
                get_platform_id(weixin),
                get_platform_id(getattr(source, "platform", None)),
                str(safe_invoke(getattr(source, "get_platform_id", None)) or ""),
                parse_unified_origin(getattr(source, "unified_msg_origin", ""))[0],
            }
        identity = bot_id_value(safe_invoke(getattr(source, "get_self_id", None)))
        if identity and identity not in aliases:
            return identity
        for candidate in (source, getattr(source, "message_obj", None)):
            for attr in ("self_id", "bot_id"):
                identity = bot_id_value(getattr(candidate, attr, None))
                if identity and identity not in aliases:
                    return identity
    bot = get_qq_official_client(context, event=event, scope=scope)
    return qq_official_profile(bot)[1]
