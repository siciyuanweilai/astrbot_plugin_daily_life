from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from pathlib import Path
from typing import Any, Callable

import aiohttp
from astrbot.api import logger

from ...config.options import ImageGenerationSettings
from ..shared import (
    LOG_PREFIX,
    REFERENCE_IMAGE_MAX_BYTES,
    GeneratedImage,
    image_mime_and_ext,
    upstream_error_text,
)
from . import gemini, openai, routes
from .pipe import ImageRoute


class GeminiImageService:
    def __init__(self, settings: ImageGenerationSettings, data_dir: Path):
        self.settings = settings
        self.data_dir = data_dir
        self.output_dir = data_dir / "generated_media" / "images"
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def generate_image(self, prompt: str) -> GeneratedImage:
        if not self.settings.enabled:
            raise RuntimeError("图片生成未启用")
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("缺少图片提示词")
        if not routes.has_channel(self.settings):
            raise RuntimeError("图片生成缺少可用接口通道")

        reference_parts = await self._character_reference_parts()
        image_bytes = await self._generate_image_bytes(
            lambda route: [{"text": self._text_to_image_prompt(prompt, route)}, *reference_parts]
        )
        path = await self._save_image(image_bytes)
        logger.info(f"{LOG_PREFIX} 图片生成完成：{path.name}")
        return GeneratedImage(path)

    async def edit_image(self, prompt: str, reference_image: str) -> GeneratedImage:
        if not self.settings.enabled:
            raise RuntimeError("图片生成未启用")
        prompt = str(prompt or "").strip()
        reference_image = str(reference_image or "").strip()
        if not prompt:
            raise ValueError("缺少图片编辑提示词")
        if not reference_image:
            raise ValueError("缺少参考图片")
        if not routes.has_channel(self.settings):
            raise RuntimeError("图片生成缺少可用接口通道")

        image_bytes, mime_type = await self._load_reference_image(reference_image)
        reference_parts = await self._character_reference_parts()
        output_bytes = await self._generate_image_bytes(
            lambda route: [
                {"text": self._image_to_image_prompt(prompt, route)},
                {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}},
                *reference_parts,
            ]
        )
        path = await self._save_image(output_bytes)
        logger.info(f"{LOG_PREFIX} 图片编辑完成：{path.name}")
        return GeneratedImage(path)

    def _text_to_image_prompt(self, prompt: str, route: ImageRoute) -> str:
        return (
            f"生成一张高质量 {route.resolution} 分辨率、{route.aspect_ratio} 比例图片。"
            f"{self._character_reference_instruction()}"
            f"请严格遵循这个画面要求：{prompt}。直接输出图片。"
        )

    def _image_to_image_prompt(self, prompt: str, route: ImageRoute) -> str:
        return (
            f"参考用户提供的图片，生成一张高质量 {route.resolution} 分辨率、{route.aspect_ratio} 比例新图片。"
            f"{self._character_reference_instruction()}"
            "只有在符合要求时才保留参考图里的视觉身份、构图和姿态线索。"
            f"画面要求：{prompt}。直接输出编辑后的图片。"
        )

    def _character_reference_instruction(self) -> str:
        if not self._character_reference_sources():
            return ""
        policy = str(getattr(self.settings, "character_reference_policy", "off") or "off")
        if policy == "always":
            return "已提供一组角色形象参考图，请综合参考并优先保持角色的脸部气质、发型、体态和整体辨识度。"
        return "已提供一组角色形象参考图；如果画面包含角色本人，请综合参考它们保持角色形象一致；如果画面不需要出现角色，不要强行加入人物。"

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

    async def _generate_image_bytes(self, parts_for_route: Callable[[ImageRoute], list[dict[str, Any]]]) -> bytes:
        session = await self._get_session()
        errors: list[str] = []
        request_routes = await self._request_routes()
        if not request_routes:
            raise RuntimeError("图片生成缺少接口地址")
        for route in request_routes:
            parts = parts_for_route(route)
            request = self._build_request(route, parts)
            route_label = f"{route.origin} / {route.label}"
            timeout = aiohttp.ClientTimeout(total=route.timeout_seconds)
            try:
                post = (
                    session.post(request.url, data=request.form, headers=request.headers, timeout=timeout)
                    if request.form is not None
                    else session.post(request.url, json=request.payload, headers=request.headers, timeout=timeout)
                )
                async with post as response:
                    if response.status != 200:
                        detail = await response.text()
                        raise RuntimeError(f"HTTP {response.status}：{detail[:220]}")
                    data = await response.json()
            except Exception as exc:
                message = f"{route_label}：{self._error_text(exc)}"
                errors.append(message)
                logger.debug(f"{LOG_PREFIX} 图片生成路线失败，尝试下一路：{message}")
                continue

            if not isinstance(data, dict):
                message = f"{route_label}：图片接口返回格式不是对象"
                errors.append(message)
                logger.debug(f"{LOG_PREFIX} 图片生成路线失败，尝试下一路：{message}")
                continue
            image_bytes = openai.extract_image(data) if route.protocol == "openai" else gemini.extract_image(data)
            if image_bytes:
                return image_bytes
            message = f"{route_label}：图片接口未返回图片：{upstream_error_text(data)}"
            errors.append(message)
            logger.debug(f"{LOG_PREFIX} 图片生成路线失败，尝试下一路：{message}")

        raise RuntimeError(f"图片生成全部接口均失败：{'；'.join(errors[-8:])}")

    def _build_request(self, route: ImageRoute, parts: list[dict[str, Any]]):
        kwargs = {"resolution": route.resolution, "aspect_ratio": route.aspect_ratio}
        return openai.build_request(route, parts, **kwargs) if route.protocol == "openai" else gemini.build_request(route, parts, **kwargs)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))
        return self._session

    async def _request_routes(self) -> list[ImageRoute]:
        return [
            routes.make_route(
                channel.api_url,
                channel.api_key,
                channel.model,
                f"接口通道 {index}",
                channel.protocol,
                channel.resolution,
                channel.aspect_ratio,
                channel.timeout_seconds,
            )
            for index, channel in enumerate(getattr(self.settings, "channels", []) or [], start=1)
        ]

    @staticmethod
    def _error_text(exc: Exception) -> str:
        text = str(exc).strip()
        return f"{type(exc).__name__}: {text}" if text else type(exc).__name__

    async def _save_image(self, image_bytes: bytes) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        _, ext = image_mime_and_ext(image_bytes)
        digest = hashlib.sha256(image_bytes).hexdigest()[:16]
        path = self.output_dir / f"gemini_{int(time.time())}_{digest}{ext}"
        await asyncio.to_thread(path.write_bytes, image_bytes)
        return path
