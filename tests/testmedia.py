# ruff: noqa: I001

import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from support import LifeSettings

from core.media import GeminiImageService
from core.media import video as video_module
from core.media.base import videos_endpoint
from core.media.picture import canvas as picture_canvas
from core.media.picture import openai as openai_image
from core.media.video import GrokVideoService
from core.media.video.protocol.size import video_aspect_ratio, video_size
from core.media.video.reference import (
    VIDEO_REFERENCE_MAX_BYTES,
    prepare_video_reference_image,
)
from core.media.video.tasks import task_status_url
from core.runtime.proactive.send import ProactiveSendMixin
from PIL import Image


def _timeout_total(value):
    total = getattr(value, "total", None)
    if total is not None:
        return total
    kwargs = getattr(value, "kwargs", None)
    if isinstance(kwargs, dict):
        return kwargs.get("total")
    return None


def _png_bytes(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + (0).to_bytes(4, "big")
    )


def _real_png_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (72, 138, 112)).save(output, format="PNG")
    return output.getvalue()


def _real_bmp_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (72, 138, 112)).save(output, format="BMP")
    return output.getvalue()


def _form_field(form, name: str):
    fields = getattr(form, "fields", None) or getattr(form, "_fields", None) or []
    for field in fields:
        if isinstance(field, tuple) and field:
            first = field[0]
            if isinstance(first, str) and first == name:
                return field[1] if len(field) > 1 else None
            field_name = first.get("name") if hasattr(first, "get") else None
            if field_name == name:
                return field[2] if len(field) > 2 else None
        elif hasattr(field, "name") and getattr(field, "name") == name:
            return getattr(field, "value", None)
    return None


class _Response:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self.payload = payload if payload is not None else {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text

    async def json(self, *args, **kwargs):
        return self.payload


class _Session:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def request(self, method, url, headers=None, json=None, data=None, timeout=None):
        self.calls.append((method, url, headers or {}, json, data, timeout))
        if method == "POST" and url.endswith("/v1/videos"):
            return _Response(payload={"task_id": "task-1"})
        if method == "GET" and url.endswith("/v1/videos/task-1"):
            return _Response(
                payload={
                    "status": "completed",
                    "video_url": "https://cdn.example/video.mp4",
                }
            )
        return _Response(500, text="unexpected")


class GeminiImageServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_generate_image_filters_channels_by_explicit_model(self):
        output_bytes = b"\x89PNG\r\n\x1a\noutput"
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, json or {}))
                return _Response(
                    payload={
                        "data": [
                            {"b64_json": base64.b64encode(output_bytes).decode("ascii")}
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://first.example/v1",
                            "api_key": "first-key",
                            "model": "gpt-image-main",
                        },
                        {
                            "__template_key": "openai",
                            "api_url": "https://selected.example/v1",
                            "api_key": "selected-key",
                            "model": "gpt-image-selected",
                        },
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        async def get_session():
            return _ImageSession()

        service._get_session = get_session

        await service.generate_image("雨夜生活照", model="gpt-image-selected")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://selected.example/v1/images/generations")
        self.assertEqual(calls[0][1]["model"], "gpt-image-selected")
        with self.assertRaisesRegex(
            RuntimeError,
            "指定的生图模型 missing-model 没有可用的文生图接口通道",
        ):
            await service.generate_image("雨夜生活照", model="missing-model")

    async def test_generate_image_filters_channels_by_explicit_protocol(self):
        output_bytes = b"\x89PNG\r\n\x1a\noutput"
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append(url)
                return _Response(
                    payload={
                        "data": [
                            {"b64_json": base64.b64encode(output_bytes).decode("ascii")}
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://gemini.example",
                            "api_key": "gemini-key",
                            "model": "gemini-image",
                        },
                        {
                            "__template_key": "openai",
                            "group_name": "OpenAI 备用线路",
                            "api_url": "https://openai.example/v1",
                            "api_key": "openai-key",
                            "model": "gpt-image-2",
                        },
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        async def get_session():
            return _ImageSession()

        service._get_session = get_session

        with patch.object(picture_canvas.logger, "debug") as debug_log:
            await service.generate_image("雨夜生活照", protocol="openai")

        self.assertEqual(calls, ["https://openai.example/v1/images/generations"])
        request_routes = await service._request_routes("text", protocol="openai")
        self.assertEqual(request_routes[0].label, "OpenAI 备用线路")
        logs = "\n".join(str(call.args[0]) for call in debug_log.call_args_list)
        self.assertIn("通道=https://openai.example / OpenAI 备用线路", logs)

    async def test_generate_image_tries_next_channel_after_first_failure(self):
        output_bytes = b"\x89PNG\r\n\x1a\noutput"
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, headers=None, timeout=None):
                calls.append((url, headers or {}, timeout))
                if url.startswith("https://bad.example/"):
                    return _Response(500, text="relay down")
                return _Response(
                    payload={
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "inlineData": {
                                                "mimeType": "image/png",
                                                "data": base64.b64encode(
                                                    output_bytes
                                                ).decode("ascii"),
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://bad.example",
                            "api_key": "main-key",
                            "model": "gemini-3-pro-image-preview",
                        },
                        {
                            "__template_key": "gemini",
                            "api_url": "https://good.example",
                            "api_key": "backup-key",
                            "model": "gemini-relay-image",
                        },
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        generated = await service.generate_image("雨夜生活照")

        self.assertTrue(generated.path.exists())
        self.assertEqual(
            [call[0] for call in calls],
            [
                "https://bad.example/v1beta/models/gemini-3-pro-image-preview:generateContent",
                "https://good.example/v1beta/models/gemini-relay-image:generateContent",
            ],
        )
        self.assertEqual(
            [call[1]["x-goog-api-key"] for call in calls], ["main-key", "backup-key"]
        )

    async def test_generate_image_policy_violation_does_not_try_backup_channel(self):
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, headers=None, timeout=None):
                calls.append(url)
                return _Response(
                    400,
                    text='{"error":{"code":"content_policy_violation","message":"blocked"}}',
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://main.example",
                            "api_key": "main-key",
                            "model": "gemini-main",
                        },
                        {
                            "__template_key": "gemini",
                            "api_url": "https://backup.example",
                            "api_key": "backup-key",
                            "model": "gemini-backup",
                        },
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        with self.assertRaisesRegex(RuntimeError, "安全拒绝"):
            await service.generate_image("rainy life photo")

        self.assertEqual(
            calls, ["https://main.example/v1beta/models/gemini-main:generateContent"]
        )

    async def test_generate_image_can_use_single_channel(self):
        output_bytes = b"\x89PNG\r\n\x1a\noutput"
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, headers=None, timeout=None):
                calls.append((url, headers or {}, timeout))
                return _Response(
                    payload={
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "inlineData": {
                                                "mimeType": "image/png",
                                                "data": base64.b64encode(
                                                    output_bytes
                                                ).decode("ascii"),
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay.example",
                            "api_key": "relay-key",
                            "model": "gemini-relay-only",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        generated = await service.generate_image("雨夜生活照")

        self.assertTrue(generated.path.exists())
        self.assertTrue(generated.path.name.startswith("gemini_"))
        self.assertEqual(
            calls[0][0],
            "https://relay.example/v1beta/models/gemini-relay-only:generateContent",
        )
        self.assertEqual(calls[0][1]["x-goog-api-key"], "relay-key")

    async def test_generate_image_supports_openai_images_channel(self):
        output_bytes = b"\x89PNG\r\n\x1a\nopenai-output"
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, headers or {}, json, data, timeout))
                return _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(output_bytes).decode(
                                    "ascii"
                                ),
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://openai-relay.example/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2",
                            "resolution": "2K",
                            "aspect_ratio": "16:9",
                            "timeout_seconds": 180,
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        generated = await service.generate_image("雨夜生活照")

        self.assertTrue(generated.path.exists())
        self.assertTrue(generated.path.name.startswith("openai_"))
        self.assertEqual(
            calls[0][0], "https://openai-relay.example/v1/images/generations"
        )
        self.assertEqual(calls[0][1]["Authorization"], "Bearer relay-key")
        self.assertEqual(calls[0][2]["model"], "gpt-image-2")
        self.assertEqual(calls[0][2]["size"], "2048x1152")
        self.assertIn("雨夜生活照", calls[0][2]["prompt"])
        self.assertIsNone(calls[0][3])
        self.assertEqual(_timeout_total(calls[0][4]), 180)

    async def test_openai_text_to_image_ignores_character_reference_images(self):
        output_bytes = b"\x89PNG\r\n\x1a\nopenai-output"
        temp_dir = Path(tempfile.mkdtemp())
        character = temp_dir / "character.png"
        character.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, headers or {}, json, data, timeout))
                return _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(output_bytes).decode(
                                    "ascii"
                                ),
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://openai-relay.example/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2",
                        }
                    ],
                    "character_reference_images": [
                        {"path": str(character), "name": "角色参考.png"}
                    ],
                    "character_reference_policy": "always",
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        original_prompt = "雨夜生活照"
        await service.generate_image(
            original_prompt,
            identity_profile="整体纤细匀称，体态自然舒展",
        )

        self.assertEqual(
            calls[0][0], "https://openai-relay.example/v1/images/generations"
        )
        self.assertIsNotNone(calls[0][2])
        self.assertIsNone(calls[0][3])
        self.assertNotIn("已提供一组角色形象参考图", calls[0][2]["prompt"])
        self.assertIn("人物稳定体貌：整体纤细匀称", calls[0][2]["prompt"])
        self.assertIn(
            "稳定体貌以角色人设和身份参考资料为准",
            calls[0][2]["prompt"],
        )
        self.assertIn("剪裁、材质、支撑、张力和重力", calls[0][2]["prompt"])
        self.assertNotIn("用户本轮明确指定的体貌变化优先", calls[0][2]["prompt"])
        self.assertIn(f"画面要求：{original_prompt}", calls[0][2]["prompt"])

    async def test_character_reference_route_helpers_respect_policy(self):
        temp_dir = Path(tempfile.mkdtemp())
        first = temp_dir / "first.png"
        second = temp_dir / "second.png"
        first.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
        second.write_bytes(b"\x89PNG\r\n\x1a\nsecond")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "edit_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://openai-relay.example/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2",
                        }
                    ],
                    "character_reference_images": [
                        {"path": str(first), "name": "正面参考.png"},
                        {"path": str(second), "name": "侧面参考.png"},
                    ],
                    "character_reference_policy": "auto",
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        self.assertTrue(service.can_edit_image())
        self.assertEqual(service.first_character_reference_image(), str(first))

        settings.character_reference_policy = "off"
        self.assertEqual(service.first_character_reference_image(), "")

    async def test_group_image_keeps_character_and_friend_references_separate(self):
        output_bytes = b"\x89PNG\r\n\x1a\noutput"
        payloads = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, headers=None, timeout=None):
                payloads.append(json)
                return _Response(
                    payload={
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "inlineData": {
                                                "mimeType": "image/png",
                                                "data": base64.b64encode(
                                                    output_bytes
                                                ).decode("ascii"),
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )

        temp_dir = Path(tempfile.mkdtemp())
        scene = temp_dir / "scene.png"
        character = temp_dir / "character.png"
        friend = temp_dir / "friend.png"
        scene.write_bytes(b"\x89PNG\r\n\x1a\nscene")
        character.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
        friend.write_bytes(b"\x89PNG\r\n\x1a\nfriend")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "edit_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay.example",
                            "api_key": "relay-key",
                            "model": "gemini-image",
                        }
                    ],
                    "character_reference_policy": "auto",
                    "character_reference_images": [{"path": str(character)}],
                    "friend_reference_profiles": [
                        {
                            "profile_id": "profile:friend",
                            "display_name": "示例好友",
                            "reference_images": [{"path": str(friend)}],
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, temp_dir)

        async def get_session():
            return _ImageSession()

        service._get_session = get_session

        await service.generate_group_image(
            "当前角色在左，示例好友在右，一起在书店自拍",
            ["profile:friend"],
            scene_reference=str(scene),
            identity_profiles={
                "current_character": "整体纤细匀称，上半身曲线自然丰满",
            },
        )

        parts = payloads[-1]["contents"][0]["parts"]
        self.assertIn("不得串脸、融合、增删或交换人物", parts[0]["text"])
        self.assertIn("个体属性必须分别绑定到人物 A 或人物 B", parts[0]["text"])
        self.assertIn("默认只应用于人物 A", parts[0]["text"])
        self.assertIn("符合当前场景的独立穿搭", parts[0]["text"])
        self.assertIn("不根据姓名或昵称猜测性别", parts[0]["text"])
        self.assertIn("不得把一个人的属性复制给另一个人", parts[0]["text"])
        self.assertIn("明确要求同款、情侣装或统一造型", parts[0]["text"])
        self.assertIn("人物 A 稳定体貌：整体纤细匀称", parts[0]["text"])
        self.assertNotIn("人物 B 稳定体貌", parts[0]["text"])
        self.assertIn(
            "不得因通用审美压平、夸张、扩大、缩小或重塑身体结构", parts[0]["text"]
        )
        self.assertIn("仅作为场景、构图或姿态参考", parts[1]["text"])
        self.assertEqual(
            base64.b64decode(parts[2]["inlineData"]["data"]),
            scene.read_bytes(),
        )
        self.assertIn("人物 A：当前角色", parts[3]["text"])
        self.assertEqual(
            base64.b64decode(parts[4]["inlineData"]["data"]),
            character.read_bytes(),
        )
        self.assertIn("人物 B：好友 示例好友", parts[5]["text"])
        self.assertEqual(
            base64.b64decode(parts[6]["inlineData"]["data"]), friend.read_bytes()
        )

    async def test_generate_image_can_override_gemini_options_per_request(self):
        output_bytes = b"\x89PNG\r\n\x1a\noutput"
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, headers=None, timeout=None):
                calls.append((url, headers or {}, json, timeout))
                return _Response(
                    payload={
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "inlineData": {
                                                "mimeType": "image/png",
                                                "data": base64.b64encode(
                                                    output_bytes
                                                ).decode("ascii"),
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay.example",
                            "api_key": "relay-key",
                            "model": "gemini-relay-only",
                            "resolution": "2K",
                            "aspect_ratio": "1:1",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        await service.generate_image("雨夜生活照", aspect_ratio="9:16", resolution="4k")

        image_config = calls[0][2]["generationConfig"]["imageConfig"]
        response_image_config = calls[0][2]["generationConfig"]["responseFormat"][
            "image"
        ]
        self.assertEqual(image_config["aspectRatio"], "9:16")
        self.assertEqual(image_config["imageSize"], "4K")
        self.assertEqual(response_image_config["aspectRatio"], "9:16")
        self.assertEqual(response_image_config["imageSize"], "4K")
        self.assertIn("9:16 比例图片", calls[0][2]["contents"][0]["parts"][0]["text"])
        self.assertIn("4K 分辨率", calls[0][2]["contents"][0]["parts"][0]["text"])

    async def test_generate_image_can_override_openai_size_per_request(self):
        output_bytes = _png_bytes(1536, 1024)
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, headers or {}, json, data, timeout))
                return _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(output_bytes).decode(
                                    "ascii"
                                ),
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://openai-relay.example/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2",
                            "resolution": "2K",
                            "aspect_ratio": "1:1",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        with (
            patch.object(picture_canvas.logger, "debug") as debug_log,
            patch.object(picture_canvas.logger, "warning") as warning_log,
        ):
            await service.generate_image(
                "雨夜生活照", aspect_ratio="3:2", resolution="4K"
            )

        self.assertEqual(calls[0][2]["size"], "3504x2336")
        self.assertIn("3:2 比例图片", calls[0][2]["prompt"])
        self.assertTrue(
            any(
                "来源=本轮指定" in str(call.args[0])
                and "请求尺寸=3504×2336" in str(call.args[0])
                for call in debug_log.call_args_list
            )
        )
        self.assertTrue(
            any(
                "请求=3504×2336；实际=1536×1024" in str(call.args[0])
                for call in warning_log.call_args_list
            )
        )

    def test_openai_size_mapping_honors_every_resolution_tier(self):
        self.assertEqual(openai_image.size_for("1K", "1:1"), "1024x1024")
        self.assertEqual(openai_image.size_for("1K", "16:9"), "1024x576")
        self.assertEqual(openai_image.size_for("1K", "9:16"), "576x1024")
        self.assertEqual(openai_image.size_for("2K", "1:1"), "2048x2048")
        self.assertEqual(openai_image.size_for("2K", "16:9"), "2048x1152")
        self.assertEqual(openai_image.size_for("2K", "9:16"), "1152x2048")
        self.assertEqual(openai_image.size_for("4K", "1:1"), "2880x2880")
        self.assertEqual(openai_image.size_for("4K", "3:2"), "3504x2336")
        self.assertEqual(openai_image.size_for("4K", "4:3"), "3296x2472")
        self.assertEqual(openai_image.size_for("4K", "5:4"), "3200x2560")
        self.assertEqual(openai_image.size_for("4K", "2:3"), "2336x3504")
        self.assertEqual(openai_image.size_for("4K", "3:4"), "2472x3296")
        self.assertEqual(openai_image.size_for("4K", "4:5"), "2560x3200")
        self.assertEqual(openai_image.size_for("4K", "16:9"), "3840x2160")
        self.assertEqual(openai_image.size_for("4K", "9:16"), "2160x3840")
        with self.assertRaisesRegex(ValueError, "只能是 1K、2K 或 4K"):
            openai_image.size_for("", "1:1")
        with self.assertRaisesRegex(ValueError, "只能是 1K、2K 或 4K"):
            openai_image.size_for("8K", "1:1")

    def test_openai_sizes_never_exceed_upstream_pixel_budget(self):
        ratios = (
            "1:1",
            "1:4",
            "1:8",
            "2:3",
            "3:2",
            "3:4",
            "4:1",
            "4:3",
            "4:5",
            "5:4",
            "8:1",
            "9:16",
            "16:9",
            "21:9",
        )
        for resolution in ("1K", "2K", "4K"):
            for ratio in ratios:
                with self.subTest(resolution=resolution, ratio=ratio):
                    width, height = (
                        int(value)
                        for value in openai_image.size_for(resolution, ratio).split("x")
                    )
                    self.assertLessEqual(width * height, 3840 * 2160)
                    if resolution == "1K":
                        self.assertLessEqual(max(width, height), 1024)
                    elif resolution == "2K":
                        self.assertLessEqual(max(width, height), 2048)
                    else:
                        self.assertGreater(max(width, height), 2048)

    async def test_generate_image_rejects_invalid_requested_resolution(self):
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay.example",
                            "api_key": "relay-key",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        with self.assertRaisesRegex(ValueError, "只能是 1K、2K 或 4K"):
            await service.generate_image("雨夜生活照", resolution="8K")

    async def test_generate_image_rejects_invalid_channel_resolution(self):
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://relay.example",
                            "api_key": "relay-key",
                        }
                    ],
                }
            }
        ).image_generation
        settings.text_channels[0].resolution = ""
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        with self.assertRaisesRegex(ValueError, "图片通道分辨率只能是"):
            await service.generate_image("雨夜生活照")

    async def test_edit_image_supports_openai_images_channel(self):
        output_bytes = b"\x89PNG\r\n\x1a\nopenai-edit"
        reference = Path(tempfile.mkdtemp()) / "reference.png"
        reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, headers or {}, json, data, timeout))
                return _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(output_bytes).decode(
                                    "ascii"
                                ),
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "edit_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://openai-relay.example/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        generated = await service.edit_image("换成雨夜窗边", str(reference))

        self.assertTrue(generated.path.exists())
        self.assertTrue(generated.path.name.startswith("openai_"))
        self.assertEqual(calls[0][0], "https://openai-relay.example/v1/images/edits")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer relay-key")
        self.assertIsNone(calls[0][2])
        self.assertIsNotNone(calls[0][3])

    async def test_edit_image_does_not_duplicate_character_identity_anchor(self):
        output_bytes = b"\x89PNG\r\n\x1a\nopenai-edit"
        temp_dir = Path(tempfile.mkdtemp())
        first = temp_dir / "first.png"
        second = temp_dir / "second.png"
        first.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
        second.write_bytes(b"\x89PNG\r\n\x1a\nsecond")
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, data))
                return _Response(
                    payload={
                        "data": [
                            {"b64_json": base64.b64encode(output_bytes).decode("ascii")}
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "edit_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://openai-relay.example/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2",
                        }
                    ],
                    "character_reference_policy": "always",
                    "character_reference_images": [
                        {"path": str(first), "name": "正面参考.png"},
                        {"path": str(second), "name": "侧面参考.png"},
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, temp_dir)

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        await service.edit_image("窗边半身生活照", str(first))

        form = calls[0][1]
        image_fields = [field for field in form.fields if field[0] == "image"]
        self.assertEqual(len(image_fields), 2)
        self.assertIn("当前角色身份图", _form_field(form, "prompt"))

    async def test_edit_image_uses_reference_image_aspect_ratio_before_config(self):
        output_bytes = b"\x89PNG\r\n\x1a\nopenai-edit"
        temp_dir = Path(tempfile.mkdtemp())
        reference = temp_dir / "reference.png"
        reference.write_bytes(_png_bytes(9, 16))
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, headers or {}, json, data, timeout))
                return _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(output_bytes).decode(
                                    "ascii"
                                ),
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "edit_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://openai-relay.example/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2",
                            "resolution": "4K",
                            "aspect_ratio": "1:1",
                            "timeout_seconds": 180,
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, temp_dir)
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        generated = await service.edit_image(
            "换成雨夜窗边", str(reference), aspect_ratio="16:9"
        )

        self.assertTrue(generated.path.exists())
        self.assertEqual(calls[0][0], "https://openai-relay.example/v1/images/edits")
        self.assertEqual(_form_field(calls[0][3], "size"), "2160x3840")
        prompt = _form_field(calls[0][3], "prompt")
        self.assertIn("9:16 比例新图片", prompt)
        self.assertNotIn("1:1 比例", prompt)
        self.assertIn("换成雨夜窗边", prompt)

    async def test_edit_image_can_use_requested_aspect_ratio_instead_of_reference(self):
        output_bytes = b"\x89PNG\r\n\x1a\nopenai-edit"
        temp_dir = Path(tempfile.mkdtemp())
        reference = temp_dir / "reference.png"
        reference.write_bytes(_png_bytes(9, 16))
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, headers or {}, json, data, timeout))
                return _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(output_bytes).decode(
                                    "ascii"
                                ),
                            }
                        ]
                    }
                )

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "edit_channels": [
                        {
                            "__template_key": "openai",
                            "api_url": "https://openai-relay.example/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2",
                            "resolution": "2K",
                            "aspect_ratio": "1:1",
                            "timeout_seconds": 180,
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, temp_dir)
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        generated = await service.edit_image(
            "换成雨夜窗边",
            str(reference),
            aspect_ratio="16:9",
            resolution="4K",
            preserve_reference_ratio=False,
        )

        self.assertTrue(generated.path.exists())
        self.assertEqual(_form_field(calls[0][3], "size"), "3840x2160")
        prompt = _form_field(calls[0][3], "prompt")
        self.assertIn("16:9 比例新图片", prompt)
        self.assertNotIn("9:16 比例", prompt)

    async def test_generate_image_does_not_use_edit_channels(self):
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "edit_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://edit-only.example",
                            "api_key": "edit-key",
                            "model": "gemini-edit-only",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        with self.assertRaisesRegex(RuntimeError, "文生图接口通道"):
            await service.generate_image("雨夜生活照")

    async def test_edit_image_does_not_use_text_channels(self):
        reference = Path(tempfile.mkdtemp()) / "reference.png"
        reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://text-only.example",
                            "api_key": "text-key",
                            "model": "gemini-text-only",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        with self.assertRaisesRegex(RuntimeError, "图生图接口通道"):
            await service.edit_image("换成雨夜窗边", str(reference))


class GrokVideoServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_generate_video_uses_video_task_endpoint(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_keys": ["key-a"],
                    "duration": 8,
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        original_session = video_module.aiohttp.ClientSession
        video_module.asyncio.sleep = fake_sleep
        video_module.aiohttp.ClientSession = lambda *args, **kwargs: _Session(calls)
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))
        self.addCleanup(
            lambda: setattr(video_module.aiohttp, "ClientSession", original_session)
        )

        result = await service.generate_video("雨夜街边短视频")

        self.assertEqual(result.url, "https://cdn.example/video.mp4")
        self.assertEqual(calls[0][1], "https://relay.example/v1/videos")
        self.assertEqual(calls[1][1], "https://relay.example/v1/videos/task-1")
        self.assertEqual(_timeout_total(calls[0][5]), 300)
        self.assertEqual(_timeout_total(calls[1][5]), 60)

    async def test_generate_video_can_override_duration_per_request(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_keys": ["key-a"],
                    "duration": 8,
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        original_session = video_module.aiohttp.ClientSession
        video_module.asyncio.sleep = fake_sleep
        video_module.aiohttp.ClientSession = lambda *args, **kwargs: _Session(calls)
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))
        self.addCleanup(
            lambda: setattr(video_module.aiohttp, "ClientSession", original_session)
        )

        await service.generate_video("雨夜街边短视频", duration=5)

        self.assertEqual(calls[0][3]["seconds"], "5")

    async def test_missing_video_base_url_fails_before_request(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "",
                    "api_keys": ["key-a"],
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))

        with self.assertRaisesRegex(RuntimeError, "Grok 视频生成缺少中转接口地址"):
            await service.generate_video("雨夜街边短视频")

    async def test_video_task_timeout_keeps_original_error(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_keys": ["key-a"],
                    "timeout_seconds": 1,
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        class _TaskSession(_Session):
            def request(
                self, method, url, headers=None, json=None, data=None, timeout=None
            ):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "POST" and url.endswith("/v1/videos"):
                    return _Response(payload={"task_id": "task-timeout"})
                return _Response(500, text="unexpected")

        original_session = video_module.aiohttp.ClientSession
        original_poll = service._poll_video_url

        async def fail_poll(session, headers, endpoint, request_id):
            raise video_module.VideoTaskError(f"Grok 视频任务超时：{request_id}")

        service._poll_video_url = fail_poll
        video_module.aiohttp.ClientSession = lambda *args, **kwargs: _TaskSession(calls)
        self.addCleanup(lambda: setattr(service, "_poll_video_url", original_poll))
        self.addCleanup(
            lambda: setattr(video_module.aiohttp, "ClientSession", original_session)
        )

        with self.assertRaisesRegex(RuntimeError, r"^Grok 视频任务超时：task-timeout$"):
            await service.generate_video("雨夜街边短视频")

    async def test_video_uses_xai_compatible_json_payload(self):
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "text_channels": [
                        {
                            "__template_key": "gemini",
                            "api_url": "https://image.example",
                            "api_key": "image-key",
                            "aspect_ratio": "9:16",
                        }
                    ],
                },
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_keys": ["key-a"],
                    "resolution": "1080p",
                    "poll_interval_seconds": 1,
                },
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []
        session = _Session(calls)

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        video_module.asyncio.sleep = fake_sleep
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))

        result = await service._generate_video_task(
            session,
            service._headers(),
            "撑伞走路",
            _real_png_bytes(900, 1600),
        )

        self.assertEqual(result.url, "https://cdn.example/video.mp4")
        self.assertEqual(calls[0][1], "https://relay.example/v1/videos")
        self.assertEqual(calls[0][3]["image"][:22], "data:image/png;base64,")
        self.assertEqual(calls[0][3]["aspect_ratio"], "9:16")
        self.assertEqual(calls[0][3]["resolution"], "1080P")
        self.assertEqual(calls[0][3]["seconds"], "8")
        self.assertNotIn("size", calls[0][3])
        self.assertNotIn("n", calls[0][3])
        self.assertIsNone(calls[0][4])
        self.assertEqual(_timeout_total(calls[0][5]), 300)

    async def test_video_size_maps_image_portrait_ratio_to_portrait_video_size(self):
        self.assertEqual(video_size("2:3", "720p"), "720x1280")
        self.assertEqual(video_size("4:5", "1080p"), "1024x1792")
        self.assertEqual(video_size("21:9", "720p"), "1280x720")

    async def test_video_aspect_ratio_uses_nearest_supported_ratio(self):
        self.assertEqual(video_aspect_ratio("2:3"), "2:3")
        self.assertEqual(video_aspect_ratio("4:5"), "3:4")
        self.assertEqual(video_aspect_ratio("21:9"), "16:9")

    async def test_video_reference_under_limit_keeps_original_image(self):
        source = _real_png_bytes(1086, 1448)

        prepared = prepare_video_reference_image(
            source,
            aspect_ratio="3:4",
            resolution="720p",
        )

        self.assertEqual((prepared.source_width, prepared.source_height), (1086, 1448))
        self.assertEqual((prepared.output_width, prepared.output_height), (1086, 1448))
        self.assertEqual(prepared.data, source)
        self.assertFalse(prepared.compressed)

    async def test_video_reference_over_limit_is_resized_and_compressed(self):
        source = _real_bmp_bytes(2400, 3600)
        self.assertGreater(len(source), VIDEO_REFERENCE_MAX_BYTES)

        prepared = prepare_video_reference_image(
            source,
            aspect_ratio="9:16",
            resolution="720p",
        )

        self.assertEqual((prepared.source_width, prepared.source_height), (2400, 3600))
        self.assertEqual((prepared.output_width, prepared.output_height), (720, 1280))
        self.assertTrue(prepared.data.startswith(b"\xff\xd8\xff"))
        self.assertLessEqual(len(prepared.data), VIDEO_REFERENCE_MAX_BYTES)
        self.assertTrue(prepared.compressed)

    async def test_invalid_video_reference_fails_before_network_request(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_keys": ["key-a"],
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        with self.assertRaisesRegex(ValueError, "视频首帧图片无法读取"):
            await service._generate_video_task(
                _Session(calls), service._headers(), "无效首帧", b"not-an-image"
            )

        self.assertEqual(calls, [])

    async def test_video_retries_legacy_payload_and_reuses_successful_format(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://legacy.example",
                    "api_keys": ["key-a"],
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        class _LegacySession(_Session):
            def request(
                self, method, url, headers=None, json=None, data=None, timeout=None
            ):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "POST" and url.endswith("/v1/videos"):
                    if "size" not in (json or {}):
                        return _Response(422, text="unsupported request fields")
                    task_number = sum(
                        call[0] == "POST" and "size" in (call[3] or {})
                        for call in self.calls
                    )
                    return _Response(payload={"task_id": f"legacy-{task_number}"})
                if method == "GET":
                    return _Response(
                        payload={
                            "status": "completed",
                            "video_url": "https://cdn.example/legacy.mp4",
                        }
                    )
                return _Response(500, text="unexpected")

        session = _LegacySession(calls)

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        video_module.asyncio.sleep = fake_sleep
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))

        first = await service._generate_video_task(
            session, service._headers(), "第一次生成", None
        )
        second = await service._generate_video_task(
            session, service._headers(), "第二次生成", None
        )

        self.assertEqual(first.url, "https://cdn.example/legacy.mp4")
        self.assertEqual(second.url, "https://cdn.example/legacy.mp4")
        post_payloads = [call[3] for call in calls if call[0] == "POST"]
        self.assertEqual(len(post_payloads), 3)
        self.assertNotIn("size", post_payloads[0])
        self.assertIn("size", post_payloads[1])
        self.assertIn("size", post_payloads[2])

    async def test_official_generation_endpoint_uses_duration_and_poll_base(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://api.x.ai/v1/videos/generations",
                    "api_keys": ["key-a"],
                    "duration": 10,
                    "resolution": "720p",
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        class _OfficialSession(_Session):
            def request(
                self, method, url, headers=None, json=None, data=None, timeout=None
            ):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "POST" and url.endswith("/v1/videos/generations"):
                    return _Response(payload={"request_id": "official-1"})
                if method == "GET" and url.endswith("/v1/videos/official-1"):
                    return _Response(
                        payload={
                            "status": "done",
                            "video": {"url": "https://cdn.example/official.mp4"},
                        }
                    )
                return _Response(500, text="unexpected")

        session = _OfficialSession(calls)

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        video_module.asyncio.sleep = fake_sleep
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))

        result = await service._generate_video_task(
            session,
            service._headers(),
            "官方接口生成",
            _real_png_bytes(640, 360),
            aspect_ratio="16:9",
        )

        self.assertEqual(result.url, "https://cdn.example/official.mp4")
        payload = calls[0][3]
        self.assertEqual(payload["duration"], 10)
        self.assertEqual(payload["aspect_ratio"], "16:9")
        self.assertEqual(payload["resolution"], "720p")
        self.assertTrue(payload["image"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("seconds", payload)
        self.assertEqual(calls[1][1], "https://api.x.ai/v1/videos/official-1")

    async def test_video_endpoint_helpers_preserve_generation_path(self):
        endpoint = "https://api.x.ai/v1/videos/generations"
        self.assertEqual(videos_endpoint(endpoint), endpoint)
        self.assertEqual(
            task_status_url(endpoint, "task/1"),
            "https://api.x.ai/v1/videos/task%2F1",
        )

    async def test_poll_timeout_continues_until_next_success(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_keys": ["key-a"],
                    "request_timeout_seconds": 10,
                    "timeout_seconds": 120,
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        class _TimeoutOnceSession(_Session):
            def __init__(self):
                super().__init__(calls)
                self.poll_count = 0

            def request(
                self, method, url, headers=None, json=None, data=None, timeout=None
            ):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "POST" and url.endswith("/v1/videos"):
                    return _Response(payload={"task_id": "task-1"})
                if method == "GET" and url.endswith("/v1/videos/task-1"):
                    self.poll_count += 1
                    if self.poll_count == 1:
                        raise video_module.asyncio.TimeoutError()
                    return _Response(
                        payload={
                            "status": "completed",
                            "video_url": "https://cdn.example/video.mp4",
                        }
                    )
                return _Response(500, text="unexpected")

        session = _TimeoutOnceSession()

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        video_module.asyncio.sleep = fake_sleep
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))

        result = await service._generate_video_task(
            session, service._headers(), "撑伞走路", None
        )

        self.assertEqual(result.url, "https://cdn.example/video.mp4")
        self.assertEqual(session.poll_count, 2)
        self.assertEqual(_timeout_total(calls[1][5]), 10)

    async def test_poll_logs_unchanged_status_once(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_keys": ["key-a"],
                    "timeout_seconds": 120,
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        class _QueuedSession(_Session):
            def __init__(self):
                super().__init__(calls)
                self.poll_count = 0

            def request(
                self, method, url, headers=None, json=None, data=None, timeout=None
            ):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "GET" and url.endswith("/v1/videos/task-1"):
                    self.poll_count += 1
                    if self.poll_count <= 3:
                        return _Response(payload={"status": "queued"})
                    return _Response(
                        payload={
                            "status": "completed",
                            "video_url": "https://cdn.example/video.mp4",
                        }
                    )
                return _Response(500, text="unexpected")

        session = _QueuedSession()
        debug_messages = []

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        original_debug = video_module.logger.debug
        video_module.asyncio.sleep = fake_sleep
        video_module.logger.debug = lambda message: debug_messages.append(str(message))
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))
        self.addCleanup(lambda: setattr(video_module.logger, "debug", original_debug))

        result = await service._poll_video_url(
            session,
            service._headers(),
            service.video_endpoint,
            "task-1",
        )

        self.assertEqual(result, "https://cdn.example/video.mp4")
        self.assertEqual(session.poll_count, 4)
        self.assertEqual(
            sum(
                "等待视频生成任务" in message and "状态：排队中" in message
                for message in debug_messages
            ),
            1,
        )

    async def test_poll_logs_in_progress_status_in_chinese(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_keys": ["key-a"],
                    "timeout_seconds": 120,
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        class _ProgressSession(_Session):
            def __init__(self):
                super().__init__(calls)
                self.poll_count = 0

            def request(
                self, method, url, headers=None, json=None, data=None, timeout=None
            ):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "GET" and url.endswith("/v1/videos/task-1"):
                    self.poll_count += 1
                    if self.poll_count == 1:
                        return _Response(payload={"status": "in_progress"})
                    return _Response(
                        payload={
                            "status": "completed",
                            "video_url": "https://cdn.example/video.mp4",
                        }
                    )
                return _Response(500, text="unexpected")

        session = _ProgressSession()
        debug_messages = []

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        original_debug = video_module.logger.debug
        video_module.asyncio.sleep = fake_sleep
        video_module.logger.debug = lambda message: debug_messages.append(str(message))
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))
        self.addCleanup(lambda: setattr(video_module.logger, "debug", original_debug))

        result = await service._poll_video_url(
            session,
            service._headers(),
            service.video_endpoint,
            "task-1",
        )

        self.assertEqual(result, "https://cdn.example/video.mp4")
        self.assertTrue(any("状态：生成中" in message for message in debug_messages))
        self.assertFalse(any("in_progress" in message for message in debug_messages))


class VideoMessageChainTest(unittest.TestCase):
    def test_local_video_uses_file_message(self):
        path = Path(tempfile.mkdtemp()) / "life.mp4"
        path.write_bytes(b"video")

        chain = ProactiveSendMixin.video_message_chain(str(path))

        self.assertIn({"type": "video", "file": str(path)}, chain.items)


if __name__ == "__main__":
    unittest.main()
