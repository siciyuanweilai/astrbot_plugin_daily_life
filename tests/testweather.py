import unittest
from unittest.mock import AsyncMock

import support  # noqa: F401 - 安装轻量级 AstrBot 测试替身
from core.config.options import WeatherSettings
from core.life.tools import analyze_weather
from core.life.weather import WeatherClient


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self.payload


class _Session:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


class WeatherClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_failure_use_same_result_shape(self):
        client = WeatherClient(WeatherSettings())
        success_payload = {
            "code": 200,
            "data": {
                "location": {"city": "测试市"},
                "weather": {"condition": "晴", "temperature": 25},
                "life_indices": [],
                "air_quality": {"aqi": 20, "quality": "优"},
            },
        }
        client._get_session = AsyncMock(
            return_value=_Session(_Response(200, success_payload))
        )

        success = await client.get_weather("测试市", max_retries=1)

        self.assertTrue(success["ok"])
        self.assertEqual(success["error"], "")
        self.assertEqual(analyze_weather(success)["temp"], 25)

        client._get_session = AsyncMock(return_value=_Session(_Response(503, {})))
        failure = await client.get_weather("测试市", max_retries=1)

        self.assertFalse(failure["ok"])
        self.assertEqual(failure["message"], "天气查询失败")
        self.assertIsInstance(failure["data"], dict)
        self.assertEqual(analyze_weather(failure)["raw"], "天气查询失败")


if __name__ == "__main__":
    unittest.main()
