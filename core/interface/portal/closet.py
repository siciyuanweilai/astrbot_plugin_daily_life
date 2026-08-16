from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from ...models import (
    STYLE_CATALOG_KIND_SET,
    StyleCatalogItemRecord,
)
from ...paths import runtime_data_root
from .codec import encode_base64, file_sha256

CLOSET_IMAGE_MAX_MB = 20
CLOSET_BACKUP_MAX_MB = 500
CLOSET_BACKUP_MAX_FILES = 1000
CLOSET_BACKUP_FORMAT = "daily_life_closet_backup"
CLOSET_BACKUP_VERSION = 1
CLOSET_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


class PortalClosetMixin:
    async def page_closet_list(self):
        return await self._page_json(self._page_closet_payload)

    async def page_closet_import(self):
        async def handler():
            kind = "auto"
            note = ""
            target = self._page_closet_transfer_path("import", ".upload")
            try:
                upload = await self._page_receive_upload(
                    target,
                    max_bytes=CLOSET_IMAGE_MAX_MB * 1024 * 1024,
                )
                filename = self._page_closet_text(upload.get("filename"), 160)
                mime = self._page_closet_text(upload.get("mime"), 80).lower()
                suffix = Path(filename).suffix.lower()
                if suffix not in CLOSET_IMAGE_SUFFIXES or (
                    mime and not mime.startswith("image/")
                ):
                    raise ValueError("仅支持 PNG、JPG、WEBP、GIF 或 BMP 图片")
                detail = "；".join(part for part in (note, filename) if part)
                learned = await self.runtime._learn_style_catalog_image(
                    None,
                    str(target),
                    source_kind="manual",
                    source_scope="dashboard",
                    note=detail,
                    kind=kind,
                )
                if not learned:
                    raise ValueError("图片中没有足够清晰、可保存的衣橱信息")
                payload = await self._page_closet_payload()
                payload["imported"] = [self._page_closet_item(item) for item in learned]
                return payload
            finally:
                await asyncio.to_thread(target.unlink, missing_ok=True)

        return await self._page_json(handler)

    async def page_closet_browse(self):
        async def handler():
            body = await self._page_json_body()
            query = self._page_closet_text(body.get("query"), 500)
            if not query:
                raise ValueError("请输入要学习的穿搭、单品或造型需求")
            kind = self._page_closet_kind(body.get("kind"))
            note = self._page_closet_text(body.get("note"), 500)
            try:
                count = max(1, min(int(body.get("count") or 3), 12))
            except (TypeError, ValueError):
                count = 3
            result = await self.runtime.life_style_browse_learn(
                self._page_closet_event(),
                query,
                kind=kind,
                count=count,
                note=note,
            )
            if str(getattr(result, "status", "ok")) != "ok":
                raise ValueError(str(result) or "联网学习失败")
            payload = await self._page_closet_payload()
            payload["message"] = str(result)
            return payload

        return await self._page_json(handler)

    async def page_closet_preview(self):
        async def handler():
            body = await self._page_json_body()
            item = await self._page_closet_record(body.get("id"))
            path = self._page_safe_closet_path(item.image_path)
            if not await asyncio.to_thread(path.is_file):
                raise ValueError("衣橱素材图片不存在")
            size = await asyncio.to_thread(lambda: path.stat().st_size)
            if size > CLOSET_IMAGE_MAX_MB * 1024 * 1024:
                raise ValueError(f"衣橱素材图片不能超过 {CLOSET_IMAGE_MAX_MB} MB")
            data = await asyncio.to_thread(path.read_bytes)
            encoded = await asyncio.to_thread(encode_base64, data)
            return {"data_url": f"data:{self._page_closet_mime(path)};base64,{encoded}"}

        return await self._page_json(handler)

    async def page_closet_status(self):
        async def handler():
            body = await self._page_json_body()
            ids = self._page_closet_ids(body)
            if not ids:
                raise ValueError("请选择要更新的衣橱素材")
            status = self._page_closet_text(body.get("status"), 24).lower()
            if status == "archived":
                status = "disabled"
            if status not in {"active", "pending", "rejected", "disabled"}:
                raise ValueError("衣橱素材状态无效")
            updated = await self.runtime.archive.set_style_catalog_status(ids, status)
            if updated <= 0:
                raise ValueError("没有找到可更新的衣橱素材")
            payload = await self._page_closet_payload()
            payload["updated_records"] = updated
            return payload

        return await self._page_json(handler)

    async def page_closet_feedback(self):
        async def handler():
            body = await self._page_json_body()
            ids = self._page_closet_ids(body)
            if not ids:
                raise ValueError("请选择要反馈的衣橱素材")
            sentiment = self._page_closet_text(body.get("sentiment"), 24).lower()
            settings = {
                "prefer": (0.35, "active", "面板标记喜欢"),
                "dislike": (-0.45, "rejected", "面板标记不喜欢"),
                "neutral": (0.0, "", "面板清除倾向"),
                "disable": (0.0, "disabled", "面板停用"),
                "archive": (0.0, "disabled", "面板停用"),
            }
            if sentiment not in settings:
                raise ValueError("衣橱反馈类型无效")
            delta, status, reason = settings[sentiment]
            updated = 0
            for item_id in ids:
                saved = await self.runtime.archive.add_style_catalog_feedback(
                    item_id,
                    scope="dashboard",
                    feedback=reason,
                    sentiment=sentiment,
                    score_delta=delta,
                    reason=reason,
                    status=status,
                )
                updated += int(saved is not None)
            if updated <= 0:
                raise ValueError("没有找到可更新的衣橱素材")
            payload = await self._page_closet_payload()
            payload["updated_records"] = updated
            return payload

        return await self._page_json(handler)

    async def page_closet_review(self):
        async def handler():
            body = await self._page_json_body()
            try:
                item_id = int(body.get("id") or 0)
            except (TypeError, ValueError):
                item_id = 0
            if item_id <= 0:
                raise ValueError("请选择要重新识别的衣橱素材")
            updated = await self.runtime.review_style_catalog_item(
                item_id,
                note=self._page_closet_text(body.get("note"), 500),
            )
            payload = await self._page_closet_payload()
            payload["item"] = self._page_closet_item(updated)
            return payload

        return await self._page_json(handler)

    async def page_closet_delete(self):
        async def handler():
            body = await self._page_json_body()
            ids = self._page_closet_ids(body)
            if not ids:
                raise ValueError("请选择要删除的衣橱素材")
            records = await self.runtime.archive.get_style_catalog_items(
                status="", ids=ids, limit=len(ids)
            )
            deleted_records = await self.runtime.archive.delete_style_catalog_items(ids)
            deleted_files = await self._page_delete_closet_files(records)
            payload = await self._page_closet_payload()
            payload.update(
                {
                    "deleted_records": deleted_records,
                    "deleted_files": deleted_files,
                }
            )
            return payload

        return await self._page_json(handler)

    async def page_closet_backup(self):
        items = await self.runtime.archive.get_style_catalog_items(
            status="", limit=500
        )
        target = self._page_closet_transfer_path("backup", ".zip")
        meta = await asyncio.to_thread(self._page_write_closet_backup, items, target)
        self._page_schedule_closet_cleanup(target)
        return await self._page_send_download(
            target,
            mime="application/zip",
            filename=meta["filename"],
        )

    async def page_closet_restore(self):
        async def handler():
            target = self._page_closet_transfer_path("restore", ".zip")
            try:
                upload = await self._page_receive_upload(
                    target,
                    max_bytes=CLOSET_BACKUP_MAX_MB * 1024 * 1024,
                )
                if not str(upload.get("filename") or "").lower().endswith(".zip"):
                    raise ValueError("请选择 ZIP 衣橱备份文件")
                records, meta = await asyncio.to_thread(
                    self._page_read_closet_backup, target
                )
            finally:
                await asyncio.to_thread(target.unlink, missing_ok=True)
            restored = 0
            for record in records:
                restored += int(
                    await self.runtime.archive.upsert_style_catalog_item(record)
                    is not None
                )
            payload = await self._page_closet_payload()
            payload.update(
                {
                    "restored": restored,
                    "files": meta["files"],
                    "skipped_records": meta["skipped_records"],
                }
            )
            return payload

        return await self._page_json(handler)

    async def _page_closet_payload(self) -> dict:
        items = await self.runtime.archive.get_style_catalog_items(
            status="", limit=500
        )
        payload = [self._page_closet_item(item) for item in items]
        source_groups = {
            str(item.get("source_group_key") or item.get("id")) for item in payload
        }
        return {
            "items": payload,
            "stats": {
                "total": len(payload),
                "source_groups": len(source_groups),
                "web": sum(item["source_kind"] in {"web_image", "product_image"} for item in payload),
                "active": sum(item["status"] == "active" for item in payload),
                "pending": sum(item["status"] == "pending" for item in payload),
                "disabled": sum(item["status"] == "disabled" for item in payload),
                "rejected": sum(item["status"] == "rejected" for item in payload),
                "outfit": sum(item["kind"] == "outfit" for item in payload),
                "top": sum(item["kind"] == "top" for item in payload),
                "bottom": sum(item["kind"] == "bottom" for item in payload),
                "footwear": sum(item["kind"] == "footwear" for item in payload),
                "accessory": sum(item["kind"] == "accessory" for item in payload),
                "hair": sum(item["kind"] == "hair" for item in payload),
                "makeup": sum(item["kind"] == "makeup" for item in payload),
                "nails": sum(item["kind"] == "nails" for item in payload),
                "liked": sum(float(item["preference_score"] or 0.0) > 0 for item in payload),
                "used": sum(int(item["used_count"] or 0) > 0 for item in payload),
            },
        }

    def _page_closet_item(self, item: StyleCatalogItemRecord) -> dict:
        data = item.as_dict()
        attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
        status = self._page_closet_text(data.get("status"), 24).lower()
        if status == "archived":
            status = "disabled"
        if status not in {"active", "pending", "disabled", "rejected"}:
            status = "pending"
        data["status"] = status
        try:
            used_count = max(0, int(attributes.get("used_count") or 0))
        except (TypeError, ValueError):
            used_count = 0
        path = self._page_closet_path(str(item.image_path or ""))
        source_host = self._page_closet_text(attributes.get("source_host"), 160)
        if not source_host and item.source_url:
            try:
                source_host = self._page_closet_text(urlsplit(item.source_url).hostname, 160)
            except ValueError:
                source_host = ""
        data.update(
            {
                "source_group_key": str(item.source_image_hash or f"id:{item.id}"),
                "source_host": source_host,
                "source_batch_id": self._page_closet_text(attributes.get("source_batch_id"), 120),
                "source_query": self._page_closet_text(attributes.get("source_query"), 500),
                "used_count": used_count,
                "file_name": path.name if path else Path(str(item.image_path or "")).name,
                "file_size": path.stat().st_size if path and path.is_file() else 0,
                "is_cached": bool(path and path.is_file()),
                "preview_available": bool(path and path.is_file()),
                "has_accessories": item.kind == "accessory"
                or bool(attributes.get("accessories")),
                "has_makeup": item.kind == "makeup"
                or bool(attributes.get("makeup")),
                "has_nails": item.kind == "nails" or bool(attributes.get("nails")),
            }
        )
        return data

    async def _page_closet_record(self, value: object) -> StyleCatalogItemRecord:
        try:
            item_id = int(value or 0)
        except (TypeError, ValueError):
            item_id = 0
        item = await self.runtime.archive.get_style_catalog_item(item_id)
        if not item:
            raise ValueError("衣橱素材不存在")
        return item

    def _page_closet_path(self, path_text: str) -> Path | None:
        try:
            return self._page_safe_closet_path(path_text)
        except ValueError:
            return None

    def _page_safe_closet_path(self, path_text: str) -> Path:
        root = (
            runtime_data_root(getattr(self.runtime, "data_path", None))
            / "style_catalog"
        ).resolve()
        path = Path(str(path_text or "")).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("只能操作插件管理的衣橱图片") from exc
        return path

    async def _page_delete_closet_files(
        self, records: list[StyleCatalogItemRecord]
    ) -> int:
        remaining = await self.runtime.archive.get_style_catalog_items(
            status="", limit=500
        )
        used_paths = {str(item.image_path or "") for item in remaining}
        deleted = 0
        seen: set[str] = set()
        for item in records:
            path_text = str(item.image_path or "")
            if not path_text or path_text in used_paths or path_text in seen:
                continue
            seen.add(path_text)
            path = self._page_closet_path(path_text)
            if path and await asyncio.to_thread(path.is_file):
                await asyncio.to_thread(path.unlink, missing_ok=True)
                deleted += 1
        return deleted

    def _page_write_closet_backup(
        self, items: list[StyleCatalogItemRecord], target: Path
    ) -> dict:
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": CLOSET_BACKUP_FORMAT,
            "version": CLOSET_BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "items": [],
        }
        written: dict[str, str] = {}
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for record in items:
                item = record.as_dict()
                item["original_id"] = item.pop("id", 0)
                item["backup_asset"] = ""
                path = self._page_closet_path(str(record.image_path or ""))
                if path and path.is_file():
                    digest = file_sha256(path)
                    asset_name = written.get(digest)
                    if not asset_name:
                        asset_name = f"assets/{digest}{path.suffix.lower()}"
                        package.write(path, asset_name)
                        written[digest] = asset_name
                    item["backup_asset"] = asset_name
                    item["backup_sha256"] = digest
                    item["image_path"] = ""
                manifest["items"].append(item)
            package.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return {
            "filename": f"daily_life_closet_backup_{stamp}.zip",
            "count": len(items),
            "files": len(written),
        }

    def _page_read_closet_backup(
        self, target: Path
    ) -> tuple[list[StyleCatalogItemRecord], dict]:
        try:
            package = zipfile.ZipFile(target)
        except zipfile.BadZipFile as exc:
            raise ValueError("衣橱备份文件不是有效 ZIP") from exc
        with package:
            infos = package.infolist()
            if len(infos) > CLOSET_BACKUP_MAX_FILES + 1:
                raise ValueError("衣橱备份文件条目过多")
            total_size = 0
            for info in infos:
                name = PurePosixPath(info.filename)
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("衣橱备份包含不安全路径")
                total_size += max(int(info.file_size or 0), 0)
                if total_size > CLOSET_BACKUP_MAX_MB * 1024 * 1024:
                    raise ValueError("衣橱备份解压后超过大小限制")
            try:
                manifest = json.loads(package.read("manifest.json").decode("utf-8"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("衣橱备份缺少有效清单") from exc
            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != CLOSET_BACKUP_FORMAT
                or int(manifest.get("version") or 0) != CLOSET_BACKUP_VERSION
            ):
                raise ValueError("衣橱备份格式或版本不受支持")
            root = runtime_data_root(getattr(self.runtime, "data_path", None)) / "style_catalog"
            root.mkdir(parents=True, exist_ok=True)
            records: list[StyleCatalogItemRecord] = []
            restored_files = 0
            skipped = 0
            for raw in manifest.get("items") or []:
                if not isinstance(raw, dict):
                    skipped += 1
                    continue
                item = dict(raw)
                item.pop("original_id", None)
                asset_name = self._page_closet_text(item.pop("backup_asset", ""), 300)
                expected_hash = self._page_closet_text(
                    item.pop("backup_sha256", ""), 64
                ).lower()
                if asset_name:
                    try:
                        info = package.getinfo(asset_name)
                    except KeyError:
                        skipped += 1
                        continue
                    if info.file_size > CLOSET_IMAGE_MAX_MB * 1024 * 1024:
                        skipped += 1
                        continue
                    data = package.read(info)
                    digest = hashlib.sha256(data).hexdigest()
                    if expected_hash and digest != expected_hash:
                        skipped += 1
                        continue
                    suffix = Path(asset_name).suffix.lower()
                    if suffix not in CLOSET_IMAGE_SUFFIXES:
                        skipped += 1
                        continue
                    path = root / f"style_{digest[:24]}{suffix}"
                    if not path.is_file():
                        path.write_bytes(data)
                        restored_files += 1
                    item["image_path"] = str(path)
                item["id"] = 0
                record = StyleCatalogItemRecord.from_value(item)
                if record:
                    records.append(record)
                else:
                    skipped += 1
            return records, {"files": restored_files, "skipped_records": skipped}

    def _page_closet_transfer_path(self, prefix: str, suffix: str) -> Path:
        root = runtime_data_root(getattr(self.runtime, "data_path", None)) / "transfer"
        return root / f"closet_{prefix}_{secrets.token_hex(8)}{suffix}"

    def _page_schedule_closet_cleanup(self, path: Path) -> None:
        scheduler = getattr(self.runtime, "_schedule_background_task", None)
        if not callable(scheduler):
            return

        async def cleanup() -> None:
            await asyncio.sleep(300)
            await asyncio.to_thread(path.unlink, missing_ok=True)

        scheduler(
            cleanup(),
            label="衣橱临时文件清理",
            key=f"closet_transfer:{path.name}",
        )

    @staticmethod
    def _page_closet_kind(value: object) -> str:
        kind = str(value or "auto").strip().lower()
        return kind if kind in {"auto", "both", *STYLE_CATALOG_KIND_SET} else "auto"

    @staticmethod
    def _page_closet_text(value: object, limit: int = 0) -> str:
        text = " ".join(str(value or "").strip().split())
        return text[:limit].strip() if limit > 0 else text

    @staticmethod
    def _page_closet_ids(body: dict) -> list[int]:
        values = body.get("ids")
        if values is None:
            values = [body.get("id")]
        if not isinstance(values, list):
            values = [values]
        result = []
        for value in values:
            try:
                item_id = int(value or 0)
            except (TypeError, ValueError):
                continue
            if item_id > 0 and item_id not in result:
                result.append(item_id)
        return result[:500]

    @staticmethod
    def _page_closet_mime(path: Path) -> str:
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }.get(path.suffix.lower(), "image/jpeg")

    @staticmethod
    def _page_closet_event():
        class DashboardEvent:
            unified_msg_origin = "dashboard"

        return DashboardEvent()


__all__ = ["PortalClosetMixin"]
