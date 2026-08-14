import unittest

import support  # noqa: F401 - 安装轻量级 AstrBot 测试替身
from core.runtime.capture.jsonclean import call_pure_json


class _Gateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_text_model(self, provider, prompt, session_id, **kwargs):
        self.calls.append((provider, prompt, session_id, kwargs))
        return self.responses.pop(0)


class JsonCleanTest(unittest.IsolatedAsyncioTestCase):
    async def test_pure_json_does_not_trigger_repair(self):
        gateway = _Gateway(['{"valid":true}'])

        payload = await call_pure_json(gateway, object(), "prompt", "session")

        self.assertEqual(payload, {"valid": True})
        self.assertEqual(len(gateway.calls), 1)

    async def test_malformed_json_is_repaired_once_in_same_session(self):
        gateway = _Gateway(['{"valid": tru', '{"valid":true}'])

        payload = await call_pure_json(
            gateway,
            object(),
            "prompt",
            "session",
            propagate_non_retryable=True,
        )

        self.assertEqual(payload, {"valid": True})
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(gateway.calls[1][2], "session")
        self.assertTrue(gateway.calls[1][3]["propagate_non_retryable"])
        self.assertIn('{"valid": tru', gateway.calls[1][1])

    async def test_parseable_payload_survives_failed_strict_repair(self):
        gateway = _Gateway(['```json\n{"valid":true}\n```', "not json"])

        payload = await call_pure_json(gateway, object(), "prompt", "session")

        self.assertEqual(payload, {"valid": True})
        self.assertEqual(len(gateway.calls), 2)


if __name__ == "__main__":
    unittest.main()
