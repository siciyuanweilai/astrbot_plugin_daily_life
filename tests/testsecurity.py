import unittest
from unittest.mock import patch

from core.security import is_http_url_allowed_async, is_public_http_url


class MediaNetworkPolicyTest(unittest.IsolatedAsyncioTestCase):
    def test_static_public_url_check(self):
        self.assertTrue(is_public_http_url("https://media.example.com/image.png"))
        self.assertFalse(is_public_http_url("http://127.0.0.1/image.png"))
        self.assertFalse(is_public_http_url("file:///tmp/image.png"))

    async def test_dns_private_address_is_rejected(self):
        with patch(
            "core.security.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.10.4", 0))],
        ):
            self.assertFalse(
                await is_http_url_allowed_async("https://media.example.com/a.png")
            )

    async def test_common_proxy_fake_ip_is_allowed_automatically(self):
        with patch(
            "core.security.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("198.18.0.8", 0))],
        ):
            self.assertTrue(
                await is_http_url_allowed_async("https://media.example.com/a.mp4")
            )
