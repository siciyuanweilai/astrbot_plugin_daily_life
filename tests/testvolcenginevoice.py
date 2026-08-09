import base64
import json
import tempfile
import unittest
from pathlib import Path

from core.config.options import LifeSettings
from core.media.volcengine import VolcengineVoiceService


class _Response:
    status = 200

    def __init__(self, chunks):
        self.content = _Stream(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return ""


class _Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class _Session:
    closed = False

    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, *, headers=None, json=None):
        self.calls.append((url, headers, json))
        return self.response


class VolcengineVoiceTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _settings(source="cloned"):
        return LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "api_key": "volc-key",
                    "speaker_source": source,
                    "speaker_id": "speaker-test",
                    "speech_rate": 15,
                    "loudness_rate": -5,
                }
            }
        ).voice_generation

    async def test_clone_payload_and_sse_audio(self):
        audio = b"fake-mp3"
        lines = [
            b"data: "
            + json.dumps({"code": 0, "data": base64.b64encode(audio).decode()}).encode()
            + b"\n",
            b'data: {"code":20000000}\n',
        ]
        session = _Session(_Response(lines))
        service = VolcengineVoiceService(self._settings(), Path(tempfile.mkdtemp()))

        async def get_session():
            return session

        service._get_session = get_session
        generated = await service.synthesize("你好", emotion_category="happy")

        self.assertEqual(generated.path.read_bytes(), audio)
        _, headers, payload = session.calls[0]
        self.assertEqual(headers["X-Api-Key"], "volc-key")
        self.assertEqual(headers["X-Api-Resource-Id"], "seed-icl-2.0")
        req_params = payload["req_params"]
        self.assertEqual(req_params["model"], "seed-tts-2.0-standard")
        self.assertEqual(req_params["speaker"], "speaker-test")
        self.assertNotIn("context_texts", json.loads(req_params["additions"]))
        self.assertEqual(req_params["audio_params"]["speech_rate"], 25)
        self.assertEqual(req_params["audio_params"]["loudness_rate"], 1)
        self.assertNotIn("pitch", req_params["audio_params"])
        self.assertEqual(json.loads(req_params["additions"])["post_process"]["pitch"], 2)
        self.assertTrue(headers["X-Api-Request-Id"])
        self.assertNotIn("X-Api-Connect-Id", headers)

    async def test_preset_payload_uses_tts_resource_without_clone_model(self):
        session = _Session(
            _Response(
                [
                    b"data: "
                    + json.dumps(
                        {"code": 0, "data": base64.b64encode(b"audio").decode()}
                    ).encode()
                    + b"\n"
                ]
            )
        )
        service = VolcengineVoiceService(
            self._settings("preset"), Path(tempfile.mkdtemp())
        )

        async def get_session():
            return session

        service._get_session = get_session
        await service.synthesize("你好", emotion="轻松", voice_style="light")
        _, headers, payload = session.calls[0]
        self.assertEqual(headers["X-Api-Resource-Id"], "seed-tts-2.0")
        self.assertNotIn("model", payload["req_params"])
        additions = json.loads(payload["req_params"]["additions"])
        self.assertEqual(additions["context_texts"], [
            "请用轻松自然的语气自然地说出这句话，不要改变文字内容。"
        ])

    async def test_emotion_text_does_not_select_voice_style(self):
        service = VolcengineVoiceService(
            self._settings("preset"), Path(tempfile.mkdtemp())
        )
        route = service._voice_route("委屈", "happy")
        self.assertEqual(route["style_key"], "happy")
        route = service._voice_route("任意自然描述", "neutral", "sad")
        self.assertEqual(route["style_key"], "sad")

    async def test_missing_speaker_is_reported_before_network_request(self):
        settings = self._settings()
        settings.speaker_id = ""
        service = VolcengineVoiceService(settings, Path(tempfile.mkdtemp()))
        with self.assertRaisesRegex(RuntimeError, "缺少音色 ID"):
            await service.synthesize("你好")

    def test_long_text_splits_at_utf8_limit_and_punctuation(self):
        text = "第一句很长。" * 120
        parts = VolcengineVoiceService._split_text(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part.encode("utf-8")) <= 900 for part in parts))
        self.assertTrue(all(part.endswith("。") for part in parts[:-1]))
        self.assertEqual("".join(parts), text)

    async def test_long_text_is_synthesized_and_joined(self):
        settings = self._settings("preset")
        service = VolcengineVoiceService(settings, Path(tempfile.mkdtemp()))
        text = "第一句很长。" * 120
        requests = []

        async def request_audio(text_part, _route):
            requests.append(text_part)
            return text_part.encode("utf-8")

        service._request_audio_with_retry = request_audio
        service._join_audio_parts = lambda parts: b"|".join(parts)
        generated = await service.synthesize(text, voice_style="light")
        self.assertGreater(len(requests), 1)
        self.assertEqual(generated.path.read_bytes(), b"|".join(
            part.encode("utf-8") for part in requests
        ))

    async def test_cloned_voice_does_not_reuse_local_cache_after_console_retraining(self):
        service = VolcengineVoiceService(self._settings(), Path(tempfile.mkdtemp()))
        calls = 0

        async def request_audio(_text, _route):
            nonlocal calls
            calls += 1
            return f"audio-{calls}".encode("ascii")

        service._request_audio_with_retry = request_audio
        first = await service.synthesize("同一句")
        second = await service.synthesize("同一句")
        self.assertEqual(calls, 2)
        self.assertNotEqual(first.path.read_bytes(), b"audio-1")
        self.assertEqual(second.path.read_bytes(), b"audio-2")


if __name__ == "__main__":
    unittest.main()
