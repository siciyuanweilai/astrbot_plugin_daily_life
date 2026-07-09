from __future__ import annotations

import asyncio
import base64
import hashlib
from math import gcd
import time
from pathlib import Path
import inspect
from typing import Any, Callable

import aiohttp
from astrbot.api import logger

from ...config.options import IMAGE_ASPECT_RATIOS, ImageGenerationSettings
from ..shared import (
    LOG_PREFIX,
    REFERENCE_IMAGE_MAX_BYTES,
    GeneratedImage,
    image_mime_and_ext,
    upstream_error_text,
)
from . import gemini, openai, routes
from .pipe import ImageRoute


_SUPPORTED_ASPECT_RATIO_VALUES = {
    ratio: int(ratio.split(":", 1)[0]) / int(ratio.split(":", 1)[1]) for ratio in IMAGE_ASPECT_RATIOS
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _best_supported_aspect_ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return ""
    divisor = gcd(width, height)
    if divisor > 1:
        exact = f"{width // divisor}:{height // divisor}"
        if exact in IMAGE_ASPECT_RATIOS:
            return exact
    target = width / height
    return min(_SUPPORTED_ASPECT_RATIO_VALUES, key=lambda ratio: abs(_SUPPORTED_ASPECT_RATIO_VALUES[ratio] - target))


def _image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if image_bytes.startswith(_PNG_SIGNATURE):
        return _png_dimensions(image_bytes)
    if image_bytes.startswith((b"\xff\xd8",)):
        return _jpeg_dimensions(image_bytes)
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return _gif_dimensions(image_bytes)
    if len(image_bytes) >= 16 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return _webp_dimensions(image_bytes)
    return 0, 0


def _png_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if len(image_bytes) < 24 or image_bytes[12:16] != b"IHDR":
        return 0, 0
    return int.from_bytes(image_bytes[16:20], "big"), int.from_bytes(image_bytes[20:24], "big")


def _gif_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if len(image_bytes) < 10:
        return 0, 0
    return int.from_bytes(image_bytes[6:8], "little"), int.from_bytes(image_bytes[8:10], "little")


def _jpeg_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if len(image_bytes) < 4 or image_bytes[:2] != b"\xff\xd8":
        return 0, 0
    offset = 2
    limit = len(image_bytes)
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset + 1 < limit:
        if image_bytes[offset] != 0xFF:
            offset += 1
            continue
        while offset < limit and image_bytes[offset] == 0xFF:
            offset += 1
        if offset >= limit:
            break
        marker = image_bytes[offset]
        offset += 1
        if marker in {0xD8, 0xD9} or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 1 >= limit:
            break
        segment_length = int.from_bytes(image_bytes[offset : offset + 2], "big")
        if segment_length < 2:
            break
        if marker in sof_markers and offset + 7 <= limit:
            return (
                int.from_bytes(image_bytes[offset + 5 : offset + 7], "big"),
                int.from_bytes(image_bytes[offset + 3 : offset + 5], "big"),
            )
        offset += segment_length
    return 0, 0


def _webp_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if len(image_bytes) < 16 or image_bytes[:4] != b"RIFF" or image_bytes[8:12] != b"WEBP":
        return 0, 0
    offset = 12
    limit = len(image_bytes)
    while offset + 8 <= limit:
        chunk_type = image_bytes[offset : offset + 4]
        chunk_size = int.from_bytes(image_bytes[offset + 4 : offset + 8], "little")
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > limit:
            break
        chunk = image_bytes[chunk_start:chunk_end]
        if chunk_type == b"VP8X" and len(chunk) >= 10:
            return int.from_bytes(chunk[4:7], "little") + 1, int.from_bytes(chunk[7:10], "little") + 1
        if chunk_type == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2F:
            bits = int.from_bytes(chunk[1:5], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk_type == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
            return int.from_bytes(chunk[6:8], "little") & 0x3FFF, int.from_bytes(chunk[8:10], "little") & 0x3FFF
        offset = chunk_end + (chunk_size & 1)
    return 0, 0


class GeminiImageService:
    def __init__(self, settings: ImageGenerationSettings, data_dir: Path):
        self.settings = settings
        self.data_dir = data_dir
        self.output_dir = data_dir / "generated" / "images"
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def can_edit_image(self) -> bool:
        return bool(self.settings.enabled and routes.has_channel(self.settings, "edit"))

    def first_character_reference_image(self) -> str:
        sources = self._character_reference_sources()
        if not sources:
            return ""
        return str(sources[0].get("path") or "").strip()

    async def generate_image(self, prompt: str, aspect_ratio: str = "") -> GeneratedImage:
        if not self.settings.enabled:
            raise RuntimeError("图片生成未启用")
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("缺少图片提示词")
        if not routes.has_channel(self.settings, "text"):
            raise RuntimeError("图片生成缺少可用文生图接口通道")

        image_bytes, route = await self._generate_image_result(
            lambda route, current_prompt: self._text_to_image_parts(current_prompt, route),
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            mode="text",
        )
        path = await self._save_image(image_bytes, prefix=route.protocol)
        logger.info(f"{LOG_PREFIX} 图片生成完成：{path.name}")
        return GeneratedImage(path)

    async def edit_image(
        self,
        prompt: str,
        reference_image: str,
        aspect_ratio: str = "",
        *,
        preserve_reference_ratio: bool = True,
    ) -> GeneratedImage:
        if not self.settings.enabled:
            raise RuntimeError("图片生成未启用")
        prompt = str(prompt or "").strip()
        reference_image = str(reference_image or "").strip()
        if not prompt:
            raise ValueError("缺少图片编辑提示词")
        if not reference_image:
            raise ValueError("缺少参考图片")
        if not routes.has_channel(self.settings, "edit"):
            raise RuntimeError("图片生成缺少可用图生图接口通道")

        image_bytes, mime_type = await self._load_reference_image(reference_image)
        reference_aspect_ratio = self._reference_image_aspect_ratio(image_bytes) if preserve_reference_ratio else ""
        effective_aspect_ratio = reference_aspect_ratio or aspect_ratio
        reference_parts = await self._character_reference_parts()
        output_bytes, route = await self._generate_image_result(
            lambda route, current_prompt: [
                {"text": self._image_to_image_prompt(current_prompt, route, character_reference=bool(reference_parts))},
                {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}},
                *reference_parts,
            ],
            prompt=prompt,
            aspect_ratio=effective_aspect_ratio,
            mode="edit",
        )
        path = await self._save_image(output_bytes, prefix=route.protocol)
        logger.info(f"{LOG_PREFIX} 图片编辑完成：{path.name}")
        return GeneratedImage(path)

    async def _text_to_image_parts(self, prompt: str, route: ImageRoute) -> list[dict[str, Any]]:
        reference_parts = []
        if self._route_accepts_character_reference(route, text_to_image=True):
            reference_parts = await self._character_reference_parts()
        return [
            {"text": self._text_to_image_prompt(prompt, route, character_reference=bool(reference_parts))},
            *reference_parts,
        ]

    def _text_to_image_prompt(self, prompt: str, route: ImageRoute, *, character_reference: bool = True) -> str:
        return (
            f"生成一张高质量 {route.resolution} 分辨率、{route.aspect_ratio} 比例图片。"
            f"{self._character_reference_instruction(character_reference)}"
            f"请严格遵循这个画面要求：{prompt}。直接输出图片。"
        )

    def _image_to_image_prompt(self, prompt: str, route: ImageRoute, *, character_reference: bool = True) -> str:
        return (
            f"参考用户提供的图片，生成一张高质量 {route.resolution} 分辨率、{route.aspect_ratio} 比例新图片。"
            f"{self._character_reference_instruction(character_reference)}"
            "只有在符合要求时才保留参考图里的视觉身份、构图和姿态线索。"
            f"画面要求：{prompt}。直接输出编辑后的图片。"
        )

    def _character_reference_instruction(self, enabled: bool = True) -> str:
        if not enabled or not self._character_reference_sources():
            return ""
        policy = str(getattr(self.settings, "character_reference_policy", "off") or "off")
        if policy == "always":
            return "已提供一组角色形象参考图，请综合参考并优先保持角色的脸部气质、发型、体态和整体辨识度。"
        return "已提供一组角色形象参考图；如果画面包含角色本人，请综合参考它们保持角色形象一致；如果画面不需要出现角色，不要强行加入人物。"

    @staticmethod
    def _route_accepts_character_reference(route: ImageRoute, *, text_to_image: bool = False) -> bool:
        return not (text_to_image and route.protocol == "openai")

    def _character_reference_sources(self) -> list[dict[str, Any]]:
        policy = str(getattr(self.settings, "character_reference_policy", "off") or "off")
        if policy == "off":
            return []
        sources = getattr(self.settings, "character_reference_images", []) or []
        if not isinstance(sources, list):
            return []
        max_count = int(getattr(self.settings, "reference_max_count", 6) or 6)
        return [item for item in sources if isinstance(item, dict) and str(item.get("path") or "").strip()][:max_count]

    async def _character_reference_parts(self) -> list[dict[str, Any]]:
        sources = self._character_reference_sources()
        if not sources:
            return []
        image_parts: list[dict[str, Any]] = []
        skipped = 0
        for index, source in enumerate(sources, start=1):
            path = str(source.get("path") or "").strip()
            if not path:
                continue
            name = str(source.get("name") or f"参考图 {index}").strip()
            try:
                image_bytes, mime_type = await self._load_reference_image(path)
            except Exception as exc:
                skipped += 1
                logger.warning(f"{LOG_PREFIX} 角色形象参考图跳过：{name}：{exc}")
                continue
            image_parts.append({"text": f"角色形象参考图 {len(image_parts) // 2 + 1}：{name}"})
            image_parts.append({"inlineData": {"mimeType": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}})
        if not image_parts:
            return []
        if skipped:
            logger.debug(f"{LOG_PREFIX} 已跳过不可用角色形象参考图 {skipped} 张")
        return [
            {"text": f"下面 {len(image_parts) // 2} 张图是角色形象参考图组，用于保持角色外貌一致。"},
            *image_parts,
        ]

    async def _load_reference_image(self, reference_image: str) -> tuple[bytes, str]:
        if reference_image.startswith(("http://", "https://")):
            return await self._download_reference_image(reference_image)
        if reference_image.startswith("base64://"):
            data = base64.b64decode(reference_image.removeprefix("base64://"), validate=True)
            if not data:
                raise ValueError("参考图片为空")
            if len(data) > REFERENCE_IMAGE_MAX_BYTES:
                raise ValueError("参考图片过大")
            mime, _ = image_mime_and_ext(data)
            return data, mime
        path = Path(reference_image).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"参考图片不存在：{reference_image}")
        size = path.stat().st_size
        if size <= 0:
            raise ValueError("参考图片为空")
        if size > REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("参考图片过大")
        data = await asyncio.to_thread(path.read_bytes)
        mime, _ = image_mime_and_ext(data)
        return data, mime

    @staticmethod
    def _reference_image_aspect_ratio(image_bytes: bytes) -> str:
        width, height = _image_dimensions(image_bytes)
        return _best_supported_aspect_ratio(width, height)

    async def _download_reference_image(self, url: str) -> tuple[bytes, str]:
        session = await self._get_session()
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"参考图片下载失败（HTTP {response.status}）")
            content_type = str(response.headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
            if content_type and not content_type.startswith("image/"):
                raise ValueError("参考图片链接不是图片内容")
            data = await response.content.read(REFERENCE_IMAGE_MAX_BYTES + 1)
        if not data:
            raise ValueError("参考图片为空")
        if len(data) > REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("参考图片过大")
        detected_mime, _ = image_mime_and_ext(data)
        return data, content_type or detected_mime

    async def _generate_image_result(
        self,
        parts_for_route: Callable[[ImageRoute, str], Any],
        *,
        prompt: str,
        aspect_ratio: str = "",
        mode: str = "text",
    ) -> tuple[bytes, ImageRoute]:
        session = await self._get_session()
        errors: list[str] = []
        request_routes = await self._request_routes(mode)
        if not request_routes:
            raise RuntimeError(f"图片生成缺少{self._mode_label(mode)}接口地址")
        prompt = str(prompt or "").strip()
        for route in request_routes:
            route = self._route_with_aspect_ratio(route, aspect_ratio)
            route_label = f"{route.origin} / {route.label}"
            timeout = aiohttp.ClientTimeout(total=route.timeout_seconds)
            try:
                data = await self._request_image_data(session, route, timeout, parts_for_route, prompt)
            except Exception as exc:
                message = f"{route_label}：{self._error_text(exc)}"
                errors.append(message)
                if self._is_policy_violation_error(exc):
                    raise RuntimeError(f"图片生成触发安全拒绝：{message}") from exc
                logger.debug(f"{LOG_PREFIX} {self._mode_label(mode)}接口通道失败，尝试下一条：{message}")
                continue

            if not isinstance(data, dict):
                message = f"{route_label}：图片接口返回格式不是对象"
                errors.append(message)
                logger.debug(f"{LOG_PREFIX} {self._mode_label(mode)}接口通道失败，尝试下一条：{message}")
                continue
            image_bytes = openai.extract_image(data) if route.protocol == "openai" else gemini.extract_image(data)
            if image_bytes:
                return image_bytes, route
            message = f"{route_label}：图片接口未返回图片：{upstream_error_text(data)}"
            errors.append(message)
            if self._is_policy_violation_text(message):
                raise RuntimeError(f"图片生成触发安全拒绝：{message}")
            logger.debug(f"{LOG_PREFIX} {self._mode_label(mode)}接口通道失败，尝试下一条：{message}")

        raise RuntimeError(f"图片生成全部{self._mode_label(mode)}接口均失败：{'；'.join(errors[-8:])}")

    async def _request_image_data(
        self,
        session: aiohttp.ClientSession,
        route: ImageRoute,
        timeout: aiohttp.ClientTimeout,
        parts_for_route: Callable[[ImageRoute, str], Any],
        prompt: str,
    ) -> dict[str, Any] | Any:
        parts = parts_for_route(route, prompt)
        if inspect.isawaitable(parts):
            parts = await parts
        request = self._build_request(route, parts)
        post = (
            session.post(request.url, data=request.form, headers=request.headers, timeout=timeout)
            if request.form is not None
            else session.post(request.url, json=request.payload, headers=request.headers, timeout=timeout)
        )
        async with post as response:
            if response.status != 200:
                detail = await response.text()
                raise RuntimeError(f"HTTP {response.status}：{detail[:1000]}")
            return await response.json()

    @staticmethod
    def _is_policy_violation_error(exc: Exception) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if GeminiImageService._is_policy_violation_text(str(current)):
                return True
            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        return False

    @staticmethod
    def _is_policy_violation_text(text: str) -> bool:
        lowered = str(text or "").lower()
        return "content_policy_violation" in lowered or "policy_violation" in lowered

    def _build_request(self, route: ImageRoute, parts: list[dict[str, Any]]):
        kwargs = {"resolution": route.resolution, "aspect_ratio": route.aspect_ratio}
        return openai.build_request(route, parts, **kwargs) if route.protocol == "openai" else gemini.build_request(route, parts, **kwargs)

    @staticmethod
    def _route_with_aspect_ratio(route: ImageRoute, aspect_ratio: str) -> ImageRoute:
        aspect_ratio = str(aspect_ratio or "").strip()
        if not aspect_ratio or aspect_ratio not in IMAGE_ASPECT_RATIOS or aspect_ratio == route.aspect_ratio:
            return route
        return ImageRoute(
            api_url=route.api_url,
            api_key=route.api_key,
            model=route.model,
            label=route.label,
            protocol=route.protocol,
            resolution=route.resolution,
            aspect_ratio=aspect_ratio,
            timeout_seconds=route.timeout_seconds,
            origin=route.origin,
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))
        return self._session

    @staticmethod
    def _mode_label(mode: str) -> str:
        return "图生图" if str(mode or "").strip().lower() == "edit" else "文生图"

    def _channels_for_mode(self, mode: str):
        return (
            getattr(self.settings, "edit_channels", []) or []
            if str(mode or "").strip().lower() == "edit"
            else getattr(self.settings, "text_channels", []) or []
        )

    async def _request_routes(self, mode: str) -> list[ImageRoute]:
        mode_label = self._mode_label(mode)
        return [
            routes.make_route(
                channel.api_url,
                channel.api_key,
                channel.model,
                f"{mode_label}接口通道 {index}",
                channel.protocol,
                channel.resolution,
                channel.aspect_ratio,
                channel.timeout_seconds,
            )
            for index, channel in enumerate(self._channels_for_mode(mode), start=1)
        ]

    @staticmethod
    def _error_text(exc: Exception) -> str:
        text = str(exc).strip()
        return f"{type(exc).__name__}: {text}" if text else type(exc).__name__

    async def _save_image(self, image_bytes: bytes, *, prefix: str = "image") -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        _, ext = image_mime_and_ext(image_bytes)
        digest = hashlib.sha256(image_bytes).hexdigest()[:16]
        safe_prefix = "".join(char for char in str(prefix or "image").strip().lower() if char.isalnum() or char == "_")
        path = self.output_dir / f"{safe_prefix or 'image'}_{int(time.time())}_{digest}{ext}"
        await asyncio.to_thread(path.write_bytes, image_bytes)
        return path
