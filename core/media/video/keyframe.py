from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .protocol.size import video_aspect_ratio

VIDEO_REFERENCE_MAX_BYTES = 20 * 1024 * 1024
_JPEG_QUALITY_STEPS = (88, 82, 76, 68, 60)


@dataclass(frozen=True, slots=True)
class PreparedVideoReference:
    data: bytes
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    compressed: bool


def prepare_video_reference_image(
    image_bytes: bytes,
    *,
    aspect_ratio: str,
    resolution: str,
) -> PreparedVideoReference:
    if not image_bytes:
        raise ValueError("视频首帧图片为空")
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            image.load()
            source_width, source_height = image.size
            if source_width <= 0 or source_height <= 0:
                raise ValueError("视频首帧图片尺寸无效")
            if len(image_bytes) <= VIDEO_REFERENCE_MAX_BYTES:
                return PreparedVideoReference(
                    data=image_bytes,
                    source_width=source_width,
                    source_height=source_height,
                    output_width=source_width,
                    output_height=source_height,
                    compressed=False,
                )
            target_size = _bounded_target_size(
                source_width,
                source_height,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
            )
            image = ImageOps.fit(
                image,
                target_size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            image = _rgb_image(image)
            rendered = _encode_jpeg(image)
            return PreparedVideoReference(
                data=rendered,
                source_width=source_width,
                source_height=source_height,
                output_width=image.width,
                output_height=image.height,
                compressed=True,
            )
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("视频首帧图片无法读取") from exc


def _bounded_target_size(
    source_width: int,
    source_height: int,
    *,
    aspect_ratio: str,
    resolution: str,
) -> tuple[int, int]:
    ratio = video_aspect_ratio(aspect_ratio)
    ratio_width, ratio_height = (int(value) for value in ratio.split(":", 1))
    short_side = 1080 if str(resolution or "").strip().lower() == "1080p" else 720
    if ratio_width >= ratio_height:
        target_width = round(short_side * ratio_width / ratio_height)
        target_height = short_side
    else:
        target_width = short_side
        target_height = round(short_side * ratio_height / ratio_width)
    scale = min(
        1.0,
        source_width / target_width,
        source_height / target_height,
    )
    return (
        max(1, round(target_width * scale)),
        max(1, round(target_height * scale)),
    )


def _rgb_image(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _encode_jpeg(image: Image.Image) -> bytes:
    last_data = b""
    for quality in _JPEG_QUALITY_STEPS:
        output = BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
        last_data = output.getvalue()
        if len(last_data) <= VIDEO_REFERENCE_MAX_BYTES:
            return last_data
    raise ValueError(
        f"视频首帧压缩后仍超过 {VIDEO_REFERENCE_MAX_BYTES // (1024 * 1024)} MB"
    )
