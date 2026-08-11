import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path

import support  # noqa: F401 - 安装轻量级 AstrBot 测试替身
from PIL import Image
from core.archive import LifeArchive
from core.life.style_catalog import StyleCatalogMixin
from core.runtime.channel.style_catalog import RuntimeStyleCatalogMixin


class _StyleCatalogRuntime(RuntimeStyleCatalogMixin):
    def __init__(self, archive, payload):
        self.archive = archive
        self.data_path = f"{tempfile.gettempdir()}/style-catalog-test.db"
        self.payload = payload
        self.search = None

    @staticmethod
    def _event_session_id(event):
        return str(getattr(event, "unified_msg_origin", "private:test-user"))

    async def _resolve_life_image_reference_async(self, event, value):
        del event
        return str(value or "/tmp/test-style.jpg")

    async def _persist_style_catalog_image(self, image, *, source_url=""):
        del image, source_url
        return "/tmp/cached-style.jpg", "b" * 64

    async def _analyze_style_catalog_image(self, image, *, note, kind):
        del image, note, kind
        return self.payload

    @staticmethod
    def _media_error_summary(exc):
        return str(exc)


class _StyleFeedbackRuntime(_StyleCatalogRuntime):
    async def _style_feedback_decision(self, items, feedback):
        del feedback
        return {
            "adjustments": [
                {
                    "item_id": items[0].id,
                    "sentiment": "prefer",
                    "score_delta": 0.5,
                    "reason": "用户明确喜欢这套候选",
                }
            ],
            "preference_points": [
                {
                    "category": "style",
                    "content": "偏好清爽、层次简洁的日常造型",
                    "weight": 0.6,
                }
            ],
        }


class _BrowseRuntime(_StyleCatalogRuntime):
    def __init__(self, archive, payloads, assets):
        super().__init__(archive, {})
        self.payloads = list(payloads)
        self.search = types.SimpleNamespace(enabled=True)
        self.search.search = self._search
        self.assets = assets
        self.search_depths = []
        self.persist_sources = []

    async def _search(self, *args, **kwargs):
        del args
        self.search_depths.append(kwargs.get("depth"))
        return types.SimpleNamespace(status="ok", images=self.assets)

    async def _persist_style_catalog_image(self, image, *, source_url=""):
        self.persist_sources.append(source_url)
        digest = str(image).encode("utf-8").hex()[:64].ljust(64, "a")
        return f"/tmp/{digest[:8]}.jpg", digest

    async def _analyze_style_catalog_image(self, image, *, note, kind):
        del image, note, kind
        return self.payloads.pop(0) if self.payloads else {}


class _StyleCatalogComposer(StyleCatalogMixin):
    def __init__(self, archive):
        self.archive = archive


class StyleCatalogRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def test_webp_style_reference_is_normalized_to_jpeg(self):
        source = BytesIO()
        Image.new("RGBA", (8, 6), (255, 40, 100, 180)).save(source, format="WEBP")
        rendered = RuntimeStyleCatalogMixin._prepare_style_catalog_image_bytes(
            source.getvalue()
        )

        self.assertTrue(rendered.startswith(b"\xff\xd8\xff"))
        with Image.open(BytesIO(rendered)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.mode, "RGB")

    async def test_browse_keeps_trying_until_requested_count_is_saved(self):
        empty = {"outfit": {"present": False}, "hair": {"present": False}}
        outfit_a = {
            "outfit": {
                "present": True,
                "title": "测试夏日造型一",
                "description": "白色无袖上衣搭配浅色短裙",
                "confidence": 0.9,
            }
        }
        outfit_b = {
            "outfit": {
                "present": True,
                "title": "测试夏日造型二",
                "description": "浅蓝吊带裙搭配凉鞋",
                "confidence": 0.9,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _BrowseRuntime(
                archive,
                [empty, outfit_a, empty, outfit_b],
                [{"url": f"https://example.test/{index}.jpg"} for index in range(4)],
            )
            event = types.SimpleNamespace(unified_msg_origin="private:test-user")
            try:
                result = await runtime.life_style_browse_learn(
                    event,
                    "测试夏日穿搭",
                    kind="outfit",
                    count=2,
                )
                items = await archive.get_style_catalog_items(limit=10)

                self.assertIn("#1", str(result))
                self.assertIn("#2", str(result))
                self.assertEqual(len(items), 2)
                self.assertEqual(runtime.search_depths, ["quick"])
            finally:
                archive.close()

    async def test_browse_uses_deep_search_when_quick_images_are_insufficient(self):
        empty = {"outfit": {"present": False}, "hair": {"present": False}}
        outfit = {
            "outfit": {
                "present": True,
                "title": "测试清爽睡前造型",
                "description": "黑色细肩带长款睡裙",
                "confidence": 0.9,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _BrowseRuntime(archive, [empty, outfit], [])
            rounds = {
                "quick": [
                    {
                        "url": "https://images.example.test/quick.jpg",
                        "source_url": "https://shop.example.test/quick",
                    }
                ],
                "deep": [
                    {
                        "url": "https://images.example.test/deep.jpg",
                        "source_url": "https://shop.example.test/deep",
                    }
                ],
            }

            async def search(*args, **kwargs):
                del args
                runtime.search_depths.append(kwargs.get("depth"))
                return types.SimpleNamespace(
                    status="ok",
                    images=rounds[kwargs["depth"]],
                )

            runtime.search.search = search
            event = types.SimpleNamespace(unified_msg_origin="private:test-user")
            try:
                result = await runtime.life_style_browse_learn(
                    event,
                    "测试睡前造型",
                    kind="outfit",
                    count=1,
                )
                items = await archive.get_style_catalog_items(limit=10)

                self.assertIn("测试清爽睡前造型", str(result))
                self.assertEqual(len(items), 1)
                self.assertEqual(runtime.search_depths, ["quick", "deep"])
                self.assertEqual(
                    runtime.persist_sources,
                    [
                        "https://shop.example.test/quick",
                        "https://shop.example.test/deep",
                    ],
                )
            finally:
                archive.close()

    async def test_semantic_feedback_updates_candidate_and_long_term_preference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            item = await archive.upsert_style_catalog_item(
                {
                    "kind": "outfit",
                    "title": "测试日常造型",
                    "description": "浅色短袖上衣搭直筒长裤",
                    "source_scope": "private:test-user",
                    "source_image_hash": "c" * 64,
                    "attributes": {"styles": ["清爽"]},
                    "confidence": 0.9,
                }
            )
            runtime = _StyleFeedbackRuntime(archive, {})
            event = types.SimpleNamespace(unified_msg_origin="private:test-user")
            try:
                result = await runtime.life_style_feedback(
                    event,
                    "这一套的层次更适合日常",
                    item_ids=[item.id],
                )
                updated = await archive.get_style_catalog_items(
                    status="", ids=[item.id], limit=1
                )
                preferences = await archive.get_preferences(5, "style")

                self.assertIn(f"#{item.id}", str(result))
                self.assertAlmostEqual(updated[0].preference_score, 0.5)
                self.assertEqual(updated[0].feedback_count, 1)
                self.assertEqual(preferences[0].content, "偏好清爽、层次简洁的日常造型")
            finally:
                archive.close()

    async def test_learning_saves_outfit_and_hair_without_touching_current_day(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试清爽套装",
                "description": "浅绿色短袖上衣搭米白直筒裤",
                "colors": ["浅绿色", "米白色"],
                "footwear": ["白色低帮鞋"],
                "accessories": ["银色细链项链"],
                "makeup": ["清透底妆"],
                "nails": ["透明短甲"],
                "scenes": ["日常外出"],
                "confidence": 0.9,
            },
            "hair": {
                "present": True,
                "title": "测试低丸子头",
                "description": "中长发低位挽成松散丸子并保留碎发",
                "scenes": ["日常外出"],
                "confidence": 0.8,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _StyleCatalogRuntime(archive, payload)
            event = types.SimpleNamespace(unified_msg_origin="private:test-user")
            image_path = Path(tmpdir) / "test-style.jpg"
            image_path.write_bytes(b"test-image")
            try:
                result = await runtime.life_style_learn(
                    event, str(image_path), kind="both"
                )
                items = await archive.get_style_catalog_items(limit=10)

                self.assertIn("已加入视觉衣橱候选", str(result))
                self.assertIn("美甲：透明短甲", str(result))
                self.assertEqual({item.kind for item in items}, {"outfit", "hair"})
                self.assertIsNone(await archive.get_day("2026-08-11"))
                outfit = next(item for item in items if item.kind == "outfit")
                self.assertNotIn("丸子", outfit.description)
                self.assertEqual(outfit.attributes["footwear"], ["白色低帮鞋"])
                self.assertEqual(outfit.attributes["accessories"], ["银色细链项链"])
                self.assertEqual(outfit.attributes["makeup"], ["清透底妆"])
                self.assertEqual(outfit.attributes["nails"], ["透明短甲"])
            finally:
                archive.close()

    async def test_adopted_reference_exposes_makeup_and_nails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            item = await archive.upsert_style_catalog_item(
                {
                    "kind": "outfit",
                    "title": "测试美甲造型",
                    "description": "浅色日常造型",
                    "source_scope": "private:test-user",
                    "source_image_hash": "d" * 64,
                    "attributes": {
                        "makeup": ["清透底妆"],
                        "nails": ["奶白色短圆甲"],
                    },
                    "confidence": 0.9,
                }
            )
            hair = await archive.upsert_style_catalog_item(
                {
                    "kind": "hair",
                    "title": "测试发型",
                    "description": "低马尾",
                    "source_scope": "private:test-user",
                    "source_image_hash": "e" * 64,
                    "attributes": {"nails": ["不应进入当前美甲"]},
                    "confidence": 0.9,
                }
            )
            runtime = _StyleCatalogComposer(archive)
            try:
                appearance = await runtime._style_catalog_reference_appearance(
                    [item.id, hair.id]
                )
                self.assertEqual(appearance["makeup"], "清透底妆")
                self.assertEqual(appearance["nails"], "奶白色短圆甲")
            finally:
                archive.close()

    def test_analysis_requires_explicit_visible_item_and_keeps_fields_separate(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试服装",
                "description": "蓝色衬衫搭白色长裤",
                "colors": ["蓝色", "白色"],
                "confidence": 0.7,
            },
            "hair": {
                "present": False,
                "description": "模型不应保存这段",
                "confidence": 0.9,
            },
        }

        outfit = _StyleCatalogRuntime._style_analysis_item(payload, "outfit")
        hair = _StyleCatalogRuntime._style_analysis_item(payload, "hair")

        self.assertEqual(outfit["kind"], "outfit")
        self.assertEqual(outfit["attributes"]["colors"], ["蓝色", "白色"])
        self.assertEqual(hair, {})


if __name__ == "__main__":
    unittest.main()
