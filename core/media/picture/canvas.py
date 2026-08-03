from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import time
from collections.abc import Callable
from math import gcd
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger

from ...config.options import (
    IMAGE_ASPECT_RATIOS,
    IMAGE_RESOLUTIONS,
    ImageGenerationSettings,
)
from ...paths import expand_path, path_is_file, path_size
from ...security import is_public_http_url_async
from ..base import (
    GROUP_IDENTITY_CONTINUITY_RULE,
    LOG_PREFIX,
    PHYSICAL_IDENTITY_CONTINUITY_RULE,
    REFERENCE_IMAGE_MAX_BYTES,
    GeneratedImage,
    image_mime_and_ext,
    upstream_error_text,
)
from . import gemini, openai, routes
from .pipe import ImageRoute

_SUPPORTED_ASPECT_RATIO_VALUES = {
    ratio: int(ratio.split(":", 1)[0]) / int(ratio.split(":", 1)[1])
    for ratio in IMAGE_ASPECT_RATIOS
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
    return min(
        _SUPPORTED_ASPECT_RATIO_VALUES,
        key=lambda ratio: abs(_SUPPORTED_ASPECT_RATIO_VALUES[ratio] - target),
    )


def _image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if image_bytes.startswith(_PNG_SIGNATURE):
        return _png_dimensions(image_bytes)
    if image_bytes.startswith((b"\xff\xd8",)):
        return _jpeg_dimensions(image_bytes)
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return _gif_dimensions(image_bytes)
    if (
        len(image_bytes) >= 16
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return _webp_dimensions(image_bytes)
    return 0, 0


def _png_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if len(image_bytes) < 24 or image_bytes[12:16] != b"IHDR":
        return 0, 0
    return int.from_bytes(image_bytes[16:20], "big"), int.from_bytes(
        image_bytes[20:24], "big"
    )


def _gif_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if len(image_bytes) < 10:
        return 0, 0
    return int.from_bytes(image_bytes[6:8], "little"), int.from_bytes(
        image_bytes[8:10], "little"
    )


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
    if (
        len(image_bytes) < 16
        or image_bytes[:4] != b"RIFF"
        or image_bytes[8:12] != b"WEBP"
    ):
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
            return int.from_bytes(chunk[4:7], "little") + 1, int.from_bytes(
                chunk[7:10], "little"
            ) + 1
        if chunk_type == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2F:
            bits = int.from_bytes(chunk[1:5], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk_type == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
            return int.from_bytes(chunk[6:8], "little") & 0x3FFF, int.from_bytes(
                chunk[8:10], "little"
            ) & 0x3FFF
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

    def friend_reference_options(self) -> list[dict[str, str]]:
        result = []
        for profile in self._friend_reference_profiles():
            if not profile.get("reference_images"):
                continue
            result.append(
                {
                    "profile_id": str(profile.get("profile_id") or "").strip(),
                    "display_name": str(
                        profile.get("display_name") or profile.get("profile_id") or ""
                    ).strip(),
                }
            )
        return result

    @staticmethod
    def _appearance_profile_text(value: Any) -> str:
        return " ".join(str(value or "").strip().split())[:600]

    @classmethod
    def _physical_identity_instruction(
        cls, appearance_profile: str, *, label: str = "人物"
    ) -> str:
        profile = cls._appearance_profile_text(appearance_profile)
        if not profile:
            return ""
        return f"{label}稳定体貌：{profile}。{PHYSICAL_IDENTITY_CONTINUITY_RULE}"

    def _group_physical_identity_instruction(
        self,
        identity_profiles: dict[str, Any] | None,
    ) -> str:
        current_profile = self._appearance_profile_text(
            identity_profiles.get("current_character")
            if isinstance(identity_profiles, dict)
            else ""
        )
        if not current_profile:
            return ""
        return (
            f"人物 A 稳定体貌：{current_profile}。{PHYSICAL_IDENTITY_CONTINUITY_RULE}"
        )

    async def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "",
        resolution: str = "",
        *,
        protocol: str = "",
        identity_profile: str = "",
    ) -> GeneratedImage:
        if not self.settings.enabled:
            raise RuntimeError("图片生成未启用")
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("缺少图片提示词")
        protocol = routes.normalize_image_provider(protocol)
        if not routes.has_channel(self.settings, "text", protocol):
            if protocol:
                raise RuntimeError(
                    f"本轮指定使用{self._protocol_label(protocol)}，但没有可用的文生图接口通道"
                )
            raise RuntimeError("图片生成缺少可用文生图接口通道")

        image_bytes, route = await self._generate_image_result(
            lambda route, current_prompt: self._text_to_image_parts(
                current_prompt, route, identity_profile=identity_profile
            ),
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            mode="text",
            protocol=protocol,
        )
        path = await self._save_image(image_bytes, prefix=route.protocol)
        logger.debug(f"{LOG_PREFIX} 图片生成文件已保存：{path.name}")
        return GeneratedImage(path)

    async def generate_group_image(
        self,
        prompt: str,
        participant_profile_ids: list[str],
        aspect_ratio: str = "",
        resolution: str = "",
        *,
        scene_reference: str = "",
        reference_context: str = "",
        protocol: str = "",
        identity_profiles: dict[str, Any] | None = None,
    ) -> GeneratedImage:
        if not self.settings.enabled:
            raise RuntimeError("图片生成未启用")
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("缺少合影画面要求")
        participant_ids = [
            str(item or "").strip() for item in participant_profile_ids or []
        ]
        participant_ids = list(dict.fromkeys(item for item in participant_ids if item))
        if len(participant_ids) != 1:
            raise ValueError("当前合影仅支持选择一位好友")
        protocol = routes.normalize_image_provider(protocol)
        if not routes.has_channel(self.settings, "edit", protocol):
            if protocol:
                raise RuntimeError(
                    f"本轮指定使用{self._protocol_label(protocol)}，但没有可用的图生图接口通道"
                )
            raise RuntimeError("合影生成缺少可用图生图接口通道")
        current_sources = self._character_reference_sources()[:3]
        if not current_sources:
            raise ValueError("当前角色参考图未启用或尚未上传")
        friend = self._friend_reference_profile(participant_ids[0])
        if friend is None:
            raise ValueError("没有找到对应的好友参考档案")
        friend_sources = list(friend.get("reference_images") or [])[:3]
        if not friend_sources:
            raise ValueError("这位好友尚未上传参考图片")
        scene_parts: list[dict[str, Any]] = []
        effective_aspect_ratio = aspect_ratio
        scene_reference = str(scene_reference or "").strip()
        if scene_reference:
            scene_bytes, scene_mime = await self._load_reference_image(scene_reference)
            effective_aspect_ratio = aspect_ratio or self._reference_image_aspect_ratio(
                scene_bytes
            )
            scene_parts = [
                {
                    "text": (
                        "下面这张图仅作为场景、构图或姿态参考，不代表人物 A 或人物 B 的身份；"
                        "人物身份只以各自标注的身份参考图为准。"
                    )
                },
                {
                    "inlineData": {
                        "mimeType": scene_mime,
                        "data": base64.b64encode(scene_bytes).decode("ascii"),
                    }
                },
            ]

        output_bytes, route = await self._generate_image_result(
            lambda route, current_prompt: self._group_image_parts(
                current_prompt,
                route,
                current_sources=current_sources,
                friend=friend,
                friend_sources=friend_sources,
                scene_parts=scene_parts,
                identity_profiles=identity_profiles,
            ),
            prompt=prompt,
            aspect_ratio=effective_aspect_ratio,
            resolution=resolution,
            mode="edit",
            protocol=protocol,
        )
        path = await self._save_image(output_bytes, prefix=route.protocol)
        context = str(reference_context or "").strip()
        if context:
            friend_name = str(
                friend.get("display_name")
                or friend.get("profile_id")
                or participant_ids[0]
            ).strip()
            logger.debug(
                f"{LOG_PREFIX} {context}参考图：当前角色 + {friend_name}；"
                f"角色 {len(current_sources)} 张；{friend_name} {len(friend_sources)} 张"
            )
        return GeneratedImage(path)

    async def edit_image(
        self,
        prompt: str,
        reference_image: str,
        aspect_ratio: str = "",
        resolution: str = "",
        *,
        preserve_reference_ratio: bool = True,
        protocol: str = "",
        identity_profile: str = "",
    ) -> GeneratedImage:
        if not self.settings.enabled:
            raise RuntimeError("图片生成未启用")
        prompt = str(prompt or "").strip()
        reference_image = str(reference_image or "").strip()
        if not prompt:
            raise ValueError("缺少图片编辑提示词")
        if not reference_image:
            raise ValueError("缺少参考图片")
        protocol = routes.normalize_image_provider(protocol)
        if not routes.has_channel(self.settings, "edit", protocol):
            if protocol:
                raise RuntimeError(
                    f"本轮指定使用{self._protocol_label(protocol)}，但没有可用的图生图接口通道"
                )
            raise RuntimeError("图片生成缺少可用图生图接口通道")

        image_bytes, mime_type = await self._load_reference_image(reference_image)
        reference_aspect_ratio = (
            self._reference_image_aspect_ratio(image_bytes)
            if preserve_reference_ratio
            else ""
        )
        effective_aspect_ratio = reference_aspect_ratio or aspect_ratio
        reference_is_character = self._is_character_reference_image(reference_image)
        reference_parts = await self._character_reference_parts(
            exclude_paths={reference_image}
        )
        output_bytes, route = await self._generate_image_result(
            lambda route, current_prompt: [
                {
                    "text": self._image_to_image_prompt(
                        current_prompt,
                        route,
                        character_reference=(
                            reference_is_character or bool(reference_parts)
                        ),
                        character_identity_anchor=reference_is_character,
                        identity_profile=identity_profile,
                    )
                },
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                },
                *reference_parts,
            ],
            prompt=prompt,
            aspect_ratio=effective_aspect_ratio,
            resolution=resolution,
            mode="edit",
            protocol=protocol,
        )
        path = await self._save_image(output_bytes, prefix=route.protocol)
        logger.debug(f"{LOG_PREFIX} 图片编辑文件已保存：{path.name}")
        return GeneratedImage(path)

    async def _text_to_image_parts(
        self, prompt: str, route: ImageRoute, *, identity_profile: str = ""
    ) -> list[dict[str, Any]]:
        reference_parts = []
        if self._route_accepts_character_reference(route, text_to_image=True):
            reference_parts = await self._character_reference_parts()
        return [
            {
                "text": self._text_to_image_prompt(
                    prompt,
                    route,
                    character_reference=bool(reference_parts),
                    identity_profile=identity_profile,
                )
            },
            *reference_parts,
        ]

    def _text_to_image_prompt(
        self,
        prompt: str,
        route: ImageRoute,
        *,
        character_reference: bool = True,
        identity_profile: str = "",
    ) -> str:
        return (
            f"生成一张高质量 {route.resolution} 分辨率、{route.aspect_ratio} 比例图片。"
            f"{self._character_reference_instruction(character_reference)}"
            f"{self._physical_identity_instruction(identity_profile)}"
            f"请严格遵循这个画面要求：{prompt}。直接输出图片。"
        )

    def _image_to_image_prompt(
        self,
        prompt: str,
        route: ImageRoute,
        *,
        character_reference: bool = True,
        character_identity_anchor: bool = False,
        identity_profile: str = "",
    ) -> str:
        reference_instruction = (
            "参考随请求提供的当前角色身份图，生成一张"
            if character_identity_anchor
            else "参考用户提供的图片，生成一张"
        )
        return (
            f"{reference_instruction}高质量 {route.resolution} 分辨率、{route.aspect_ratio} 比例新图片。"
            f"{self._character_reference_instruction(character_reference)}"
            f"{self._physical_identity_instruction(identity_profile)}"
            "只有在符合要求时才保留参考图里的视觉身份、构图和姿态线索。"
            f"画面要求：{prompt}。直接输出编辑后的图片。"
        )

    def _character_reference_instruction(self, enabled: bool = True) -> str:
        if not enabled or not self._character_reference_sources():
            return ""
        policy = str(
            getattr(self.settings, "character_reference_policy", "off") or "off"
        )
        if policy == "always":
            return "已提供一组角色形象参考图，请综合参考并优先保持角色的脸部气质、发型、体态和整体辨识度。"
        return "已提供一组角色形象参考图；如果画面包含角色本人，请综合参考它们保持角色形象一致；如果画面不需要出现角色，不要强行加入人物。"

    @staticmethod
    def _route_accepts_character_reference(
        route: ImageRoute, *, text_to_image: bool = False
    ) -> bool:
        return not (text_to_image and route.protocol == "openai")

    def _character_reference_sources(self) -> list[dict[str, Any]]:
        policy = str(
            getattr(self.settings, "character_reference_policy", "off") or "off"
        )
        if policy == "off":
            return []
        sources = getattr(self.settings, "character_reference_images", []) or []
        if not isinstance(sources, list):
            return []
        return [
            item
            for item in sources
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ][:6]

    def _is_character_reference_image(self, reference_image: str) -> bool:
        target = str(reference_image or "").strip()
        return bool(target) and any(
            str(source.get("path") or "").strip() == target
            for source in self._character_reference_sources()
        )

    def _friend_reference_profiles(self) -> list[dict[str, Any]]:
        profiles = getattr(self.settings, "friend_reference_profiles", []) or []
        if not isinstance(profiles, list):
            return []
        return [
            item
            for item in profiles
            if isinstance(item, dict) and str(item.get("profile_id") or "").strip()
        ]

    def _friend_reference_profile(self, profile_id: str) -> dict[str, Any] | None:
        target = str(profile_id or "").strip()
        if not target:
            return None
        for profile in self._friend_reference_profiles():
            if str(profile.get("profile_id") or "").strip() == target:
                return profile
        return None

    async def _group_image_parts(
        self,
        prompt: str,
        route: ImageRoute,
        *,
        current_sources: list[dict[str, Any]],
        friend: dict[str, Any],
        friend_sources: list[dict[str, Any]],
        scene_parts: list[dict[str, Any]],
        identity_profiles: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        friend_name = str(
            friend.get("display_name") or friend.get("profile_id") or "好友"
        ).strip()
        current_parts = await self._person_reference_parts(
            "人物 A：当前角色", current_sources
        )
        friend_parts = await self._person_reference_parts(
            f"人物 B：好友 {friend_name}", friend_sources
        )
        physical_identity = self._group_physical_identity_instruction(identity_profiles)
        if not current_parts:
            raise ValueError("当前角色参考图均不可用")
        if not friend_parts:
            raise ValueError(f"好友 {friend_name} 的参考图均不可用")
        return [
            {
                "text": (
                    f"生成一张高质量 {route.resolution} 分辨率、{route.aspect_ratio} 比例的双人合影。"
                    "下面的参考图片分属于两个不同人物，只用于保持各自身份一致。"
                    f"{GROUP_IDENTITY_CONTINUITY_RULE}"
                    f"{physical_identity}"
                    "严格按照画面要求安排两人的站位、动作和互动；不要添加第三个人。"
                    f"画面要求：{prompt}。直接输出图片。"
                )
            },
            *scene_parts,
            *current_parts,
            *friend_parts,
        ]

    async def _person_reference_parts(
        self, identity_label: str, sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
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
                logger.debug(f"{LOG_PREFIX} {identity_label}参考图跳过：{name}：{exc}")
                continue
            parts.extend(
                [
                    {"text": f"{identity_label}，参考图 {len(parts) // 2 + 1}：{name}"},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ]
            )
        if skipped:
            logger.debug(
                f"{LOG_PREFIX} {identity_label}已跳过不可用参考图 {skipped} 张"
            )
        return parts

    async def _character_reference_parts(
        self, *, exclude_paths: set[str] | None = None
    ) -> list[dict[str, Any]]:
        sources = self._character_reference_sources()
        if not sources:
            return []
        excluded = {str(path or "").strip() for path in exclude_paths or set()}
        image_parts: list[dict[str, Any]] = []
        skipped = 0
        for index, source in enumerate(sources, start=1):
            path = str(source.get("path") or "").strip()
            if not path or path in excluded:
                continue
            name = str(source.get("name") or f"参考图 {index}").strip()
            try:
                image_bytes, mime_type = await self._load_reference_image(path)
            except Exception as exc:
                skipped += 1
                logger.debug(f"{LOG_PREFIX} 角色形象参考图跳过：{name}：{exc}")
                continue
            image_parts.append(
                {"text": f"角色形象参考图 {len(image_parts) // 2 + 1}：{name}"}
            )
            image_parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                }
            )
        if not image_parts:
            return []
        if skipped:
            logger.debug(f"{LOG_PREFIX} 已跳过不可用角色形象参考图 {skipped} 张")
        return [
            {
                "text": f"下面 {len(image_parts) // 2} 张图是角色形象参考图组，用于保持角色外貌一致。"
            },
            *image_parts,
        ]

    async def _load_reference_image(self, reference_image: str) -> tuple[bytes, str]:
        if reference_image.startswith(("http://", "https://")):
            return await self._download_reference_image(reference_image)
        if reference_image.startswith("base64://"):
            data = base64.b64decode(
                reference_image.removeprefix("base64://"), validate=True
            )
            if not data:
                raise ValueError("参考图片为空")
            if len(data) > REFERENCE_IMAGE_MAX_BYTES:
                raise ValueError("参考图片过大")
            mime, _ = image_mime_and_ext(data)
            return data, mime
        path = await asyncio.to_thread(expand_path, reference_image)
        if not await asyncio.to_thread(path_is_file, path):
            raise FileNotFoundError(f"参考图片不存在：{reference_image}")
        size = await asyncio.to_thread(path_size, path)
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
        if not await is_public_http_url_async(url):
            raise ValueError("参考图片地址不是公网 HTTP(S) 地址")
        session = await self._get_session()
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"参考图片下载失败（HTTP {response.status}）")
            content_type = (
                str(response.headers.get("Content-Type", "") or "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
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
        resolution: str = "",
        mode: str = "text",
        protocol: str = "",
    ) -> tuple[bytes, ImageRoute]:
        requested_resolution = str(resolution or "").strip().upper()
        if requested_resolution and requested_resolution not in IMAGE_RESOLUTIONS:
            raise ValueError("图片输出分辨率只能是 1K、2K 或 4K")
        errors: list[str] = []
        protocol = routes.normalize_image_provider(protocol)
        request_routes = await self._request_routes(mode, protocol=protocol)
        if not request_routes:
            if protocol:
                raise RuntimeError(
                    f"本轮指定使用{self._protocol_label(protocol)}，"
                    f"但没有可用的{self._mode_label(mode)}接口通道"
                )
            raise RuntimeError(f"图片生成缺少{self._mode_label(mode)}接口地址")
        prompt = str(prompt or "").strip()
        for route in request_routes:
            route = self._route_with_options(route, aspect_ratio, requested_resolution)
            session = await self._get_session()
            route_label = f"{route.origin} / {route.label}"
            timeout = aiohttp.ClientTimeout(total=route.timeout_seconds)
            requested_size = self._request_size_label(route)
            logger.debug(
                f"{LOG_PREFIX} 图片请求：通道={route_label}；"
                f"模式={self._mode_label(mode)}；"
                f"协议={self._protocol_label(route.protocol)}；"
                f"来源={route.resolution_source}；分辨率={route.resolution}；"
                f"比例={route.aspect_ratio}；请求尺寸={requested_size}"
            )
            try:
                data = await self._request_image_data(
                    session, route, timeout, parts_for_route, prompt
                )
            except Exception as exc:
                message = f"{route_label}：{self._error_text(exc)}"
                errors.append(message)
                if self._is_policy_violation_error(exc):
                    raise RuntimeError(f"图片生成触发安全拒绝：{message}") from exc
                logger.debug(
                    f"{LOG_PREFIX} {self._mode_label(mode)}接口通道失败，尝试下一条：{message}"
                )
                continue

            if not isinstance(data, dict):
                message = f"{route_label}：图片接口返回格式不是对象"
                errors.append(message)
                logger.debug(
                    f"{LOG_PREFIX} {self._mode_label(mode)}接口通道失败，尝试下一条：{message}"
                )
                continue
            image_bytes = (
                openai.extract_image(data)
                if route.protocol == "openai"
                else gemini.extract_image(data)
            )
            if image_bytes:
                width, height = _image_dimensions(image_bytes)
                actual_size = f"{width}×{height}" if width and height else "未知"
                logger.debug(
                    f"{LOG_PREFIX} 图片完成：通道={route_label}；"
                    f"请求={requested_size}；实际={actual_size}"
                )
                if (
                    route.protocol == "openai"
                    and width
                    and height
                    and actual_size != requested_size
                ):
                    logger.warning(
                        f"{LOG_PREFIX} 图片接口返回尺寸与请求不一致："
                        f"通道={route_label}；"
                        f"请求={requested_size}；实际={actual_size}"
                    )
                return image_bytes, route
            message = f"{route_label}：图片接口未返回图片：{upstream_error_text(data)}"
            errors.append(message)
            if self._is_policy_violation_text(message):
                raise RuntimeError(f"图片生成触发安全拒绝：{message}")
            logger.debug(
                f"{LOG_PREFIX} {self._mode_label(mode)}接口通道失败，尝试下一条：{message}"
            )

        raise RuntimeError(
            f"图片生成全部{self._mode_label(mode)}接口均失败：{'；'.join(errors[-8:])}"
        )

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
            session.post(
                request.url, data=request.form, headers=request.headers, timeout=timeout
            )
            if request.form is not None
            else session.post(
                request.url,
                json=request.payload,
                headers=request.headers,
                timeout=timeout,
            )
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
            current = getattr(current, "__cause__", None) or getattr(
                current, "__context__", None
            )
        return False

    @staticmethod
    def _is_policy_violation_text(text: str) -> bool:
        lowered = str(text or "").lower()
        return "content_policy_violation" in lowered or "policy_violation" in lowered

    def _build_request(self, route: ImageRoute, parts: list[dict[str, Any]]):
        kwargs = {"resolution": route.resolution, "aspect_ratio": route.aspect_ratio}
        return (
            openai.build_request(route, parts, **kwargs)
            if route.protocol == "openai"
            else gemini.build_request(route, parts, **kwargs)
        )

    @staticmethod
    def _route_with_options(
        route: ImageRoute, aspect_ratio: str, resolution: str
    ) -> ImageRoute:
        aspect_ratio = str(aspect_ratio or "").strip()
        resolution = str(resolution or "").strip().upper()
        if resolution and resolution not in IMAGE_RESOLUTIONS:
            raise ValueError("图片输出分辨率只能是 1K、2K 或 4K")
        effective_aspect_ratio = (
            aspect_ratio if aspect_ratio in IMAGE_ASPECT_RATIOS else route.aspect_ratio
        )
        effective_resolution = resolution or str(route.resolution or "").strip().upper()
        if effective_resolution not in IMAGE_RESOLUTIONS:
            raise ValueError("图片通道分辨率只能是 1K、2K 或 4K")
        resolution_source = "本轮指定" if resolution else "通道配置"
        if (
            effective_aspect_ratio == route.aspect_ratio
            and effective_resolution == route.resolution
            and resolution_source == route.resolution_source
        ):
            return route
        return ImageRoute(
            api_url=route.api_url,
            api_key=route.api_key,
            model=route.model,
            label=route.label,
            protocol=route.protocol,
            resolution=effective_resolution,
            aspect_ratio=effective_aspect_ratio,
            timeout_seconds=route.timeout_seconds,
            origin=route.origin,
            resolution_source=resolution_source,
        )

    @staticmethod
    def _protocol_label(protocol: str) -> str:
        return "GPT" if str(protocol or "").lower() == "openai" else "Gemini"

    @staticmethod
    def _request_size_label(route: ImageRoute) -> str:
        if route.protocol == "openai":
            return openai.size_for(route.resolution, route.aspect_ratio).replace(
                "x", "×"
            )
        return f"{route.resolution}档位"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None)
            )
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

    async def _request_routes(
        self, mode: str, *, protocol: str = ""
    ) -> list[ImageRoute]:
        protocol = routes.normalize_image_provider(protocol)
        mode_label = self._mode_label(mode)
        return [
            routes.make_route(
                channel.api_url,
                channel.api_key,
                channel.model,
                channel.group_name or f"{mode_label}接口通道 {index}",
                channel.protocol,
                channel.resolution,
                channel.aspect_ratio,
                channel.timeout_seconds,
            )
            for index, channel in enumerate(
                (
                    channel
                    for channel in self._channels_for_mode(mode)
                    if not protocol
                    or str(getattr(channel, "protocol", "") or "").lower() == protocol
                ),
                start=1,
            )
        ]

    @staticmethod
    def _error_text(exc: Exception) -> str:
        text = str(exc).strip()
        return f"{type(exc).__name__}: {text}" if text else type(exc).__name__

    async def _save_image(self, image_bytes: bytes, *, prefix: str = "image") -> Path:
        await asyncio.to_thread(self.output_dir.mkdir, parents=True, exist_ok=True)
        _, ext = image_mime_and_ext(image_bytes)
        digest = hashlib.sha256(image_bytes).hexdigest()[:16]
        safe_prefix = "".join(
            char
            for char in str(prefix or "image").strip().lower()
            if char.isalnum() or char == "_"
        )
        path = (
            self.output_dir
            / f"{safe_prefix or 'image'}_{int(time.time())}_{digest}{ext}"
        )
        await asyncio.to_thread(path.write_bytes, image_bytes)
        return path
