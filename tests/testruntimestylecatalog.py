import json
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path

import support  # noqa: F401 - 安装轻量级 AstrBot 测试替身
from core.archive import LifeArchive
from core.life.inspiration import StyleCatalogMixin
from core.runtime.channel.stylist import RuntimeStyleCatalogMixin
from PIL import Image


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


class _VisionProvider:
    def __init__(self, *, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return types.SimpleNamespace(
            completion_text=json.dumps(self.payload, ensure_ascii=False)
        )


class _VisionFallbackRuntime(RuntimeStyleCatalogMixin):
    def __init__(self, primary, fallback):
        self.config = types.SimpleNamespace(
            vision=types.SimpleNamespace(provider="test-primary")
        )
        self.primary = primary
        self.fallback = fallback
        self.provider_requests = []
        self.closed_sessions = []

    async def get_text_provider_candidates(self, provider_id=""):
        self.provider_requests.append(str(provider_id or ""))
        yield self.primary
        self.provider_requests.append("")
        yield self.fallback

    @staticmethod
    async def _reverse_prompt_call_provider(provider, prompt, image, session_id):
        return await provider.text_chat(
            prompt=prompt,
            image_urls=[image],
            session_id=session_id,
        )

    @staticmethod
    def _completion_text(result):
        return str(getattr(result, "completion_text", "") or "")

    async def close_text_session(self, session_id):
        self.closed_sessions.append(session_id)


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
    async def test_legacy_archived_candidate_is_treated_as_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            try:
                item = await archive.upsert_style_catalog_item(
                    {
                        "kind": "outfit",
                        "title": "测试旧版造型",
                        "description": "用于验证旧状态兼容的测试造型",
                        "source_image_hash": "a" * 64,
                        "confidence": 0.9,
                    }
                )

                def write_legacy_status():
                    archive._conn.execute(
                        "UPDATE style_catalog_items SET status = 'archived' WHERE id = ?",
                        (item.id,),
                    )
                    archive._conn.commit()

                await archive._run_db(write_legacy_status)
                restored = await archive.get_style_catalog_item(item.id)
                disabled = await archive.get_style_catalog_items(
                    status="disabled", limit=10
                )

                self.assertEqual(restored.status, "disabled")
                self.assertEqual([entry.id for entry in disabled], [item.id])
                self.assertEqual(await archive.get_style_catalog_items(limit=10), [])
            finally:
                archive.close()

    async def test_style_image_analysis_falls_back_to_current_default_provider(self):
        primary = _VisionProvider(error=RuntimeError("测试指定模型不可用"))
        fallback = _VisionProvider(
            payload={
                "outfit": {
                    "present": True,
                    "title": "测试默认模型识别造型",
                    "description": "浅色上衣搭配深色短裙",
                    "confidence": 0.9,
                }
            }
        )
        runtime = _VisionFallbackRuntime(primary, fallback)

        payload = await runtime._analyze_style_catalog_image(
            "/tmp/test-style.jpg",
            note="测试衣橱识图",
            kind="outfit",
        )

        self.assertEqual(payload["outfit"]["title"], "测试默认模型识别造型")
        self.assertEqual(runtime.provider_requests, ["test-primary", ""])
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 1)
        self.assertEqual(len(runtime.closed_sessions), 2)
        self.assertTrue(runtime.closed_sessions[1].endswith("_fallback"))

    def test_web_asset_key_ignores_tracking_parameters_but_keeps_signed_urls(self):
        first = RuntimeStyleCatalogMixin._style_asset_key(
            "https://images.example.test/look.jpg?utm_source=test&ref=search"
        )
        second = RuntimeStyleCatalogMixin._style_asset_key(
            "https://IMAGES.example.test/look.jpg"
        )
        self.assertEqual(first, second)
        signed = RuntimeStyleCatalogMixin._style_asset_key(
            "https://images.example.test/look.jpg?token=abc"
        )
        self.assertNotEqual(first, signed)
        self.assertEqual(
            RuntimeStyleCatalogMixin._style_asset_key(
                "https://images.example.test/look.jpg?b=2&a=1"
            ),
            RuntimeStyleCatalogMixin._style_asset_key(
                "https://images.example.test/look.jpg?a=1&b=2"
            ),
        )

    def test_web_asset_candidate_only_rejects_explicitly_tiny_dimensions(self):
        self.assertFalse(
            RuntimeStyleCatalogMixin._style_asset_is_candidate(
                {
                    "url": "https://images.example.test/tiny.jpg",
                    "width": 120,
                    "height": 180,
                }
            )
        )
        self.assertTrue(
            RuntimeStyleCatalogMixin._style_asset_is_candidate(
                {"url": "https://images.example.test/unknown-size.jpg"}
            )
        )

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

    def test_style_perceptual_hash_is_stable_for_resized_copy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = Path(tmpdir) / "first.png"
            second = Path(tmpdir) / "second.jpg"
            image = Image.new("RGB", (80, 120), "white")
            for x in range(20, 60):
                for y in range(30, 90):
                    image.putpixel((x, y), (80, 160, 120))
            image.save(first)
            image.resize((160, 240), Image.Resampling.LANCZOS).save(second)

            first_hash = RuntimeStyleCatalogMixin._style_perceptual_hash(str(first))
            second_hash = RuntimeStyleCatalogMixin._style_perceptual_hash(str(second))
            self.assertLessEqual(
                RuntimeStyleCatalogMixin._style_hash_distance(first_hash, second_hash),
                5,
            )

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

    async def test_learning_splits_visible_style_categories_without_touching_day(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试清爽套装",
                "description": "浅绿色短袖上衣搭米白直筒裤",
                "visual_prompt": "浅绿色圆领短袖上衣采用微宽松直身版型，搭配米白色高腰直筒长裤，裤腿自然垂落，配白色低帮鞋与银色细链项链。",
                "colors": ["浅绿色", "米白色"],
                "footwear": ["白色低帮鞋"],
                "accessories": ["银色细链项链"],
                "scenes": ["日常外出"],
                "confidence": 0.9,
            },
            "top": {
                "present": True,
                "title": "测试浅绿短袖",
                "description": "浅绿色短袖上衣",
                "colors": ["浅绿色"],
                "confidence": 0.9,
            },
            "bottom": {
                "present": True,
                "title": "测试米白直筒裤",
                "description": "米白色直筒长裤",
                "colors": ["米白色"],
                "confidence": 0.9,
            },
            "footwear": {
                "present": True,
                "title": "测试白色低帮鞋",
                "description": "白色低帮鞋",
                "items": ["低帮鞋"],
                "confidence": 0.9,
            },
            "accessory": {
                "present": True,
                "title": "测试银色细链",
                "description": "银色细链项链",
                "items": ["项链"],
                "confidence": 0.9,
            },
            "hair": {
                "present": True,
                "title": "测试低丸子头",
                "description": "中长发低位挽成松散丸子并保留碎发",
                "scenes": ["日常外出"],
                "confidence": 0.8,
            },
            "makeup": {
                "present": True,
                "title": "测试清透妆容",
                "description": "清透底妆搭自然唇色",
                "finish": "清透",
                "confidence": 0.86,
            },
            "nails": {
                "present": True,
                "title": "测试透明短甲",
                "description": "透明光泽短圆甲",
                "shape": "短圆甲",
                "confidence": 0.84,
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
                    event, str(image_path), kind="auto"
                )
                items = await archive.get_style_catalog_items(limit=10)

                self.assertIn("已加入视觉衣橱候选", str(result))
                self.assertIn("美甲", str(result))
                self.assertEqual(
                    {item.kind for item in items},
                    {
                        "outfit",
                        "top",
                        "bottom",
                        "footwear",
                        "accessory",
                        "hair",
                        "makeup",
                        "nails",
                    },
                )
                self.assertTrue(all(item.status == "active" for item in items))
                self.assertIsNone(await archive.get_day("2026-08-11"))
                outfit = next(item for item in items if item.kind == "outfit")
                self.assertNotIn("丸子", outfit.description)
                self.assertIn("微宽松直身版型", outfit.description)
                self.assertNotIn("visual_prompt", outfit.attributes)
                self.assertEqual(outfit.attributes["footwear"], ["白色低帮鞋"])
                self.assertEqual(outfit.attributes["accessories"], ["银色细链项链"])
                makeup = next(item for item in items if item.kind == "makeup")
                nails = next(item for item in items if item.kind == "nails")
                self.assertEqual(makeup.attributes["finish"], "清透")
                self.assertEqual(nails.attributes["shape"], "短圆甲")
            finally:
                archive.close()

    async def test_low_confidence_learning_waits_for_manual_review(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试模糊造型",
                "description": "浅色上衣搭短裤",
                "confidence": 0.55,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _StyleCatalogRuntime(archive, payload)
            event = types.SimpleNamespace(unified_msg_origin="private:test-user")
            image_path = Path(tmpdir) / "test-style.jpg"
            image_path.write_bytes(b"test-image")
            try:
                await runtime.life_style_learn(event, str(image_path), kind="outfit")
                items = await archive.get_style_catalog_items(status="", limit=10)
                self.assertEqual(items[0].status, "pending")
                self.assertEqual(await archive.get_style_catalog_items(limit=10), [])
            finally:
                archive.close()

    async def test_adopted_references_expose_independent_appearance_components(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            item = await archive.upsert_style_catalog_item(
                {
                    "kind": "outfit",
                    "title": "测试浅色造型",
                    "description": "浅色日常造型",
                    "source_scope": "private:test-user",
                    "source_image_hash": "d" * 64,
                    "attributes": {
                        "makeup": ["测试旧版妆容"],
                        "nails": ["测试旧版美甲"],
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
                    "attributes": {},
                    "confidence": 0.9,
                }
            )
            makeup = await archive.upsert_style_catalog_item(
                {
                    "kind": "makeup",
                    "title": "测试清透妆容",
                    "description": "清透底妆",
                    "source_scope": "private:test-user",
                    "source_image_hash": "f" * 64,
                    "confidence": 0.9,
                }
            )
            nails = await archive.upsert_style_catalog_item(
                {
                    "kind": "nails",
                    "title": "测试奶白短甲",
                    "description": "奶白色短圆甲",
                    "source_scope": "private:test-user",
                    "source_image_hash": "1" * 64,
                    "confidence": 0.9,
                }
            )
            runtime = _StyleCatalogComposer(archive)
            try:
                appearance = await runtime._style_catalog_reference_appearance(
                    [item.id, hair.id, makeup.id, nails.id]
                )
                self.assertEqual(appearance["outfit"], "浅色日常造型")
                self.assertEqual(appearance["hair_style"], "测试发型")
                self.assertEqual(appearance["hair"], "低马尾")
                self.assertEqual(appearance["makeup"], "测试旧版妆容；清透底妆")
                self.assertEqual(appearance["nails"], "测试旧版美甲；奶白色短圆甲")
            finally:
                archive.close()

    def test_analysis_requires_explicit_visible_item_and_keeps_fields_separate(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试服装",
                "description": "蓝色衬衫搭白色长裤",
                "visual_prompt": "蓝色翻领衬衫采用宽松直身剪裁，搭配白色高腰直筒长裤。",
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
        self.assertEqual(
            outfit["description"],
            "蓝色翻领衬衫采用宽松直身剪裁，搭配白色高腰直筒长裤。",
        )
        self.assertNotIn("visual_prompt", outfit["attributes"])
        self.assertEqual(hair, {})

    def test_legacy_visual_prompt_becomes_detailed_description(self):
        visual_prompt = "细节" * 180
        payload = {
            "top": {
                "present": True,
                "title": "测试详细上装",
                "description": "浅色长袖上装",
                "visual_prompt": visual_prompt,
                "confidence": 0.9,
            }
        }

        item = _StyleCatalogRuntime._style_analysis_item(payload, "top")

        self.assertEqual(item["description"], visual_prompt)
        self.assertNotIn("visual_prompt", item["attributes"])
        self.assertGreater(len(item["description"]), 240)

    def test_contract_uses_one_detailed_description(self):
        runtime = object.__new__(_StyleCatalogRuntime)
        contract = runtime._style_catalog_contract("", "auto")

        self.assertIn("description 就是供衣橱展示、检索和后续生图", contract)
        self.assertNotIn('"visual_prompt"', contract)
        self.assertNotIn('"image_summary"', contract)

    async def test_adopted_catalog_item_prefers_detailed_visual_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            item = await archive.upsert_style_catalog_item(
                {
                    "kind": "outfit",
                    "title": "测试层次造型",
                    "description": "浅色上衣搭深色短裙",
                    "source_scope": "private:test-user",
                    "source_image_hash": "2" * 64,
                    "attributes": {
                        "visual_prompt": "浅蓝色方领长袖上衣采用贴身剪裁，黑色双层荷叶边高腰短裙形成清晰层次。"
                    },
                    "confidence": 0.9,
                }
            )
            runtime = _StyleCatalogComposer(archive)
            try:
                appearance = await runtime._style_catalog_reference_appearance(
                    [item.id]
                )
                self.assertIn("双层荷叶边", appearance["outfit"])
                self.assertNotEqual(appearance["outfit"], item.description)
            finally:
                archive.close()


if __name__ == "__main__":
    unittest.main()
