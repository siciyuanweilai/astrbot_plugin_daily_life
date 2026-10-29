# ruff: noqa: I001

import base64
import json
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

from support import LifeSettings, PLUGIN_ROOT
from PIL import Image

from core.config.options.generate import IMAGE_ASPECT_RATIOS
from core.media import GeminiImageService
from core.media.picture import openai
from core.media.picture.pipe import ImageRoute
from core.media.picture.polling import ImageTaskError


def _image_bytes(color="blue"):
    output = BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
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
    def __init__(self, *, status=200, error_phase=""):
        self.status = status
        self.error_phase = error_phase

    async def __aenter__(self):
        if self.error_phase == "connect":
            raise TimeoutError()
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if self.error_phase == "body":
            raise TimeoutError()
        return {
            "data": [{"b64_json": base64.b64encode(_image_bytes()).decode("ascii")}]
        }

    async def text(self):
        return "接口暂时不可用"


class _Session:
    closed = False

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [_Response()])

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class ScywlImageTest(unittest.IsolatedAsyncioTestCase):
    def test_existing_gpt_template_loads_documented_models(self):
        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text())
        for mode in ("text_channels", "edit_channels"):
            spec = schema["image_generation_config"]["items"][mode]["templates"][
                "openai"
            ]
            raw = {key: value["default"] for key, value in spec["items"].items()}
            for model in ("gpt-image-2", "gpt-image-2.5"):
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
        for model in ("gpt-image-2", "gpt-image-2.5"):
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
                            lambda *_, count=count: _parts(count), prompt="窗边的生活照", mode=mode
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
