"""媒体输入地址的网络访问策略。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google.com",
}


def _parse_url(value: object):
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
        hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return None, ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, ""
    if parsed.username or parsed.password or not hostname:
        return None, ""
    return parsed, hostname


def _is_blocked_address(address: object) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )


def is_public_http_url(value: object) -> bool:
    """执行不依赖 DNS 的公网媒体地址检查。"""

    parsed, hostname = _parse_url(value)
    if parsed is None:
        return False
    if hostname in _BLOCKED_HOSTS or hostname.endswith((".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not _is_blocked_address(address)


async def is_http_url_allowed_async(value: object) -> bool:
    """检查媒体 URL，并在请求前校验域名解析结果。"""

    parsed, hostname = _parse_url(value)
    if parsed is None:
        return False
    addresses: set[object] = set()
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        addresses.add(literal)
    else:
        if hostname in _BLOCKED_HOSTS or hostname.endswith((".local", ".internal")):
            return False
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
        except OSError:
            return False
        for info in infos:
            address_text = str(info[4][0] if len(info) > 4 else "").strip()
            try:
                addresses.add(ipaddress.ip_address(address_text))
            except ValueError:
                continue
    if not addresses:
        return False
    for address in addresses:
        if not _is_blocked_address(address):
            continue
        if address in _FAKE_IP_NETWORK:
            continue
        return False
    return True


async def is_public_http_url_async(value: object) -> bool:
    """兼容旧调用方的公网地址检查。"""

    return await is_http_url_allowed_async(value)
