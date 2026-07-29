from __future__ import annotations

import asyncio
import hashlib
import secrets
from pathlib import Path

from ...paths import runtime_data_root
from .codec import encode_base64, file_sha256

REFERENCE_IMAGE_MAX_MB = 12
IMAGE_GALLERIES = {
    "character_reference": {
        "dir": ("references",),
        "prefix": "character_reference",
        "label": "参考图",
    },
    "friend_reference": {
        "dir": ("references", "friends"),
        "prefix": "friend_reference",
        "label": "好友参考图",
    },
}


class PortalReferenceMixin:
    def _page_gallery_spec(self, gallery: str) -> dict:
        key = str(gallery or "character_reference").strip() or "character_reference"
        spec = IMAGE_GALLERIES.get(key)
        if not spec:
            raise ValueError("未知图片库")
        return spec

    @staticmethod
    def _page_friend_reference_key(profile_id: str) -> str:
        value = str(profile_id or "").strip()
        if not value:
            raise ValueError("请先选择好友关系档案")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

    def _page_gallery_dir(self, gallery: str, profile_id: str = "") -> Path:
        target = runtime_data_root(getattr(self.runtime, "data_path", None))
        for part in self._page_gallery_spec(gallery)["dir"]:
            target /= part
        if gallery == "friend_reference":
            target /= self._page_friend_reference_key(profile_id)
        return target

    def _page_reference_dir(self) -> Path:
        return self._page_gallery_dir("character_reference")

    def _page_safe_gallery_path(
        self, gallery: str, path_text: str, profile_id: str = ""
    ) -> Path:
        target_dir = self._page_gallery_dir(gallery, profile_id).resolve()
        path = Path(str(path_text or "")).expanduser().resolve()
        try:
            path.relative_to(target_dir)
        except ValueError as exc:
            raise ValueError("只能操作插件管理的图片") from exc
        return path

    def _page_safe_reference_path(self, path_text: str) -> Path:
        return self._page_safe_gallery_path("character_reference", path_text)

    @staticmethod
    def _page_reference_mime(path: Path) -> str:
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(path.suffix.lower(), "image/png")

    async def _page_reference_image_upload(
        self, gallery: str = "character_reference", profile_id: str = ""
    ):
        async def handler():
            spec = self._page_gallery_spec(gallery)
            target_dir = self._page_gallery_dir(gallery, profile_id)
            temporary = target_dir / f".reference_{secrets.token_hex(8)}.upload"
            try:
                try:
                    upload = await self._page_receive_upload(
                        temporary,
                        max_bytes=REFERENCE_IMAGE_MAX_MB * 1024 * 1024,
                    )
                except ValueError as exc:
                    if str(exc) == "上传文件超过大小限制":
                        raise ValueError(
                            f"{spec['label']}不能超过 {REFERENCE_IMAGE_MAX_MB} MB"
                        ) from exc
                    raise
                filename = str(upload.get("filename") or "")
                suffix = Path(filename).suffix.lower()
                mime = str(upload.get("mime") or "").lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                    raise ValueError("仅支持 PNG、JPG、WEBP 或 GIF 图片")
                if mime and not mime.startswith("image/"):
                    raise ValueError("请上传图片文件")
                digest = (await asyncio.to_thread(file_sha256, temporary))[:16]
                target = target_dir / f"{spec['prefix']}_{digest}{suffix}"
                if not await asyncio.to_thread(target.is_file):
                    await asyncio.to_thread(temporary.replace, target)
                return {
                    "item": {
                        "path": str(target),
                        "name": filename.strip() or target.name,
                        "mime": mime or self._page_reference_mime(target),
                        "size": int(upload.get("size") or 0),
                    }
                }
            finally:
                await asyncio.to_thread(temporary.unlink, missing_ok=True)

        return await self._page_json(handler)

    async def _page_reference_image_delete(
        self, gallery: str = "character_reference", profile_id: str = ""
    ):
        async def handler():
            body = await self._page_json_body()
            target = self._page_safe_gallery_path(
                gallery, str(body.get("path") or ""), profile_id
            )
            if await asyncio.to_thread(target.is_file):
                await asyncio.to_thread(lambda: target.unlink(missing_ok=True))
            return {"path": str(target)}

        return await self._page_json(handler)

    async def _page_reference_image_preview(
        self, gallery: str = "character_reference", profile_id: str = ""
    ):
        async def handler():
            body = await self._page_json_body()
            target = self._page_safe_gallery_path(
                gallery, str(body.get("path") or ""), profile_id
            )
            if not await asyncio.to_thread(target.is_file):
                raise ValueError("图片不存在")
            max_bytes = REFERENCE_IMAGE_MAX_MB * 1024 * 1024
            if await asyncio.to_thread(lambda: target.stat().st_size) > max_bytes:
                raise ValueError(f"图片不能超过 {REFERENCE_IMAGE_MAX_MB} MB")
            data = await asyncio.to_thread(target.read_bytes)
            encoded = await asyncio.to_thread(encode_base64, data)
            return {
                "data_url": f"data:{self._page_reference_mime(target)};base64,{encoded}"
            }

        return await self._page_json(handler)

    async def page_character_reference_upload(self):
        return await self._page_reference_image_upload()

    async def page_character_reference_delete(self):
        return await self._page_reference_image_delete()

    async def page_character_reference_preview(self):
        return await self._page_reference_image_preview()

    async def page_friend_reference_upload(self, profile_id: str = ""):
        return await self._page_reference_image_upload("friend_reference", profile_id)

    async def page_friend_reference_delete(self, profile_id: str = ""):
        return await self._page_reference_image_delete("friend_reference", profile_id)

    async def page_friend_reference_preview(self, profile_id: str = ""):
        return await self._page_reference_image_preview("friend_reference", profile_id)
