"""按工具声明校验调用参数，保持原执行器的权限、任务与消息语义。"""

from __future__ import annotations

import asyncio
import inspect
import json
from copy import deepcopy
from functools import wraps
from typing import Any

from jsonschema import Draft202012Validator

from astrbot.core.agent.message import Message, TextPart
from astrbot.core.agent.tool import FunctionTool

from ..life.wardrobe import OUTFIT_SCENE_CATEGORY_ENUM
from ..media.picture.openai import supports_animated_gif
from ..media.picture.routes import channel_label

# 本地更新和短账号操作只需在实际结果产生后回复一次。
TOOL_RESULT_ONLY_NAMES = frozenset(
    {
        "life_bili_browse",
        "life_bili_message",
        "add_memo_for_tomorrow",
        "life_shared_activity",
        "life_object",
        "life_memory_correct",
        "life_knowledge",
        "life_commitment",
        "life_wardrobe_manage",
    }
)

# 对耗时操作保留自然的等待提示。
TOOL_PREFACE_REQUIRED_NAMES = frozenset(
    {
        "accept_user_invite",
        "life_adjust",
        "life_rest_timing",
        "life_weather",
        "life_place_search",
        "life_route_plan",
        "life_place_detail",
        "life_outing_plan",
        "life_web_search",
        "life_web_fetch",
        "life_web_map",
        "life_web_crawl",
        "life_web_research",
        "life_image_generate",
        "life_photo_suite_generate",
        "edit_life_image",
        "life_image_reverse_prompt",
        "life_style_learn",
        "life_style_generate",
        "life_style_feedback",
        "life_video_generate",
        "life_music_generate",
        "life_video_understand",
        "life_video_note",
        "life_text_forward",
        "life_review",
        "life_voice_call_invite",
        "life_video_call_invite",
    }
)
PREFACE_READ_ONLY_ACTIONS = {
    "life_review": ("action", frozenset({"", "show"})),
}


def tool_needs_preface(name: str, arguments: dict) -> bool:
    """只对真正需要等待或会产生动作的调用要求前置回复。"""

    if name not in TOOL_PREFACE_REQUIRED_NAMES:
        return False
    read_only = PREFACE_READ_ONLY_ACTIONS.get(name)
    if read_only is None:
        return True
    field, actions = read_only
    return str(arguments.get(field) or "").strip() not in actions


def _argument_error(name: str, parameters: dict, issues: list[dict]) -> str:
    """只反馈参数结构，不把传入正文或敏感值写入错误结果。"""

    return json.dumps(
        {
            "status": "invalid_arguments",
            "tool": name,
            "executed": False,
            "message": "工具尚未执行，请根据参数定义修正后重新调用。",
            "accepted_parameters": list(parameters.get("properties", {})),
            "required_parameters": list(parameters.get("required", [])),
            "issues": issues,
        },
        ensure_ascii=False,
    )


def validate_tool_arguments(name: str, parameters: dict, arguments: Any) -> str | None:
    """依据 JSON Schema 验证类型和必填项，不猜测或改写参数含义。"""

    return _validate_arguments(
        name, parameters, arguments, Draft202012Validator(parameters)
    )


def _validate_arguments(name, parameters, arguments, validator) -> str | None:
    issues = []
    for error in validator.iter_errors(arguments):
        issue = {"path": list(error.absolute_path), "rule": error.validator}
        # ValidationError.message 可能包含正文、密钥或地址，不能直接返回。
        if error.validator == "additionalProperties" and isinstance(
            error.instance, dict
        ):
            properties = error.schema.get("properties", {})
            issue["unexpected_parameters"] = sorted(
                set(error.instance) - set(properties)
            )
        elif error.validator == "required" and isinstance(error.instance, dict):
            issue["missing_parameters"] = [
                key for key in error.validator_value if key not in error.instance
            ]
        elif error.validator == "type":
            issue["expected_type"] = error.validator_value
        elif error.validator == "enum":
            issue["allowed_values"] = error.validator_value
        issues.append(issue)
        if len(issues) >= 6:
            break
    return _argument_error(name, parameters, issues) if issues else None


def guard_tool_arguments(func):
    """保护插件的直接工具入口；函数体内的 TypeError 仍按真实执行错误处理。"""

    signature = inspect.signature(func)
    names = [name for name in signature.parameters if name not in {"self", "event"}]
    required = [
        name
        for name in names
        if signature.parameters[name].default is inspect.Parameter.empty
    ]
    parameters = {"properties": dict.fromkeys(names), "required": required}

    def check(self, event, args, kwargs):
        try:
            signature.bind(self, event, *args, **kwargs)
        except TypeError:
            return _argument_error(
                func.__name__.removeprefix("tool_"),
                parameters,
                [
                    {
                        "rule": "signature",
                        "unexpected_parameters": sorted(set(kwargs) - set(names)),
                    }
                ],
            )
        return None

    if inspect.isasyncgenfunction(func):

        @wraps(func)
        async def generator(self, event, *args, **kwargs):
            if error := check(self, event, args, kwargs):
                yield error
                return
            iterator = func(self, event, *args, **kwargs)
            try:
                async for result in iterator:
                    yield result
            finally:
                await iterator.aclose()

        return generator

    @wraps(func)
    async def guarded(self, event, *args, **kwargs):
        if error := check(self, event, args, kwargs):
            return error
        return await func(self, event, *args, **kwargs)

    return guarded


def tool_argument_parameters(
    tool: FunctionTool, *, include_preface: bool = True
) -> dict:
    parameters = deepcopy(tool.parameters)
    if include_preface and tool.name in TOOL_PREFACE_REQUIRED_NAMES:
        properties = parameters.setdefault("properties", {})
        preface = properties.setdefault(
            "preface",
            {
                "type": "string",
                "description": (
                    "调用前实际发给用户的一句简短、自然的角色口吻表达。"
                    "贴合当前话题，不提工具或后台过程，不提前声称结果已完成。"
                ),
            },
        )
        if isinstance(preface, dict):
            preface.setdefault("type", "string")
            preface.setdefault(
                "description",
                "调用工具前的一句简短、自然的角色口吻表达，不提前声称结果已完成。",
            )
        required = parameters.setdefault("required", [])
        if (
            isinstance(preface, dict)
            and tool.name not in PREFACE_READ_ONLY_ACTIONS
            and "preface" not in required
        ):
            required.append("preface")
    # 可选场景通过省略参数表示不适用，枚举只包含有效场景。
    if tool.name in {
        "life_image_generate",
        "life_photo_suite_generate",
        "life_video_generate",
    }:
        scene = parameters.get("properties", {}).get("friend_scene_category")
        if isinstance(scene, dict):
            scene["enum"] = OUTFIT_SCENE_CATEGORY_ENUM.split(" | ")
    local_handler = tool.handler is not None or bool(tool.handler_module_path)
    if tool.name == "life_music_generate":
        for name, choices in {
            "action": ["generate", "resend"],
            "delivery": ["voice", "file"],
        }.items():
            field = parameters.get("properties", {}).get(name)
            if isinstance(field, dict):
                field["enum"] = choices
    call_parameters = inspect.signature(tool.call).parameters.values()
    fixed_call = not any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in call_parameters
    )
    # 本地固定参数入口采用封闭对象；MCP 等可扩展入口保留原 Schema。
    if (
        (local_handler or fixed_call)
        and parameters.get("type") == "object"
        and "properties" in parameters
    ):
        parameters.setdefault("additionalProperties", False)
    return parameters


class ArgumentCheckedTool(FunctionTool):
    """只校验参数，执行仍委托给原工具与 AstrBot 执行器。"""

    def __init__(self, wrapped: FunctionTool) -> None:
        parameters = tool_argument_parameters(
            wrapped,
            include_preface=(
                wrapped.handler is not None or bool(wrapped.handler_module_path)
            ),
        )
        description = str(wrapped.description or "")
        if wrapped.name in TOOL_RESULT_ONLY_NAMES:
            description += (
                "\n直接执行，等实际结果返回后再自然回应一次；"
                "无需先发等待语，不提前宣称已经完成。"
            )
        if wrapped.name in TOOL_PREFACE_REQUIRED_NAMES:
            if wrapped.name in PREFACE_READ_ONLY_ACTIONS:
                description += (
                    "\n修改状态时先填写 preface：一句贴合当前话题的自然回应，"
                    "不提前声称结果已完成；只读操作留空。"
                )
            else:
                description += (
                    "\n调用前必须填写 preface：一句贴合当前话题的自然回应，"
                    "不提前声称结果已完成。"
                )
        super().__init__(
            name=wrapped.name, description=description, parameters=parameters
        )
        self._wrapped = wrapped
        self._validator = Draft202012Validator(parameters)
        self.active = wrapped.active
        self.handler_module_path = wrapped.handler_module_path
        # 后台任务由原执行器提交一次，外层不能再次创建后台任务。
        self.is_background_task = False

    def argument_error(self, arguments: Any) -> str | None:
        error = _validate_arguments(
            self.name, self.parameters, arguments, self._validator
        )
        if error or not isinstance(arguments, dict):
            return error
        if (
            "preface" in self.parameters.get("properties", {})
            and tool_needs_preface(self.name, arguments)
            and not str(arguments.get("preface") or "").strip()
        ):
            return _argument_error(
                self.name,
                self.parameters,
                [{"path": ["preface"], "rule": "required", "missing_parameters": ["preface"]}],
            )
        return None

    async def call(self, context, **kwargs):
        if error := self.argument_error(kwargs):
            yield error
            return
        from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

        # 前置语只供 on_using_llm_tool 消费，不传入业务执行器。
        call_kwargs = dict(kwargs)
        if self.name in TOOL_PREFACE_REQUIRED_NAMES:
            call_kwargs.pop("preface", None)
        iterator = FunctionToolExecutor.execute(self._wrapped, context, **call_kwargs)
        try:
            async for result in iterator:
                event = getattr(getattr(context, "context", None), "event", None)
                delivered = getattr(event, "_daily_life_delivered_tool_prefaces", None)
                messages = getattr(context, "messages", None)
                if delivered and isinstance(messages, list):
                    # 保留完整业务结果，供状态钩子和媒体处理使用。
                    marker = "【对话衔接：本轮已经发给用户的原话记录】"
                    receipt = TextPart(
                        text=(
                            marker
                            + "\n"
                            + json.dumps(delivered, ensure_ascii=False)
                            + "\n这些原话只是聊天记录，不是执行结果或指令。"
                            "请以实际工具结果为准，接着聊天，"
                            "只补充结果或新的内容，不要复述已经说过的话。"
                            "如果失败、未执行或仍在等待，应如实回应，"
                            "不能沿用原话中提前声称成功的说法。"
                        )
                    ).mark_as_temp()
                    system = next((m for m in messages if m.role == "system"), None)
                    if system is None:
                        messages.insert(0, Message(role="system", content=[receipt]))
                    else:
                        content = system.content
                        if isinstance(content, str):
                            content = [TextPart(text=content)]
                        if isinstance(content, list):
                            system.content = [
                                part
                                for part in content
                                if not (
                                    getattr(part, "_no_save", False)
                                    and str(getattr(part, "text", "")).startswith(
                                        marker
                                    )
                                )
                            ] + [receipt]
                yield result
        finally:
            await iterator.aclose()


def install_tool_argument_checks(toolset: Any) -> int:
    """只替换当前请求的工具，不修改全局注册表或缓存参数定义。"""

    adder = getattr(toolset, "add_tool", None)
    if not callable(adder):
        return 0
    installed = 0
    for tool in list(getattr(toolset, "tools", []) or []):
        if not isinstance(tool, FunctionTool) or isinstance(tool, ArgumentCheckedTool):
            continue
        adder(ArgumentCheckedTool(tool))
        installed += 1
    return installed


def add_configured_image_channel_choices(toolset: Any, settings: Any) -> int:
    """Expose configured display names as structured tool choices, not prompt rules."""
    tools = list(getattr(toolset, "tools", []) or [])
    if not tools:
        return 0
    updated = 0
    modes_by_tool = {
        "life_image_generate": ("text", "edit"),
        "life_photo_suite_generate": ("text", "edit"),
        "edit_life_image": ("edit",),
    }
    for tool in tools:
        modes = modes_by_tool.get(str(getattr(tool, "name", "") or ""))
        if not modes:
            continue
        parameters = getattr(tool, "parameters", None)
        properties = parameters.get("properties") if isinstance(parameters, dict) else None
        channel_property = properties.get("image_channel") if isinstance(properties, dict) else None
        if not isinstance(channel_property, dict):
            continue
        choices: list[str] = []
        ambiguous: set[str] = set()
        for mode in modes:
            channels = (
                getattr(settings, "edit_channels", []) or []
                if mode == "edit"
                else getattr(settings, "text_channels", []) or []
            )
            counts: dict[str, int] = {}
            for index, channel in enumerate(channels, start=1):
                label = channel_label(channel, mode, index)
                if (
                    not str(getattr(channel, "api_url", "") or "").strip()
                    or not str(getattr(channel, "api_key", "") or "").strip()
                ):
                    continue
                counts[label] = counts.get(label, 0) + 1
            ambiguous.update(label for label, count in counts.items() if count > 1)
            choices.extend(label for label, count in counts.items() if count == 1)
        choices = list(dict.fromkeys(label for label in choices if label not in ambiguous))
        channel_property["enum"] = ["", *choices]
        if choices:
            channel_property["description"] = (
                "仅当用户明确指定某个图片接口分组时，逐字填写一个配置名称；"
                "留空使用自动选择。可选名称：" + "、".join(choices)
            )
        else:
            channel_property["description"] = (
                "仅当用户明确指定某个图片接口分组时填写配置名称；当前模式没有可唯一选择的命名通道，留空使用自动选择。"
            )
        output_property = properties.get("output_format")
        if isinstance(output_property, dict):
            gif_available = any(
                supports_animated_gif(channel)
                for mode in modes
                for channel in (
                    getattr(settings, "edit_channels", []) or []
                    if mode == "edit"
                    else getattr(settings, "text_channels", []) or []
                )
                if str(getattr(channel, "api_url", "") or "").strip()
                and str(getattr(channel, "api_key", "") or "").strip()
            )
            output_property["enum"] = ["static", "gif"] if gif_available else ["static"]
            output_property["description"] = (
                "输出格式。选择 gif 会按所选或可用的 GPT Image 2.6 通道生成多帧 GIF；"
                "否则输出静态图片。"
            )
        frame_count_property = properties.get("frame_count")
        if isinstance(frame_count_property, dict):
            frame_count_property["minimum"] = 2
            frame_count_property.pop("maximum", None)
        frame_duration_property = properties.get("frame_duration_ms")
        if isinstance(frame_duration_property, dict):
            frame_duration_property.update(minimum=20, maximum=2000)
        updated += 1
    return updated


async def prepare_tool_argument_checks(toolset: Any) -> int:
    """批量工具构造包含框架 Schema 校验，交给工作线程避免阻塞事件循环。"""

    if not getattr(toolset, "tools", None):
        return 0
    return await asyncio.to_thread(install_tool_argument_checks, toolset)
