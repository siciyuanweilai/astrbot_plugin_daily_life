import json
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path

import support  # noqa: F401 - 安装轻量级 AstrBot 测试替身
from core.archive import LifeArchive
from core.life.appearance import format_life_preference_context
from core.life.inspiration import StyleCatalogMixin
from core.life.rhythm import LifecycleMixin
from core.models import PreferenceRecord
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


class _CreativeStyleRuntime(_StyleCatalogRuntime):
    def __init__(self, archive, payload, *, character_reference="/tmp/character.jpg"):
        super().__init__(archive, payload)
        self.config = types.SimpleNamespace(
            image_generation=types.SimpleNamespace(
                creative_wardrobe=types.SimpleNamespace(
                    enabled=True,
                    default_mode="text_to_image",
                )
            )
        )
        self.media = types.SimpleNamespace(
            image=types.SimpleNamespace(can_edit_image=lambda: True)
        )
        self.character_reference = character_reference
        self.generated_calls = []
        self.edited_calls = []

    async def _generate_life_image_with_policy_retry(self, event, prompt, **kwargs):
        del event
        self.generated_calls.append((prompt, kwargs))
        return types.SimpleNamespace(path="/tmp/creative-text.jpg")

    async def _edit_life_image_with_policy_retry(
        self, event, prompt, reference_image, **kwargs
    ):
        del event
        self.edited_calls.append((prompt, reference_image, kwargs))
        return types.SimpleNamespace(path="/tmp/creative-edit.jpg")

    def _life_character_reference_image(self):
        return self.character_reference


class _StyleCatalogComposer(StyleCatalogMixin):
    def __init__(self, archive):
        self.archive = archive


class _PreferenceRuntime(LifecycleMixin):
    def __init__(self, archive):
        self.archive = archive


class StyleCatalogRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_list_reports_complete_category_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _StyleCatalogRuntime(archive, {})
            try:
                for index in range(11):
                    await archive.upsert_style_catalog_item(
                        {
                            "kind": "outfit",
                            "title": f"测试套装{index + 1}",
                            "description": f"测试完整穿搭{index + 1}",
                            "source_image_hash": f"{index + 1:064x}",
                            "confidence": 0.9,
                        }
                    )
                for index in range(4):
                    await archive.upsert_style_catalog_item(
                        {
                            "kind": "hair",
                            "title": f"测试发型{index + 1}",
                            "description": f"测试发型描述{index + 1}",
                            "source_image_hash": f"{index + 101:064x}",
                            "confidence": 0.9,
                        }
                    )

                result = await runtime.life_style_catalog_list(
                    None, kind="outfit"
                )

                self.assertIn("共 15 个已启用候选", result)
                self.assertIn("套装 11", result)
                self.assertIn("发型 4", result)
                self.assertIn("当前查询：套装共 11 个，已显示 11 个", result)
                self.assertIn("套装 #", result)
            finally:
                archive.close()

    async def test_mixed_catalog_list_marks_limited_result_as_partial(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _StyleCatalogRuntime(archive, {})
            try:
                for index in range(5):
                    await archive.upsert_style_catalog_item(
                        {
                            "kind": "outfit" if index < 3 else "hair",
                            "title": f"测试候选{index + 1}",
                            "description": f"测试候选描述{index + 1}",
                            "source_image_hash": f"{index + 1:064x}",
                            "confidence": 0.9,
                        }
                    )

                result = await runtime.life_style_catalog_list(None, limit=3)

                self.assertIn("共 5 个已启用候选", result)
                self.assertIn("前 3 个条目，总计 5 个", result)
                self.assertIn("这不是各分类的完整清单", result)
                self.assertIn("不得据此声称衣橱只有当前这些候选", result)
                self.assertIn("必须重新调用 life_style_catalog", result)
            finally:
                archive.close()

    async def test_autonomous_new_outfit_requires_complete_catalog_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            outfit = await archive.upsert_style_catalog_item(
                {
                    "kind": "outfit",
                    "title": "测试完整套装",
                    "description": "蓝色短上衣搭配白色半身裙",
                    "source_image_hash": "a" * 64,
                    "confidence": 0.9,
                }
            )
            top = await archive.upsert_style_catalog_item(
                {
                    "kind": "top",
                    "title": "测试上装",
                    "description": "浅蓝色短袖上衣",
                    "source_image_hash": "b" * 64,
                    "confidence": 0.9,
                }
            )
            bottom = await archive.upsert_style_catalog_item(
                {
                    "kind": "bottom",
                    "title": "测试下装",
                    "description": "白色高腰半身裙",
                    "source_image_hash": "c" * 64,
                    "confidence": 0.9,
                }
            )
            runtime = _StyleCatalogComposer(archive)
            try:
                missing, missing_reason = (
                    await runtime._style_catalog_new_outfit_selection([])
                )
                partial, partial_reason = (
                    await runtime._style_catalog_new_outfit_selection([top.id])
                )
                complete, complete_reason = (
                    await runtime._style_catalog_new_outfit_selection(
                        [top.id, bottom.id]
                    )
                )
                one_piece, one_piece_reason = (
                    await runtime._style_catalog_new_outfit_selection([outfit.id])
                )

                self.assertEqual(missing, {})
                self.assertIn("必须选择", missing_reason)
                self.assertEqual(partial, {})
                self.assertIn("上装与下装", partial_reason)
                self.assertEqual(complete_reason, "")
                self.assertIn("浅蓝色短袖上衣", complete["outfit"])
                self.assertIn("白色高腰半身裙", complete["outfit"])
                self.assertEqual(one_piece_reason, "")
                self.assertEqual(
                    one_piece["outfit"], "蓝色短上衣搭配白色半身裙"
                )
            finally:
                archive.close()

    async def test_autonomous_new_outfit_reference_falls_back_to_active_catalog(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            outfit = await archive.upsert_style_catalog_item(
                {
                    "kind": "outfit",
                    "title": "优先完整套装",
                    "description": "米白针织上衣搭配浅灰长裙",
                    "source_image_hash": "d" * 64,
                    "confidence": 0.9,
                }
            )
            top = await archive.upsert_style_catalog_item(
                {
                    "kind": "top",
                    "title": "备用上装",
                    "description": "浅蓝色短袖上衣",
                    "source_image_hash": "e" * 64,
                    "confidence": 0.9,
                }
            )
            try:
                runtime = _StyleCatalogComposer(archive)
                self.assertEqual(
                    await runtime._style_catalog_resolve_new_outfit_reference_ids([]),
                    [outfit.id],
                )
                self.assertEqual(
                    await runtime._style_catalog_resolve_new_outfit_reference_ids(
                        [top.id]
                    ),
                    [outfit.id],
                )
            finally:
                archive.close()

    async def test_autonomous_new_outfit_reference_falls_back_to_separate_pieces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            top = await archive.upsert_style_catalog_item(
                {
                    "kind": "top",
                    "title": "备用上装",
                    "description": "浅蓝色短袖上衣",
                    "source_image_hash": "f" * 64,
                    "confidence": 0.9,
                }
            )
            bottom = await archive.upsert_style_catalog_item(
                {
                    "kind": "bottom",
                    "title": "备用下装",
                    "description": "白色高腰半身裙",
                    "source_image_hash": "1" * 64,
                    "confidence": 0.9,
                }
            )
            try:
                runtime = _StyleCatalogComposer(archive)
                self.assertEqual(
                    await runtime._style_catalog_resolve_new_outfit_reference_ids([]),
                    [top.id, bottom.id],
                )
            finally:
                archive.close()

    async def test_catalog_context_reserves_multiple_complete_outfits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            for index in range(7):
                await archive.upsert_style_catalog_item(
                    {
                        "kind": "outfit",
                        "title": f"测试套装{index}",
                        "description": f"测试完整穿搭{index}",
                        "source_image_hash": f"{index + 1:064x}",
                        "confidence": 0.9,
                    }
                )
            for index, kind in enumerate(
                ("top", "bottom", "footwear", "accessory", "hair", "makeup", "nails"),
                start=20,
            ):
                await archive.upsert_style_catalog_item(
                    {
                        "kind": kind,
                        "title": f"测试{kind}",
                        "description": f"测试{kind}细节",
                        "source_image_hash": f"{index:064x}",
                        "confidence": 0.9,
                    }
                )
            runtime = _StyleCatalogComposer(archive)
            try:
                context = await runtime._style_catalog_context(limit=14)

                self.assertGreaterEqual(context.count("[套装]"), 6)
                self.assertIn("[上装]", context)
                self.assertIn("[下装]", context)
                self.assertIn("[妆容]", context)
                self.assertIn("[美甲]", context)
                self.assertIn("避免把高偏好候选穿成固定制服", context)
            finally:
                archive.close()

    async def test_daily_review_does_not_relearn_its_own_outfit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _PreferenceRuntime(archive)
            try:
                saved = await runtime.learn_preferences_from_payload(
                    {
                        "preference_points": [
                            {
                                "category": "outfit",
                                "content": "每天穿同一件测试睡裙",
                                "weight": 1.2,
                            },
                            {
                                "category": "activity",
                                "content": "晚饭后喜欢短暂散步",
                                "weight": 0.6,
                            },
                        ]
                    },
                    date_str="2026-08-16",
                    source="daily_review",
                )

                self.assertEqual([item.category for item in saved], ["activity"])
                stored = await archive.get_preferences(10)
                self.assertEqual([item.category for item in stored], ["activity"])
            finally:
                archive.close()

    def test_existing_autonomous_appearance_preferences_are_not_injected(self):
        context = format_life_preference_context(
            [
                PreferenceRecord(
                    category="outfit",
                    content="每天穿同一件测试睡裙",
                    weight=1.2,
                    source="daily_review",
                ),
                PreferenceRecord(
                    category="style",
                    content="偏好清爽且有层次的造型",
                    weight=0.8,
                    source="user_feedback",
                ),
            ],
            limit=10,
            catalog_backed=True,
        )

        self.assertNotIn("同一件测试睡裙", context)
        self.assertIn("偏好清爽且有层次的造型", context)
        self.assertIn("具体服装必须来自本轮提供的衣橱候选", context)

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

    async def test_creative_generation_uses_text_mode_without_character_reference(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试完整创意造型",
                "description": "浅灰针织上衣搭配高腰直筒长裤和白色低跟鞋。",
                "confidence": 0.91,
            },
            "top": {
                "present": True,
                "title": "测试上装",
                "description": "浅灰色针织圆领上衣。",
                "confidence": 0.9,
            },
            "bottom": {
                "present": True,
                "title": "测试下装",
                "description": "高腰直筒长裤。",
                "confidence": 0.9,
            },
            "hair": {
                "present": True,
                "title": "测试发型",
                "description": "自然锁骨发。",
                "confidence": 0.9,
            },
            "makeup": {
                "present": True,
                "title": "测试妆容",
                "description": "清透日常妆。",
                "confidence": 0.9,
            },
            "nails": {
                "present": True,
                "title": "测试美甲",
                "description": "裸色短圆甲。",
                "confidence": 0.9,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _CreativeStyleRuntime(archive, payload)
            event = types.SimpleNamespace(unified_msg_origin="private:test-user")
            try:
                result = await runtime.life_style_generate(
                    event,
                    requirement="适合春天去书店的浅色通勤穿搭，带自然锁骨发和裸色短甲",
                    generation_mode="text_to_image",
                    count=1,
                )
                items = await archive.get_style_catalog_items(status="", limit=20)

                self.assertEqual(getattr(result, "status", ""), "ok")
                self.assertEqual(len(runtime.generated_calls), 1)
                self.assertEqual(runtime.edited_calls, [])
                self.assertFalse(
                    runtime.generated_calls[0][1]["include_character_reference"]
                )
                self.assertIn("不使用任何角色形象参考图", runtime.generated_calls[0][0])
                self.assertIn(
                    "用户本次生成需求（最高优先级）：适合春天去书店的浅色通勤穿搭",
                    runtime.generated_calls[0][0],
                )
                self.assertNotIn("联网灵感", runtime.generated_calls[0][0])
                self.assertEqual({item.kind for item in items}, {"outfit", "top", "bottom", "hair", "makeup", "nails"})
                self.assertTrue(
                    all(item.source_kind == "generated_style_image" for item in items)
                )
                self.assertTrue(
                    all(
                        item.attributes.get("generation_mode") == "text_to_image"
                        and item.attributes.get("creative_request")
                        == "适合春天去书店的浅色通勤穿搭，带自然锁骨发和裸色短甲"
                        and "web_inspiration_used" not in item.attributes
                        for item in items
                    )
                )
            finally:
                archive.close()

    async def test_creative_generation_requires_complete_clothing_before_storage(self):
        payload = {
            "hair": {
                "present": True,
                "title": "测试发型",
                "description": "自然锁骨发。",
                "confidence": 0.9,
            },
            "makeup": {
                "present": True,
                "title": "测试妆容",
                "description": "清透日常妆。",
                "confidence": 0.9,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _CreativeStyleRuntime(archive, payload)
            try:
                result = await runtime.life_style_generate(
                    types.SimpleNamespace(unified_msg_origin="private:test-user"),
                    generation_mode="text_to_image",
                )
                self.assertEqual(getattr(result, "status", ""), "failed")
                self.assertIn("完整套装或上下装组合", str(result))
                self.assertEqual(
                    await archive.get_style_catalog_items(status="", limit=10), []
                )
            finally:
                archive.close()

    async def test_creative_generation_uses_requested_count_without_hidden_cap(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试造型",
                "description": "浅色针织上衣搭配直筒长裤和低跟鞋。",
                "confidence": 0.9,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _CreativeStyleRuntime(archive, payload)
            try:
                result = await runtime.life_style_generate(
                    types.SimpleNamespace(unified_msg_origin="private:test-user"),
                    generation_mode="text_to_image",
                    count=5,
                )

                self.assertEqual(getattr(result, "status", ""), "ok")
                self.assertEqual(len(runtime.generated_calls), 5)
            finally:
                archive.close()

    async def test_creative_image_mode_does_not_fall_back_without_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _CreativeStyleRuntime(archive, {}, character_reference="")
            try:
                result = await runtime.life_style_generate(
                    types.SimpleNamespace(unified_msg_origin="private:test-user"),
                    generation_mode="image_to_image",
                )
                self.assertEqual(getattr(result, "status", ""), "failed")
                self.assertIn("不会改用文生图", str(result))
                self.assertEqual(runtime.generated_calls, [])
                self.assertEqual(runtime.edited_calls, [])
            finally:
                archive.close()

    async def test_creative_image_mode_uses_character_reference_strictly(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试图生图造型",
                "description": "短外套搭配连衣裙和低跟鞋。",
                "confidence": 0.9,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            runtime = _CreativeStyleRuntime(archive, payload)
            try:
                result = await runtime.life_style_generate(
                    types.SimpleNamespace(unified_msg_origin="private:test-user"),
                    generation_mode="image_to_image",
                )
                self.assertEqual(getattr(result, "status", ""), "ok")
                self.assertEqual(runtime.generated_calls, [])
                self.assertEqual(len(runtime.edited_calls), 1)
                self.assertEqual(runtime.edited_calls[0][1], "/tmp/character.jpg")
                self.assertFalse(
                    runtime.edited_calls[0][2]["preserve_reference_ratio"]
                )
                self.assertIn("忽略参考图已有的服装", runtime.edited_calls[0][0])
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
                "description": "浅绿色圆领短袖上衣采用微宽松直身版型，搭配米白色高腰直筒长裤，裤腿自然垂落，配白色低帮鞋与银色细链项链。",
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
                "attributes": {},
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
                self.assertEqual(appearance["makeup_style"], "测试清透妆容")
                self.assertEqual(appearance["makeup"], "清透底妆")
                self.assertEqual(appearance["nails_style"], "测试奶白短甲")
                self.assertEqual(appearance["nails"], "奶白色短圆甲")
            finally:
                archive.close()

    def test_analysis_requires_explicit_visible_item_and_keeps_fields_separate(self):
        payload = {
            "outfit": {
                "present": True,
                "title": "测试服装",
                "description": "蓝色翻领衬衫采用宽松直身剪裁，搭配白色高腰直筒长裤。",
                "colors": ["蓝色", "白色"],
                "component_roles": [
                    {
                        "kind": "accessory",
                        "name": "候选 A-01",
                        "home_presence": "outdoor",
                        "carry_mode": "carried",
                    }
                ],
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
        self.assertEqual(
            outfit["attributes"]["component_roles"][0]["home_presence"],
            "outdoor",
        )
        self.assertNotIn("visual_prompt", outfit["attributes"])
        self.assertEqual(hair, {})

    def test_contract_uses_one_detailed_description(self):
        runtime = object.__new__(_StyleCatalogRuntime)
        contract = runtime._style_catalog_contract("", "auto")

        self.assertIn("description 就是供衣橱展示、检索和后续生图", contract)
        self.assertIn('"home_presence": "home | outdoor | both | unknown"', contract)
        self.assertIn('"carry_mode": "worn | carried | staged | none | unknown"', contract)
        self.assertNotIn('"visual_prompt"', contract)
        self.assertNotIn('"image_summary"', contract)


if __name__ == "__main__":
    unittest.main()
