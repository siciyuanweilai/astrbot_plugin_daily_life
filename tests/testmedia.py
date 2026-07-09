import base64
import tempfile
import unittest
from pathlib import Path

from support import LifeSettings
from core.media import video as video_module
from core.media import GeminiImageService
from core.media.video import GrokVideoService
from core.media.video.protocol.size import video_size
from core.runtime.proactive.send import ProactiveSendMixin


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
            return _Response(payload={"status": "completed", "video_url": "https://cdn.example/video.mp4"})
        return _Response(500, text="unexpected")


class GeminiImageServiceTest(unittest.IsolatedAsyncioTestCase):
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
                                                "data": base64.b64encode(output_bytes).decode("ascii"),
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
        self.assertEqual(
            [call[0] for call in calls],
            [
                "https://bad.example/v1beta/models/gemini-3-pro-image-preview:generateContent",
                "https://good.example/v1beta/models/gemini-relay-image:generateContent",
            ],
        )
        self.assertEqual([call[1]["x-goog-api-key"] for call in calls], ["main-key", "backup-key"])

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

        self.assertEqual(calls, ["https://main.example/v1beta/models/gemini-main:generateContent"])

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
                                                "data": base64.b64encode(output_bytes).decode("ascii"),
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
        self.assertEqual(calls[0][0], "https://relay.example/v1beta/models/gemini-relay-only:generateContent")
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
                                "b64_json": base64.b64encode(output_bytes).decode("ascii"),
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
        self.assertEqual(calls[0][0], "https://openai-relay.example/v1/images/generations")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer relay-key")
        self.assertEqual(calls[0][2]["model"], "gpt-image-2")
        self.assertEqual(calls[0][2]["size"], "2560x1440")
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
                                "b64_json": base64.b64encode(output_bytes).decode("ascii"),
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
                    "character_reference_images": [{"path": str(character), "name": "角色参考.png"}],
                    "character_reference_policy": "always",
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))
        session = _ImageSession()

        async def get_session():
            return session

        service._get_session = get_session

        await service.generate_image("雨夜生活照")

        self.assertEqual(calls[0][0], "https://openai-relay.example/v1/images/generations")
        self.assertIsNotNone(calls[0][2])
        self.assertIsNone(calls[0][3])
        self.assertNotIn("已提供一组角色形象参考图", calls[0][2]["prompt"])

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

    async def test_generate_image_can_override_gemini_aspect_ratio_per_request(self):
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
                                                "data": base64.b64encode(output_bytes).decode("ascii"),
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

        await service.generate_image("雨夜生活照", aspect_ratio="9:16")

        image_config = calls[0][2]["generationConfig"]["imageConfig"]
        response_image_config = calls[0][2]["generationConfig"]["responseFormat"]["image"]
        self.assertEqual(image_config["aspectRatio"], "9:16")
        self.assertEqual(response_image_config["aspectRatio"], "9:16")
        self.assertIn("9:16 比例图片", calls[0][2]["contents"][0]["parts"][0]["text"])

    async def test_generate_image_can_override_openai_size_per_request(self):
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
                                "b64_json": base64.b64encode(output_bytes).decode("ascii"),
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

        await service.generate_image("雨夜生活照", aspect_ratio="16:9")

        self.assertEqual(calls[0][2]["size"], "2560x1440")
        self.assertIn("16:9 比例图片", calls[0][2]["prompt"])

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
                                "b64_json": base64.b64encode(output_bytes).decode("ascii"),
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
                                "b64_json": base64.b64encode(output_bytes).decode("ascii"),
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

        generated = await service.edit_image("换成雨夜窗边", str(reference), aspect_ratio="16:9")

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
                                "b64_json": base64.b64encode(output_bytes).decode("ascii"),
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
            "换成雨夜窗边",
            str(reference),
            aspect_ratio="16:9",
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
        self.addCleanup(lambda: setattr(video_module.aiohttp, "ClientSession", original_session))

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
        self.addCleanup(lambda: setattr(video_module.aiohttp, "ClientSession", original_session))

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
            def request(self, method, url, headers=None, json=None, data=None, timeout=None):
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
        self.addCleanup(lambda: setattr(video_module.aiohttp, "ClientSession", original_session))

        with self.assertRaisesRegex(RuntimeError, r"^Grok 视频任务超时：task-timeout$"):
            await service.generate_video("雨夜街边短视频")

    async def test_video_uploads_reference_image_as_json_data_url(self):
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
                }
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
            b"\x89PNG\r\n\x1a\nabc",
        )

        self.assertEqual(result.url, "https://cdn.example/video.mp4")
        self.assertEqual(calls[0][1], "https://relay.example/v1/videos")
        self.assertEqual(calls[0][3]["image"][:22], "data:image/png;base64,")
        self.assertEqual(calls[0][3]["size"], "1024x1792")
        self.assertIsNone(calls[0][4])
        self.assertEqual(_timeout_total(calls[0][5]), 300)

    async def test_video_size_maps_image_portrait_ratio_to_portrait_video_size(self):
        self.assertEqual(video_size("2:3", "720p"), "720x1280")
        self.assertEqual(video_size("4:5", "1080p"), "1024x1792")
        self.assertEqual(video_size("21:9", "720p"), "1280x720")

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

            def request(self, method, url, headers=None, json=None, data=None, timeout=None):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "POST" and url.endswith("/v1/videos"):
                    return _Response(payload={"task_id": "task-1"})
                if method == "GET" and url.endswith("/v1/videos/task-1"):
                    self.poll_count += 1
                    if self.poll_count == 1:
                        raise video_module.asyncio.TimeoutError()
                    return _Response(payload={"status": "completed", "video_url": "https://cdn.example/video.mp4"})
                return _Response(500, text="unexpected")

        session = _TimeoutOnceSession()

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        video_module.asyncio.sleep = fake_sleep
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))

        result = await service._generate_video_task(session, service._headers(), "撑伞走路", None)

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

            def request(self, method, url, headers=None, json=None, data=None, timeout=None):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "GET" and url.endswith("/v1/videos/task-1"):
                    self.poll_count += 1
                    if self.poll_count <= 3:
                        return _Response(payload={"status": "queued"})
                    return _Response(payload={"status": "completed", "video_url": "https://cdn.example/video.mp4"})
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
            sum("等待视频生成任务" in message and "状态：排队中" in message for message in debug_messages),
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

            def request(self, method, url, headers=None, json=None, data=None, timeout=None):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "GET" and url.endswith("/v1/videos/task-1"):
                    self.poll_count += 1
                    if self.poll_count == 1:
                        return _Response(payload={"status": "in_progress"})
                    return _Response(payload={"status": "completed", "video_url": "https://cdn.example/video.mp4"})
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

