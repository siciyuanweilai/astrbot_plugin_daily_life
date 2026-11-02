from __future__ import annotations

import base64
import math
import time
from io import BytesIO

from PIL import Image, ImageDraw

from .filepath import local_media_path


MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_PIXELS = 4 * 1024 * 1024
MAX_DECODED_PIXELS = 96 * 1024 * 1024
MAX_SOURCE_FRAMES = 240
MAX_SAMPLE_FRAMES = 8
MAX_TILE_SIDE = 384
DECODE_TIMEOUT_SECONDS = 3.0


def is_animated_gif(source: str) -> bool:
    """按文件内容确认 GIF 是否有第二帧，不根据文件名或平台标签猜测。"""
    data = _gif_data(str(source or "").strip())
    if data is None:
        return False
    with Image.open(BytesIO(data)) as animation:
        if animation.format != "GIF":
            return False
        width, height = animation.size
        if width * height <= 0 or width * height > MAX_SOURCE_PIXELS:
            raise ValueError("GIF 画面尺寸超出识别限制")
        try:
            animation.seek(1)
        except EOFError:
            return False
        return True


def prepare_animated_image(
    source: str, *, sample_frames: int = MAX_SAMPLE_FRAMES
) -> tuple[str, str]:
    """多帧 GIF 转为顺序采样图，单帧 GIF 转为静态 PNG，其余保持原来源。"""
    source = str(source or "").strip()
    started = time.monotonic()
    data = _gif_data(source)
    if data is None:
        return source, ""
    if not data.startswith((b"GIF87a", b"GIF89a")):
        raise ValueError("GIF 文件内容无效")

    with Image.open(BytesIO(data)) as animation:
        width, height = animation.size
        pixels = width * height
        if pixels <= 0 or pixels > MAX_SOURCE_PIXELS:
            raise ValueError("GIF 画面尺寸超出识别限制")
        timestamps: list[float] = []
        durations: list[float] = []
        elapsed_ms = 0.0
        # 限制解码量；顺序 seek 让 Pillow 正确合成透明层和 disposal 帧。
        for index in range(MAX_SOURCE_FRAMES + 1):
            if time.monotonic() - started > DECODE_TIMEOUT_SECONDS:
                raise ValueError("GIF 解码超时")
            try:
                animation.seek(index)
            except EOFError:
                break
            if index >= MAX_SOURCE_FRAMES or (index + 1) * pixels > MAX_DECODED_PIXELS:
                raise ValueError("GIF 帧数或解码量超出识别限制")
            duration = float(animation.info.get("duration", 100) or 100)
            if not math.isfinite(duration) or duration <= 0:
                duration = 100.0
            timestamps.append(elapsed_ms / 1000.0)
            durations.append(duration)
            elapsed_ms += duration

        count = len(timestamps)
        if not count:
            raise ValueError("GIF 没有可用画面")
        if count == 1:
            animation.seek(0)
            output = BytesIO()
            animation.convert("RGBA").save(output, format="PNG")
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
            return f"data:image/png;base64,{encoded}", ""
        sample_frames = max(2, min(int(sample_frames), 16))
        if count <= sample_frames:
            indices = list(range(count))
        else:
            # 按实际时间采样，并始终保留首尾，避免只看到第一帧。
            indices = {0, count - 1}
            for number in range(1, sample_frames - 1):
                target = elapsed_ms / 1000.0 * number / (sample_frames - 1)
                index = next(
                    (
                        i
                        for i, (timestamp, duration) in enumerate(
                            zip(timestamps, durations)
                        )
                        if timestamp + duration / 1000.0 > target
                    ),
                    count - 1,
                )
                indices.add(index)
            indices = sorted(indices)

        tile_width = min(width, MAX_TILE_SIDE)
        tile_height = max(1, round(height * tile_width / width))
        if tile_height > MAX_TILE_SIDE:
            tile_width = max(1, round(tile_width * MAX_TILE_SIDE / tile_height))
            tile_height = MAX_TILE_SIDE
        columns = min(4, len(indices))
        rows = math.ceil(len(indices) / columns)
        gap, label_height = 8, 24
        sheet = Image.new(
            "RGB",
            (columns * (tile_width + gap) + gap,
             rows * (tile_height + label_height + gap) + gap),
            "white",
        )
        draw = ImageDraw.Draw(sheet)
        for number, index in enumerate(indices):
            if time.monotonic() - started > DECODE_TIMEOUT_SECONDS:
                raise ValueError("GIF 解码超时")
            animation.seek(index)
            frame = animation.convert("RGBA")
            frame.thumbnail((tile_width, tile_height), Image.Resampling.LANCZOS)
            x = gap + number % columns * (tile_width + gap)
            y = gap + number // columns * (tile_height + label_height + gap)
            draw.text((x, y + 3), f"{number + 1}  {timestamps[index]:.2f}s", fill="black")
            sheet.paste(frame, (x, y + label_height), frame)

        output = BytesIO()
        sheet.save(output, format="JPEG", quality=85)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        hint = (
            f"输入是同一张 GIF 的 {len(indices)} 个采样画面，原动图共 {count} 帧，"
            f"时长约 {elapsed_ms / 1000.0:.2f} 秒。"
            "格子按从左到右、从上到下排列，标有顺序与时间；不是多张独立图片。"
            "结合各帧描述可见动作变化、文字和表情含义；未采样的细节保持未知。"
        )
        return f"data:image/jpeg;base64,{encoded}", hint


def _gif_data(source: str) -> bytes | None:
    if source.startswith(("data:image/", "base64://")):
        if source.startswith("base64://"):
            header, encoded = "", source[len("base64://"):]
        else:
            header, encoded = source.split(",", 1)
        if len(encoded) > (MAX_SOURCE_BYTES + 2) // 3 * 4:
            raise ValueError("图片大小超出识别限制")
        data = base64.b64decode(encoded, validate=True)
        if data.startswith((b"GIF87a", b"GIF89a")) or header.startswith("data:image/gif"):
            return data
        return None
    path = local_media_path(source)
    if path is None or not path.is_file():
        return None
    with path.open("rb") as handle:
        header = handle.read(6)
        if not header.startswith((b"GIF87a", b"GIF89a")):
            if path.suffix.lower() == ".gif":
                raise ValueError("GIF 文件内容无效")
            return None
        data = header + handle.read(MAX_SOURCE_BYTES + 1 - len(header))
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("GIF 文件大小超出识别限制")
    return data
