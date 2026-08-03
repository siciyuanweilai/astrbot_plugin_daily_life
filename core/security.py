"""供外部媒体和研究输入共用的轻量安全判断。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


def is_public_http_url(value: object) -> bool:
    """判断值是否为普通公网 HTTP(S) 地址。"""

    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
        hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password or not hostname:
        return False
    if hostname in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        return False
    if hostname.endswith((".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    return True


async def is_public_http_url_async(value: object) -> bool:
    """先执行静态检查，再拒绝解析到内网地址的域名。"""

    if not is_public_http_url(value):
        return False
    parsed = urlparse(str(value or "").strip())
    hostname = str(parsed.hostname or "").strip()
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except OSError:
        # 解析失败时拒绝请求，避免把未确认的域名交给外部网络客户端。
        return False
    if not infos:
        return False
    for info in infos:
        address_text = str(info[4][0] if len(info) > 4 else "").strip()
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
        ):
            return False
    return True
