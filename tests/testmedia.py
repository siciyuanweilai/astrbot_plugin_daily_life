# ruff: noqa: I001

import base64
import asyncio
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import LifeSettings

from core.media import GeminiImageService
from core.media import video as video_module
from core.media.base import videos_endpoint
from core.media.picture import canvas as picture_canvas
from core.media.picture import imagine as grok_image
from core.media.picture import openai as openai_image
from core.media.picture.pipe import ImageRoute
from core.media.picture.routes import (
    channel_matches_provider,
    make_route,
    requested_image_provider,
)
from core.media.video import GrokVideoService
from core.media.video.errors import VideoTaskError
from core.media.video.protocol.size import video_aspect_ratio
from core.media.video.keyframe import (
    VIDEO_REFERENCE_MAX_BYTES,
    prepare_video_reference_image,
)
from core.media.video.tasks import task_status_url
from core.media.video.tasks import poll_video_url
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
    def test_image_timeout_error_explains_late_upstream_completion(self):
        message = GeminiImageService._error_text(TimeoutError(), 300)

        self.assertIn("等待超过 300 秒", message)
        self.assertIn("上游可能仍在生成", message)

    def test_grok_text_request_uses_documented_json_fields(self):
        route = ImageRoute(
            api_url="https://grok-relay.example/v1/images/generations",
            api_key="grok-key",
            model="grok-imagine-image",
            label="Grok 主线路",
            protocol="grok",
            resolution="2K",
            aspect_ratio="16:9",
            timeout_seconds=120,
            origin="https://grok-relay.example",
        )

        request = grok_image.build_request(
            route,
            [{"text": "雨后街巷生活照"}],
            resolution="2K",
            aspect_ratio="16:9",
        )

        self.assertEqual(
            request.url, "https://grok-relay.example/v1/images/generations"
        )
        self.assertEqual(request.headers["Authorization"], "Bearer grok-key")
        self.assertEqual(request.payload["model"], "grok-imagine-image")
        self.assertEqual(request.payload["prompt"], "雨后街巷生活照")
        self.assertEqual(request.payload["aspect_ratio"], "16:9")
        self.assertEqual(request.payload["resolution"], "2k")
        self.assertEqual(request.payload["response_format"], "b64_json")
        self.assertFalse(request.payload["stream"])

    def test_grok_edit_request_embeds_all_reference_images_as_data_urls(self):
        route = ImageRoute(
            api_url="https://grok-relay.example",
            api_key="grok-key",
            model="grok-imagine-image",
            label="Grok 编辑线路",
            protocol="grok",
            resolution="2K",
            aspect_ratio="9:16",
            timeout_seconds=120,
            origin="https://grok-relay.example",
        )
        parts = [
            {"text": "保持两个人物身份，改成夜景合影"},
            {
                "inlineData": {
                    "mimeType": "image/png",
                    "data": base64.b64encode(b"first").decode("ascii"),
                }
            },
            {
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": base64.b64encode(b"second").decode("ascii"),
                }
            },
        ]

        request = grok_image.build_request(
            route,
            parts,
            resolution="2K",
            aspect_ratio="9:16",
        )

        self.assertEqual(request.url, "https://grok-relay.example/v1/images/edits")
        self.assertNotIn("image", request.payload)
        self.assertEqual(len(request.payload["images"]), 2)
        self.assertTrue(
            request.payload["images"][0]["url"].startswith("data:image/png;base64,")
        )
        self.assertTrue(
            request.payload["images"][1]["url"].startswith("data:image/jpeg;base64,")
        )
        self.assertEqual(request.payload["resolution"], "2k")
        self.assertEqual(request.payload["aspect_ratio"], "9:16")
        self.assertFalse(request.payload["stream"])
        self.assertNotIn("size", request.payload)

    def test_grok_output_size_validation_checks_ratio_and_resolution(self):
        matches = GeminiImageService._grok_output_matches_request

        self.assertTrue(matches(1152, 2048, resolution="2K", aspect_ratio="9:16"))
        self.assertTrue(matches(1024, 1024, resolution="1K", aspect_ratio="1:1"))
        self.assertFalse(matches(1024, 1024, resolution="2K", aspect_ratio="9:16"))
        self.assertFalse(matches(1024, 1024, resolution="2K", aspect_ratio="1:1"))

    async def test_grok_wrong_edit_size_keeps_current_channel_result(self):
        square_output = _real_png_bytes(1024, 1024)
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, json, data))
                return _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(square_output).decode(
                                    "ascii"
                                )
                            }
                        ]
                    }
                )

        temp_dir = Path(tempfile.mkdtemp())
        reference = temp_dir / "reference.png"
        reference.write_bytes(_real_png_bytes(900, 1600))
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "edit_channels": [
                        {
                            "__template_key": "grok",
                            "api_url": "https://grok-relay.example",
                            "api_key": "grok-key",
                            "resolution": "2K",
                            "aspect_ratio": "9:16",
                        },
                        {
                            "__template_key": "openai",
                            "api_url": "https://backup.example",
                            "api_key": "backup-key",
                            "resolution": "2K",
                            "aspect_ratio": "9:16",
                        },
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, temp_dir)

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        generated = await service.edit_image("换成雨夜街景", str(reference))

        self.assertEqual(generated.path.read_bytes(), square_output)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://grok-relay.example/v1/images/edits")
        self.assertEqual(calls[0][1]["aspect_ratio"], "9:16")
        self.assertEqual(calls[0][1]["resolution"], "2k")
        self.assertFalse(calls[0][1]["stream"])
        self.assertNotIn("size", calls[0][1])

    async def test_grok_generation_downloads_url_response(self):
        output_bytes = _real_png_bytes(2, 2)
        calls = []

        class _Content:
            async def read(self, _limit):
                return output_bytes

        class _DownloadResponse(_Response):
            headers = {"Content-Type": "image/png"}
            content = _Content()

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append(("POST", url, json, headers or {}))
                return _Response(
                    payload={"data": [{"url": "https://cdn.example/generated.png"}]}
                )

            def get(self, url, timeout=None):
                calls.append(("GET", url, timeout))
                return _DownloadResponse()

        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "grok",
                            "group_name": "Grok 图片",
                            "api_url": "https://grok-relay.example",
                            "api_key": "grok-key",
                            "model": "grok-imagine-image",
                            "resolution": "2K",
                            "aspect_ratio": "16:9",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        with patch.object(
            picture_canvas,
            "is_http_url_allowed_async",
            new=AsyncMock(return_value=True),
        ):
            generated = await service.generate_image("雨夜生活照", protocol="grok")

        self.assertTrue(generated.path.exists())
        self.assertTrue(generated.path.name.startswith("grok_"))
        self.assertEqual(generated.path.read_bytes(), output_bytes)
        self.assertEqual(
            calls[0][0:2], ("POST", "https://grok-relay.example/v1/images/generations")
        )
        self.assertEqual(calls[0][2]["resolution"], "2k")
        self.assertEqual(calls[1][0:2], ("GET", "https://cdn.example/generated.png"))

    async def test_grok_url_download_failure_tries_next_channel(self):
        output_bytes = b"\x89PNG\r\n\x1a\nbackup-output"
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append(url)
                if url.startswith("https://grok-relay.example/"):
                    return _Response(
                        payload={"data": [{"url": "https://blocked.example/image.png"}]}
                    )
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
                            "__template_key": "grok",
                            "api_url": "https://grok-relay.example",
                            "api_key": "grok-key",
                        },
                        {
                            "__template_key": "openai",
                            "api_url": "https://backup.example",
                            "api_key": "backup-key",
                        },
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        with patch.object(
            picture_canvas,
            "is_http_url_allowed_async",
            new=AsyncMock(return_value=False),
        ):
            generated = await service.generate_image("雨夜生活照")

        self.assertEqual(generated.path.read_bytes(), output_bytes)
        self.assertEqual(
            calls,
            [
                "https://grok-relay.example/v1/images/generations",
                "https://backup.example/v1/images/generations",
            ],
        )

    async def test_grok_rejects_unsupported_4k_without_calling_api(self):
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "enabled": True,
                    "text_channels": [
                        {
                            "__template_key": "grok",
                            "api_url": "https://grok-relay.example",
                            "api_key": "grok-key",
                        }
                    ],
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        with self.assertRaisesRegex(RuntimeError, "只支持 1K 或 2K"):
            await service.generate_image("雨夜生活照", resolution="4K")

    def test_grok_provider_aliases_are_supported(self):
        self.assertEqual(requested_image_provider("grok"), "grok")
        self.assertEqual(requested_image_provider("grok-imagine-image"), "grok")

    def test_removed_image_provider_is_rejected(self):
        for provider in ("nai", "nai-diffusion-4-5-full", "nai-diffusion-5-full"):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                requested_image_provider(provider)

    def test_openai_channel_model_does_not_change_protocol(self):
        channel = type(
            "Channel", (), {"protocol": "openai", "model": "custom-image-model"}
        )()
        self.assertTrue(channel_matches_provider(channel, "openai"))
        route = make_route(
            "https://openai.example/v1",
            "openai-key",
            "custom-image-model",
            "OpenAI 通道",
            "openai",
            "1K",
            "1:1",
            120,
        )
        self.assertEqual(route.protocol, "openai")

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
        self.assertEqual(calls[0][2]["size"], "2048x1136")
        self.assertNotIn("quality", calls[0][2])
        self.assertIn("雨夜生活照", calls[0][2]["prompt"])
        self.assertIsNone(calls[0][3])
        self.assertEqual(_timeout_total(calls[0][4]), 180)

    def test_siciyuanweilai_gpt_text_request_omits_quality_and_uses_base64(self):
        route = ImageRoute(
            api_url="https://siciyuanweilai.com",
            api_key="relay-key",
            model="gpt-image-2",
            label="GPT Image",
            protocol="openai",
            resolution="1K",
            aspect_ratio="1:1",
            timeout_seconds=120,
            origin="https://siciyuanweilai.com",
        )

        request = openai_image.build_request(
            route,
            [{"text": "雨夜街头生活照"}],
            resolution="1K",
            aspect_ratio="1:1",
        )

        self.assertEqual(
            request.payload,
            {
                "model": "gpt-image-2",
                "prompt": "雨夜街头生活照",
                "size": "1024x1024",
                "n": 1,
                "response_format": "b64_json",
            },
        )
        self.assertNotIn("extra_fields", request.payload)
        self.assertTrue(
            str(request.headers.get("X-Client-Request-ID") or "").startswith(
                "daily-life-"
            )
        )

    def test_openai_edit_request_omits_quality_multipart_field(self):
        route = ImageRoute(
            api_url="https://openai-relay.example/v1",
            api_key="relay-key",
            model="gpt-image-2",
            label="GPT Image",
            protocol="openai",
            resolution="1K",
            aspect_ratio="1:1",
            timeout_seconds=120,
            origin="https://openai-relay.example",
        )
        request = openai_image.build_request(
            route,
            [
                {"text": "改成雨夜街景"},
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(b"reference").decode("ascii"),
                    }
                },
            ],
            resolution="1K",
            aspect_ratio="1:1",
        )

        self.assertIsNotNone(request.form)
        self.assertIsNone(_form_field(request.form, "quality"))
        self.assertEqual(_form_field(request.form, "image"), b"reference")
        self.assertEqual(request.reference_image_count, 1)

    def test_siciyuanweilai_edit_request_uses_documented_json_payload(self):
        route = ImageRoute(
            api_url="https://siciyuanweilai.com",
            api_key="relay-key",
            model="gpt-image-2",
            label="GPT Image",
            protocol="openai",
            resolution="1K",
            aspect_ratio="1:1",
            timeout_seconds=120,
            origin="https://siciyuanweilai.com",
        )
        request = openai_image.build_request(
            route,
            [
                {"text": "改成雨夜街景"},
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(b"reference").decode("ascii"),
                    }
                },
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(b"second-reference").decode("ascii"),
                    }
                },
            ],
            resolution="1K",
            aspect_ratio="1:1",
        )

        self.assertEqual(request.url, "https://siciyuanweilai.com/v1/images/edits")
        self.assertIsNone(request.form)
        self.assertEqual(request.reference_image_count, 2)
        self.assertEqual(
            request.payload,
            {
                "model": "gpt-image-2",
                "prompt": (
                    "改成雨夜街景\n参考随请求提供的图片线索，保持画面要求自然一致。"
                ),
                "size": "1024x1024",
                "n": 1,
                "response_format": "b64_json",
                "images": [
                    {
                        "image_url": "data:image/png;base64,"
                        + base64.b64encode(b"reference").decode("ascii")
                    },
                    {
                        "image_url": "data:image/png;base64,"
                        + base64.b64encode(b"second-reference").decode("ascii")
                    },
                ],
            },
        )

    def test_gpt_json_reference_contract_without_quality(self):
        for api_url in (
            "https://api.scywl.cc",
            "https://api.scywl.cc/v1",
            "https://api.scywl.cc/v1/images/generations",
            "https://api.scywl.cc/v1/images/edits",
            "https://siciyuanweilai.com",
            "https://siciyuanweilai.com/v1",
            "https://www.siciyuanweilai.com/v1/images/edits",
        ):
            for model in ("gpt-image-2", "gpt-image-2.5"):
                for reference_count in (0, 1, 2):
                    with self.subTest(
                        api_url=api_url, model=model, references=reference_count
                    ):
                        route = ImageRoute(
                            api_url=api_url,
                            api_key="test-key",
                            model=model,
                            label="GPT Image",
                            protocol="openai",
                            resolution="1K",
                            aspect_ratio="1:1",
                            timeout_seconds=300,
                            origin=api_url,
                        )
                        references = [
                            (b"first-reference", "image/png"),
                            (b"second-reference", "image/jpeg"),
                        ][:reference_count]
                        parts = [{"text": "保留构图，调整颜色"}] + [
                            {
                                "inline_data": {
                                    "mime_type": mime,
                                    "data": base64.b64encode(content).decode("ascii"),
                                }
                            }
                            for content, mime in references
                        ]

                        request = openai_image.build_request(
                            route, parts, resolution="1K", aspect_ratio="1:1"
                        )

                        self.assertIsNone(request.form)
                        self.assertEqual(request.payload["model"], model)
                        self.assertNotIn("quality", request.payload)
                        self.assertEqual(request.payload["response_format"], "b64_json")
                        self.assertNotIn("extra_fields", request.payload)
                        self.assertNotIn("image", request.payload)
                        self.assertEqual(request.reference_image_count, reference_count)
                        endpoint = "edits" if reference_count else "generations"
                        self.assertTrue(request.url.endswith(f"/v1/images/{endpoint}"))
                        if not reference_count:
                            self.assertNotIn("images", request.payload)
                            continue
                        self.assertEqual(
                            len(request.payload["images"]), reference_count
                        )
                        for entry, (content, mime) in zip(
                            request.payload["images"], references
                        ):
                            prefix, encoded = entry["image_url"].split(",", 1)
                            self.assertEqual(prefix, f"data:{mime};base64")
                            self.assertEqual(base64.b64decode(encoded), content)

    async def test_generate_image_downloads_openai_url_response(self):
        output_bytes = _real_png_bytes(2, 2)
        calls = []

        class _Content:
            async def read(self, _limit):
                return output_bytes

        class _DownloadResponse(_Response):
            headers = {"Content-Type": "image/png"}
            content = _Content()

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append(("POST", url, json, headers or {}))
                return _Response(
                    payload={"data": [{"url": "https://cdn.example/openai.png"}]}
                )

            def get(self, url, timeout=None):
                calls.append(("GET", url, timeout))
                return _DownloadResponse()

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
                }
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        with patch.object(
            picture_canvas,
            "is_http_url_allowed_async",
            new=AsyncMock(return_value=True),
        ):
            generated = await service.generate_image("雨夜生活照")

        self.assertEqual(generated.path.read_bytes(), output_bytes)
        self.assertEqual(
            calls[0][0:2],
            ("POST", "https://openai-relay.example/v1/images/generations"),
        )
        self.assertEqual(calls[1][0:2], ("GET", "https://cdn.example/openai.png"))

    async def test_generated_url_retries_incomplete_cdn_body(self):
        output_bytes = _real_png_bytes(2, 2)
        calls = []
        attempts = 0

        class _Content:
            def __init__(self, data):
                self.data = data

            async def read(self, _limit):
                return self.data

        class _DownloadResponse(_Response):
            headers = {
                "Content-Type": "image/png",
                "Content-Length": str(len(output_bytes)),
            }

            def __init__(self, data):
                super().__init__()
                self.content = _Content(data)

        class _ImageSession:
            closed = False

            def get(self, url, timeout=None, headers=None):
                nonlocal attempts
                attempts += 1
                calls.append((url, headers or {}))
                data = (
                    output_bytes[: len(output_bytes) // 2]
                    if attempts == 1
                    else output_bytes
                )
                return _DownloadResponse(data)

        settings = LifeSettings.from_dict(
            {"image_generation_config": {"enabled": True}}
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        with (
            patch.object(
                picture_canvas,
                "is_http_url_allowed_async",
                new=AsyncMock(return_value=True),
            ),
            patch.object(picture_canvas.asyncio, "sleep", new=AsyncMock()),
        ):
            result = await service._download_generated_image(
                "https://cdn.example/eventual.png",
                timeout=picture_canvas.aiohttp.ClientTimeout(total=10),
            )

        self.assertEqual(result, output_bytes)
        self.assertEqual(attempts, 2)
        self.assertEqual(calls[0][1], {})
        self.assertEqual(calls[1][0], "https://cdn.example/eventual.png")
        self.assertEqual(
            calls[1][1],
            {
                "Cache-Control": "no-cache, no-store",
                "Pragma": "no-cache",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )

    async def test_generated_url_retries_original_after_http_400(self):
        output_bytes = _real_png_bytes(2, 2)
        calls = []
        attempts = 0

        class _Content:
            def __init__(self, data):
                self.data = data

            async def read(self, _limit):
                return self.data

        class _DownloadResponse(_Response):
            headers = {
                "Content-Type": "image/png",
                "Content-Length": str(len(output_bytes)),
            }

            def __init__(self, data):
                super().__init__()
                self.content = _Content(data)

        class _ImageSession:
            closed = False

            def get(self, url, timeout=None, headers=None):
                nonlocal attempts
                attempts += 1
                calls.append((url, headers or {}))
                if attempts == 1:
                    return _DownloadResponse(output_bytes[: len(output_bytes) // 2])
                if attempts == 2:
                    return _Response(status=400)
                return _DownloadResponse(output_bytes)

        settings = LifeSettings.from_dict(
            {"image_generation_config": {"enabled": True}}
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        with (
            patch.object(
                picture_canvas,
                "is_http_url_allowed_async",
                new=AsyncMock(return_value=True),
            ),
            patch.object(picture_canvas.asyncio, "sleep", new=AsyncMock()),
        ):
            result = await service._download_generated_image(
                "https://cdn.example/eventual.png",
                timeout=picture_canvas.aiohttp.ClientTimeout(total=10),
            )

        self.assertEqual(result, output_bytes)
        self.assertEqual(attempts, 3)
        self.assertEqual(calls[1][0], "https://cdn.example/eventual.png")
        self.assertEqual(calls[2][0], "https://cdn.example/eventual.png")
        self.assertEqual(calls[2][1].get("Range"), "bytes=0-")

    async def test_generated_url_reads_all_response_chunks_until_eof(self):
        output_bytes = _real_png_bytes(2, 2)

        class _Content:
            def iter_chunked(self, _size):
                async def chunks():
                    yield output_bytes[:11]
                    yield output_bytes[11:]

                return chunks()

            async def read(self, _limit):
                self.unexpected_read = True
                return output_bytes[:11]

        class _DownloadResponse(_Response):
            headers = {
                "Content-Type": "image/png",
                "Content-Length": str(len(output_bytes)),
            }
            content = _Content()

        class _ImageSession:
            closed = False

            def get(self, url, timeout=None, headers=None):
                return _DownloadResponse()

        settings = LifeSettings.from_dict(
            {"image_generation_config": {"enabled": True}}
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()))

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        with patch.object(
            picture_canvas,
            "is_http_url_allowed_async",
            new=AsyncMock(return_value=True),
        ):
            result = await service._download_generated_image(
                "https://cdn.example/streamed.png",
                timeout=picture_canvas.aiohttp.ClientTimeout(total=10),
            )

        self.assertEqual(result, output_bytes)

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
        self.assertIn("本轮画面要求明确指定的当天造型优先", calls[0][2]["prompt"])
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
        self.assertEqual(
            service.first_configured_character_reference_image(), str(first)
        )

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
        self.assertEqual(image_config["aspectRatio"], "9:16")
        self.assertEqual(image_config["imageSize"], "4K")
        self.assertNotIn("responseFormat", calls[0][2]["generationConfig"])
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

    def test_gpt_image_2_uses_fixed_size_catalog(self):
        expected = {
            "1K": {
                "2:3": "848x1264",
                "1:1": "1024x1024",
                "16:9": "1376x768",
                "3:2": "1264x848",
                "3:4": "896x1200",
                "5:4": "1152x928",
                "4:3": "1200x896",
                "4:5": "928x1152",
                "9:16": "768x1376",
                "21:9": "1584x672",
            },
            "2K": {
                "2:3": "1376x2048",
                "1:1": "2048x2048",
                "16:9": "2048x1136",
                "3:2": "2048x1376",
                "3:4": "1536x2048",
                "5:4": "2048x1648",
                "4:3": "2048x1536",
                "4:5": "1648x2048",
                "9:16": "1136x2048",
                "21:9": "2048x864",
            },
            "4K": {
                "2:3": "2336x3504",
                "1:1": "2880x2880",
                "16:9": "3584x2016",
                "3:2": "3504x2336",
                "3:4": "2448x3264",
                "5:4": "3200x2560",
                "4:3": "3264x2448",
                "4:5": "2560x3200",
                "9:16": "2016x3584",
                "21:9": "3808x1632",
            },
        }
        for resolution, ratios in expected.items():
            for aspect_ratio, size in ratios.items():
                with self.subTest(resolution=resolution, aspect_ratio=aspect_ratio):
                    self.assertEqual(
                        openai_image.size_for(
                            resolution,
                            aspect_ratio,
                            model="gpt-image-2",
                        ),
                        size,
                    )
        self.assertEqual(
            openai_image.size_for("1K", "1:4", model="gpt-image-2"),
            "768x1376",
        )
        self.assertEqual(
            openai_image.size_for("1K", "4:1", model="gpt-image-2"),
            "1584x672",
        )
        with self.assertRaisesRegex(ValueError, "只能是 1K、2K 或 4K"):
            openai_image.size_for("", "1:1")
        with self.assertRaisesRegex(ValueError, "只能是 1K、2K 或 4K"):
            openai_image.size_for("8K", "1:1")

    def test_gpt_image_2_always_maps_to_supported_aspect_ratios(self):
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
                    self.assertIn(
                        openai_image.supported_aspect_ratio("gpt-image-2", ratio),
                        {
                            "2:3",
                            "1:1",
                            "16:9",
                            "3:2",
                            "3:4",
                            "5:4",
                            "4:3",
                            "4:5",
                            "9:16",
                            "21:9",
                        },
                    )

    def test_gpt_image_2_normalizes_legacy_unsupported_ratio_before_prompting(self):
        route = ImageRoute(
            api_url="https://openai-relay.example/v1",
            api_key="relay-key",
            model="gpt-image-2",
            label="GPT Image",
            protocol="openai",
            resolution="1K",
            aspect_ratio="1:4",
            timeout_seconds=120,
            origin="https://openai-relay.example",
        )

        normalized = GeminiImageService._route_with_options(route, "", "")

        self.assertEqual(normalized.aspect_ratio, "9:16")
        self.assertEqual(
            openai_image.size_for(
                normalized.resolution,
                normalized.aspect_ratio,
                model=normalized.model,
            ),
            "768x1376",
        )

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

    async def test_gpt_image_25_edit_image_posts_application_json_without_quality(self):
        output_bytes = _real_png_bytes(16, 16)
        reference = Path(tempfile.mkdtemp()) / "reference.png"
        reference.write_bytes(_real_png_bytes(32, 32))
        calls = []

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append((url, headers or {}, json, data, timeout))
                if not (json or {}).get("images") or any(
                    not image.get("image_url") for image in json["images"]
                ):
                    return _Response(
                        status=400,
                        text='{"error":{"message":"images[].image_url is required",'
                        '"type":"invalid_request_error"}}',
                    )
                if json.get("response_format") != "b64_json":
                    return _Response(
                        status=503, text="image task storage is unavailable"
                    )
                if "quality" in json or "extra_fields" in json:
                    return _Response(status=400, text="unsupported image options")
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
                            "api_url": "https://api.scywl.cc/v1",
                            "api_key": "relay-key",
                            "model": "gpt-image-2.5",
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
        self.assertEqual(calls[0][0], "https://api.scywl.cc/v1/images/edits")
        self.assertIsNotNone(calls[0][2])
        self.assertIsNone(calls[0][3])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]["model"], "gpt-image-2.5")
        self.assertNotIn("quality", calls[0][2])
        self.assertEqual(calls[0][2]["response_format"], "b64_json")
        self.assertEqual(generated.path.read_bytes(), output_bytes)
        image_url = calls[0][2]["images"][0]["image_url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        self.assertEqual(
            base64.b64decode(image_url.split(",", 1)[1]), reference.read_bytes()
        )

    async def test_edit_image_downloads_openai_url_response(self):
        output_bytes = _real_png_bytes(2, 2)
        reference = Path(tempfile.mkdtemp()) / "reference.png"
        reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")
        calls = []

        class _Content:
            async def read(self, _limit):
                return output_bytes

        class _DownloadResponse(_Response):
            headers = {"Content-Type": "image/png"}
            content = _Content()

        class _ImageSession:
            closed = False

            def post(self, url, json=None, data=None, headers=None, timeout=None):
                calls.append(("POST", url, json, data, headers or {}))
                return _Response(
                    payload={"data": [{"url": "https://cdn.example/openai-edit.png"}]}
                )

            def get(self, url, timeout=None):
                calls.append(("GET", url, timeout))
                return _DownloadResponse()

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

        async def get_session():
            return _ImageSession()

        service._get_session = get_session
        with patch.object(
            picture_canvas,
            "is_http_url_allowed_async",
            new=AsyncMock(return_value=True),
        ):
            generated = await service.edit_image("换成雨夜窗边", str(reference))

        self.assertEqual(generated.path.read_bytes(), output_bytes)
        self.assertEqual(
            calls[0][0:2], ("POST", "https://openai-relay.example/v1/images/edits")
        )
        self.assertEqual(calls[1][0:2], ("GET", "https://cdn.example/openai-edit.png"))

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
        self.assertEqual(_form_field(calls[0][3], "size"), "2016x3584")
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
        self.assertEqual(_form_field(calls[0][3], "size"), "3584x2016")
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
    async def test_poll_timeout_does_not_exceed_total_budget_during_status_request(
        self,
    ):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_key": "key-a",
                    "timeout_seconds": 30,
                    "request_timeout_seconds": 60,
                    "poll_interval_seconds": 5,
                }
            }
        ).video_generation
        request_timeouts = []

        async def immediate_sleep(_seconds):
            return None

        async def timeout_request(*_args, timeout_seconds=None, **_kwargs):
            request_timeouts.append(timeout_seconds)
            raise asyncio.TimeoutError

        with patch(
            "core.media.video.tasks.time.monotonic", side_effect=[0.0, 0.0, 5.0]
        ):
            with self.assertRaises(asyncio.TimeoutError):
                await poll_video_url(
                    settings=settings,
                    session=object(),
                    headers={},
                    endpoint="https://relay.example/v1/videos",
                    request_id="task-budget",
                    request=timeout_request,
                    download=lambda *_args, **_kwargs: None,
                    sleep=immediate_sleep,
                    log_debug=lambda _message: None,
                    log_info=lambda _message: None,
                )

        self.assertEqual(request_timeouts, [25.0])

    async def test_poll_stops_when_sleep_consumes_remaining_budget(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_key": "key-a",
                    "timeout_seconds": 30,
                    "poll_interval_seconds": 5,
                }
            }
        ).video_generation
        requested = False

        async def immediate_sleep(_seconds):
            return None

        async def request(*_args, **_kwargs):
            nonlocal requested
            requested = True
            return {}

        with patch(
            "core.media.video.tasks.time.monotonic", side_effect=[0.0, 29.0, 30.0]
        ):
            with self.assertRaisesRegex(VideoTaskError, "Grok 视频任务超时"):
                await poll_video_url(
                    settings=settings,
                    session=object(),
                    headers={},
                    endpoint="https://relay.example/v1/videos",
                    request_id="task-budget",
                    request=request,
                    download=lambda *_args, **_kwargs: None,
                    sleep=immediate_sleep,
                    log_debug=lambda _message: None,
                    log_info=lambda _message: None,
                )

        self.assertFalse(requested)

    async def test_generate_video_uses_video_task_endpoint(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_key": "key-a",
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
                    "api_key": "key-a",
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
                    "api_key": "key-a",
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
                    "api_key": "key-a",
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

    async def test_video_uses_documented_first_frame_payload(self):
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
                    "api_key": "key-a",
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
        self.assertTrue(
            calls[0][3]["input_reference"]["image_url"].startswith(
                "data:image/png;base64,"
            )
        )
        self.assertEqual(calls[0][3]["aspect_ratio"], "9:16")
        self.assertEqual(calls[0][3]["resolution"], "1080p")
        self.assertEqual(calls[0][3]["seconds"], "8")
        self.assertNotIn("image", calls[0][3])
        self.assertNotIn("duration", calls[0][3])
        self.assertNotIn("size", calls[0][3])
        self.assertNotIn("n", calls[0][3])
        self.assertIsNone(calls[0][4])
        self.assertEqual(_timeout_total(calls[0][5]), 300)

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

    async def test_video_reference_480p_uses_480_pixel_short_side(self):
        source = _real_bmp_bytes(2400, 3600)

        prepared = prepare_video_reference_image(
            source,
            aspect_ratio="9:16",
            resolution="480p",
        )

        self.assertEqual((prepared.output_width, prepared.output_height), (480, 853))
        self.assertLessEqual(len(prepared.data), VIDEO_REFERENCE_MAX_BYTES)

    async def test_invalid_video_reference_fails_before_network_request(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_key": "key-a",
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

    async def test_video_rejects_unsupported_resolution_before_request(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_key": "key-a",
                    "resolution": "4K",
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        with self.assertRaisesRegex(ValueError, "分辨率仅支持"):
            await service._generate_video_task(
                _Session(calls), service._headers(), "最新格式生成", None
            )

        self.assertEqual(calls, [])

    async def test_video_uses_480p_text_request_and_task_id(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example/v1/videos",
                    "api_key": "key-a",
                    "model": "grok-imagine-video-custom",
                    "duration": 4,
                    "resolution": "480p",
                    "poll_interval_seconds": 1,
                }
            }
        ).video_generation
        service = GrokVideoService(settings, Path(tempfile.mkdtemp()))
        calls = []

        class _UpdatedSession(_Session):
            def request(
                self, method, url, headers=None, json=None, data=None, timeout=None
            ):
                self.calls.append((method, url, headers or {}, json, data, timeout))
                if method == "POST" and url.endswith("/v1/videos"):
                    return _Response(
                        payload={
                            "id": "task-2",
                            "task_id": "task-2",
                            "status": "queued",
                        }
                    )
                if method == "GET" and url.endswith("/v1/videos/task-2"):
                    return _Response(
                        payload={
                            "status": "completed",
                            "video": {"url": "https://cdn.example/new.mp4"},
                        }
                    )
                return _Response(500, text="unexpected")

        session = _UpdatedSession(calls)

        async def fake_sleep(_seconds):
            return None

        original_sleep = video_module.asyncio.sleep
        video_module.asyncio.sleep = fake_sleep
        self.addCleanup(lambda: setattr(video_module.asyncio, "sleep", original_sleep))

        result = await service._generate_video_task(
            session,
            service._headers(),
            "文字生成",
            None,
        )

        self.assertEqual(result.url, "https://cdn.example/new.mp4")
        payload = calls[0][3]
        self.assertEqual(payload["model"], "grok-imagine-video-custom")
        self.assertEqual(payload["seconds"], "4")
        self.assertEqual(payload["resolution"], "480p")
        self.assertEqual(
            set(payload),
            {"model", "prompt", "seconds", "aspect_ratio", "resolution"},
        )
        self.assertEqual(calls[1][1], "https://relay.example/v1/videos/task-2")

    async def test_video_endpoint_only_accepts_current_path(self):
        endpoint = "https://relay.example/v1/videos"
        self.assertEqual(videos_endpoint(endpoint), endpoint)
        self.assertEqual(videos_endpoint(f"{endpoint}/generations"), "")
        self.assertEqual(
            task_status_url(endpoint, "task/1"),
            "https://relay.example/v1/videos/task%2F1",
        )

    async def test_old_video_endpoint_reports_current_path(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example/v1/videos/generations",
                    "api_key": "key-a",
                }
            }
        ).video_generation
        service = GrokVideoService(settings)

        with self.assertRaisesRegex(RuntimeError, "接口地址须使用 /v1/videos"):
            await service.generate_video("下雨的街道")

    async def test_poll_timeout_continues_until_next_success(self):
        settings = LifeSettings.from_dict(
            {
                "video_generation_config": {
                    "enabled": True,
                    "base_url": "https://relay.example",
                    "api_key": "key-a",
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
                    "api_key": "key-a",
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
                    "api_key": "key-a",
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

    def test_webchat_video_uses_downloadable_file_attachment(self):
        chain = ProactiveSendMixin.video_file_message_chain(
            "https://cdn.example/video.mp4"
        )

        self.assertEqual(len(chain.items), 1)
        self.assertEqual(chain.items[0].name, "生活视频.mp4")
        self.assertEqual(chain.items[0].url, "https://cdn.example/video.mp4")

    def test_webchat_local_video_uses_file_path(self):
        chain = ProactiveSendMixin.video_file_message_chain("/tmp/life.mp4")

        self.assertEqual(chain.items[0].file, "/tmp/life.mp4")
        self.assertEqual(chain.items[0].url, "")


if __name__ == "__main__":
    unittest.main()
