import ast
import asyncio
import inspect
import json
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from support import Event

# isort: split

from core.runtime.tooling import (
    TOOL_PREFACE_REQUIRED_NAMES,
    TOOL_RESULT_ONLY_NAMES,
    ArgumentCheckedTool,
    guard_tool_arguments,
    install_tool_argument_checks,
    prepare_tool_argument_checks,
    tool_argument_parameters,
    tool_needs_preface,
    validate_tool_arguments,
)
from core.runtime.voicecall.toolbridge import VoiceCallToolBridge
from mcp.types import CallToolResult, ImageContent, TextContent

from astrbot.core.agent.tool import FunctionTool


class ImageTool(FunctionTool):
    def __init__(self):
        super().__init__(
            name="life_image_generate",
            description="生成生活照",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["prompt"],
            },
        )

    async def call(self, context, prompt, count=1):
        raise AssertionError("隔离测试不能调用真实服务")


class ToolContractTest(unittest.IsolatedAsyncioTestCase):
    def test_every_registered_tool_has_a_preface_policy(self):
        source = Path(__file__).resolve().parents[1] / "main.py"
        names = set()
        for node in ast.walk(ast.parse(source.read_text())):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "llm_tool"
                ):
                    names.update(
                        kw.value.value for kw in decorator.keywords if kw.arg == "name"
                    )
        self.assertTrue(names)
        self.assertEqual(names - TOOL_PREFACE_REQUIRED_NAMES, {
            "life_query", "life_memory_search", "life_style_catalog",
            "life_web_research_status", "life_voice_generate", "life_emoji_send",
        } | TOOL_RESULT_ONLY_NAMES)
        self.assertTrue(TOOL_PREFACE_REQUIRED_NAMES <= names)

    def test_actions_require_nonblank_preface_but_reads_do_not(self):
        async def handler(event, **kwargs):
            raise AssertionError("校验不能执行实际操作")

        read_actions = {
            "life_review": ("action", ["show"], "generate"),
        }
        for name in sorted(TOOL_PREFACE_REQUIRED_NAMES):
            with self.subTest(tool=name):
                tool = FunctionTool(name=name, description="执行操作", handler=handler,
                    parameters={"type": "object", "properties": {
                        "action": {"type": "string"},
                        "operation": {"type": "string"},
                    }})
                checked = ArgumentCheckedTool(tool)
                self.assertNotIn("preface", tool.parameters["properties"])
                self.assertNotIn("preface", tool_argument_parameters(
                    tool, include_preface=False)["properties"])
                args = {}
                if name in read_actions:
                    field, reads, write = read_actions[name]
                    for read in ["", *reads]:
                        self.assertIsNone(checked.argument_error({field: read}))
                        self.assertFalse(tool_needs_preface(name, {field: read}))
                    self.assertIsNone(checked.argument_error({}))
                    args[field] = write
                self.assertTrue(tool_needs_preface(name, args))
                for blank in [None, "", " \n\t"]:
                    candidate = dict(args)
                    if blank is not None:
                        candidate["preface"] = blank
                    error = json.loads(checked.argument_error(candidate))
                    self.assertFalse(error["executed"])
                self.assertIsNone(checked.argument_error(
                    {**args, "preface": "等我看看这件事。"}))

    async def test_preface_is_consumed_before_business_executor(self):
        async def handler(event, prompt=""):
            raise AssertionError("隔离测试不执行真实工具")

        tool = FunctionTool(name="life_image_generate", description="生成图片",
            handler=handler, parameters={"type": "object", "properties": {
                "prompt": {"type": "string"}
            }})
        checked = ArgumentCheckedTool(tool)
        calls = []
        module = types.ModuleType("astrbot.core.astr_agent_tool_exec")

        class Executor:
            @classmethod
            async def execute(cls, original, context, **kwargs):
                calls.append((original, kwargs))
                yield "已受理"

        module.FunctionToolExecutor = Executor
        with patch.dict(sys.modules, {module.__name__: module}):
            invalid = [value async for value in checked.call(None, prompt="自拍")]
            self.assertFalse(json.loads(invalid[0])["executed"])
            self.assertEqual(calls, [])
            valid = [value async for value in checked.call(
                None, prompt="自拍", preface="我拍给你看。")]
        self.assertEqual(valid, ["已受理"])
        self.assertEqual(calls, [(tool, {"prompt": "自拍"})])

    async def test_local_updates_execute_without_a_preface(self):
        async def handler(event, detail=""):
            raise AssertionError("No live writes in tests")

        calls = []
        module = types.ModuleType("astrbot.core.astr_agent_tool_exec")

        class Executor:
            @classmethod
            async def execute(cls, original, context, **kwargs):
                calls.append((original.name, kwargs))
                yield CallToolResult(content=[TextContent(type="text", text="saved")])

        module.FunctionToolExecutor = Executor
        with patch.dict(sys.modules, {module.__name__: module}):
            for name in sorted(TOOL_RESULT_ONLY_NAMES):
                checked = ArgumentCheckedTool(
                    FunctionTool(
                        name=name,
                        description="Update",
                        handler=handler,
                        parameters={
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                    )
                )
                self.assertNotIn("preface", checked.parameters["properties"])
                result = [item async for item in checked.call(None, detail="明天散步")]
                self.assertEqual(result[0].content[0].text, "saved")
                self.assertEqual(calls[-1], (name, {"detail": "明天散步"}))

    async def test_delivery_context_uses_only_sent_prefaces_and_preserves_tool_result(
        self,
    ):
        from componentfixtures import VoiceSwitchComponent

        class Runtime(VoiceSwitchComponent):
            send_ok = True

            @staticmethod
            def _event_session_id(event):
                return event.unified_msg_origin

            async def send_background_text(self, scope, text, **kwargs):
                if not self.send_ok:
                    return False
                await kwargs["source_event"].send(text)
                return True

        async def handler(event):
            raise AssertionError("No live tools in tests")

        original = FunctionTool(
            name="life_route_plan",
            description="Route",
            handler=handler,
            parameters={"type": "object", "properties": {}},
        )
        checked = ArgumentCheckedTool(original)
        raw = CallToolResult(
            content=[
                TextContent(type="text", text='{"status":"failed","reason":"offline"}'),
                ImageContent(type="image", data="aGVsbG8=", mimeType="image/png"),
            ],
            isError=True,
            structuredContent={"status": "failed"},
        )
        module = types.ModuleType("astrbot.core.astr_agent_tool_exec")

        class Executor:
            @classmethod
            async def execute(cls, original, context, **kwargs):
                yield raw

        module.FunctionToolExecutor = Executor
        runtime = Runtime()
        delivered_event = None
        with patch.dict(sys.modules, {module.__name__: module}):
            for send_ok in (True, False):
                runtime.send_ok = send_ok
                event = Event()
                runtime._part_silent_tool_preface_component._remember_tool_preface(
                    event, event.chain_result(["我先看看路线。"])
                )
                await runtime.handle_llm_tool_start(
                    event, checked, {"preface": "我看看怎么走。"}
                )
                context = types.SimpleNamespace(
                    context=types.SimpleNamespace(event=event),
                    messages=[
                        types.SimpleNamespace(role="system", content="角色原有设定")
                    ],
                )
                (result,) = [
                    item
                    async for item in checked.call(context, preface="我看看怎么走。")
                ]
                self.assertTrue(result.isError)
                self.assertEqual(result.structuredContent, {"status": "failed"})
                self.assertEqual(result.content[:2], raw.content)
                self.assertEqual(len(raw.content), 2)
                self.assertIs(result, raw)
                if send_ok:
                    delivered_event = event
                    content = context.messages[0].content
                    self.assertEqual(content[0].text, "角色原有设定")
                    self.assertTrue(content[-1]._no_save)
                    self.assertIn("我先看看路线。", content[-1].text)
                    self.assertNotIn("我看看怎么走。", content[-1].text)
                    self.assertIn("失败", content[-1].text)
                else:
                    self.assertEqual(context.messages[0].content, "角色原有设定")
            context = types.SimpleNamespace(
                context=types.SimpleNamespace(event=Event()), messages=[]
            )
            (result,) = [
                item async for item in checked.call(context, preface="我看看怎么走。")
            ]
            self.assertIs(result, raw)
            self.assertEqual(context.messages, [])
            context.context.event = delivered_event
            from core.runtime.reaction import ToolReactionService

            for status, outcome in (("sent", "success"), ("pending", "pending")):
                raw = CallToolResult(
                    content=[
                        TextContent(type="text", text=json.dumps({"status": status}))
                    ]
                )
                (result,) = [
                    item
                    async for item in checked.call(context, preface="我看看怎么走。")
                ]
                self.assertIs(result, raw)
                self.assertEqual(
                    ToolReactionService._tool_reaction_outcome(
                        "life_image_generate", result
                    ),
                    outcome,
                )
                self.assertEqual(len(context.messages), 1)
                self.assertEqual(context.messages[0].role, "system")
                self.assertEqual(len(context.messages[0].content), 1)
                self.assertIn("我先看看路线。", context.messages[0].content[0].text)

    def test_rejects_reason_only_without_exposing_value(self):
        original = ImageTool()
        checked = ArgumentCheckedTool(original)
        error = checked.argument_error({"reason": "SECRET正文"})
        result = json.loads(error)
        self.assertFalse(result["executed"])
        self.assertEqual(result["accepted_parameters"], ["prompt", "count"])
        self.assertIn(
            {"path": [], "rule": "required", "missing_parameters": ["prompt"]},
            result["issues"],
        )
        self.assertIn(
            {
                "path": [],
                "rule": "additionalProperties",
                "unexpected_parameters": ["reason"],
            },
            result["issues"],
        )
        self.assertNotIn("SECRET", error)
        self.assertNotIn("additionalProperties", original.parameters)

    def test_validates_types_and_nested_required_fields(self):
        parameters = {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                }
            },
        }
        for arguments, path in [
            ({"messages": [{"text": 17}]}, ["messages", 0, "text"]),
            ({"messages": [{}]}, ["messages", 0]),
        ]:
            with self.subTest(arguments=arguments):
                result = json.loads(
                    validate_tool_arguments("send", parameters, arguments)
                )
                self.assertEqual(result["issues"][0]["path"], path)
        self.assertIsNone(
            validate_tool_arguments(
                "send", parameters, {"messages": [{"text": "你好"}]}
            )
        )

    def test_preserves_extensible_external_schemas(self):
        class ExternalTool(FunctionTool):
            async def call(self, context, **kwargs):
                return kwargs

        for additional in (None, True, {"type": "string"}):
            schema = {"type": "object", "properties": {}}
            if additional is not None:
                schema["additionalProperties"] = additional
            original = ExternalTool(name="external", description="", parameters=schema)
            checked = ArgumentCheckedTool(original)
            self.assertIsNone(checked.argument_error({"dynamic": "value"}))
            self.assertEqual(checked.parameters, original.parameters)

    def test_group_media_scene_enum_rejects_invalid_value_before_execution(self):
        for name in (
            "life_image_generate",
            "life_photo_suite_generate",
            "life_video_generate",
        ):
            with self.subTest(tool=name):
                original = ImageTool()
                original.name = name
                original.parameters["properties"]["friend_scene_category"] = {
                    "type": "string"
                }
                checked = ArgumentCheckedTool(original)
                for invalid in ("street_walk", "", " "):
                    error = checked.argument_error(
                        {"prompt": "两个人在街边合影", "friend_scene_category": invalid}
                    )
                    result = json.loads(error)
                    self.assertFalse(result["executed"])
                    self.assertEqual(result["issues"][0]["path"], ["friend_scene_category"])
                    self.assertEqual(result["issues"][0]["rule"], "enum")
                    self.assertEqual(
                        result["issues"][0]["allowed_values"],
                        ["home", "sleep", "outdoor", "public", "mixed"],
                    )
                self.assertNotIn("enum", original.parameters["properties"]["friend_scene_category"])
                self.assertIsNone(checked.argument_error({"prompt": "居家随手自拍"}))
                self.assertNotIn("friend_scene_category", checked.parameters["required"])
                for category in result["issues"][0]["allowed_values"]:
                    self.assertIsNone(checked.argument_error(
                        {"prompt": "两个人在街边合影", "friend_scene_category": category}
                    ))
                voice_parameters = tool_argument_parameters(original, include_preface=False)
                self.assertEqual(
                    voice_parameters["properties"]["friend_scene_category"]["enum"],
                    ["home", "sleep", "outdoor", "public", "mixed"],
                )
                self.assertIsNone(validate_tool_arguments(
                    name, voice_parameters, {"prompt": "居家随手自拍"}
                ))
                self.assertIsNotNone(validate_tool_arguments(
                    name, voice_parameters, {"prompt": "居家随手自拍", "friend_scene_category": ""}
                ))

    async def test_invalid_call_never_reaches_executor_and_valid_retry_does(self):
        calls = []
        module = types.ModuleType("astrbot.core.astr_agent_tool_exec")

        class Executor:
            @classmethod
            async def execute(cls, tool, context, **kwargs):
                calls.append((tool, context, kwargs))
                yield "图片任务已受理"

        module.FunctionToolExecutor = Executor
        original = ImageTool()
        checked = ArgumentCheckedTool(original)
        context = object()
        with patch.dict(sys.modules, {module.__name__: module}):
            invalid = [item async for item in checked.call(context, reason="看看自拍")]
            self.assertFalse(json.loads(invalid[0])["executed"])
            self.assertEqual(calls, [])
            valid = [
                item async for item in checked.call(context, prompt="居家随手自拍")
            ]
        self.assertEqual(valid, ["图片任务已受理"])
        self.assertEqual(calls, [(original, context, {"prompt": "居家随手自拍"})])

    async def test_delegates_permissions_and_background_submission_once(self):
        module = types.ModuleType("astrbot.core.astr_agent_tool_exec")
        calls = []

        class Executor:
            @classmethod
            async def execute(cls, tool, context, **kwargs):
                calls.append(tool)
                yield (
                    "background submitted"
                    if tool.is_background_task
                    else "permission denied"
                )

        module.FunctionToolExecutor = Executor
        for background in (False, True):
            original = ImageTool()
            original.is_background_task = background
            checked = ArgumentCheckedTool(original)
            self.assertFalse(checked.is_background_task)
            with patch.dict(sys.modules, {module.__name__: module}):
                results = [item async for item in checked.call(None, prompt="场景")]
            self.assertEqual(
                results, ["background submitted" if background else "permission denied"]
            )
            self.assertIs(calls[-1], original)
        self.assertEqual(len(calls), 2)

    async def test_cancellation_and_generator_close_release_executor(self):
        module = types.ModuleType("astrbot.core.astr_agent_tool_exec")
        released = []
        ready = asyncio.Event()

        class Executor:
            @classmethod
            async def execute(cls, tool, context, **kwargs):
                try:
                    yield "first"
                    ready.set()
                    await asyncio.Future()
                finally:
                    released.append(True)

        module.FunctionToolExecutor = Executor
        checked = ArgumentCheckedTool(ImageTool())
        with patch.dict(sys.modules, {module.__name__: module}):
            iterator = checked.call(None, prompt="场景")
            self.assertEqual(await anext(iterator), "first")
            await iterator.aclose()
            self.assertEqual(released, [True])
            iterator = checked.call(None, prompt="场景")
            await anext(iterator)
            task = asyncio.create_task(anext(iterator))
            await ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(released, [True, True])

    def test_request_install_is_idempotent_and_leaves_global_tool_unchanged(self):
        class ToolSet:
            def __init__(self, tool):
                self.tools = [tool]

            def add_tool(self, tool):
                self.tools = [tool]

        original = ImageTool()
        toolset = ToolSet(original)
        self.assertEqual(install_tool_argument_checks(toolset), 1)
        self.assertEqual(install_tool_argument_checks(toolset), 0)
        self.assertIs(toolset.tools[0]._wrapped, original)
        self.assertNotIn("additionalProperties", original.parameters)

    async def test_schema_construction_does_not_block_event_loop(self):
        started = threading.Event()
        release = threading.Event()

        def busy_constructor(_toolset):
            started.set()
            if not release.wait(2):
                raise AssertionError("工具初始化占住了事件循环")
            return 1

        with patch(
            "core.runtime.tooling.install_tool_argument_checks", busy_constructor
        ):
            task = asyncio.create_task(
                prepare_tool_argument_checks(types.SimpleNamespace(tools=[ImageTool()]))
            )
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                self.assertFalse(task.done())
                # 该回调需要事件循环继续运行，不能等构造函数结束后才执行。
                progressed = asyncio.Event()
                asyncio.get_running_loop().call_soon(progressed.set)
                await asyncio.wait_for(progressed.wait(), 0.2)
            finally:
                release.set()
                self.assertEqual(await task, 1)

    async def test_direct_entry_invalid_arguments_do_not_enter_lifecycle_guard(self):
        calls = []

        class Plugin:
            @guard_tool_arguments
            async def tool_image(self, event, prompt=""):
                calls.append(prompt)
                return "queued"

        plugin = Plugin()
        signature = inspect.signature(plugin.tool_image)
        self.assertEqual(list(signature.parameters), ["event", "prompt"])
        result = json.loads(await plugin.tool_image(None, reason="不要静默生成空图"))
        self.assertFalse(result["executed"])
        self.assertEqual(calls, [])
        self.assertEqual(await plugin.tool_image(None, prompt="自拍"), "queued")
        self.assertEqual(calls, ["自拍"])

    async def test_does_not_disguise_typeerror_inside_function(self):
        class Plugin:
            @guard_tool_arguments
            async def tool_image(self, event, prompt=""):
                raise TypeError("函数体内部的真实错误")

        with self.assertRaisesRegex(TypeError, "真实错误"):
            await Plugin().tool_image(None, prompt="自拍")

    async def test_direct_generator_entry_closes_underlying_iterator(self):
        released = []

        class Plugin:
            @guard_tool_arguments
            async def tool_image(self, event, prompt=""):
                try:
                    yield prompt
                finally:
                    released.append(True)

        plugin = Plugin()
        invalid = [item async for item in plugin.tool_image(None, reason="场景")]
        self.assertFalse(json.loads(invalid[0])["executed"])
        iterator = plugin.tool_image(None, prompt="场景")
        self.assertEqual(await anext(iterator), "场景")
        await iterator.aclose()
        self.assertEqual(released, [True])

    def test_all_registered_plugin_tools_have_entry_checks(self):
        source = Path(__file__).resolve().parents[1] / "main.py"
        count = 0
        for node in ast.walk(ast.parse(source.read_text())):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "llm_tool"
                for d in node.decorator_list
            ):
                self.assertTrue(
                    any(
                        isinstance(d, ast.Name) and d.id == "guard_tool_arguments"
                        for d in node.decorator_list
                    ),
                    node.name,
                )
                count += 1
        self.assertGreater(count, 0)

    async def test_voice_invalid_arguments_neither_execute_nor_increment_count(self):
        original = ImageTool()
        runtime = types.SimpleNamespace(
            config=types.SimpleNamespace(
                realtime_voice_call=types.SimpleNamespace(allow_function_calls=True)
            )
        )
        invite = types.SimpleNamespace(tool_call_count=0)
        bridge = VoiceCallToolBridge(runtime, invite)
        bridge._find = lambda _name: original
        for arguments in ({"reason": "SECRET"}, ["不是参数对象"]):
            result = json.loads(await bridge.call(original.name, arguments))
            self.assertFalse(result["executed"])
            self.assertEqual(invite.tool_call_count, 0)

    async def test_voice_end_invalid_arguments_do_not_hang_up(self):
        calls = []
        manager = types.SimpleNamespace(request_hangup=lambda *args: calls.append(args))
        bridge = VoiceCallToolBridge(
            types.SimpleNamespace(), types.SimpleNamespace(), manager=manager
        )
        result = json.loads(
            await bridge.call(
                "life_voice_call_end", {"reason": {"unexpected": "SECRET"}}
            )
        )
        self.assertFalse(result["executed"])
        self.assertEqual(calls, [])
