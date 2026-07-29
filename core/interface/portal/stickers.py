from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath

from ...models import EmojiAssetRecord
from .codec import encode_base64, file_sha256


EMOJI_PREVIEW_MAX_MB = 20
EMOJI_IMPORT_DEFAULT_MAX_MB = 5
EMOJI_BACKUP_MAX_MB = 200
EMOJI_BACKUP_MAX_FILES = 1000
EMOJI_BACKUP_FORMAT = "daily_life_emoji_backup"
EMOJI_BACKUP_VERSION = 1
EMOJI_STILL_PREVIEW_SIZE = 160


class PortalEmojiMixin:
    async def page_emoji_list(self):
        async def handler():
            return await self._page_emoji_payload()

        return await self._page_json(handler)

    async def page_emoji_import(self):
        async def handler():
            cached_path, fingerprint, display_name = await self._page_receive_emoji(
                self._page_emoji_transfer_path("emoji", ".upload")
            )
            existing = await self.runtime.archive.get_emoji_asset_by_hash(fingerprint)
            item = self._page_imported_emoji_record(
                existing,
                fingerprint=fingerprint,
                file_path=str(cached_path),
                source_url="",
                display_name=display_name,
            )
            saved = await self.runtime.archive.upsert_emoji_asset(item)
            if saved:
                await self._page_schedule_emoji_import_vision(saved)
            return {
                **(await self._page_emoji_payload()),
                "item": self._page_emoji_item(saved) if saved else None,
                "imported": bool(saved),
            }

        return await self._page_json(handler)

    async def page_emoji_preview(self):
        async def handler():
            body = await self._page_json_body()
            emoji_id = int(body.get("id") or 0)
            still = self._page_bool(body.get("still")) or str(
                body.get("mode") or ""
            ).strip().lower() in {
                "still",
                "thumb",
                "thumbnail",
            }
            asset = await self._page_emoji_asset_by_id(emoji_id)
            if not asset:
                raise ValueError("表情素材不存在")
            path_text = str(asset.file_path or "").strip()
            if self.runtime._emoji_asset_is_remote(path_text):
                return (
                    {"data_url": self._page_emoji_placeholder_data_url(path_text)}
                    if still
                    else {"url": path_text}
                )
            path = self._page_safe_emoji_path(path_text)
            if not await asyncio.to_thread(path.is_file):
                raise ValueError("表情素材文件不存在")
            max_bytes = EMOJI_PREVIEW_MAX_MB * 1024 * 1024
            size = await asyncio.to_thread(lambda: path.stat().st_size)
            if not still and size > max_bytes:
                raise ValueError(f"表情素材不能超过 {EMOJI_PREVIEW_MAX_MB} MB")
            data, mime = await self._page_emoji_preview_data(path, still=still)
            encoded = await asyncio.to_thread(encode_base64, data)
            return {"data_url": f"data:{mime};base64,{encoded}", "still": bool(still)}

        return await self._page_json(handler)

    async def page_emoji_delete(self):
        async def handler():
            body = await self._page_json_body()
            ids = self._page_emoji_ids(body)
            if not ids:
                raise ValueError("请选择要删除的表情素材")
            assets = await self._page_emoji_assets_by_ids(ids)
            deleted_files = await self._page_delete_emoji_files(assets)
            deleted_records = await self.runtime.archive.delete_emoji_assets(ids)
            cleanup = getattr(self.runtime, "cleanup_emoji_asset_cache", None)
            if callable(cleanup):
                await cleanup()
            return {
                **(await self._page_emoji_payload()),
                "deleted_records": deleted_records,
                "deleted_files": deleted_files,
            }

        return await self._page_json(handler)

    async def page_emoji_sendable(self):
        async def handler():
            body = await self._page_json_body()
            ids = self._page_emoji_ids(body)
            if not ids:
                raise ValueError("请选择要更新的表情素材")
            assets = await self._page_emoji_assets_by_ids(ids)
            if not assets:
                raise ValueError("表情素材不存在")
            sendable = self._page_bool(body.get("sendable"))
            saved_items = []
            for asset in assets:
                saved = await self.runtime.archive.upsert_emoji_asset(
                    EmojiAssetRecord(
                        **{
                            **asset.as_dict(),
                            "sendable": sendable,
                            "status": asset.status or "ready",
                        }
                    )
                )
                if saved:
                    saved_items.append(saved)
            return {
                **(await self._page_emoji_payload()),
                "item": self._page_emoji_item(saved_items[0])
                if len(saved_items) == 1
                else None,
                "updated_records": len(saved_items),
            }

        return await self._page_json(handler)

    async def page_emoji_backup(self):
        assets = await self.runtime.archive.get_emoji_assets(limit=0)
        target = self._page_emoji_transfer_path("backup", ".zip")
        meta = await asyncio.to_thread(
            self._page_build_emoji_backup_file,
            assets,
            target,
        )
        self._page_schedule_emoji_transfer_cleanup(target)
        return await self._page_send_download(
            target,
            mime="application/zip",
            filename=meta["filename"],
        )

    async def page_emoji_restore(self):
        async def handler():
            target = self._page_emoji_transfer_path("restore", ".zip")
            try:
                upload = await self._page_receive_upload(
                    target,
                    max_bytes=EMOJI_BACKUP_MAX_MB * 1024 * 1024,
                )
                filename = str(upload.get("filename") or "").lower()
                if not filename.endswith(".zip"):
                    raise ValueError("请选择 ZIP 表情备份文件")
                records, meta = await asyncio.to_thread(
                    self._page_restore_emoji_backup_records,
                    target,
                )
            finally:
                await asyncio.to_thread(target.unlink, missing_ok=True)
            restored = 0
            for record in records:
                existing = await self.runtime.archive.get_emoji_asset_by_hash(
                    record.file_hash
                )
                merged = self._page_merge_restored_emoji_asset(existing, record)
                if await self.runtime.archive.upsert_emoji_asset(merged):
                    restored += 1
            return {
                **(await self._page_emoji_payload()),
                "restored": restored,
                "files": meta["files"],
                "skipped_records": meta["skipped_records"],
            }

        return await self._page_json(handler)

    async def _page_emoji_payload(self) -> dict:
        assets = await self.runtime.archive.get_emoji_assets(limit=0)
        items, stats = await asyncio.to_thread(
            self._page_emoji_payload_from_assets, assets
        )
        return {"items": items, "stats": stats}

    def _page_emoji_payload_from_assets(
        self, assets: list[EmojiAssetRecord]
    ) -> tuple[list[dict], dict]:
        items = [self._page_emoji_item(asset) for asset in assets]
        return items, {
            "total": len(items),
            "ready": sum(1 for item in items if item["status"] == "ready"),
            "sendable": sum(1 for item in items if item["sendable"]),
            "manual": sum(1 for item in items if item["source_kind"] == "manual"),
            "review": sum(1 for item in items if item["source_kind"] == "review"),
            "trusted": sum(1 for item in items if item["source_kind"] == "trusted"),
            "missing": sum(1 for item in items if item["status"] == "missing"),
        }

    def _page_emoji_item(self, asset: EmojiAssetRecord | None) -> dict:
        asset = asset or EmojiAssetRecord()
        data = asset.as_dict()
        path_text = str(asset.file_path or "").strip()
        file_path = self._page_cached_emoji_path(path_text)
        data.update(
            {
                "file_name": file_path.name if file_path else Path(path_text).name,
                "file_size": file_path.stat().st_size
                if file_path and file_path.is_file()
                else 0,
                "is_animated": self._page_emoji_is_animated(path_text, file_path),
                "is_remote": self.runtime._emoji_asset_is_remote(path_text),
                "is_cached": bool(file_path and file_path.is_file()),
                "preview_available": bool(
                    path_text
                    and (
                        self.runtime._emoji_asset_is_remote(path_text)
                        or (file_path and file_path.is_file())
                    )
                ),
                "short_hash": str(asset.file_hash or "")[:10],
            }
        )
        return data

    async def _page_emoji_asset_by_id(self, emoji_id: int) -> EmojiAssetRecord | None:
        if emoji_id <= 0:
            return None
        for asset in await self.runtime.archive.get_emoji_assets(limit=0):
            if int(asset.id or 0) == emoji_id:
                return asset
        return None

    async def _page_emoji_assets_by_ids(self, ids: list[int]) -> list[EmojiAssetRecord]:
        id_set = set(ids)
        return [
            asset
            for asset in await self.runtime.archive.get_emoji_assets(limit=0)
            if int(asset.id or 0) in id_set
        ]

    @staticmethod
    def _page_emoji_ids(body: dict) -> list[int]:
        raw_ids = body.get("ids")
        if raw_ids is None:
            raw_ids = [body.get("id")]
        if not isinstance(raw_ids, list):
            raw_ids = [raw_ids]
        result = []
        for item in raw_ids:
            try:
                emoji_id = int(item or 0)
            except (TypeError, ValueError):
                continue
            if emoji_id > 0:
                result.append(emoji_id)
        return sorted(set(result))

    def _page_cached_emoji_path(self, path_text: str) -> Path | None:
        cache_dir = self.runtime._emoji_asset_cache_dir(create=False)
        if not cache_dir:
            return None
        return self.runtime._resolve_cached_emoji_path(path_text, cache_dir)

    @staticmethod
    def _page_emoji_is_animated(path_text: str, cached_path: Path | None) -> bool:
        name = (
            cached_path.name
            if cached_path
            else str(path_text or "").split("?", 1)[0].split("#", 1)[0]
        )
        return Path(name).suffix.lower() == ".gif"

    def _page_safe_emoji_path(self, path_text: str) -> Path:
        path = self._page_cached_emoji_path(path_text)
        if not path:
            raise ValueError("只能预览插件缓存中的表情素材")
        return path

    async def _page_delete_emoji_files(self, assets: list[EmojiAssetRecord]) -> int:
        deleted = 0
        seen: set[Path] = set()
        for asset in assets:
            path = self._page_cached_emoji_path(str(asset.file_path or ""))
            if (
                not path
                or path in seen
                or not await asyncio.to_thread(path.is_file)
            ):
                continue
            seen.add(path)
            await asyncio.to_thread(lambda target=path: target.unlink(missing_ok=True))
            preview_path = self._page_static_emoji_preview_path(path)
            if await asyncio.to_thread(preview_path.is_file):
                await asyncio.to_thread(
                    lambda target=preview_path: target.unlink(missing_ok=True)
                )
            deleted += 1
        return deleted

    def _page_build_emoji_backup(
        self, assets: list[EmojiAssetRecord]
    ) -> tuple[bytes, dict]:
        buffer = BytesIO()
        meta = self._page_write_emoji_backup(assets, buffer)
        return buffer.getvalue(), meta

    def _page_build_emoji_backup_file(
        self,
        assets: list[EmojiAssetRecord],
        target: Path,
    ) -> dict:
        target.parent.mkdir(parents=True, exist_ok=True)
        meta = self._page_write_emoji_backup(assets, target)
        meta["size"] = target.stat().st_size
        return meta

    def _page_write_emoji_backup(
        self,
        assets: list[EmojiAssetRecord],
        destination: BytesIO | Path,
    ) -> dict:
        manifest = {
            "format": EMOJI_BACKUP_FORMAT,
            "version": EMOJI_BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "items": [],
        }
        used_names: set[str] = set()
        file_count = 0
        skipped_files = 0
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED
        ) as package:
            for asset in assets:
                item = self._page_emoji_backup_item(asset)
                path = self._page_cached_emoji_path(str(asset.file_path or ""))
                if path and path.is_file() and not path.name.endswith(".still.png"):
                    try:
                        size = path.stat().st_size
                    except OSError:
                        size = 0
                    if self._page_backup_file_allowed(path, size):
                        digest = file_sha256(path)
                        file_hash = (
                            str(item.get("file_hash") or digest).strip() or digest
                        )
                        item["file_hash"] = file_hash
                        item["file_path"] = ""
                        item["file_name"] = path.name
                        item["backup_asset"] = self._page_backup_asset_name(
                            file_hash, path.suffix, used_names
                        )
                        item["backup_sha256"] = digest
                        item["backup_size"] = size
                        package.write(path, item["backup_asset"])
                        file_count += 1
                    else:
                        skipped_files += 1
                        item["file_path"] = (
                            ""
                            if not self.runtime._emoji_asset_is_remote(
                                str(asset.file_path or "")
                            )
                            else str(asset.file_path or "")
                        )
                manifest["items"].append(item)
            package.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return {
            "filename": f"daily_life_emoji_backup_{stamp}.zip",
            "count": len(manifest["items"]),
            "files": file_count,
            "skipped_files": skipped_files,
        }

    def _page_emoji_backup_item(self, asset: EmojiAssetRecord) -> dict:
        item = asset.as_dict()
        item["original_id"] = item.pop("id", 0)
        item["backup_asset"] = ""
        item["backup_sha256"] = ""
        item["backup_size"] = 0
        path_text = str(item.get("file_path") or "").strip()
        if not self.runtime._emoji_asset_is_remote(path_text):
            item["file_path"] = ""
        return item

    def _page_backup_file_allowed(self, path: Path, size: int) -> bool:
        if size <= 0:
            return False
        max_bytes = self._page_emoji_max_bytes()
        suffixes = set(
            getattr(
                self.runtime,
                "EMOJI_ASSET_SUFFIXES",
                {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
            )
        )
        return (
            size <= max_bytes
            and path.suffix.lower() in suffixes
            and not path.name.endswith(".still.png")
        )

    def _page_backup_asset_name(
        self, file_hash: str, suffix: str, used_names: set[str]
    ) -> str:
        safe_hash = (
            "".join(ch for ch in str(file_hash or "") if ch.isalnum())[:80]
            or hashlib.sha256(str(file_hash).encode()).hexdigest()
        )
        safe_suffix = suffix.lower() if str(suffix).startswith(".") else f".{suffix}"
        base = f"assets/{safe_hash}{safe_suffix}"
        name = base
        counter = 2
        while name in used_names:
            name = f"assets/{safe_hash}_{counter}{safe_suffix}"
            counter += 1
        used_names.add(name)
        return name

    def _page_restore_emoji_backup_records(
        self, archive_data: bytes | Path
    ) -> tuple[list[EmojiAssetRecord], dict]:
        cache_dir = self.runtime._emoji_asset_cache_dir()
        if not cache_dir:
            raise ValueError("表情缓存目录不可用")
        try:
            source = BytesIO(archive_data) if isinstance(archive_data, bytes) else archive_data
            package = zipfile.ZipFile(source)
        except zipfile.BadZipFile as exc:
            raise ValueError("表情备份文件不是有效 ZIP") from exc
        with package:
            self._page_validate_emoji_backup_zip(package)
            manifest = self._page_read_emoji_backup_manifest(package)
            records: list[EmojiAssetRecord] = []
            skipped = 0
            restored_files = 0
            for raw in manifest.get("items") or []:
                if not isinstance(raw, dict):
                    skipped += 1
                    continue
                item = dict(raw)
                item.pop("original_id", None)
                backup_asset = str(item.pop("backup_asset", "") or "").strip()
                if backup_asset:
                    try:
                        path = self._page_restore_emoji_backup_file(
                            package, item, backup_asset, cache_dir
                        )
                    except ValueError:
                        skipped += 1
                        continue
                    item["file_path"] = str(path)
                    restored_files += 1
                elif not self.runtime._emoji_asset_is_remote(
                    str(item.get("file_path") or "")
                ):
                    item["file_path"] = ""
                    item["status"] = "missing"
                    item["sendable"] = False
                item["id"] = 0
                record = EmojiAssetRecord.from_value(item)
                if record:
                    records.append(record)
                else:
                    skipped += 1
            return records, {"files": restored_files, "skipped_records": skipped}

    def _page_emoji_transfer_path(self, prefix: str, suffix: str) -> Path:
        root = Path(self.runtime.data_path).parent / "transfer"
        token = secrets.token_hex(8)
        return root / f"{prefix}_{token}{suffix}"

    def _page_schedule_emoji_transfer_cleanup(self, path: Path) -> None:
        scheduler = getattr(self.runtime, "_schedule_background_task", None)
        if not callable(scheduler):
            return

        async def cleanup() -> None:
            await asyncio.sleep(300)
            await asyncio.to_thread(path.unlink, missing_ok=True)

        scheduler(
            cleanup(),
            label="面板临时文件清理",
            key=f"portal_transfer:{path.name}",
        )

    @staticmethod
    def _page_merge_restored_emoji_asset(
        existing: EmojiAssetRecord | None, restored: EmojiAssetRecord
    ) -> EmojiAssetRecord:
        if not existing:
            return restored
        return EmojiAssetRecord(
            id=existing.id,
            file_hash=existing.file_hash or restored.file_hash,
            file_path=restored.file_path or existing.file_path,
            label=existing.label or restored.label,
            description=existing.description or restored.description,
            emotions=existing.emotions or restored.emotions,
            source_scope=existing.source_scope or restored.source_scope,
            source_message_id=existing.source_message_id or restored.source_message_id,
            source_url=existing.source_url or restored.source_url,
            source_kind=existing.source_kind or restored.source_kind,
            asset_type=existing.asset_type or restored.asset_type,
            confidence=max(
                float(existing.confidence or 0.0), float(restored.confidence or 0.0)
            ),
            sendable=existing.sendable,
            rejected_reason=existing.rejected_reason or restored.rejected_reason,
            status=existing.status
            if existing.status not in {"missing", ""}
            else restored.status,
            used_count=max(
                int(existing.used_count or 0), int(restored.used_count or 0)
            ),
            last_used_at=existing.last_used_at or restored.last_used_at,
            created_at=existing.created_at or restored.created_at,
            updated_at=existing.updated_at or restored.updated_at,
        )

    def _page_validate_emoji_backup_zip(self, package: zipfile.ZipFile) -> None:
        infos = [info for info in package.infolist() if not info.is_dir()]
        if len(infos) > EMOJI_BACKUP_MAX_FILES:
            raise ValueError("表情备份文件数量过多")
        total_size = sum(max(int(info.file_size or 0), 0) for info in infos)
        if total_size > EMOJI_BACKUP_MAX_MB * 1024 * 1024:
            raise ValueError(f"表情备份解压后不能超过 {EMOJI_BACKUP_MAX_MB} MB")
        for info in infos:
            name = str(info.filename or "")
            if name == "manifest.json":
                continue
            self._page_safe_backup_member(name)

    def _page_read_emoji_backup_manifest(self, package: zipfile.ZipFile) -> dict:
        try:
            raw = package.read("manifest.json")
        except KeyError as exc:
            raise ValueError("表情备份缺少 manifest.json") from exc
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError("表情备份清单解析失败") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != EMOJI_BACKUP_FORMAT
        ):
            raise ValueError("表情备份格式不正确")
        if int(manifest.get("version") or 0) != EMOJI_BACKUP_VERSION:
            raise ValueError("表情备份版本不支持")
        if not isinstance(manifest.get("items"), list):
            raise ValueError("表情备份清单缺少素材列表")
        return manifest

    def _page_restore_emoji_backup_file(
        self, package: zipfile.ZipFile, item: dict, backup_asset: str, cache_dir: Path
    ) -> Path:
        member = self._page_safe_backup_member(backup_asset)
        try:
            info = package.getinfo(member)
        except KeyError as exc:
            raise ValueError("表情备份素材文件缺失") from exc
        max_bytes = self._page_emoji_max_bytes()
        if int(info.file_size or 0) <= 0 or int(info.file_size or 0) > max_bytes:
            raise ValueError("表情备份素材大小不符合限制")
        data = package.read(info)
        digest = hashlib.sha256(data).hexdigest()
        expected = str(item.get("backup_sha256") or "").strip().lower()
        if expected and expected != digest:
            raise ValueError("表情备份素材校验失败")
        suffix = self._page_backup_member_suffix(member, data)
        file_hash = str(item.get("file_hash") or digest).strip() or digest
        filename = self._page_emoji_cache_filename(file_hash, suffix)
        target = (cache_dir / filename).resolve()
        target.relative_to(cache_dir.resolve())
        if (
            not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != digest
        ):
            target.write_bytes(data)
        item["file_hash"] = file_hash
        return target

    def _page_safe_backup_member(self, name: str) -> str:
        normalized = str(name or "").replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or not normalized.startswith("assets/")
            or any(part in {"", ".."} for part in path.parts)
        ):
            raise ValueError("表情备份素材路径不安全")
        suffixes = set(
            getattr(
                self.runtime,
                "EMOJI_ASSET_SUFFIXES",
                {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
            )
        )
        if path.suffix.lower() not in suffixes:
            raise ValueError("表情备份素材格式不支持")
        return path.as_posix()

    def _page_backup_member_suffix(self, member: str, data: bytes) -> str:
        detector = getattr(self.runtime, "_emoji_asset_suffix", None)
        if callable(detector):
            return detector(member, data=data, content_type="")
        suffix = PurePosixPath(member).suffix.lower()
        return (
            suffix
            if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
            else ".png"
        )

    def _page_emoji_cache_filename(self, file_hash: str, suffix: str) -> str:
        namer = getattr(self.runtime, "_emoji_asset_filename", None)
        if callable(namer):
            return namer(file_hash, suffix)
        safe_hash = (
            "".join(ch for ch in str(file_hash or "") if ch.isalnum())[:80]
            or hashlib.sha256(str(file_hash).encode()).hexdigest()
        )
        safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        return f"{safe_hash}{safe_suffix.lower()}"

    def _page_emoji_max_bytes(self) -> int:
        getter = getattr(self.runtime, "_emoji_max_bytes", None)
        if callable(getter):
            return int(getter() or 0)
        return EMOJI_IMPORT_DEFAULT_MAX_MB * 1024 * 1024

    async def _page_receive_emoji(self, temporary: Path) -> tuple[Path, str, str]:
        upload = await self._page_receive_upload(
            temporary,
            max_bytes=self._page_emoji_max_bytes(),
        )
        display_name = str(upload.get("filename") or "").strip()
        suffix = Path(display_name).suffix.lower()
        allowed = set(
            getattr(
                self.runtime,
                "EMOJI_ASSET_SUFFIXES",
                {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
            )
        )
        mime = str(upload.get("mime") or "").lower()
        if suffix not in allowed or (mime and not mime.startswith("image/")):
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
            raise ValueError("仅支持 PNG、JPG、WEBP、GIF 或 BMP 图片")
        fingerprint = await asyncio.to_thread(file_sha256, temporary)
        cache_dir = self.runtime._emoji_asset_cache_dir()
        if not cache_dir:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
            raise ValueError("表情素材缓存失败")
        target = cache_dir / self._page_emoji_cache_filename(fingerprint, suffix)
        if await asyncio.to_thread(target.is_file):
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
        else:
            await asyncio.to_thread(temporary.replace, target)
        return target, fingerprint, display_name or f"导入表情{suffix}"

    @staticmethod
    def _page_imported_emoji_record(
        existing: EmojiAssetRecord | None,
        *,
        fingerprint: str,
        file_path: str,
        source_url: str,
        display_name: str,
    ) -> EmojiAssetRecord:
        data = existing.as_dict() if existing else {}
        label = str(data.get("label") or "").strip()
        if not label:
            label = Path(str(display_name or "")).stem.strip()[:80] or "导入表情"
        status = str(data.get("status") or "").strip()
        return EmojiAssetRecord(
            id=int(data.get("id") or 0),
            file_hash=fingerprint,
            file_path=file_path,
            label=label,
            description=str(data.get("description") or ""),
            emotions=list(data.get("emotions") or []),
            source_scope=str(data.get("source_scope") or "dashboard"),
            source_message_id=str(data.get("source_message_id") or ""),
            source_url=source_url or str(data.get("source_url") or ""),
            source_kind=str(data.get("source_kind") or "manual"),
            asset_type=str(data.get("asset_type") or "emoji"),
            confidence=float(data.get("confidence") or 0.0),
            sendable=bool(data.get("sendable")) if existing else False,
            rejected_reason=str(data.get("rejected_reason") or ""),
            status=status if status else "pending",
            used_count=int(data.get("used_count") or 0),
            last_used_at=str(data.get("last_used_at") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    async def _page_schedule_emoji_import_vision(self, asset: EmojiAssetRecord) -> None:
        if not asset or str(asset.status or "") == "ready":
            return
        describe = getattr(self.runtime, "_describe_emoji_asset_with_vision", None)
        if not callable(describe):
            return
        task = describe(asset)
        scheduler = getattr(self.runtime, "_schedule_background_task", None)
        if callable(scheduler):
            scheduler(
                task,
                label="表情素材识别",
                key=f"emoji_asset_import:{asset.id or asset.file_hash}",
            )
            return
        if inspect.isawaitable(task):
            await task

    @staticmethod
    def _page_image_mime(path: Path) -> str:
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }.get(path.suffix.lower(), "image/png")

    async def _page_emoji_preview_data(
        self, path: Path, *, still: bool
    ) -> tuple[bytes, str]:
        if still and path.suffix.lower() == ".gif":
            return await asyncio.to_thread(self._page_static_emoji_preview_data, path)
        return await asyncio.to_thread(path.read_bytes), self._page_image_mime(path)

    def _page_static_emoji_preview_data(self, path: Path) -> tuple[bytes, str]:
        preview_path = self._page_static_emoji_preview_path(path)
        try:
            source_stat = path.stat()
            if (
                preview_path.is_file()
                and preview_path.stat().st_mtime_ns >= source_stat.st_mtime_ns
                and preview_path.stat().st_size > 0
            ):
                return preview_path.read_bytes(), "image/png"
        except OSError:
            return self._page_emoji_placeholder_bytes(path.name), "image/svg+xml"

        try:
            from PIL import Image, ImageOps

            with Image.open(path) as source:
                source.seek(0)
                image = ImageOps.exif_transpose(source)
                image.thumbnail(
                    (EMOJI_STILL_PREVIEW_SIZE, EMOJI_STILL_PREVIEW_SIZE),
                    Image.Resampling.LANCZOS,
                )
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA")
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
                data = output.getvalue()
            if data:
                preview_path.write_bytes(data)
                return data, "image/png"
        except Exception:
            pass
        return self._page_emoji_placeholder_bytes(path.name), "image/svg+xml"

    @staticmethod
    def _page_static_emoji_preview_path(path: Path) -> Path:
        return path.with_name(f"{path.stem}.still.png")

    @classmethod
    def _page_emoji_placeholder_data_url(cls, label: str = "") -> str:
        data = cls._page_emoji_placeholder_bytes(label)
        encoded = encode_base64(data)
        return f"data:image/svg+xml;base64,{encoded}"

    @staticmethod
    def _page_emoji_placeholder_bytes(label: str = "") -> bytes:
        name = Path(str(label or "GIF")).suffix.upper().lstrip(".") or "GIF"
        text = name[:4] if name else "GIF"
        return (
            "<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160' viewBox='0 0 160 160'>"
            "<rect width='160' height='160' rx='18' fill='#fff0f6'/>"
            "<path d='M24 24h112v112H24z' fill='#ffffff' stroke='#df5f95' stroke-opacity='.35'/>"
            "<text x='80' y='88' text-anchor='middle' font-size='34' font-family='Arial,sans-serif' "
            "font-weight='700' fill='#8d4969'>"
            f"{text}"
            "</text>"
            "</svg>"
        ).encode("utf-8")
