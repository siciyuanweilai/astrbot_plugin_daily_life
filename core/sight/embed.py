from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlparse


IMAGE_SOURCE_LIMIT = 16 * 1024 * 1024
IMAGE_DATA_LIMIT = 650 * 1024
IMAGE_DATA_TOTAL_LIMIT = 8 * 1024 * 1024
IMAGE_MAX_SIDE = 960
IMAGE_QUALITY = 82


def embed_local_markdown_images(markdown: str) -> str:
    text = str(markdown or "")
    if "![" not in text:
        return text
    output: list[str] = []
    index = 0
    total = 0
    while index < len(text):
        start = text.find("![", index)
        if start < 0:
            output.append(text[index:])
            break
        label_end = text.find("]", start + 2)
        if label_end < 0 or label_end + 1 >= len(text) or text[label_end + 1] != "(":
            output.append(text[index : start + 2])
            index = start + 2
            continue
        target_end = _target_end(text, label_end + 2)
        if target_end < 0:
            output.append(text[index:])
            break
        target = text[label_end + 2 : target_end].strip()
        embedded, size = _embedded_target(target, remaining=IMAGE_DATA_TOTAL_LIMIT - total)
        output.append(text[index : label_end + 2])
        output.append(embedded or target)
        output.append(")")
        if embedded:
            total += size
        index = target_end + 1
    return "".join(output)


def _target_end(text: str, start: int) -> int:
    escaped = False
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            if depth <= 0:
                return index
            depth -= 1
    return -1


def _embedded_target(target: str, *, remaining: int) -> tuple[str, int]:
    path = _local_image_path(target)
    if path is None or remaining <= 0:
        return "", 0
    try:
        size = path.stat().st_size
    except OSError:
        return "", 0
    if size <= 0 or size > IMAGE_SOURCE_LIMIT:
        return "", 0
    try:
        data = path.read_bytes()
    except OSError:
        return "", 0
    data, mime = _renderable_image_data(data)
    size = len(data)
    if size <= 0 or size > min(IMAGE_DATA_LIMIT, remaining):
        return "", 0
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}", size


def _renderable_image_data(data: bytes) -> tuple[bytes, str]:
    try:
        from PIL import Image, ImageOps

        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=IMAGE_QUALITY, optimize=True)
            rendered = output.getvalue()
            if rendered:
                return rendered, "image/jpeg"
    except Exception:
        pass
    return data, _image_mime(data)


def _local_image_path(target: str) -> Path | None:
    value = _strip_markdown_target(target)
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "data"}:
        return None
    if parsed.scheme == "file":
        value = unquote(parsed.path)
        if parsed.netloc and value:
            value = f"//{parsed.netloc}{value}"
    elif parsed.scheme and not _looks_like_windows_path(value):
        return None
    path = Path(value)
    return path if path.is_file() else None


def _strip_markdown_target(target: str) -> str:
    value = str(target or "").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    return value.replace(r"\)", ")").replace(r"\(", "(")


def _looks_like_windows_path(value: str) -> bool:
    return len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"} and value[0].isalpha()


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"
