"""把本地媒体地址转换为宿主路径；解析本身不访问文件系统。"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit
from urllib.request import url2pathname


def local_media_path(source: str) -> Path | None:
    text = str(source or "").strip()
    if not text:
        return None
    # 先识别盘符与 UNC，不能把 C: 当作网络协议，也不能解码原始文件名中的 %。
    if PureWindowsPath(text).drive:
        return Path(text).expanduser()
    try:
        parsed = urlsplit(text)
        if parsed.scheme == "file":
            authority = parsed.netloc
            path = parsed.path
            if authority and authority.lower() != "localhost":
                path = f"//{authority}{path}"
            return Path(url2pathname(path)).expanduser() if path else None
        if parsed.scheme:
            return None
    except (OSError, ValueError):
        return None
    return Path(text).expanduser()
