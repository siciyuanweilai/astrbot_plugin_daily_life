import inspect
from typing import Any


ONEBOT_TYPES = {"aiocqhttp", "onebot", "cqhttp"}
WEIXIN_OC_TYPE = "weixin_oc"


def iter_platform_instances(context: Any) -> list[Any]:
    try:
        manager = getattr(context, "platform_manager", None)
        if not manager:
            return []

        get_insts = getattr(manager, "get_insts", None)
        if callable(get_insts):
            return list(get_insts() or [])

        return list(getattr(manager, "platform_insts", []) or [])
    except Exception:
        return []


def get_platform_client(instance: Any) -> Any:
    if not instance:
        return None

    get_client = getattr(instance, "get_client", None)
    if callable(get_client):
        try:
            return get_client()
        except Exception:
            pass

    return getattr(instance, "bot", None)


def first_onebot_client(context: Any) -> Any:
    for instance in iter_platform_instances(context):
        if not is_onebot_platform(get_platform_type(instance)):
            continue
        client = get_platform_client(instance)
        if client:
            return client
    return None


def first_weixin_oc_adapter_id(context: Any) -> str:
    for instance in iter_platform_instances(context):
        if is_weixin_oc_instance(instance):
            adapter_id = get_platform_id(instance)
            if adapter_id:
                return adapter_id
    return ""


def get_platform_meta(instance: Any) -> Any:
    meta = getattr(instance, "meta", None)
    if callable(meta):
        try:
            return meta()
        except Exception:
            pass
    return getattr(instance, "metadata", None)


def get_platform_id(instance: Any) -> str:
    direct_id = str(getattr(instance, "id", "") or "").strip()
    if direct_id:
        return direct_id

    meta = get_platform_meta(instance)
    platform_id = str(getattr(meta, "id", "") or "").strip()
    if platform_id:
        return platform_id

    config = getattr(instance, "config", {}) or {}
    return str(config.get("id", "") or getattr(instance, "id", "") or "").strip()


def get_platform_type(instance: Any) -> str:
    direct_name = str(getattr(instance, "name", "") or "").strip()
    if direct_name:
        return direct_name

    meta = get_platform_meta(instance)
    platform_type = str(getattr(meta, "name", "") or "").strip()
    if platform_type:
        return platform_type

    config = getattr(instance, "config", {}) or {}
    return str(config.get("type", "") or "").strip()


def is_onebot_platform(adapter_id: str) -> bool:
    adapter = str(adapter_id or "").lower()
    return adapter in ONEBOT_TYPES


def is_weixin_oc_platform(adapter_id: str) -> bool:
    return str(adapter_id or "").strip().lower() == WEIXIN_OC_TYPE


def is_weixin_oc_instance(instance: Any) -> bool:
    return any(
        is_weixin_oc_platform(name)
        for name in (get_platform_type(instance), get_platform_id(instance))
    )


def event_platform_names(event: Any) -> list[str]:
    names: list[str] = []
    get_platform_name = getattr(event, "get_platform_name", None)
    if callable(get_platform_name):
        try:
            names.append(str(get_platform_name() or ""))
        except Exception:
            pass

    for attr in ("platform", "platform_meta"):
        platform = getattr(event, attr, None)
        if platform:
            names.extend([get_platform_id(platform), get_platform_type(platform)])

    return [name for name in names if str(name or "").strip()]


def is_onebot_event(event: Any) -> bool:
    return any(is_onebot_platform(name) for name in event_platform_names(event))


def is_weixin_oc_event(event: Any) -> bool:
    return any(is_weixin_oc_platform(name) for name in event_platform_names(event))


def find_onebot_platform_instance(context: Any) -> Any:
    for instance in iter_platform_instances(context):
        platform_type = get_platform_type(instance).lower()
        platform_id = get_platform_id(instance).lower()
        if platform_type in ONEBOT_TYPES or platform_id in ONEBOT_TYPES:
            return instance
    return None


def parse_unified_origin(origin: str) -> tuple[str, str]:
    parts = str(origin or "").split(":")
    if len(parts) >= 3:
        return parts[0], ":".join(parts[2:])
    return "", ""


def get_onebot_client(context: Any, target_umo: str = "", event: Any = None, adapter_id: str = "") -> Any:
    if event and is_onebot_event(event):
        bot = getattr(event, "bot", None)
        if bot:
            return bot

        platform_client = get_platform_client(getattr(event, "platform", None))
        if platform_client:
            return platform_client

    target_s = str(target_umo or "").strip()
    umo_adapter_id, real_id = parse_unified_origin(target_s)

    for target_adapter_id in (adapter_id, umo_adapter_id):
        if not target_adapter_id:
            continue
        for instance in iter_platform_instances(context):
            if get_platform_id(instance) != target_adapter_id:
                continue
            if not is_onebot_platform(get_platform_type(instance)):
                continue
            bot = get_platform_client(instance)
            if bot:
                return bot

    probe = real_id or target_s
    if str(probe).isdigit():
        instance = find_onebot_platform_instance(context)
        bot = get_platform_client(instance)
        if bot:
            return bot

    return None


def has_bot_action(bot: Any) -> bool:
    api_caller = getattr(getattr(bot, "api", None), "call_action", None)
    bot_caller = getattr(bot, "call_action", None)
    return callable(api_caller) or callable(bot_caller)


async def call_bot_action(bot: Any, action: str, *, raise_missing: bool = False, **params) -> Any:
    api = getattr(bot, "api", None)
    caller = getattr(api, "call_action", None)
    if not callable(caller):
        caller = getattr(bot, "call_action", None)

    if not callable(caller):
        if raise_missing:
            raise AttributeError(f"平台客户端不支持动作调用: {type(bot).__name__}")
        return None

    result = caller(action, **params)
    if inspect.isawaitable(result):
        return await result
    return result
