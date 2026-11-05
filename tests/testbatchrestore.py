"""Exercise browser-produced ZIP parts through the real restore handlers/database."""
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from core.archive import LifeArchive
from core.models import EmojiAssetRecord
from core.paths import STYLE_CATALOG_DIR_NAME
from testdashboard import PagePlugin


ROOT = Path(__file__).resolve().parents[1]


def split_backup(source: Path, destination: Path, kind: str) -> list[Path]:
    script = """
import { readFileSync, writeFileSync } from 'node:fs';
import { File } from 'node:buffer';
import { prepareBackup } from './pages/dashboard/api/backup-restore.mjs';
globalThis.File = File;
const [source, destination, kind] = process.argv.slice(2);
const job = await prepareBackup(new File([readFileSync(source)], 'backup.zip'), kind);
for (const [index, group] of job.groups.entries()) {
  writeFileSync(`${destination}/${String(index).padStart(4, '0')}.zip`,
    new Uint8Array(await job.makeFile(group).arrayBuffer()));
}
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-", str(source), str(destination), kind],
        cwd=ROOT, input=script,
        text=True, capture_output=True, timeout=60,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return sorted(destination.glob("*.zip"))


class BatchRestoreTest(unittest.IsolatedAsyncioTestCase):
    def plugin(self):
        plugin = PagePlugin()
        self.addCleanup(shutil.rmtree, plugin.runtime.data_path.parent)
        plugin.runtime.archive = LifeArchive(plugin.runtime.data_path)
        self.addCleanup(plugin.runtime.archive.close)
        return plugin

    async def test_empty_libraries_do_not_create_downloads_or_transfer_files(self):
        plugin = self.plugin()
        for kind, label in [("emoji", "表情"), ("closet", "衣橱")]:
            response = await getattr(plugin, f"page_{kind}_backup")()
            self.assertFalse(response["ok"])
            self.assertTrue(response["error"]["public"])
            self.assertEqual(response["error"]["message"], f"暂无{label}素材，无法备份")
            self.assertFalse((plugin.runtime.data_path.parent / "transfer").exists())

    async def test_emoji_parts_preserve_metadata_files_and_existing_choices(self):
        source, target = self.plugin(), self.plugin()
        expected = {}
        for i in range(3):
            data = b"\x89PNG\r\n\x1a\n" + bytes([i])
            digest = hashlib.sha256(data).hexdigest()
            path = source.runtime._emoji_asset_cache_dir() / f"{digest}.png"
            path.write_bytes(data)
            record = EmojiAssetRecord(
                file_hash=digest, file_path=str(path), label=f"表情{i}",
                description="备份中的识别说明", emotions=["开心", "期待"],
                source_kind="manual", confidence=0.95, sendable=True,
                status="ready", used_count=7,
            )
            await source.runtime.archive.upsert_emoji_asset(record)
            expected[digest] = (record, data)
        backup = await source.page_emoji_backup()
        with tempfile.TemporaryDirectory() as directory:
            parts = split_backup(Path(backup["data"]["path"]), Path(directory), "emoji")
            self.assertEqual(len(parts), 3)
            for part in parts:
                target.upload_path = part
                result = await target.page_emoji_restore()
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["data"]["restored"], 1)
                self.assertEqual(result["data"]["skipped_records"], 0)
            records = await target.runtime.archive.get_emoji_assets(limit=0)
            self.assertEqual(len(records), 3)
            for item in records:
                original, data = expected[item.file_hash]
                self.assertEqual(Path(item.file_path).read_bytes(), data)
                self.assertEqual(item.label, original.label)
                self.assertEqual(item.description, original.description)
                self.assertEqual(item.emotions, original.emotions)
                self.assertEqual(item.used_count, 7)
                item.sendable = False
                item.label = "本地新名称"
                await target.runtime.archive.upsert_emoji_asset(item)
            # A retry after a lost response must merge, not create duplicates
            # or override the user's current sendable choice.
            for part in parts:
                target.upload_path = part
                self.assertTrue((await target.page_emoji_restore())["ok"])
            records = await target.runtime.archive.get_emoji_assets(limit=0)
            self.assertEqual(len(records), 3)
            self.assertTrue(all(not item.sendable and item.label == "本地新名称" for item in records))

    async def test_closet_parts_preserve_shared_picture_links_and_care_facts(self):
        source, target = self.plugin(), self.plugin()
        picture = b"\x89PNG\r\n\x1a\nshared-outfit"
        digest = hashlib.sha256(picture).hexdigest()
        root = source.runtime.data_path.parent / STYLE_CATALOG_DIR_NAME
        root.mkdir(parents=True, exist_ok=True)
        path = root / "outfit.png"
        path.write_bytes(picture)
        for kind, condition in [("top", "washing"), ("bottom", "stored"), ("outfit", "clean")]:
            await source.runtime.archive.upsert_style_catalog_item({
                "kind": kind, "title": kind, "source_image_hash": digest,
                "image_path": str(path), "description": "同图上衣与长裤", "confidence": 0.95,
                "attributes": {"wardrobe": {"ownership": "owned", "condition": condition}},
            })
        original = {
            item.kind: item.attributes["wardrobe"]
            for item in await source.runtime.archive.get_style_catalog_items(status="", limit=500)
        }
        backup = await source.page_closet_backup()
        with tempfile.TemporaryDirectory() as directory:
            parts = split_backup(Path(backup["data"]["path"]), Path(directory), "closet")
            self.assertEqual(len(parts), 1)
            target.upload_path = parts[0]
            result = await target.page_closet_restore()
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["data"]["restored"], 3)
            records = await target.runtime.archive.get_style_catalog_items(status="", limit=500)
            self.assertEqual(len(records), 3)
            self.assertEqual(len({item.image_path for item in records}), 1)
            for item in records:
                self.assertEqual(Path(item.image_path).read_bytes(), picture)
                self.assertEqual(item.attributes["wardrobe"], original[item.kind])
            self.assertTrue((await target.page_closet_restore())["ok"])
            self.assertEqual(len(await target.runtime.archive.get_style_catalog_items(status="", limit=500)), 3)
