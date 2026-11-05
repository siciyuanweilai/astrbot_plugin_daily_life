# Test stubs must be installed before importing plugin modules.
# ruff: noqa: I001

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from support import PLUGIN_ROOT

from core.config.options import LifeSettings, MusicGenerationSettings
from core.media.music import (
    GeneratedMusic,
    MusicGenerationError,
    MusicGenerationService,
    _audio_extension,
    music_api_base,
    prompt_utf16_units,
)
from core.runtime.tooling import tool_argument_parameters, validate_tool_arguments
from runtimehelpers import Context, DailyLifeRuntime, Event, Provider

from astrbot.api.message_components import File
from astrbot.core.agent.tool import FunctionTool


class _Content:
    def __init__(self, body, chunk_size=5):
        self.body = body
        self.chunk_size = chunk_size
        self.position = 0

    async def read(self, size=-1):
        chunk = self.body[self.position : self.position + min(size, self.chunk_size)]
        self.position += len(chunk)
        return chunk


class _Response:
    def __init__(
        self,
        *,
        payload=None,
        body=b"",
        content_type="application/json",
        status=200,
        location="",
    ):
        self.status = status
        self.headers = {"Content-Type": content_type}
        if location:
            self.headers["Location"] = location
        self.payload = payload
        self.content = _Content(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        return self.payload


class _RequestContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return await self.response.__aenter__()

    async def __aexit__(self, *args):
        return await self.response.__aexit__(*args)


class _Session:
    def __init__(self, states=None, download=None, create_error=None):
        self.calls = []
        self.poll_count = 0
        self.states = list(states or ["uncertain", "success"])
        self.download = download
        self.create_error = create_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            if self.create_error:
                raise self.create_error
            return _RequestContext(_Response(payload={"task_id": "music_test_123"}))
        self.poll_count += 1
        state = self.states.pop(0)
        if isinstance(state, int):
            return _RequestContext(_Response(status=state))
        return _RequestContext(_Response(payload={"status": state}))

    def get(self, url, **kwargs):
        self.calls.append(("DOWNLOAD", url, kwargs))
        return _RequestContext(
            self.download or _Response(body=b"ID3test-audio", content_type="audio/mpeg")
        )


class MusicGenerationTest(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_task_resumes_in_same_scope_after_reload_without_post(
        self,
    ):
        class InterruptedSession(_Session):
            def request(self, method, url, **kwargs):
                if method == "GET":
                    self.calls.append((method, url, kwargs))
                    raise asyncio.CancelledError()
                return super().request(method, url, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            settings = MusicGenerationSettings(
                enabled=True, base_url="https://music.example", api_key="private-key"
            )
            service = MusicGenerationService(settings, Path(directory))
            original = InterruptedSession()
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=original),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await service.generate("private prompt", scope="session-a")
            self.assertEqual([call[0] for call in original.calls], ["POST", "GET"])
            reloaded = MusicGenerationService(settings, Path(directory))
            self.assertTrue(await reloaded.has_task_for_scope("session-a"))
            self.assertFalse(await reloaded.has_task_for_scope("session-b"))
            self.assertIsNone(await reloaded.latest_for_scope("session-a"))
            resumed = _Session(states=["success"])
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=resumed),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                result = await reloaded.resume_for_scope("session-a")
            self.assertEqual([call[0] for call in resumed.calls], ["GET", "DOWNLOAD"])
            self.assertEqual(result.task_id, "music_test_123")
            self.assertEqual(result.path.read_bytes(), b"ID3test-audio")
            self.assertEqual(await reloaded.latest_for_scope("session-a"), result)
            with patch("core.media.music.aiohttp.ClientSession") as network:
                self.assertEqual(await reloaded.resume_for_scope("session-a"), result)
                network.assert_not_called()
            for path in service.output_dir.glob("*.json"):
                self.assertNotIn("private", path.read_text())
                self.assertNotIn("session-a", path.read_text())

    async def test_download_failure_can_recover_the_original_task(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = MusicGenerationSettings(
                enabled=True, base_url="https://music.example", api_key="test-key"
            )
            service = MusicGenerationService(settings, Path(directory))
            failed = _Session(states=["success"], download=_Response(status=503))
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=failed),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                with self.assertRaisesRegex(MusicGenerationError, "下载失败"):
                    await service.generate("music", scope="session-a")
            resumed = _Session(states=["success"])
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=resumed),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                result = await MusicGenerationService(
                    settings, Path(directory)
                ).resume_for_scope("session-a")
            self.assertTrue(result.path.exists())
            self.assertEqual([call[0] for call in resumed.calls], ["GET", "DOWNLOAD"])

    async def test_unknown_creation_is_bound_to_scope_but_never_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(
                MusicGenerationSettings(
                    enabled=True, base_url="https://music.example", api_key="test-key"
                ),
                Path(directory),
            )
            with patch(
                "core.media.music.aiohttp.ClientSession",
                return_value=_Session(create_error=asyncio.TimeoutError()),
            ):
                with self.assertRaises(MusicGenerationError):
                    await service.generate("music", scope="session-a")
            self.assertTrue(await service.has_task_for_scope("session-a"))
            with patch("core.media.music.aiohttp.ClientSession") as network:
                with self.assertRaisesRegex(
                    MusicGenerationError, "没有可查询的任务编号"
                ):
                    await service.resume_for_scope("session-a")
                network.assert_not_called()

    async def test_recovery_rejects_cross_scope_reference_and_changed_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = MusicGenerationSettings(
                enabled=True, base_url="https://music.example", api_key="test-key"
            )
            service = MusicGenerationService(settings, Path(directory))
            with (
                patch(
                    "core.media.music.aiohttp.ClientSession",
                    return_value=_Session(
                        states=["success"], download=_Response(status=503)
                    ),
                ),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                with self.assertRaises(MusicGenerationError):
                    await service.generate("music", scope="session-a")
            service._scope_record("session-b").write_bytes(
                service._scope_record("session-a").read_bytes()
            )
            with patch("core.media.music.aiohttp.ClientSession") as network:
                self.assertFalse(await service.has_task_for_scope("session-b"))
                with self.assertRaisesRegex(MusicGenerationError, "没有可恢复"):
                    await service.resume_for_scope("session-b")
                settings.api_key = "changed-key"
                with self.assertRaisesRegex(MusicGenerationError, "密钥已变更"):
                    await service.resume_for_scope("session-a")
                settings.api_key = "test-key"
                settings.base_url = "https://other.example"
                with self.assertRaisesRegex(MusicGenerationError, "地址已变更"):
                    await service.resume_for_scope("session-a")
                network.assert_not_called()

    async def test_recent_music_is_scoped_and_survives_service_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(MusicGenerationSettings(), Path(directory))
            service.output_dir.mkdir(parents=True)
            path = service.output_dir / "music_example.mp3"
            path.write_bytes(b"ID3test-audio")
            music = GeneratedMusic(path, "task-local", "test-model")
            await service.remember_for_scope("platform:private:user-a", music)
            reloaded = MusicGenerationService(
                MusicGenerationSettings(), Path(directory)
            )
            self.assertEqual(
                await reloaded.latest_for_scope("platform:private:user-a"), music
            )
            self.assertIsNone(
                await reloaded.latest_for_scope("platform:private:user-b")
            )
            self.assertEqual(await reloaded.mp3_path(music), path)
            path.unlink()
            self.assertIsNone(
                await reloaded.latest_for_scope("platform:private:user-a")
            )

    async def test_recent_music_never_falls_back_to_global_files_or_external_paths(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(MusicGenerationSettings(), Path(directory))
            service.output_dir.mkdir(parents=True)
            external = Path(directory) / "music_private.mp3"
            external.write_bytes(b"ID3private")
            with self.assertRaises(MusicGenerationError):
                await service.remember_for_scope(
                    "session-a", GeneratedMusic(external, "task", "model")
                )
            (service.output_dir / "music_other_session.mp3").write_bytes(b"ID3other")
            self.assertIsNone(await service.latest_for_scope("session-a"))
            for filename in (str(external), "../../music_private.mp3"):
                service._scope_record("session-a").write_text(
                    json.dumps(
                        {
                            "file": filename,
                            "task_id": "task",
                            "model": "model",
                        }
                    )
                )
                self.assertIsNone(await service.latest_for_scope("session-a"))

    def test_music_tool_exposes_structured_action_and_delivery_choices(self):
        tool = FunctionTool(
            name="life_music_generate",
            description="音乐",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "delivery": {"type": "string"},
                },
            },
        )
        parameters = tool_argument_parameters(tool, include_preface=False)
        self.assertEqual(
            parameters["properties"]["action"]["enum"], ["generate", "resend"]
        )
        self.assertEqual(
            parameters["properties"]["delivery"]["enum"], ["voice", "file"]
        )
        self.assertIsNone(
            validate_tool_arguments(
                tool.name, parameters, {"action": "resend", "delivery": "file"}
            )
        )
        self.assertIsNotNone(
            validate_tool_arguments(tool.name, parameters, {"delivery": "unrecognized"})
        )

    async def test_mp3_conversion_timeout_stops_process_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(MusicGenerationSettings(), Path(directory))
            service.output_dir.mkdir(parents=True)
            source = service.output_dir / "music_original.wav"
            source.write_bytes(b"RIFF0000WAVEtest")
            process = SimpleNamespace(
                returncode=None,
                communicate=AsyncMock(side_effect=asyncio.TimeoutError()),
                kill=Mock(),
                wait=AsyncMock(),
            )
            with (
                patch("core.sight.ffmpeg.ffmpeg_executable", return_value="ffmpeg"),
                patch(
                    "core.media.music.asyncio.create_subprocess_exec",
                    new=AsyncMock(return_value=process),
                ),
            ):
                with self.assertRaisesRegex(MusicGenerationError, "超时"):
                    await service.mp3_path(GeneratedMusic(source, "task", "model"))
            process.kill.assert_called_once()
            process.wait.assert_awaited_once()
            self.assertEqual(source.read_bytes(), b"RIFF0000WAVEtest")
            self.assertEqual(list(service.output_dir.glob("*.part")), [])

    def test_music_config_has_an_independent_disabled_section(self):
        defaults = LifeSettings.from_dict({})
        self.assertFalse(defaults.music_generation.enabled)
        settings = LifeSettings.from_dict(
            {
                "music_generation_config": {
                    "enabled": True,
                    "base_url": "https://music.example",
                    "api_key": "local-test-key",
                    "model": "suno-v5.5",
                    "timeout_seconds": 5000,
                    "poll_interval_seconds": 0,
                }
            }
        ).music_generation
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.api_key, "local-test-key")
        self.assertEqual(settings.base_url, "https://music.example")
        self.assertEqual(settings.model, "suno-v5.5")
        self.assertEqual(settings.timeout_seconds, 1800)
        self.assertEqual(settings.poll_interval_seconds, 1.0)

    def test_music_api_address_accepts_root_version_and_creation_path(self):
        for raw in [
            "https://music.example",
            "https://music.example/",
            "https://music.example/v1/",
            "https://music.example/v1/music/generations",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(music_api_base(raw), "https://music.example/v1")
        self.assertEqual(
            music_api_base("https://music.example/proxy/v1/music/generations/"),
            "https://music.example/proxy/v1",
        )
        self.assertEqual(
            music_api_base("http://localhost:8000/proxy"),
            "http://localhost:8000/proxy/v1",
        )
        self.assertEqual(music_api_base(""), "")
        for raw in [
            "music.example",
            "file:///tmp/music",
            "https://user:secret@music.example",
            "https://music.example?key=secret",
        ]:
            with self.subTest(raw=raw), self.assertRaises(MusicGenerationError):
                music_api_base(raw)

    async def test_missing_interface_address_cannot_create_a_task(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(
                MusicGenerationSettings(enabled=True, api_key="test-key"),
                Path(directory),
            )
            with patch("core.media.music.aiohttp.ClientSession") as session:
                with self.assertRaisesRegex(MusicGenerationError, "接口地址"):
                    await service.generate("纯音乐")
                session.assert_not_called()

    def test_prompt_length_uses_utf16_units_and_audio_format_is_detected(self):
        self.assertEqual(prompt_utf16_units("a😀"), 3)
        self.assertEqual(_audio_extension(b"ID3x", "application/octet-stream"), ".mp3")
        self.assertEqual(
            _audio_extension(b"RIFF0000WAVE", "application/octet-stream"), ".wav"
        )

    async def test_custom_model_name_is_preserved_and_sent_to_the_api(self):
        settings = MusicGenerationSettings.from_dict(
            {
                "enabled": True,
                "base_url": "https://music.example",
                "api_key": "test-key",
                "model": "  custom-music-model  ",
            }
        )
        self.assertEqual(settings.model, "custom-music-model")
        self.assertEqual(
            MusicGenerationSettings.from_dict({"model": "  "}).model, "suno-v6"
        )
        session = _Session(states=["success"])
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(settings, Path(directory))
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=session),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                generated = await service.generate("纯音乐")
            self.assertEqual(generated.model, "custom-music-model")
            self.assertEqual(session.calls[0][2]["json"]["model"], "custom-music-model")

    async def test_create_poll_and_download_reuses_one_task_and_auth(self):
        session = _Session()
        settings = MusicGenerationSettings.from_dict(
            {
                "enabled": True,
                "base_url": "https://music.example",
                "api_key": "local-test-key",
                "model": "suno-v6",
                "timeout_seconds": 60,
                "request_timeout_seconds": 10,
                "poll_interval_seconds": 1,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(settings, Path(directory))
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=session),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                generated = await service.generate(
                    "温暖的钢琴纯音乐", instrumental=True
                )

            self.assertIsInstance(generated, GeneratedMusic)
            self.assertTrue(generated.path.is_file())
            self.assertEqual(generated.path.read_bytes(), b"ID3test-audio")
            self.assertEqual(generated.path.suffix, ".mp3")
            posts = [call for call in session.calls if call[0] == "POST"]
            polls = [call for call in session.calls if call[0] == "GET"]
            self.assertEqual(len(posts), 1)
            self.assertEqual(len(polls), 2)
            self.assertTrue(all("music_test_123" in call[1] for call in polls))
            self.assertEqual(posts[0][1], "https://music.example/v1/music/generations")
            self.assertTrue(
                all(
                    call[1] == "https://music.example/v1/music/results/music_test_123"
                    for call in polls
                )
            )
            self.assertEqual(
                session.calls[-1][1],
                "https://music.example/v1/tasks/music_test_123/artifacts/audio/content",
            )
            post_args = posts[0][2]
            self.assertEqual(post_args["json"]["n"], 1)
            self.assertTrue(post_args["json"]["instrumental"])
            self.assertEqual(post_args["headers"]["Idempotency-Key"].count("-"), 4)
            self.assertEqual(
                post_args["headers"]["Authorization"], "Bearer local-test-key"
            )
            task_records = list(
                (Path(directory) / "generated" / "music").glob("task_*.json")
            )
            self.assertEqual(len(task_records), 1)
            record = json.loads(task_records[0].read_text())
            self.assertEqual(record["status"], "success")
            self.assertEqual(record["api_base"], "https://music.example/v1")
            self.assertEqual(record["file"], generated.path.name)
            self.assertEqual(
                record["idempotency_key"], post_args["headers"]["Idempotency-Key"]
            )
            self.assertNotIn("local-test-key", task_records[0].read_text())

    async def test_transient_poll_errors_retry_the_original_task_only(self):
        session = _Session(states=[503, 429, "uncertain", "success"])
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(
                MusicGenerationSettings(
                    enabled=True, base_url="https://music.example", api_key="test-key"
                ),
                Path(directory),
            )
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=session),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                result = await service.generate("纯音乐")
            self.assertEqual(result.path.read_bytes(), b"ID3test-audio")
            self.assertEqual(
                [x[0] for x in session.calls],
                ["POST", "GET", "GET", "GET", "GET", "DOWNLOAD"],
            )

    async def test_uncertain_create_saves_idempotency_without_resubmitting(self):
        session = _Session(create_error=asyncio.TimeoutError())
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(
                MusicGenerationSettings(
                    enabled=True,
                    base_url="https://music.example",
                    api_key="private-test-key",
                ),
                Path(directory),
            )
            with patch("core.media.music.aiohttp.ClientSession", return_value=session):
                with self.assertRaisesRegex(MusicGenerationError, "不要再次提交"):
                    await service.generate("private prompt")
            self.assertEqual(len(session.calls), 1)
            records = list(service.output_dir.glob("task_*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text())
            self.assertEqual(record["status"], "uncertain")
            self.assertEqual(
                record["idempotency_key"],
                session.calls[0][2]["headers"]["Idempotency-Key"],
            )
            self.assertNotIn("private", records[0].read_text())

    async def test_task_deadline_prevents_poll_after_sleep(self):
        session = _Session()
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(
                MusicGenerationSettings(
                    enabled=True,
                    base_url="https://music.example",
                    api_key="test-key",
                    timeout_seconds=60,
                ),
                Path(directory),
            )
            clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 61, 61]))
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=session),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
                patch("core.media.music.time", clock),
            ):
                with self.assertRaisesRegex(MusicGenerationError, "music_test_123"):
                    await service.generate("纯音乐")
            self.assertEqual([x[0] for x in session.calls], ["POST"])
            record = json.loads(
                next(service.output_dir.glob("task_*.json")).read_text()
            )
            self.assertEqual(record["status"], "timeout")

    async def test_failed_task_does_not_download_or_recreate(self):
        session = _Session(states=["failed"])
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(
                MusicGenerationSettings(
                    enabled=True, base_url="https://music.example", api_key="test-key"
                ),
                Path(directory),
            )
            with (
                patch("core.media.music.aiohttp.ClientSession", return_value=session),
                patch("core.media.music.asyncio.sleep", new=AsyncMock()),
            ):
                with self.assertRaisesRegex(MusicGenerationError, "任务失败"):
                    await service.generate("纯音乐")
            self.assertEqual([x[0] for x in session.calls], ["POST", "GET"])

    async def test_download_rejects_oversize_and_non_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(MusicGenerationSettings(), Path(directory))
            for response, expected in [
                (_Response(body=b"ID3" + b"a" * 30, content_type="audio/mpeg"), "超过"),
                (
                    _Response(body=b"<html>error</html>", content_type="text/html"),
                    "不是",
                ),
            ]:
                with (
                    self.subTest(expected=expected),
                    patch("core.media.music.MUSIC_MAX_AUDIO_BYTES", 20),
                ):
                    with self.assertRaisesRegex(MusicGenerationError, expected):
                        await service._download_audio(
                            _Session(download=response),
                            "https://music.example/content",
                            {"Authorization": "Bearer test-key"},
                            timeout_seconds=10,
                        )

    async def test_download_does_not_forward_auth_to_another_origin(self):
        session = _Session(
            download=_Response(status=302, location="https://other.example/audio.mp3")
        )
        with tempfile.TemporaryDirectory() as directory:
            service = MusicGenerationService(MusicGenerationSettings(), Path(directory))
            with self.assertRaisesRegex(MusicGenerationError, "地址无效"):
                await service._download_audio(
                    session,
                    "https://music.example/content",
                    {"Authorization": "Bearer test-key"},
                    timeout_seconds=10,
                )
            self.assertEqual(len(session.calls), 1)

    def test_schema_adds_a_music_page(self):
        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text())
        self.assertIn("music_generation_config", schema)
        self.assertEqual(
            schema["music_generation_config"]["items"]["enabled"]["default"], False
        )
        model = schema["music_generation_config"]["items"]["model"]
        self.assertEqual(model["type"], "string")
        self.assertEqual(model["description"], "模型名称")
        self.assertEqual(model["hint"], "填写音乐接口支持的模型名称。")
        self.assertNotIn("options", model)
        address = schema["music_generation_config"]["items"]["base_url"]
        self.assertEqual(address["type"], "string")
        self.assertEqual(address["description"], "接口地址")
        settings_ui = (PLUGIN_ROOT / "pages/dashboard/ui/settings.js").read_text()
        order = settings_ui.split("const CONFIG_SECTION_ORDER = [", 1)[1].split("]", 1)[
            0
        ]
        self.assertIn('"music_generation_config"', order)
        self.assertLess(
            order.index('"video_generation_config"'),
            order.index('"music_generation_config"'),
        )


class MusicRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "music.mp3"
        self.path.write_bytes(b"ID3test-audio")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "music_generation_config": {
                    "enabled": True,
                    "base_url": "https://music.example",
                    "api_key": "test-key",
                },
            }
        )
        runtime.context = Context(Provider([]))
        runtime.media = SimpleNamespace(
            music=SimpleNamespace(
                generate=AsyncMock(
                    return_value=GeneratedMusic(self.path, "music_test_123", "suno-v6")
                ),
                remember_for_scope=AsyncMock(),
                latest_for_scope=AsyncMock(return_value=None),
                has_task_for_scope=AsyncMock(return_value=False),
                resume_for_scope=AsyncMock(
                    return_value=GeneratedMusic(self.path, "music_test_123", "suno-v6")
                ),
                mp3_path=AsyncMock(return_value=self.path),
            )
        )
        runtime.can_send_for_source = Mock(return_value=True)
        runtime.send_message_if_not_recalled = AsyncMock(return_value=True)
        runtime.note_structured_bot_message = Mock()
        runtime.finish_tool_reaction = AsyncMock()
        runtime.cancel_tool_reaction = Mock()
        runtime._release_media_chat_turn = Mock()
        runtime._send_background_failure_reply = AsyncMock()
        self.scheduled = []

        def schedule(coro, **kwargs):
            self.scheduled.append(coro)
            return True

        runtime._schedule_background_task = schedule
        self.runtime = runtime
        self.event = Event(message_id="music-request-1")
        self.addCleanup(self._close_unrun_tasks)

    def _close_unrun_tasks(self):
        for coro in self.scheduled:
            coro.close()

    async def test_background_delivery_uses_audio_and_deduplicates_one_request(self):
        pending = json.loads(
            await self.runtime.life_music_generate(
                self.event, "雨夜钢琴", instrumental=True
            )
        )
        self.assertEqual(pending["status"], "pending")
        self.runtime.media.music.generate.assert_not_awaited()
        repeated = json.loads(
            await self.runtime.life_music_generate(
                self.event, "雨夜钢琴", instrumental=True
            )
        )
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual(len(self.scheduled), 1)
        await self.scheduled.pop()
        self.runtime.media.music.generate.assert_awaited_once_with(
            "雨夜钢琴", instrumental=True, scope=self.event.unified_msg_origin
        )
        self.runtime.send_message_if_not_recalled.assert_awaited_once()
        chain = self.runtime.send_message_if_not_recalled.call_args.args[1]
        self.assertIn({"type": "record", "file": str(self.path)}, chain.items)
        self.assertEqual(
            self.event._daily_life_background_tool_requests["life_music_generate"][
                "status"
            ],
            "sent",
        )
        self.runtime._send_background_failure_reply.assert_not_awaited()

    async def test_file_delivery_uses_mp3_attachment(self):
        await self.runtime.life_music_generate(self.event, "雨夜钢琴", delivery="file")
        await self.scheduled.pop()
        chain = self.runtime.send_message_if_not_recalled.call_args.args[1]
        self.assertEqual(len(chain.chain), 1)
        self.assertIsInstance(chain.chain[0], File)
        self.assertEqual(chain.chain[0].file, str(self.path))
        self.assertEqual(chain.chain[0].name, "音乐.mp3")
        self.runtime.media.music.mp3_path.assert_awaited_once()
        self.runtime.media.music.remember_for_scope.assert_awaited_once()
        self.assertIn(
            "音乐文件", self.runtime.note_structured_bot_message.call_args.args[1]
        )

    async def test_resend_reuses_current_scope_without_generation_or_credentials(self):
        music = GeneratedMusic(self.path, "music_test_123", "suno-v6")
        self.runtime.media.music.latest_for_scope.return_value = music
        self.runtime.config.music_generation.api_key = ""
        self.runtime.config.music_generation.base_url = ""
        result = await self.runtime.life_music_generate(
            self.event, action="resend", delivery="file"
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        await self.scheduled.pop()
        self.runtime.media.music.latest_for_scope.assert_awaited_once_with(
            self.event.unified_msg_origin
        )
        self.runtime.media.music.generate.assert_not_awaited()
        self.runtime.media.music.remember_for_scope.assert_not_awaited()
        self.assertIsInstance(
            self.runtime.send_message_if_not_recalled.call_args.args[1].chain[0], File
        )

    async def test_missing_recent_song_does_not_start_a_new_generation(self):
        result = await self.runtime.life_music_generate(
            self.event, action="resend", delivery="file"
        )
        self.assertIn("没有可补发", result)
        self.runtime.media.music.generate.assert_not_awaited()
        self.assertEqual(self.scheduled, [])

    async def test_resend_recovers_pending_task_in_background_without_new_generation(
        self,
    ):
        self.runtime.media.music.has_task_for_scope.return_value = True
        result = await self.runtime.life_music_generate(
            self.event, action="resend", delivery="file"
        )
        self.assertEqual(json.loads(result)["status"], "pending")
        self.runtime.media.music.resume_for_scope.assert_not_awaited()
        await self.scheduled.pop()
        self.runtime.media.music.resume_for_scope.assert_awaited_once_with(
            self.event.unified_msg_origin
        )
        self.runtime.media.music.generate.assert_not_awaited()
        self.runtime.send_message_if_not_recalled.assert_awaited_once()
        self.assertIsInstance(
            self.runtime.send_message_if_not_recalled.call_args.args[1].chain[0], File
        )

    async def test_rejected_attachment_reports_failure_without_voice_fallback(self):
        self.runtime.send_message_if_not_recalled.side_effect = RuntimeError(
            "attachment rejected"
        )
        await self.runtime.life_music_generate(self.event, "雨夜钢琴", delivery="file")
        await self.scheduled.pop()
        self.runtime.send_message_if_not_recalled.assert_awaited_once()
        self.assertTrue(
            self.runtime.send_message_if_not_recalled.call_args.kwargs[
                "raise_delivery_errors"
            ]
        )
        self.runtime._send_background_failure_reply.assert_awaited_once()
        self.assertEqual(
            self.event._daily_life_background_tool_requests["life_music_generate"][
                "status"
            ],
            "failed",
        )

    async def test_invalid_structured_options_do_not_schedule_work(self):
        self.assertIn(
            "发送方式",
            await self.runtime.life_music_generate(
                self.event, "纯音乐", delivery="invalid"
            ),
        )
        self.assertIn(
            "音乐操作",
            await self.runtime.life_music_generate(self.event, action="invalid"),
        )
        self.assertEqual(self.scheduled, [])

    async def test_recalled_request_does_not_send_generated_audio(self):
        async def generate(*args, **kwargs):
            self.runtime.can_send_for_source.return_value = False
            return GeneratedMusic(self.path, "music_test_123", "suno-v6")

        self.runtime.media.music.generate.side_effect = generate
        await self.runtime.life_music_generate(self.event, "雨夜钢琴")
        await self.scheduled.pop()
        self.runtime.send_message_if_not_recalled.assert_not_awaited()
        self.assertEqual(
            self.event._daily_life_background_tool_requests["life_music_generate"][
                "status"
            ],
            "cancelled",
        )

    async def test_failed_task_reports_failure_without_false_delivery(self):
        self.runtime.media.music.generate.side_effect = MusicGenerationError(
            "音乐生成任务失败"
        )
        await self.runtime.life_music_generate(self.event, "雨夜钢琴")
        await self.scheduled.pop()
        self.runtime.send_message_if_not_recalled.assert_not_awaited()
        self.runtime._send_background_failure_reply.assert_awaited_once()
        self.assertEqual(
            self.event._daily_life_background_tool_requests["life_music_generate"][
                "status"
            ],
            "failed",
        )

    async def test_recording_failure_does_not_report_a_delivered_song_as_failed(self):
        self.runtime.note_structured_bot_message.side_effect = RuntimeError(
            "record unavailable"
        )
        await self.runtime.life_music_generate(self.event, "雨夜钢琴")
        await self.scheduled.pop()
        self.runtime.send_message_if_not_recalled.assert_awaited_once()
        self.runtime._send_background_failure_reply.assert_not_awaited()
        self.assertEqual(
            self.event._daily_life_background_tool_requests["life_music_generate"][
                "status"
            ],
            "sent",
        )

    async def test_disabled_and_invalid_requests_do_not_schedule_work(self):
        self.runtime.config.music_generation.enabled = False
        self.assertIn(
            "尚未启用", await self.runtime.life_music_generate(self.event, "雨夜钢琴")
        )
        self.runtime.config.music_generation.enabled = True
        self.runtime.config.music_generation.api_key = ""
        self.assertIn(
            "密钥", await self.runtime.life_music_generate(self.event, "雨夜钢琴")
        )
        self.runtime.config.music_generation.api_key = "test-key"
        self.runtime.config.music_generation.base_url = ""
        self.assertIn(
            "接口地址", await self.runtime.life_music_generate(self.event, "雨夜钢琴")
        )
        self.assertIn(
            "3000", await self.runtime.life_music_generate(self.event, "😀" * 1501)
        )
        self.assertIn("描述", await self.runtime.life_music_generate(self.event, ""))
        self.assertEqual(self.scheduled, [])
