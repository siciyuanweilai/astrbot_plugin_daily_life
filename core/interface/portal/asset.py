from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path

from ...paths import runtime_data_root

REFERENCE_IMAGE_MAX_MB = 12
IMAGE_GALLERIES = {
    "character_reference": {
        "dir": ("references",),
        "prefix": "character_reference",
        "label": "参考图",
    },
}


class PortalReferenceMixin:
    @staticmethod
    def _page_decode_image_data_url(data_url: str, max_mb: int) -> tuple[bytes, str, str]:
        body = str(data_url or "").strip()
        if not body.startswith("data:image/") or ";base64," not in body:
            raise ValueError("请上传图片文件")
        header, encoded = body.split(",", 1)
        mime = header[5:].split(";", 1)[0].strip().lower()
        suffix = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(mime)
        if not suffix:
            raise ValueError("仅支持 PNG、JPG、WEBP 或 GIF 图片")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("图片数据解析失败") from exc
        max_bytes = max(1, min(int(max_mb or 12), 50)) * 1024 * 1024
        if not data:
            raise ValueError("图片内容为空")
        if len(data) > max_bytes:
            raise ValueError(f"图片不能超过 {max_mb} MB")
        return data, suffix, mime

    def _page_gallery_spec(self, gallery: str) -> dict:
        key = str(gallery or "character_reference").strip() or "character_reference"
        spec = IMAGE_GALLERIES.get(key)
        if not spec:
            raise ValueError("未知图片库")
        return spec

    def _page_gallery_dir(self, gallery: str) -> Path:
        target = runtime_data_root(getattr(self.runtime, "data_path", None))
        for part in self._page_gallery_spec(gallery)["dir"]:
            target /= part
        return target

    def _page_reference_dir(self) -> Path:
        return self._page_gallery_dir("character_reference")

    def _page_safe_gallery_path(self, gallery: str, path_text: str) -> Path:
        target_dir = self._page_gallery_dir(gallery).resolve()
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

    async def _page_reference_image_upload(self):
        async def handler():
            body = await self._page_json_body()
            spec = self._page_gallery_spec("character_reference")
            image_bytes, suffix, mime = self._page_decode_image_data_url(
                str(body.get("image") or ""),
                REFERENCE_IMAGE_MAX_MB,
            )
            target_dir = self._page_gallery_dir("character_reference")
            target_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(image_bytes).hexdigest()[:16]
            target = target_dir / f"{spec['prefix']}_{digest}{suffix}"
            await asyncio.to_thread(target.write_bytes, image_bytes)
            return {
                "item": {
                    "path": str(target),
                    "name": str(body.get("filename") or target.name).strip() or target.name,
                    "mime": mime,
                    "size": len(image_bytes),
                }
            }

        return await self._page_json(handler)

    async def _page_reference_image_delete(self):
        async def handler():
            body = await self._page_json_body()
            target = self._page_safe_reference_path(str(body.get("path") or ""))
            if target.is_file():
                await asyncio.to_thread(lambda: target.unlink(missing_ok=True))
            return {"path": str(target)}

        return await self._page_json(handler)

    async def _page_reference_image_preview(self):
        async def handler():
            body = await self._page_json_body()
            target = self._page_safe_reference_path(str(body.get("path") or ""))
            if not target.is_file():
                raise ValueError("图片不存在")
            max_bytes = REFERENCE_IMAGE_MAX_MB * 1024 * 1024
            if target.stat().st_size > max_bytes:
                raise ValueError(f"图片不能超过 {REFERENCE_IMAGE_MAX_MB} MB")
            data = await asyncio.to_thread(target.read_bytes)
            encoded = base64.b64encode(data).decode("ascii")
            return {"data_url": f"data:{self._page_reference_mime(target)};base64,{encoded}"}

        return await self._page_json(handler)

    async def page_character_reference_upload(self):
        return await self._page_reference_image_upload()

    async def page_character_reference_delete(self):
        return await self._page_reference_image_delete()

    async def page_character_reference_preview(self):
        return await self._page_reference_image_preview()
