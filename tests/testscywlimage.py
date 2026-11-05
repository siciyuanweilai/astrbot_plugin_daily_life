# ruff: noqa: I001

import base64
import json
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import LifeSettings, PLUGIN_ROOT
from PIL import Image

from core.config.options.generate import IMAGE_ASPECT_RATIOS
from core.media import GeminiImageService
from core.media.picture import openai
from core.media.picture.pipe import ImageRoute
from core.media.picture.polling import ImageTaskError
from core.media.picture.results import ImageResultError


def _image_bytes(color="blue"):
    output = BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def _animated_gif_bytes(frame_count=4):
    frames = [
        Image.new("RGB", (32, 32), color)
        for color in ("red", "blue", "green", "yellow")[:frame_count]
    ]
    output = BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=200,
        loop=0,
    )
    return output.getvalue()


def _parts(count=0):
    return [{"text": "窗边的生活照"}] + [
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": base64.b64encode(
                    _image_bytes(("red", "blue", "green")[index % 3])
                ).decode("ascii"),
            }
        }
        for index in range(count)
    ]


def _route(**changes):
    return replace(
        ImageRoute(
            api_url="https://api.scywl.cc",
            api_key="test-key",
            model="gpt-image-2",
            label="GPT Image",
            protocol="openai",
            resolution="1K",
            aspect_ratio="1:1",
            timeout_seconds=300,
            origin="https://api.scywl.cc",
        ),
        **changes,
    )


class _Response:
    def __init__(self, *, status=200, error_phase="", payload=None):
        self.status = status
        self.error_phase = error_phase
        self.payload = payload
        self.headers = {"X-Request-ID": "upstream-request-123"}

    async def __aenter__(self):
        if self.error_phase == "connect":
            raise TimeoutError()
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if self.error_phase == "body":
            raise TimeoutError()
        if self.error_phase == "json":
            raise ValueError("private prompt echoed by upstream")
        if self.payload is not None:
            return self.payload
        return {
            "data": [{"b64_json": base64.b64encode(_image_bytes()).decode("ascii")}]
        }

    async def text(self):
        return "接口暂时不可用"


class _Session:
    closed = False

    def __init__(self, responses=None, downloads=None):
        self.calls = []
        self.responses = list(responses or [_Response()])
        self.downloads = list(downloads or [])
        self.get_calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.downloads.pop(0)


class _DownloadResponse(_Response):
    def __init__(self, content=b"", *, status=200, content_type="image/gif"):
        super().__init__(status=status)
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(content)),
        }
        self.content = self
        self.body = content

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class ScywlImageTest(unittest.IsolatedAsyncioTestCase):
    async def test_gif_square_default_does_not_change_static_ratios(self):
        for mode in ("text", "edit"):
            for output_format, explicit_ratio, expected_ratio in (
                ("gif", "", "1:1"),
                ("gif", "16:9", "16:9"),
                ("", "", "9:16" if mode == "text" else "3:4"),
            ):
                with self.subTest(mode=mode, format=output_format, ratio=explicit_ratio), tempfile.TemporaryDirectory() as folder:
                    settings = LifeSettings.from_dict({"image_generation_config": {
                        "enabled": True,
                        mode + "_channels": [{
                            "__template_key": "openai", "api_url": "https://api.scywl.cc",
                            "api_key": "test-key", "model": "gpt-image-2.6",
                            "aspect_ratio": "9:16", "resolution": "1K",
                        }],
                    }}).image_generation
                    service = GeminiImageService(settings, Path(folder))
                    content = _animated_gif_bytes() if output_format == "gif" else _image_bytes()
                    session = _Session([_Response(payload={"data": [{
                        "b64_json": base64.b64encode(content).decode("ascii"),
                    }]})])
                    service._get_session = AsyncMock(return_value=session)
                    if mode == "text":
                        await service.generate_image("cat", aspect_ratio=explicit_ratio, output_format=output_format)
                    else:
                        reference = Path(folder) / "portrait.png"
                        Image.new("RGB", (48, 64), "red").save(reference)
                        await service.edit_image("cat", str(reference), aspect_ratio=explicit_ratio, output_format=output_format)
                    request = session.calls[0][1]
                    sent = request.get("json")
                    if sent is None:
                        sent = {field[0]: field[1] for field in request["data"].fields}
                    self.assertEqual(sent["size"], openai.size_for("1K", expected_ratio, model="gpt-image-2.6", api_url="https://api.scywl.cc"))
                    if output_format == "gif":
                        self.assertEqual(sent["frame_count"], 9)

    def test_existing_gpt_template_loads_documented_models(self):
        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text())
        for mode in ("text_channels", "edit_channels"):
            spec = schema["image_generation_config"]["items"][mode]["templates"][
                "openai"
            ]
            raw = {key: value["default"] for key, value in spec["items"].items()}
            for model in ("gpt-image-2", "gpt-image-2.5", "gpt-image-2.6"):
                raw.update(
                    __template_key="openai",
                    api_key="test-key",
                    api_url="https://api.scywl.cc",
                    model=model,
                )
                settings = LifeSettings.from_dict(
                    {"image_generation_config": {mode: [raw]}}
                )
                channel = getattr(settings.image_generation, mode)[0]
                self.assertEqual(channel.protocol, "openai")
                self.assertEqual(channel.model, model)
                self.assertEqual(channel.api_url, "https://api.scywl.cc")

    def test_gpt_image_26_gif_request_matches_documented_text_and_edit_contract(self):
        route = _route(model="gpt-image-2.6")
        self.assertTrue(openai.supports_animated_gif(route))
        request = openai.build_request(
            route,
            _parts(),
            resolution="1K",
            aspect_ratio="1:1",
            output_format="gif",
            frame_count=32,
            frame_duration_ms=200,
        )
        self.assertEqual(request.url, "https://api.scywl.cc/v1/images/generations")
        self.assertEqual(
            request.payload,
            {
                "model": "gpt-image-2.6",
                "prompt": "窗边的生活照",
                "size": "1024x1024",
                "n": 1,
                "response_format": "url",
                "output_format": "gif",
                "frame_count": 32,
                "frame_duration_ms": 200,
            },
        )

        edit = openai.build_request(
            route,
            _parts(1),
            resolution="1K",
            aspect_ratio="1:1",
            output_format="gif",
        )
        self.assertEqual(edit.url, "https://api.scywl.cc/v1/images/edits")
        self.assertEqual(edit.payload["output_format"], "gif")
        self.assertEqual(edit.payload["frame_count"], 9)
        self.assertEqual(edit.payload["response_format"], "url")
        self.assertTrue(
            edit.payload["images"][0]["image_url"].startswith("data:image/png;base64,")
        )

    def test_gif_is_restricted_to_the_configured_scywl_gpt_image_26_route(self):
        self.assertFalse(openai.supports_animated_gif(_route(model="gpt-image-2.5")))
        self.assertFalse(
            openai.supports_animated_gif(
                _route(model="gpt-image-2.6", api_url="https://other.example")
            )
        )
        with self.assertRaisesRegex(ValueError, "仅支持已配置的 GPT Image 2.6 通道"):
            openai.build_request(
                _route(model="gpt-image-2.5"),
                _parts(),
                resolution="1K",
                aspect_ratio="1:1",
                output_format="gif",
            )

    async def test_gif_generation_saves_and_validates_a_real_animated_gif(self):
        gif_bytes = _animated_gif_bytes()

        class _GifResponse(_Response):
            async def json(self):
                return {
                    "data": [{"b64_json": base64.b64encode(gif_bytes).decode("ascii")}]
                }

        class _GifSession(_Session):
            def __init__(self):
                super().__init__([_GifResponse()])

        with tempfile.TemporaryDirectory() as directory:
            settings = LifeSettings.from_dict(
                {
                    "image_generation_config": {
                        "enabled": True,
                        "text_channels": [
                            {
                                "__template_key": "openai",
                                "group_name": "GPT Image 2.6（低价）",
                                "api_url": "https://api.scywl.cc",
                                "api_key": "test-key",
                                "model": "gpt-image-2.6",
                            }
                        ],
                    }
                }
            ).image_generation
            service = GeminiImageService(settings, Path(directory))
            session = _GifSession()
            service._get_session = AsyncMock(return_value=session)
            result = await service.generate_image(
                "红色风筝随风轻摆，固定镜头，首尾衔接循环",
                channel_name="GPT Image 2.6（低价）",
                output_format="gif",
                frame_count=4,
                frame_duration_ms=200,
            )
            self.assertEqual(result.path.suffix, ".gif")
            self.assertEqual(len(session.calls), 1)
            payload = session.calls[0][1]["json"]
            self.assertEqual(payload["model"], "gpt-image-2.6")
            self.assertEqual(payload["output_format"], "gif")
            with Image.open(result.path) as output:
                self.assertEqual(output.format, "GIF")
                self.assertEqual(output.n_frames, 4)

    async def test_gif_generation_rejects_a_static_upstream_result(self):
        class _StaticResponse(_Response):
            async def json(self):
                return {
                    "data": [
                        {"b64_json": base64.b64encode(_image_bytes()).decode("ascii")}
                    ]
                }

        with tempfile.TemporaryDirectory() as directory:
            settings = LifeSettings.from_dict(
                {
                    "image_generation_config": {
                        "enabled": True,
                        "text_channels": [
                            {
                                "__template_key": "openai",
                                "group_name": "GIF 通道",
                                "api_url": "https://api.scywl.cc",
                                "api_key": "test-key",
                                "model": "gpt-image-2.6",
                            }
                        ],
                    }
                }
            ).image_generation
            service = GeminiImageService(settings, Path(directory))
            session = _Session([_StaticResponse()])
            service._get_session = AsyncMock(return_value=session)
            with self.assertRaisesRegex(RuntimeError, "上游返回 PNG 静态图"):
                await service.generate_image(
                    "红色风筝轻摆",
                    output_format="gif",
                    channel_name="GIF 通道",
                )

    async def test_text_and_edit_pick_animated_url_over_base64_preview(self):
        preview = base64.b64encode(_image_bytes()).decode("ascii")
        gif = _animated_gif_bytes()
        # Neither the extension nor the Content-Type describes the actual file.
        url = "https://cdn.example/preview.png?signature=private-download-token"
        for mode in ("text", "edit"):
            for split_items in (False, True):
                with (
                    self.subTest(mode=mode, split_items=split_items),
                    tempfile.TemporaryDirectory() as directory,
                    patch(
                        "core.media.picture.canvas.is_http_url_allowed_async",
                        AsyncMock(return_value=True),
                    ),
                    patch("core.media.picture.results.logger.info") as logs,
                ):
                    items = (
                        [{"b64_json": preview}, {"url": url}]
                        if split_items
                        else [{"b64_json": preview, "url": url}]
                    )
                    service = GeminiImageService(
                        self._settings(mode=mode, model="gpt-image-2.6"),
                        Path(directory),
                    )
                    session = _Session(
                        [_Response(payload={"data": items})],
                        [
                            _DownloadResponse(
                                gif, content_type="application/octet-stream"
                            )
                        ],
                    )
                    service._get_session = AsyncMock(return_value=session)
                    prompt = "private user prompt not for diagnostics"
                    if mode == "edit":
                        reference = Path(directory) / "reference.png"
                        reference.write_bytes(_image_bytes())
                        result = await service.edit_image(
                            prompt, str(reference), output_format="gif", frame_count=4
                        )
                    else:
                        result = await service.generate_image(
                            prompt, output_format="gif", frame_count=4
                        )
                    self.assertEqual(result.path.read_bytes(), gif)
                    self.assertEqual(result.path.suffix, ".gif")
                    self.assertEqual(len(session.calls), 1)
                    self.assertEqual(len(session.get_calls), 1)
                    payload = session.calls[0][1]["json"]
                    self.assertEqual(payload["output_format"], "gif")
                    self.assertEqual(payload["frame_count"], 4)
                    self.assertEqual(bool(payload.get("images")), mode == "edit")
                    records = [
                        json.loads(call.args[0].split("图片结果诊断：", 1)[1])
                        for call in logs.call_args_list
                    ]
                    candidate_records = [
                        r for r in records if r["stage"] == "candidate"
                    ]
                    self.assertEqual(
                        [
                            (r["source"], r["actual_format"], r["actual_frames"])
                            for r in candidate_records
                        ],
                        [("base64", "PNG", 1), ("url", "GIF", 4)],
                    )
                    selected = candidate_records[-1]
                    self.assertEqual(selected["download_http_status"], 200)
                    self.assertEqual(selected["http_status"], 200)
                    self.assertEqual(
                        selected["upstream_request_id"], "upstream-request-123"
                    )
                    self.assertEqual(
                        selected["request_id"],
                        session.calls[0][1]["headers"]["X-Client-Request-ID"],
                    )
                    self.assertTrue(selected["requested_at"])
                    self.assertEqual(selected["mode"], mode)
                    log_text = str(logs.call_args_list)
                    for private in (
                        prompt,
                        "test-key",
                        preview,
                        url,
                        "private-download-token",
                        base64.b64encode(gif).decode("ascii"),
                    ):
                        self.assertNotIn(private, log_text)

    async def test_bad_candidates_do_not_hide_a_later_animated_result(self):
        gif = _animated_gif_bytes()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "core.media.picture.canvas.is_http_url_allowed_async",
                AsyncMock(return_value=True),
            ),
            patch("core.media.picture.results.logger.info") as logs,
        ):
            service = GeminiImageService(
                self._settings(model="gpt-image-2.6"), Path(directory)
            )
            session = _Session(
                [
                    _Response(
                        payload={
                            "data": [
                                {"b64_json": "not-valid-base64!"},
                                {"url": "https://cdn.example/expired.gif"},
                                {
                                    "url": "data:image/png;base64,"
                                    + base64.b64encode(gif).decode("ascii")
                                },
                            ]
                        }
                    )
                ],
                [_DownloadResponse(status=404)],
            )
            service._get_session = AsyncMock(return_value=session)
            result = await service.generate_image("test", output_format="gif")
            self.assertEqual(result.path.read_bytes(), gif)
            self.assertEqual(len(session.calls), 1)
            self.assertIn('"download_http_status": 404', str(logs.call_args_list))

    async def test_gif_failures_never_submit_a_second_generation(self):
        cases = [
            (_Response(), "上游返回 PNG 静态图"),
            (
                _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(
                                    _animated_gif_bytes(1)
                                ).decode("ascii")
                            }
                        ]
                    }
                ),
                "上游返回 GIF，但只有一帧",
            ),
            (
                _Response(
                    payload={
                        "data": [
                            {
                                "b64_json": base64.b64encode(
                                    _animated_gif_bytes()[:40]
                                ).decode("ascii")
                            }
                        ]
                    }
                ),
                "不完整或无法解析",
            ),
            (_Response(payload={"data": []}), "没有可读取的图片候选"),
            (_Response(payload=[]), "返回格式不是对象"),
            (_Response(status=503), "HTTP 503"),
            (_Response(error_phase="connect"), "连接中断或等待超时"),
            (_Response(error_phase="body"), "连接中断或等待超时"),
            (_Response(error_phase="json"), "响应无法读取或解析"),
        ]
        for response, expected in cases:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                settings = self._settings(model="gpt-image-2.6", backup=True)
                settings.text_channels[1].model = "gpt-image-2.6"
                service = GeminiImageService(settings, Path(directory))
                self.assertEqual(
                    len(await service._request_routes("text", output_format="gif")), 2
                )
                session = _Session([response, _Response()])
                service._get_session = AsyncMock(return_value=session)
                with self.assertRaisesRegex(ImageResultError, expected) as error:
                    await service.generate_image(
                        "private user prompt", output_format="gif"
                    )
                self.assertIn("未自动重新生图", str(error.exception))
                self.assertNotIn("private", str(error.exception))
                self.assertEqual(len(session.calls), 1)
                self.assertEqual(list(Path(directory).rglob("*.gif")), [])
                self.assertFalse((service.output_dir / "tasks").exists())

    async def test_gif_http_error_records_only_safe_metadata_without_regeneration(self):
        secret_prompt = "private prompt never logged"
        image_content = base64.b64encode(_image_bytes()).decode("ascii")
        response = _Response(status=400)
        response.content = _DownloadResponse(
            json.dumps(
                {
                    "request_id": "upstream-error-request-456",
                    "error": {
                        "code": "invalid_parameter",
                        "type": "invalid_request_error",
                        "param": "frame_count",
                        "message": f"{secret_prompt} test-key https://cdn.example/private {image_content}",
                    },
                }
            ).encode()
        ).content
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("core.media.picture.results.logger.info") as logs,
        ):
            settings = self._settings(model="gpt-image-2.6", backup=True)
            settings.text_channels[1].model = "gpt-image-2.6"
            service = GeminiImageService(settings, Path(directory))
            session = _Session([response, _Response()])
            service._get_session = AsyncMock(return_value=session)
            with self.assertRaisesRegex(
                ImageResultError, "HTTP 400.*invalid_parameter.*frame_count"
            ):
                await service.generate_image(secret_prompt, output_format="gif")
            self.assertEqual(len(session.calls), 1)
            records = [
                json.loads(call.args[0].split("图片结果诊断：", 1)[1])
                for call in logs.call_args_list
            ]
            detail = next(row for row in records if row["stage"] == "error_response")
            self.assertEqual(detail["upstream_error_code"], "invalid_parameter")
            self.assertEqual(detail["upstream_error_type"], "invalid_request_error")
            self.assertEqual(detail["upstream_error_param"], "frame_count")
            self.assertEqual(
                detail["upstream_request_id"], "upstream-error-request-456"
            )
            for private in (
                secret_prompt,
                "test-key",
                "https://cdn.example/private",
                image_content,
            ):
                self.assertNotIn(private, str(logs.call_args_list))

    async def test_gif_error_body_limits_and_privacy_keep_original_http_status(self):
        cases = [
            (b"<html>private upstream body</html>", "not_json"),
            (b"x" * (65536 + 1), "too_large"),
            (
                json.dumps(
                    {
                        "error": {
                            "code": "test-key",
                            "type": "private_prompt",
                            "param": "https://private.example",
                            "message": "private upstream body",
                        }
                    }
                ).encode(),
                "json",
            ),
        ]
        for content, state in cases:
            with (
                self.subTest(state=state),
                tempfile.TemporaryDirectory() as directory,
                patch("core.media.picture.results.logger.info") as logs,
            ):
                response = _Response(status=400)
                response.content = _DownloadResponse(content).content
                service = GeminiImageService(
                    self._settings(model="gpt-image-2.6"), Path(directory)
                )
                session = _Session([response])
                service._get_session = AsyncMock(return_value=session)
                with self.assertRaisesRegex(ImageResultError, "HTTP 400"):
                    await service.generate_image("private_prompt", output_format="gif")
                self.assertEqual(len(session.calls), 1)
                records = [
                    json.loads(call.args[0].split("图片结果诊断：", 1)[1])
                    for call in logs.call_args_list
                ]
                detail = next(
                    row for row in records if row["stage"] == "error_response"
                )
                self.assertEqual(detail["body_state"], state)
                self.assertEqual(detail["upstream_error_code"], "")
                self.assertEqual(detail["upstream_error_type"], "")
                self.assertEqual(detail["upstream_error_param"], "")
                self.assertNotIn("private", str(logs.call_args_list))
                self.assertNotIn("test-key", str(logs.call_args_list))

    async def test_gif_download_failure_is_distinct_and_stops_failover(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "core.media.picture.canvas.is_http_url_allowed_async",
                AsyncMock(return_value=True),
            ),
            patch("core.media.picture.results.logger.info") as logs,
        ):
            settings = self._settings(model="gpt-image-2.6", backup=True)
            settings.text_channels[1].model = "gpt-image-2.6"
            service = GeminiImageService(settings, Path(directory))
            session = _Session(
                [
                    _Response(
                        payload={"data": [{"url": "https://cdn.example/expired.gif"}]}
                    ),
                    _Response(),
                ],
                [_DownloadResponse(status=403)],
            )
            service._get_session = AsyncMock(return_value=session)
            with self.assertRaisesRegex(ImageResultError, "图片链接下载失败") as error:
                await service.generate_image("test", output_format="gif")
            self.assertNotIn("PNG", str(error.exception))
            self.assertNotIn("只有一帧", str(error.exception))
            self.assertEqual(len(session.calls), 1)
            self.assertIn('"download_http_status": 403', str(logs.call_args_list))

    async def test_missing_gif_route_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            service = GeminiImageService(self._settings(), Path(directory))
            with self.assertRaisesRegex(RuntimeError, "没有可用的文生图 GIF 通道"):
                await service.generate_image("test", output_format="gif")

    def test_text_request_uses_json_contract(self):
        for model in ("gpt-image-2", "gpt-image-2.5"):
            for endpoint in ("", "/v1", "/v1/images/edits", "/v1/images/generations"):
                with self.subTest(model=model, endpoint=endpoint):
                    route = _route(
                        model=model, api_url=f"https://api.scywl.cc{endpoint}"
                    )
                    request = openai.build_request(
                        route, _parts(), resolution="1K", aspect_ratio="1:1"
                    )
                    self.assertEqual(
                        request.url, "https://api.scywl.cc/v1/images/generations"
                    )
                    self.assertEqual(
                        request.headers["Authorization"], "Bearer test-key"
                    )
                    self.assertEqual(request.payload["model"], model)
                    self.assertEqual(request.payload["size"], "1024x1024")
                    self.assertEqual(request.payload["n"], 1)
                    self.assertEqual(request.payload["response_format"], "b64_json")
                    self.assertEqual(request.reference_image_count, 0)
                    self.assertIsNone(request.form)
                    self.assertFalse(
                        {"image", "images", "resolution", "quality", "stream", "mask"}
                        & request.payload.keys()
                    )
                    self.assertFalse(openai.uses_async_tasks(route))

    def test_sizes_obey_alignment_pixel_budget_and_ratio_limits(self):
        for tier in ("1K", "2K", "4K"):
            for ratio in IMAGE_ASPECT_RATIOS:
                with self.subTest(tier=tier, ratio=ratio):
                    size = openai.size_for(
                        tier, ratio, model="gpt-image-2", api_url="https://api.scywl.cc"
                    )
                    width, height = map(int, size.split("x"))
                    self.assertEqual(width % 16, 0)
                    self.assertEqual(height % 16, 0)
                    self.assertLessEqual(max(width / height, height / width), 3)
                    effective = openai.supported_aspect_ratio(
                        "gpt-image-2", ratio, api_url="https://api.scywl.cc"
                    )
                    rw, rh = map(int, effective.split(":"))
                    self.assertEqual(width * rh, height * rw)
                    if width == height:
                        self.assertEqual(
                            width, {"1K": 1024, "2K": 2048, "4K": 4096}[tier]
                        )
                    else:
                        self.assertLessEqual(max(width, height), 3840)
                        self.assertGreaterEqual(width * height, 655360)
                        self.assertLessEqual(width * height, 8294400)

    def test_documented_sizes_and_overrides_match_request(self):
        self.assertEqual(
            openai.size_for("1K", "2:3", api_url="https://api.scywl.cc"), "1024x1536"
        )
        self.assertEqual(
            openai.size_for("1K", "3:2", api_url="https://api.scywl.cc"), "1536x1024"
        )
        route = GeminiImageService._route_with_options(_route(), "1:8", "4K")
        self.assertEqual(route.aspect_ratio, "1:3")
        self.assertEqual(GeminiImageService._request_size_label(route), "1280×3840")
        request = openai.build_request(
            route,
            _parts(),
            resolution=route.resolution,
            aspect_ratio=route.aspect_ratio,
        )
        self.assertEqual(request.payload["size"], "1280x3840")

    def test_edits_use_common_multipart_without_losing_references(self):
        for model in ("gpt-image-2", "gpt-image-2.5"):
            for count in (1, 2, 3):
                for endpoint in (
                    "",
                    "/v1",
                    "/v1/images/edits",
                    "/v1/images/generations",
                ):
                    with self.subTest(model=model, references=count, endpoint=endpoint):
                        parts = _parts(count)
                        request = openai.build_request(
                            _route(
                                model=model, api_url=f"https://api.scywl.cc{endpoint}"
                            ),
                            parts,
                            resolution="1K",
                            aspect_ratio="1:1",
                        )
                        self.assertEqual(
                            request.url, "https://api.scywl.cc/v1/images/edits"
                        )
                        self.assertIsNone(request.payload)
                        self.assertEqual(request.reference_image_count, count)
                        fields = request.form.fields
                        values = {
                            name: value for name, value, _ in fields if name != "image"
                        }
                        self.assertEqual(values["model"], model)
                        self.assertEqual(values["size"], "1024x1024")
                        self.assertEqual(values["n"], "1")
                        self.assertEqual(values["response_format"], "b64_json")
                        self.assertNotIn("Content-Type", request.headers)
                        self.assertEqual(
                            request.headers["Idempotency-Key"],
                            request.headers["X-Client-Request-ID"],
                        )
                        self.assertEqual(
                            [value for name, value, _ in fields if name == "image"],
                            [data for data, _ in openai.inline_images(parts)],
                        )
                        self.assertEqual(
                            [
                                options["content_type"]
                                for name, _, options in fields
                                if name == "image"
                            ],
                            [mime for _, mime in openai.inline_images(parts)],
                        )

    def test_unsupported_mime_is_rejected_for_every_reference(self):
        parts = _parts(2)
        parts[-1]["inlineData"]["mimeType"] = "image/gif"
        with self.assertRaisesRegex(ValueError, "PNG"):
            openai.build_request(_route(), parts, resolution="1K", aspect_ratio="1:1")

    async def test_generation_and_edit_save_image_through_configured_channel(self):
        for model in ("gpt-image-2", "gpt-image-2.5", "gpt-image-2.6"):
            for mode in ("text", "edit"):
                with (
                    self.subTest(model=model, mode=mode),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    settings = self._settings(mode=mode, model=model)
                    service = GeminiImageService(settings, Path(directory))
                    session = _Session()
                    service._get_session = AsyncMock(return_value=session)
                    if mode == "edit":
                        reference = Path(directory) / "reference.png"
                        reference.write_bytes(_image_bytes())
                        result = await service.edit_image(
                            "窗边的生活照",
                            str(reference),
                            resolution="4K",
                            aspect_ratio="1:1",
                        )
                    else:
                        result = await service.generate_image(
                            "窗边的生活照", resolution="4K", aspect_ratio="1:1"
                        )
                    self.assertEqual(result.path.read_bytes(), _image_bytes())
                    self.assertEqual(len(session.calls), 1)
                    sent = session.calls[0][1]
                    if mode == "edit":
                        fields = sent["data"].fields
                        self.assertEqual(
                            [value for name, value, _ in fields if name == "image"],
                            [reference.read_bytes()],
                        )
                        payload = {name: value for name, value, _ in fields}
                        self.assertNotIn("json", sent)
                    else:
                        payload = sent["json"]
                    self.assertEqual(payload["size"], "4096x4096")
                    self.assertFalse((service.output_dir / "tasks").exists())

    async def test_timeout_stops_failover_after_submission(self):
        for phase in ("connect", "body"):
            for count in (0, 1, 2):
                with (
                    self.subTest(phase=phase, references=count),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    mode = "edit" if count else "text"
                    service = GeminiImageService(
                        self._settings(mode=mode, backup=True), Path(directory)
                    )
                    session = _Session([_Response(error_phase=phase), _Response()])
                    service._get_session = AsyncMock(return_value=session)
                    with self.assertRaises(ImageTaskError) as caught:
                        await service._generate_image_result(
                            lambda *_, count=count: _parts(count),
                            prompt="窗边的生活照",
                            mode=mode,
                        )
                    self.assertEqual(len(session.calls), 1)
                    self.assertTrue(caught.exception.request_id)
                    self.assertFalse(caught.exception.task_id)

    async def test_explicit_failure_can_use_backup_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            service = GeminiImageService(self._settings(backup=True), Path(directory))
            session = _Session([_Response(status=400), _Response()])
            service._get_session = AsyncMock(return_value=session)
            result = await service.generate_image("窗边的生活照")
            self.assertTrue(result.path.exists())
            self.assertEqual(len(session.calls), 2)

    async def test_multiple_references_succeed_on_primary_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            service = GeminiImageService(
                self._settings(mode="edit", backup=True), Path(directory)
            )
            session = _Session()
            service._get_session = AsyncMock(return_value=session)
            path, route = await service._generate_image_result(
                lambda *_: _parts(2), prompt="合影", mode="edit"
            )
            self.assertTrue(path.exists())
            self.assertEqual(route.model, "gpt-image-2")
            self.assertEqual(len(session.calls), 1)
            fields = session.calls[0][1]["data"].fields
            self.assertEqual(sum(name == "image" for name, _, _ in fields), 2)

    async def test_references_survive_explicit_failure_and_failover(self):
        for count in (1, 2):
            with tempfile.TemporaryDirectory() as directory:
                service = GeminiImageService(
                    self._settings(mode="edit", backup=True), Path(directory)
                )
                session = _Session([_Response(status=400), _Response()])
                service._get_session = AsyncMock(return_value=session)
                parts = _parts(count)
                path, route = await service._generate_image_result(
                    lambda *_, parts=parts: parts, prompt="合影", mode="edit"
                )
                self.assertTrue(path.exists())
                self.assertEqual(route.model, "gpt-image-2.5-flare")
                self.assertEqual(len(session.calls), 2)
                self.assertEqual(
                    [
                        value
                        for name, value, _ in session.calls[0][1]["data"].fields
                        if name == "image"
                    ],
                    [data for data, _ in openai.inline_images(parts)],
                )
                self.assertEqual(
                    [row["image_url"] for row in session.calls[1][1]["json"]["images"]],
                    [
                        openai.image_data_url(data, mime)
                        for data, mime in openai.inline_images(parts)
                    ],
                )

    async def test_group_generation_uploads_scene_and_both_people(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            for color in ("red", "blue", "green"):
                reference = root / f"{color}.png"
                reference.write_bytes(_image_bytes(color))
                sources.append(reference)
            settings = self._settings(mode="edit")
            settings.character_reference_policy = "auto"
            settings.character_reference_images = [{"path": str(sources[1])}]
            settings.friend_reference_profiles = [
                {
                    "profile_id": "test-friend",
                    "display_name": "好友",
                    "reference_images": [{"path": str(sources[2])}],
                }
            ]
            service = GeminiImageService(settings, root)
            session = _Session()
            service._get_session = AsyncMock(return_value=session)
            result = await service.generate_group_image(
                "两个人在窗边合照", ["test-friend"], scene_reference=str(sources[0])
            )
            self.assertTrue(result.path.exists())
            self.assertEqual(len(session.calls), 1)
            fields = session.calls[0][1]["data"].fields
            self.assertEqual(
                [value for name, value, _ in fields if name == "image"],
                [source.read_bytes() for source in sources],
            )
            prompt = next(value for name, value, _ in fields if name == "prompt")
            self.assertIn("人物 A：当前角色", prompt)
            self.assertIn("人物 B：好友", prompt)

    async def test_edit_keeps_main_reference_and_character_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference, character = root / "reference.png", root / "character.png"
            reference.write_bytes(_image_bytes("red"))
            character.write_bytes(_image_bytes("blue"))
            settings = self._settings(mode="edit")
            settings.character_reference_policy = "auto"
            settings.character_reference_images = [{"path": str(character)}]
            service = GeminiImageService(settings, root)
            session = _Session()
            service._get_session = AsyncMock(return_value=session)
            result = await service.edit_image("人物在这个场景里", str(reference))
            self.assertTrue(result.path.exists())
            self.assertEqual(
                [
                    value
                    for name, value, _ in session.calls[0][1]["data"].fields
                    if name == "image"
                ],
                [reference.read_bytes(), character.read_bytes()],
            )

    @staticmethod
    def _settings(*, mode="text", model="gpt-image-2", backup=False):
        raw = {
            "__template_key": "openai",
            "api_url": "https://api.scywl.cc",
            "api_key": "test-key",
            "model": model,
        }
        channels = [raw]
        if backup:
            channels.append(
                {**raw, "model": "gpt-image-2.5-flare", "api_key": "backup-key"}
            )
        return LifeSettings.from_dict(
            {"image_generation_config": {"enabled": True, mode + "_channels": channels}}
        ).image_generation
