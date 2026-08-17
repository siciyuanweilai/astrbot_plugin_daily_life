import time
import unittest
from unittest.mock import patch

from runtimehelpers import (
    CORE_INTERNAL_SYSTEM_PROMPT,
    Context,
    DailyLifeRuntime,
    DataManager,
    DayRecord,
    Event,
    GeminiImageService,
    LifeArchive,
    LifeSettings,
    MediaPromptExtractionError,
    Path,
    Provider,
    ReversePromptRecord,
    RuntimeAsyncHelperMixin,
    TimelineItem,
    async_return,
    asyncio,
    base64,
    datetime,
    image_generation_config,
    json,
    tempfile,
    types,
)


class RuntimeImageAsyncTest(RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase):
    def test_image_failure_text_hides_internal_provider_error(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        hidden = runtime._image_tool_failure_text(
            "图片生成", "HTTP 502 /v1/images/generations internal gateway error"
        )
        allowed = runtime._image_tool_failure_text("图片生成", "没有收到可用图片结果")

        self.assertEqual(hidden, "图片生成失败，已记录失败原因。")
        self.assertEqual(allowed, "图片生成失败：没有收到可用图片结果")

    def test_reverse_prompt_visual_profiles_have_distinct_focus(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        cover_name, cover_instruction = runtime._reverse_prompt_profile_instruction(
            "视觉封面"
        )
        cover_contract = runtime._reverse_prompt_contract("", "视觉封面")
        self.assertEqual(cover_name, "视觉封面")
        self.assertIn("标题或文案留白", cover_instruction)
        self.assertIn("裁切安全区", cover_instruction)
        self.assertIn("反推方案：视觉封面", cover_contract)
        self.assertIn("只记录实际可见文字", cover_contract)

        design_name, design_instruction = runtime._reverse_prompt_profile_instruction(
            "设计视觉"
        )
        design_contract = runtime._reverse_prompt_contract("", "设计视觉")
        self.assertEqual(design_name, "设计视觉")
        self.assertIn("网格与对齐", design_instruction)
        self.assertIn("文字层级", design_instruction)
        self.assertIn("反推方案：设计视觉", design_contract)
        self.assertIn("无法辨认的文字留空", design_contract)

    async def test_character_appearance_profile_uses_semantics_and_persona_cache(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        personas = ["成年女性，整体纤细匀称，上半身曲线自然丰满，与肩腰胯比例协调。"]
        prompts = []

        runtime.get_persona_text = lambda scope="": async_return(personas[0])

        async def extract(prompt):
            prompts.append(prompt)
            await asyncio.sleep(0)
            if len(prompts) == 2:
                return {
                    "supported": True,
                    "appearance_profile": "成年女性，身形高挑，体态自然舒展",
                }
            return {
                "supported": True,
                "appearance_profile": (
                    "成年女性，整体纤细匀称，上半身曲线自然丰满，与肩腰胯比例协调"
                ),
            }

        runtime._media_director_call = extract
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        first, second = await asyncio.gather(
            runtime._character_appearance_profile(event),
            runtime._character_appearance_profile(event),
        )
        cached = await runtime._character_appearance_profile(event)
        personas[0] = (
            "成年女性，  整体纤细匀称，\n上半身曲线自然丰满，与肩腰胯比例协调。"
        )
        equivalent = await runtime._character_appearance_profile(event)
        personas[0] = "成年女性，身形高挑，体态自然舒展。"
        third = await runtime._character_appearance_profile(event)
        fourth = await runtime._character_appearance_profile(event)

        self.assertEqual(first, second)
        self.assertIn("上半身曲线自然丰满", first)
        self.assertIn("上半身曲线自然丰满", cached)
        self.assertEqual(equivalent, cached)
        self.assertEqual(third, "成年女性，身形高挑，体态自然舒展")
        self.assertEqual(fourth, "成年女性，身形高挑，体态自然舒展")
        self.assertEqual(len(prompts), 2)
        self.assertIn("不得根据性别、年龄、性格或审美推断", prompts[0])
        self.assertNotIn("用户本轮", prompts[0])

    async def test_character_appearance_failure_is_not_cached(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        calls = []
        runtime.get_persona_text = lambda scope="": async_return(
            "成年女性，体型匀称，体态自然。"
        )

        async def extract(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                raise RuntimeError("temporary failure")
            return {
                "supported": True,
                "appearance_profile": "成年女性，体型匀称，体态自然",
            }

        runtime._media_director_call = extract

        self.assertEqual(await runtime._character_appearance_profile(Event()), "")
        self.assertEqual(
            await runtime._character_appearance_profile(Event()),
            "成年女性，体型匀称，体态自然",
        )
        self.assertEqual(len(calls), 2)

    async def test_character_appearance_profile_caches_no_evidence_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        calls = []
        runtime.get_persona_text = lambda scope="": async_return(
            "性格温暖，喜欢摄影和旧书店。"
        )

        async def extract(prompt):
            calls.append(prompt)
            return {"supported": False, "appearance_profile": ""}

        runtime._media_director_call = extract

        self.assertEqual(await runtime._character_appearance_profile(Event()), "")
        await asyncio.sleep(0)
        self.assertEqual(await runtime._character_appearance_profile(Event()), "")
        self.assertEqual(len(calls), 1)

    async def test_identity_profiles_only_include_current_character(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        character_calls = []
        runtime._character_appearance_profile = lambda event: (
            character_calls.append(event)
            or async_return("整体纤细匀称，上半身曲线自然丰满")
        )
        event = Event()

        for route in ("scene", "object", "free"):
            self.assertEqual(
                await runtime._resolve_life_identity_profiles(event, route),
                {},
            )
        self.assertEqual(character_calls, [])

        current = await runtime._resolve_life_identity_profiles(
            event, "current_character"
        )
        group = await runtime._resolve_life_identity_profiles(event, "group")
        self.assertEqual(
            current,
            {"current_character": "整体纤细匀称，上半身曲线自然丰满"},
        )
        self.assertEqual(
            group,
            {"current_character": "整体纤细匀称，上半身曲线自然丰满"},
        )
        self.assertEqual(character_calls, [event, event])

    async def test_character_image_edit_keeps_persona_profile_over_request_change(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        edit_calls = []

        async def direct(event, prompt, **kwargs):
            return types.SimpleNamespace(
                prompt=prompt,
                contains_character=True,
                needs_character_reference=True,
            )

        runtime._direct_life_image_payload = direct
        runtime._character_appearance_profile = lambda event: async_return(
            "整体纤细匀称，上半身曲线自然丰满，与肩腰胯比例协调"
        )
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        prompt = "保留角色本人身份，但改成与人设不同的身体比例"

        result = await runtime.edit_life_image(
            event, prompt, "https://example.com/reference.png"
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(
            edit_calls,
            [
                (
                    prompt,
                    "https://example.com/reference.png",
                    {
                        "preserve_reference_ratio": True,
                        "identity_profile": (
                            "整体纤细匀称，上半身曲线自然丰满，与肩腰胯比例协调"
                        ),
                    },
                )
            ],
        )

    async def test_life_image_generate_passes_explicit_provider_to_service(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        calls = []
        image_path = Path(tempfile.mkdtemp()) / "life.png"
        image_path.write_bytes(b"x" * 2048)

        async def generate_image(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return types.SimpleNamespace(path=image_path)

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate_image)
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(
            event, "雨夜生活照", provider="gemini"
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(calls, [("雨夜生活照", {"protocol": "gemini"})])

    async def test_life_image_generate_sends_media(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "life.png"
        image_path.write_bytes(b"x" * 2048)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=image_path)
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(json.loads(result)["media"], "image")
        self.assertNotIn("图片已发送", result)
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(event.sent_messages), 1)
        self.assertIn(
            {"type": "image", "file": str(image_path)},
            event.sent_messages[0].items,
        )
        self.assertFalse(
            any(
                call[0] == "update_conversation"
                for call in runtime.context.conversation_manager.calls
            )
        )
        cadence = runtime._media_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_media"], "图片")
        self.assertEqual(cadence["consecutive"], 1)

    async def test_life_image_generate_persists_explicit_outfit_before_rendering(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        now = datetime.datetime(2026, 7, 30, 16, 44)
        updated_day = DayRecord(
            date="2026-07-30",
            outfit="浅粉色泡泡袖针织短袖上衣，搭配米白色A字百褶短裙",
            meta={
                "style": "清爽甜妹风",
                "hair_style": "锁骨发半扎",
                "hair": "自然黑色微卷锁骨发，半扎并留有空气感碎刘海",
            },
        )
        calls = []

        class Composer:
            async def update_outfit(
                self,
                date,
                period,
                *,
                current_time=None,
                instruction="",
                source_instruction="",
            ):
                calls.append(
                    (
                        "update",
                        date,
                        period,
                        current_time,
                        instruction,
                        source_instruction,
                    )
                )
                return updated_day

        async def generate_image(prompt, **kwargs):
            calls.append(("generate", prompt, kwargs))
            return types.SimpleNamespace(path=Path("life.png"))

        runtime.composer = Composer()

        class Archive:
            async def get_day(self, date):
                return DayRecord(
                    date=date,
                    outfit="米白色居家短袖和淡青色居家短裤",
                    meta={"style": "清爽居家风", "hair": "抓夹半扎发"},
                )

        runtime.archive = Archive()
        runtime._runtime_now = lambda: now
        runtime.resolve_injection_target = lambda current: async_return(
            ("2026-07-30", False)
        )
        runtime._get_curr_period = lambda current: "evening"
        status_changes = []
        runtime.mark_page_status_changed = lambda reason: (
            status_changes.append(reason) or async_return(1)
        )
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate_image)
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "换甜妹穿搭"
        scene_prompts = []

        async def isolate_scene(prompt, route):
            scene_prompts.append((prompt, route))
            return "站在门口手拿小风扇，柔和自然光，半身生活照"

        runtime._isolate_outfit_change_scene_prompt = isolate_scene

        result = await runtime.life_image_generate(
            event,
            "站在门口展示浅黄色夏日连衣裙，手拿小风扇，柔和自然光",
            subject_route="current_character",
            current_outfit_change=True,
            current_outfit_instruction="换甜妹穿搭",
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(
            calls[0],
            (
                "update",
                "2026-07-30",
                "evening",
                now,
                "换甜妹穿搭",
                "换甜妹穿搭",
            ),
        )
        self.assertEqual(calls[1][0], "generate")
        rendered_prompt = calls[1][1]
        self.assertIn(updated_day.outfit, rendered_prompt)
        self.assertIn("当前穿搭风格：清爽甜妹风", rendered_prompt)
        self.assertIn("当前角色造型（来自当前生活状态）", rendered_prompt)
        self.assertIn("本轮真实换装已经保存后的唯一最终造型", rendered_prompt)
        self.assertIn("原始换装要求已经完成解析", rendered_prompt)
        self.assertNotIn("浅黄色夏日连衣裙", rendered_prompt)
        self.assertNotIn("用户当前原始请求：换甜妹穿搭", rendered_prompt)
        self.assertNotIn("只有用户当前原始请求明确要求", rendered_prompt)
        self.assertLess(
            rendered_prompt.index("站在门口手拿小风扇"),
            rendered_prompt.index("当前生活状态权威造型快照"),
        )
        self.assertEqual(
            scene_prompts,
            [
                (
                    "站在门口展示浅黄色夏日连衣裙，手拿小风扇，柔和自然光",
                    "current_character",
                )
            ],
        )
        self.assertEqual(status_changes, ["outfit_update"])

    async def test_outfit_change_scene_prompt_removes_conflicting_appearance(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        prompts = []

        async def rewrite(prompt, *, provider_id=""):
            prompts.append((prompt, provider_id))
            return json.dumps(
                {"prompt": "站在房间门口手拿小风扇，柔和自然光，半身构图"},
                ensure_ascii=False,
            )

        runtime._media_director_text_call = rewrite

        result = await runtime._isolate_outfit_change_scene_prompt(
            "身穿浅黄色连衣裙，站在房间门口手拿小风扇，柔和自然光，半身构图",
            "current_character",
        )

        self.assertEqual(
            result,
            "站在房间门口手拿小风扇，柔和自然光，半身构图",
        )
        self.assertEqual(len(prompts), 1)
        self.assertIn("移除当前角色的服装", prompts[0][0])
        self.assertIn("浅黄色连衣裙", prompts[0][0])

    async def test_outfit_change_scene_prompt_uses_safe_fallback_on_failure(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})

        async def rewrite(*args, **kwargs):
            raise RuntimeError("provider unavailable")

        runtime._media_director_text_call = rewrite

        result = await runtime._isolate_outfit_change_scene_prompt(
            "身穿浅黄色连衣裙和红色高跟鞋",
            "current_character",
        )

        self.assertEqual(
            result,
            "当前角色完成换装后的自然生活照，真实日常抓拍感。",
        )
        self.assertNotIn("浅黄色连衣裙", result)

    async def test_life_image_generate_cancels_when_outfit_persistence_fails(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime._runtime_now = lambda: datetime.datetime(2026, 7, 30, 16, 44)
        runtime.resolve_injection_target = lambda current: async_return(
            ("2026-07-30", False)
        )
        runtime._get_curr_period = lambda current: "evening"

        class Composer:
            async def update_outfit(self, *args, **kwargs):
                return None

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda *args, **kwargs: self.fail(
                    "image generation must not run"
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(
            event,
            "站在门口准备出门",
            subject_route="current_character",
            current_outfit_change=True,
            current_outfit_instruction="换甜妹穿搭",
        )

        self.assertEqual(result, "这次换装状态没有更新成功，已取消图片生成。")

    async def test_life_image_generate_uses_full_message_prompt(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        full_prompt = "生成一张高度写实的竖版 9:16 古风 POV，雨后古镇青石板小巷，油纸伞，衣袖牵引，胶片写实质感，保留这句唯一细节"
        event.message_str = f"  {full_prompt}"

        result = await runtime.life_image_generate(event, "雨后古镇少女撑伞")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, [(full_prompt, {"aspect_ratio": "9:16"})])

    async def test_life_image_generate_uses_director_when_agent_expands_short_request(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        director_prompts = []

        async def direct(event, prompt, **kwargs):
            director_prompts.append(prompt)
            return types.SimpleNamespace(
                prompt=f"导演整理：{prompt}",
                contains_character=False,
                needs_character_reference=False,
            )

        runtime._direct_life_image_payload = direct
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "拍张照看看"
        agent_prompt = (
            "一个年轻女孩穿着浅灰色蕾丝边棉质吊带睡裙，头发扎成低马尾，"
            "站在洗手间镜子前准备洗漱，暖黄色灯光，夜晚居家氛围，生活随手抓拍镜头。"
        )

        result = await runtime.life_image_generate(event, agent_prompt)

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(director_prompts, [agent_prompt])
        self.assertEqual(image_prompts, [(f"导演整理：{agent_prompt}", {})])

    async def test_life_image_generate_uses_last_reverse_prompt_when_requested(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "/把反推的提示词生成一张"
        reverse_prompt = (
            "蓝色长发角色近景肖像，冰雪饰品，冷色调梦幻烟雾，高清写实摄影。"
        )
        runtime._remember_reverse_prompt_for_scope(event, reverse_prompt)

        result = await runtime.life_image_generate(
            event, "", use_last_reverse_prompt=True
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, [(reverse_prompt, {})])

    async def test_life_image_generate_loads_last_reverse_prompt_from_archive(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        reverse_prompt = "窗边热茶，暖色台灯，浅景深生活照。"

        class Archive:
            async def get_latest_reverse_prompt(self, scope):
                self.scope = scope
                return ReversePromptRecord(
                    scope=scope,
                    prompt=reverse_prompt,
                    image_path="D:/tmp/reverse.png",
                )

        archive = Archive()
        runtime.archive = archive
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(
            event, "", use_last_reverse_prompt=True
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(archive.scope, "aiocqhttp:FriendMessage:10001")
        self.assertEqual(image_prompts, [(reverse_prompt, {})])
        self.assertEqual(runtime._last_reverse_prompt_for_scope(event), reverse_prompt)

    async def test_life_image_generate_does_not_auto_use_last_reverse_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []

        class ImageService:
            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                image_prompts.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("life.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                raise AssertionError("should not auto use reverse reference image")

        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        reverse_prompt = (
            "蓝色长发角色近景肖像，冰雪饰品，冷色调梦幻烟雾，高清写实摄影。"
        )
        runtime._remember_reverse_prompt_for_scope(
            event, reverse_prompt, "D:/tmp/reverse.png"
        )

        result = await runtime.life_image_generate(
            event, "", use_last_reverse_prompt=True
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, [(reverse_prompt, {})])

    async def test_life_image_generate_does_not_auto_use_reverse_reference_for_exact_prompt(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []

        class ImageService:
            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                image_prompts.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("life.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                raise AssertionError(
                    "should not auto use cached reverse reference image"
                )

        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "把这条提示词生成一张"
        reverse_prompt = "冷色调梦幻烟雾中的蓝色长发角色近景肖像，高清写实摄影。"
        runtime._remember_reverse_prompt_for_scope(
            event, reverse_prompt, "D:/tmp/reverse.png"
        )

        result = await runtime.life_image_generate(event, reverse_prompt)

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, [(reverse_prompt, {})])

    async def test_life_image_generate_uses_detailed_user_text_directly(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append(prompt)
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "雨后古镇青石板小巷，暖黄色灯笼光，月白淡粉汉服，第一视角斜俯拍，手机夜间抓拍胶片质感"

        result = await runtime.life_image_generate(event, "雨后古镇少女撑伞")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, [event.message_str])

    async def test_life_image_generate_prefers_user_prompt_aspect_ratio(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    calls.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = (
            "雨后古镇青石板小巷，第一视角斜俯拍，竖版 9:16，手机夜间抓拍胶片质感"
        )

        result = await runtime.life_image_generate(event, "雨后古镇少女撑伞")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(calls, [(event.message_str, {"aspect_ratio": "9:16"})])

    async def test_life_image_generate_parses_text_resolution(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    calls.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "雨后古镇生活照，输出 4K 图片"

        result = await runtime.life_image_generate(event, "备用提示词")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(calls, [("备用提示词", {"resolution": "4K"})])

    async def test_life_image_generate_tool_resolution_overrides_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    calls.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "雨后古镇生活照，输出 4K 图片"

        result = await runtime.life_image_generate(event, "备用提示词", resolution="2K")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(calls, [("备用提示词", {"resolution": "2K"})])

    def test_image_prompt_aspect_ratio_only_accepts_supported_numeric_ratio(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        self.assertEqual(
            runtime._image_prompt_aspect_ratio("竖版 9:16 手机拍摄"), "9:16"
        )
        self.assertEqual(
            runtime._image_prompt_aspect_ratio("横版 16：9 手机拍摄"), "16:9"
        )
        self.assertEqual(
            runtime._image_prompt_aspect_ratio("编号 119:160，不是支持比例"), ""
        )
        self.assertEqual(runtime._image_prompt_aspect_ratio("不要默认方图"), "")

    def test_image_prompt_resolution_accepts_standalone_last_token(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        self.assertEqual(runtime._image_prompt_resolution("输出 2K 图片"), "2K")
        self.assertEqual(runtime._image_prompt_resolution("先 1k，改成 4K"), "4K")
        self.assertEqual(runtime._image_prompt_resolution("14K、4KB"), "")

    async def test_life_image_generate_uses_cached_source_event_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        source_event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001"
        )
        full_prompt = "生成一张高度写实的竖版 9:16 古风 POV 夜游抓拍写真，互动动作、场景、服装、光线、负面要求全部保留"
        source_event.message_str = full_prompt
        runtime.note_media_source_event(source_event)
        tool_event = Event(
            unified_msg_origin=source_event.unified_msg_origin, group_id="20001"
        )

        result = await runtime.life_image_generate(tool_event, "古风灯会街巷少女递花灯")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, [(full_prompt, {"aspect_ratio": "9:16"})])

    async def test_life_image_generate_does_not_treat_slash_as_direct_mode(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        director_prompts = []

        async def direct(event, prompt, **kwargs):
            director_prompts.append(prompt)
            return types.SimpleNamespace(
                prompt=f"导演整理：{prompt}",
                contains_character=False,
                needs_character_reference=False,
            )

        runtime._direct_life_image_payload = direct
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append(prompt)
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "/拍张现在"

        result = await runtime.life_image_generate(event, "拍一张当前生活照")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(director_prompts, ["拍一张当前生活照"])
        self.assertEqual(image_prompts, ["导演整理：拍一张当前生活照"])

    async def test_life_image_generate_uses_director_for_short_life_request(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        director_prompts = []

        async def direct(event, prompt, **kwargs):
            director_prompts.append(prompt)
            return types.SimpleNamespace(
                prompt=f"导演整理：{prompt}",
                contains_character=False,
                needs_character_reference=False,
            )

        runtime._direct_life_image_payload = direct
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: (
                    image_prompts.append(prompt)
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "拍张现在"

        result = await runtime.life_image_generate(event, "拍一张当前生活照")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(director_prompts, ["拍一张当前生活照"])
        self.assertEqual(image_prompts, ["导演整理：拍一张当前生活照"])

    async def test_life_image_generate_switches_to_edit_route_when_director_needs_character_reference(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        generate_calls = []
        edit_calls = []

        async def direct(event, prompt, *, reference=False):
            return types.SimpleNamespace(
                prompt=f"导演整理：{prompt}",
                contains_character=True,
                needs_character_reference=True,
            )

        class ImageService:
            def first_character_reference_image(self):
                return "D:/ref/role.png"

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("life.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path("role.png"))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "请按角色本人参考图再拍一张"

        result = await runtime.life_image_generate(event, "拍一张角色本人参考图")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [
                (
                    "导演整理：拍一张角色本人参考图",
                    "D:/ref/role.png",
                    {"preserve_reference_ratio": False},
                )
            ],
        )

    async def test_life_image_generate_keeps_text_route_when_director_marks_character_but_not_reference(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        edit_calls = []
        generate_calls = []

        async def direct(event, prompt, *, reference=False):
            return types.SimpleNamespace(
                prompt=f"导演整理：{prompt}",
                contains_character=True,
                needs_character_reference=False,
            )

        class ImageService:
            def first_character_reference_image(self):
                return "D:/ref/role.png"

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("scene.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path("role.png"))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "拍一下窗外"

        result = await runtime.life_image_generate(event, "拍一张窗外雨夜")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [("导演整理：拍一张窗外雨夜", {})])
        self.assertEqual(edit_calls, [])

    async def test_life_image_generate_keeps_text_route_when_director_reports_character_but_no_reference(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        edit_calls = []
        generate_calls = []

        async def direct(event, prompt, *, reference=False):
            return types.SimpleNamespace(
                prompt=f"导演整理：{prompt}",
                contains_character=True,
                needs_character_reference=False,
            )

        class ImageService:
            def first_character_reference_image(self):
                return "D:/ref/role.png"

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("scene.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path("role.png"))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "拍一下窗外"

        result = await runtime.life_image_generate(event, "拍一张窗外雨夜")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [("导演整理：拍一张窗外雨夜", {})])
        self.assertEqual(edit_calls, [])

    async def test_life_image_generate_director_character_reference_uses_config_ratio(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        generate_calls = []
        edit_calls = []

        async def direct(event, prompt, *, reference=False):
            return types.SimpleNamespace(
                prompt=f"导演整理：{prompt}",
                contains_character=True,
                needs_character_reference=True,
            )

        class ImageService:
            def first_character_reference_image(self):
                return "D:/ref/role.png"

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("scene.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path("role.png"))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "画图"

        result = await runtime.life_image_generate(event, "角色本人坐在窗边")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [
                (
                    "导演整理：角色本人坐在窗边",
                    "D:/ref/role.png",
                    {"preserve_reference_ratio": False},
                )
            ],
        )

    async def test_life_image_generate_keeps_tool_prompt_without_slash_command(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: (
                    image_prompts.append(prompt)
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "我在聊天里提到 / 这个符号，但不是图片直给命令"

        result = await runtime.life_image_generate(event, "模型整理后的图片提示词")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, ["模型整理后的图片提示词"])

    async def test_life_image_generate_reports_empty_exception_type(self):
        async def fail_image(prompt):
            raise TimeoutError()

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=fail_image)
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertIn("图片生成失败：超时", result)
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_life_image_generate_rewrites_policy_violation_once(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "life.png"
        image_path.write_bytes(b"x")
        prompts = []
        rewrites = []

        async def generate_image(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                raise RuntimeError(
                    'HTTP 400：{"error":{"code":"content_policy_violation"}}'
                )
            return types.SimpleNamespace(path=image_path)

        async def rewrite(event, prompt, *, reference=False):
            rewrites.append((prompt, reference))
            return "雨夜生活照，自然生活化表达"

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate_image)
        )
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(prompts, ["雨夜生活照", "雨夜生活照，自然生活化表达"])
        self.assertEqual(rewrites, [("雨夜生活照", False)])

    async def test_life_image_generate_returns_short_failure_after_policy_retry_failure(
        self,
    ):
        from core.runtime.channel import image as image_channel

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        messages = []

        async def generate_image(prompt):
            raise RuntimeError(
                'HTTP 400：{"error":{"code":"content_policy_violation","message":"blocked"}}'
            )

        async def rewrite(event, prompt, *, reference=False):
            return "雨夜生活照，自然生活化表达"

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate_image)
        )
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        old_warning = image_channel.logger.warning
        image_channel.logger.warning = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
        try:
            result = await runtime.life_image_generate(event, "雨夜生活照")
        finally:
            image_channel.logger.warning = old_warning

        self.assertIn("图片生成失败：图片轻量润色后重试仍失败", result)
        self.assertNotIn("HTTP 400", result)
        self.assertNotIn("content_policy_violation", result)
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("图片生成或发送失败", messages[0])
        self.assertIn("HTTP 400", messages[0])
        self.assertIn("content_policy_violation", messages[0])

    async def test_life_image_generate_hides_rewrite_failure_detail(self):
        from core.runtime.channel import image as image_channel

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        messages = []

        async def generate_image(prompt):
            raise RuntimeError(
                'HTTP 400：{"error":{"code":"content_policy_violation","message":"blocked"}}'
            )

        async def rewrite(event, prompt, *, reference=False):
            raise RuntimeError("图片轻量润色失败：图片智能提取没有返回有效画面字段")

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate_image)
        )
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        old_warning = image_channel.logger.warning
        image_channel.logger.warning = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
        try:
            result = await runtime.life_image_generate(event, "雨夜生活照")
        finally:
            image_channel.logger.warning = old_warning

        self.assertEqual(result, "图片生成失败：图片触发安全拒绝，轻量润色失败。")
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(messages), 1)
        self.assertIn(
            "图片生成或发送失败：图片触发安全拒绝，轻量润色失败。", messages[0]
        )
        self.assertNotIn("图片智能提取没有返回有效画面字段", messages[0])

    async def test_life_image_policy_rewrite_logs_safety_rejection(self):
        from core.runtime.channel import image as image_channel

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "life.png"
        image_path.write_bytes(b"x")
        prompts = []
        messages = []

        async def generate_image(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                raise RuntimeError(
                    'HTTP 400：{"error":{"code":"content_policy_violation","message":"blocked"}}'
                )
            return types.SimpleNamespace(path=image_path)

        async def rewrite(event, prompt, *, reference=False):
            return "雨夜生活照，自然生活化表达"

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate_image)
        )
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        old_info = image_channel.logger.info
        old_debug = image_channel.logger.debug
        image_channel.logger.info = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
        image_channel.logger.debug = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
        try:
            result = await runtime.life_image_generate(event, "雨夜生活照")
        finally:
            image_channel.logger.info = old_info
            image_channel.logger.debug = old_debug

        self.assertEqual(json.loads(result)["status"], "sent")
        policy_logs = [
            message for message in messages if "图片触发安全拒绝，尝试" in message
        ]
        rewrite_logs = [
            message for message in messages if "图片轻量润色完成" in message
        ]
        self.assertEqual(len(policy_logs), 1)
        self.assertEqual(len(rewrite_logs), 1)
        self.assertIn("图片触发安全拒绝", policy_logs[0])
        self.assertNotIn("content_policy_violation", policy_logs[0])
        self.assertNotIn("blocked", policy_logs[0])
        self.assertIn("提示词长度=", rewrite_logs[0])

    async def test_life_image_policy_rewrite_uses_media_director_json(self):
        provider = Provider(['{"prompt":"雨夜窗边生活照，自然生活化表达，保留原构图"}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(prompt, session_id)
                return resp.completion_text

        runtime.composer = Composer()
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        prompt = await runtime._rewrite_life_image_prompt_for_policy_retry(
            event, "雨夜窗边生活照"
        )

        self.assertEqual(prompt, "雨夜窗边生活照，自然生活化表达，保留原构图")
        self.assertIn("图片轻量润色", provider.prompts[0])
        self.assertIn("尽量少改原文", provider.prompts[0])
        self.assertIn("雨夜窗边生活照", provider.prompts[0])

    async def test_life_image_policy_rewrite_accepts_plain_text(self):
        provider = Provider(["雨夜窗边生活照，自然生活化表达，保留原构图"])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(prompt, session_id)
                return resp.completion_text

        runtime.composer = Composer()
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        prompt = await runtime._rewrite_life_image_prompt_for_policy_retry(
            event, "雨夜窗边生活照"
        )

        self.assertEqual(prompt, "雨夜窗边生活照，自然生活化表达，保留原构图")

    async def test_life_image_policy_rewrite_accepts_loose_json(self):
        provider = Provider(
            ["```json\n{'prompt':'雨夜窗边生活照，自然生活化表达，保留原构图',}\n```"]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(prompt, session_id)
                return resp.completion_text

        runtime.composer = Composer()
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        prompt = await runtime._rewrite_life_image_prompt_for_policy_retry(
            event, "雨夜窗边生活照"
        )

        self.assertEqual(prompt, "雨夜窗边生活照，自然生活化表达，保留原构图")

    async def test_life_image_policy_rewrite_uses_configured_provider(self):
        default_provider = Provider([], provider_id="default-model")
        rewrite_provider = Provider(
            ['{"prompt":"雨夜窗边生活照，自然生活化表达，保留原构图"}'],
            provider_id="rewrite-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            default_provider, providers={"rewrite-model": rewrite_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"image_generation_config": {"prompt_rewrite_provider": "rewrite-model"}}
        )
        requested_providers = []
        primary_provider_ids = []

        class Composer:
            async def _get_provider(self, provider_id=""):
                requested_providers.append(provider_id)
                if provider_id == "rewrite-model":
                    return rewrite_provider
                return default_provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                primary_provider_ids.append(primary_provider_id)
                resp = await provider.text_chat(prompt, session_id)
                return resp.completion_text

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        prompt = await runtime._rewrite_life_image_prompt_for_policy_retry(
            event, "雨夜窗边生活照"
        )

        self.assertEqual(prompt, "雨夜窗边生活照，自然生活化表达，保留原构图")
        self.assertEqual(requested_providers, ["rewrite-model"])
        self.assertEqual(primary_provider_ids, ["rewrite-model"])
        self.assertEqual(default_provider.prompts, [])
        self.assertEqual(len(rewrite_provider.prompts), 1)

    async def test_life_image_generate_resolves_agent_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=Path("life.png"))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        wrapped_event = types.SimpleNamespace(
            context=types.SimpleNamespace(event=event)
        )

        result = await runtime.life_image_generate(wrapped_event, "咖喱店生活照")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)

    async def test_life_image_generate_uses_life_media_director_prompt(self):
        provider = Provider(
            [
                (
                    '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false,'
                    '"appearance_profile":"整体纤细匀称，上半身曲线自然丰满，与肩腰胯比例协调",'
                    '"subject":"窗边的我","subject_kind":"character","scene":"雨夜客厅","composition":"半身生活照","visible_scope":"半身",'
                    '"scene_type":"家里","temperature_feel":"微凉","weather_condition":"小雨",'
                    '"frame_logic":"半身取景能看到抱枕、窗边和上半身居家穿搭",'
                    '"lighting":"暖色台灯","outfit":"宽松白色长T恤",'
                    '"hair":"黑色中长直发，低马尾，碎发自然垂落",'
                    '"makeup":"清透自然妆","nails":"奶白色短圆甲",'
                    '"appearance_style":"清爽柔和的居家风",'
                    '"body_presentation":"合身上衣自然呈现既有的整体轮廓与比例",'
                    '"outfit_visibility":"上半身可见",'
                    '"outfit_logic":"人在客厅休息，只呈现半身可见的居家长T恤",'
                    '"action":"抱着抱枕看窗外",'
                    '"weather_vibe":"窗玻璃上有细雨水痕","mood":"慵懒治愈","constraints":"真实生活抓拍"}'
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.archive.days["2026-05-24"] = DayRecord(
            date="2026-05-24",
            weather="小雨 20°C",
            outfit="宽松白色长T恤",
            timeline=[
                TimelineItem(time="20:10", activity="窝在客厅看窗外下雨", status="放松")
            ],
            meta={
                "mood": "薄荷绿·治愈",
                "theme": "宅家充电的慵懒一日",
                "style": "清爽柔和的居家风",
                "hair_style": "松散低马尾",
                "hair": "黑色中长直发，低马尾，碎发自然垂落",
                "makeup_style": "清透自然妆",
                "makeup": "薄透底妆，淡粉唇色",
                "nails_style": "奶白色短圆甲",
                "nails": "短圆甲面保持奶白色，表面干净",
            },
        )

        async def current_day():
            return (
                runtime.archive.days["2026-05-24"],
                datetime.datetime(2026, 5, 24, 20, 30),
                False,
            )

        runtime._media_director_current_day = current_day
        runtime._character_appearance_context = lambda event, schedule_extract=False: (
            async_return(
                (
                    "成年女性，整体纤细匀称，上半身曲线自然丰满，与肩腰胯比例协调。",
                    "整体纤细匀称，上半身曲线自然丰满，与肩腰胯比例协调",
                )
            )
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append(prompt)
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜沙发上随手拍")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertIn("雨夜客厅", image_prompts[0])
        self.assertIn("半身生活照", image_prompts[0])
        self.assertIn("场景类型：家里", image_prompts[0])
        self.assertIn("温感：微凉", image_prompts[0])
        self.assertIn("天气：小雨", image_prompts[0])
        self.assertIn("可见范围：半身", image_prompts[0])
        self.assertIn("取景逻辑：半身取景能看到抱枕", image_prompts[0])
        self.assertIn("穿搭可见性：上半身可见", image_prompts[0])
        self.assertIn("穿搭逻辑：人在客厅休息", image_prompts[0])
        self.assertIn("发型：黑色中长直发，低马尾，碎发自然垂落", image_prompts[0])
        self.assertIn("妆容：清透自然妆", image_prompts[0])
        self.assertIn("美甲：奶白色短圆甲", image_prompts[0])
        self.assertIn("造型风格：清爽柔和的居家风", image_prompts[0])
        self.assertIn(
            "体貌呈现：合身上衣自然呈现既有的整体轮廓与比例",
            image_prompts[0],
        )
        self.assertNotIn("写实生活照", image_prompts[0])
        self.assertNotIn("画面要求：雨夜沙发上随手拍", image_prompts[0])
        self.assertIn("当前生活上下文", provider.prompts[0])
        self.assertIn('"identity_route"', provider.prompts[0])
        self.assertIn("图片导演裁定", provider.prompts[0])
        self.assertIn("subject_kind", provider.prompts[0])
        self.assertIn("subject_kind 必须与 identity_route 一致", provider.prompts[0])
        self.assertIn("scene_type", provider.prompts[0])
        self.assertIn("temperature_feel", provider.prompts[0])
        self.assertIn("visible_scope", provider.prompts[0])
        self.assertIn("outfit_visibility", provider.prompts[0])
        self.assertIn('"hair"', provider.prompts[0])
        self.assertIn('"makeup"', provider.prompts[0])
        self.assertIn('"nails"', provider.prompts[0])
        self.assertIn('"appearance_style"', provider.prompts[0])
        self.assertIn('"body_presentation"', provider.prompts[0])
        self.assertIn("当前角色稳定体貌", provider.prompts[0])
        self.assertIn("上半身曲线自然丰满", provider.prompts[0])
        self.assertIn("当前穿搭风格：清爽柔和的居家风", provider.prompts[0])
        self.assertIn("当前发型名称：松散低马尾", provider.prompts[0])
        self.assertIn(
            "当前发型细节：黑色中长直发，低马尾，碎发自然垂落",
            provider.prompts[0],
        )
        self.assertIn("当前妆容名称：清透自然妆", provider.prompts[0])
        self.assertIn("当前妆容细节：薄透底妆，淡粉唇色", provider.prompts[0])
        self.assertIn("当前美甲名称：奶白色短圆甲", provider.prompts[0])
        self.assertIn("当前美甲细节：短圆甲面保持奶白色，表面干净", provider.prompts[0])
        self.assertIn("原始画面要求 > 真实参考图 > 当前生活外观", provider.prompts[0])
        self.assertIn("frame_logic", provider.prompts[0])
        self.assertIn("outfit_logic", provider.prompts[0])
        self.assertLess(
            provider.prompts[0].index("当前生活上下文"),
            provider.prompts[0].index("原始画面要求（最终画面需求"),
        )
        self.assertEqual(
            runtime._life_media_last_images[event.unified_msg_origin], "life.png"
        )

    async def test_life_image_director_uses_semantic_character_reference_decision(self):
        provider = Provider(
            [
                (
                    '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false,'
                    '"subject":"窗边的我","subject_kind":"character","scene":"雨夜客厅","composition":"半身生活照",'
                    '"visible_scope":"半身","frame_logic":"自然生活取景"}'
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime._direct_life_image_payload(event, "雨夜沙发上随手拍")

        self.assertTrue(result.contains_character)
        self.assertFalse(result.needs_character_reference)
        self.assertEqual(result.identity_route, "角色本人")
        prompt = provider.prompts[0]
        self.assertIn("identity_route", prompt)
        self.assertIn("身份路线裁定", prompt)
        self.assertIn("subject", prompt)
        self.assertIn("图片导演裁定", prompt)
        self.assertNotIn("coser", prompt)
        self.assertNotIn("路人", prompt)

    async def test_life_image_director_uses_one_call_per_image(self):
        provider = Provider(
            [
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false,"subject":"窗边的我","subject_kind":"character","scene":"卧室","composition":"半身生活照","visible_scope":"半身","frame_logic":"自然取景"}',
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false,"subject":"镜前的我","subject_kind":"character","scene":"洗手间","composition":"半身生活照","visible_scope":"半身","frame_logic":"自然取景"}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self, umo=""):
                raise AssertionError("图片导演不应再读取人设外貌")

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        first = await runtime._direct_life_image_payload(event, "拍张窗边照")
        second = await runtime._direct_life_image_payload(event, "拍张镜前照")

        self.assertIn("窗边的我", first.prompt)
        self.assertIn("镜前的我", second.prompt)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("图片导演裁定", provider.prompts[0])
        self.assertIn("图片导演裁定", provider.prompts[1])
        self.assertTrue(
            all("角色视觉设定" not in prompt for prompt in provider.prompts)
        )

    async def test_life_image_director_does_not_treat_generic_person_as_character(self):
        provider = Provider(
            [
                '{"identity_route":"独立主体","contains_character":true,"needs_character_reference":true}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime._direct_life_image_payload(
            event,
            "20多岁亚洲女性夏日写真，完整服装细节，竖版全身构图",
            judge_only=True,
        )

        self.assertEqual(result.identity_route, "独立主体")
        self.assertFalse(result.contains_character)
        self.assertFalse(result.needs_character_reference)

    async def test_life_image_director_keeps_independent_subject_without_persona(self):
        provider = Provider(
            [
                (
                    '{"identity_route":"独立主体","contains_character":false,"needs_character_reference":false,'
                    '"subject":"20多岁亚洲女性写真","subject_kind":"person","scene":"海边露台","composition":"竖版全身构图",'
                    '"frame_logic":"完整展示人物穿搭和背景"}'
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self, umo=""):
                raise AssertionError("独立人物路线不应读取角色人设")

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime._direct_life_image_payload(
            event,
            "20多岁亚洲女性夏日写真，完整服装细节，竖版全身构图",
        )

        self.assertEqual(result.identity_route, "独立主体")
        self.assertFalse(result.contains_character)
        self.assertFalse(result.needs_character_reference)
        self.assertIn("20多岁亚洲女性写真", result.prompt)
        self.assertNotIn("画面主体是当前角色本人", result.prompt)
        self.assertEqual(len(provider.prompts), 1)
        self.assertNotIn("角色视觉设定", provider.prompts[0])
        self.assertIn("图片导演裁定", provider.prompts[0])

    async def test_life_image_director_rejects_person_subject_when_route_has_no_people(
        self,
    ):
        provider = Provider(
            [
                (
                    '{"identity_route":"无人物","contains_character":false,"needs_character_reference":false,'
                    '"subject":"站在窗边的人","subject_kind":"person","scene":"雨夜客厅",'
                    '"composition":"半身生活照","frame_logic":"人物站在窗边入镜"}'
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        with self.assertRaises(MediaPromptExtractionError) as caught:
            await runtime._direct_life_image_payload(event, "拍一张无人的雨夜客厅")

        self.assertIn("无人物", str(caught.exception))
        self.assertIn("人物主体", str(caught.exception))

    async def test_life_image_generate_keeps_generic_full_prompt_on_text_route(self):
        provider = Provider(
            [
                '{"identity_route":"独立主体","contains_character":true,"needs_character_reference":true}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        generate_calls = []
        edit_calls = []

        class ImageService:
            def first_character_reference_image(self):
                return "D:/ref/role.png"

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("life.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path("role.png"))

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "20多岁亚洲女性夏日写真，完整服装细节，竖版全身构图。海边露台，阳光明亮，生活化拍摄。"

        result = await runtime.life_image_generate(event, "备用提示词", resolution="4k")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [(event.message_str, {"resolution": "4K"})])
        self.assertEqual(edit_calls, [])

    async def test_life_image_generate_direct_prompt_only_uses_director_judgement(self):
        provider = Provider(
            [
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        image_prompts = []
        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = (
            "1girl, full body portrait, vertical composition, looking at camera"
        )

        result = await runtime.life_image_generate(event, "备用提示词")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, [(event.message_str, {})])
        prompt = provider.prompts[0]
        self.assertIn("保持原文直出", prompt)
        self.assertIn("只返回路线判断", prompt)
        self.assertIn('"identity_route"', prompt)
        self.assertNotIn('"subject"', prompt)
        self.assertNotIn('"scene"', prompt)

    async def test_life_image_generate_current_character_request_uses_agent_person_prompt_and_reference(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        generate_calls = []
        edit_calls = []

        async def direct_image(*args, **kwargs):
            raise AssertionError(
                "current-character image requests should not ask the director again"
            )

        class ImageService:
            def can_edit_image(self):
                return True

            def first_character_reference_image(self):
                return "D:/ref/role.png"

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("scene.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path("role.png"))

        runtime._direct_life_image_payload = direct_image
        runtime._character_appearance_profile = lambda event: async_return(
            "成年女性，整体纤细匀称，上半身曲线自然丰满"
        )
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "\u62cd\u5f20\u7167\u770b\u770b\u4f60"
        prompt = (
            "\u4e00\u5f20\u5c45\u5bb6\u751f\u6d3b\u7167\u3002\u4e00\u4f4d18\u5c81"
            "\u6e05\u723d\u53ef\u7231\u7684\u4e2d\u56fd\u5973\u5b69\uff0c"
            "\u9ed1\u8272\u957f\u53d1\u624e\u7740\u9ad8\u9a6c\u5c3e\uff0c"
            "\u7a7f\u7740\u6d45\u84dd\u8272\u5bbd\u677e\u68c9\u8d28\u77ed\u8896\u4e0a\u8863\uff0c"
            "\u5750\u5728\u5ba2\u5385\u6c99\u53d1\u4e0a\u6367\u7740\u4e00\u7897\u7eff\u8c46\u6c99\u5fae\u7b11\u3002"
        )

        result = await runtime.life_image_generate(
            event, prompt, subject_route="current_character"
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [])
        self.assertEqual(len(edit_calls), 1)
        generated_prompt, reference_image, kwargs = edit_calls[0]
        self.assertEqual(reference_image, "D:/ref/role.png")
        self.assertEqual(
            kwargs,
            {
                "preserve_reference_ratio": False,
                "identity_profile": "成年女性，整体纤细匀称，上半身曲线自然丰满",
            },
        )
        self.assertEqual(generated_prompt, prompt)

    async def test_life_image_generate_current_character_request_keeps_agent_prompt_without_reference(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        generate_calls = []

        async def direct_image(*args, **kwargs):
            raise AssertionError(
                "current-character image requests should keep the trusted agent prompt"
            )

        class ImageService:
            def can_edit_image(self):
                return False

            def first_character_reference_image(self):
                return ""

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("life.png"))

        runtime._direct_life_image_payload = direct_image
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "\u62cd\u5f20\u7167\u770b\u770b\u4f60"
        prompt = (
            "\u4e00\u5f20\u751f\u6d3b\u7167\uff0c\u5e74\u8f7b\u5973\u5b69"
            "\u7ad9\u5728\u7a97\u8fb9\uff0c\u9ed1\u8272\u957f\u53d1\uff0c"
            "\u7a7f\u6d45\u8272\u5bb6\u5c45\u4e0a\u8863\uff0c\u81ea\u7136\u5149\uff0c"
            "\u534a\u8eab\u6784\u56fe\uff0c\u771f\u5b9e\u751f\u6d3b\u6293\u62cd\u611f\u3002"
        )

        result = await runtime.life_image_generate(
            event, prompt, subject_route="current_character"
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [(prompt, {})])

    async def test_life_image_generate_current_character_locks_current_appearance(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        generate_calls = []
        runtime._current_life_appearance_snapshot = lambda route: async_return(
            "当前穿搭：浅杏色吊带搭配白色高腰短裤和米白厚底凉鞋\n"
            "当前发型细节：黑色长发自然披肩"
        )
        align_calls = []

        async def align_scene(prompt, source_request, route, *, final_snapshot=False):
            align_calls.append((prompt, source_request, route, final_snapshot))
            return "公园长椅上的自然生活照"

        runtime._align_current_appearance_scene_prompt = align_scene

        class ImageService:
            def can_edit_image(self):
                return False

            def first_character_reference_image(self):
                return ""

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("life.png"))

        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "拍张现在的照片"

        result = await runtime.life_image_generate(
            event,
            "公园长椅上的生活照，穿浅蓝色衬衫和帆布鞋",
            subject_route="current_character",
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        generated_prompt = generate_calls[0][0]
        self.assertIn("当前生活状态权威造型快照", generated_prompt)
        self.assertIn("浅杏色吊带搭配白色高腰短裤", generated_prompt)
        self.assertNotIn("浅蓝色衬衫和帆布鞋", generated_prompt)
        self.assertIn("用户当前原始请求：拍张现在的照片", generated_prompt)
        self.assertIn("工具整理后的画面提示词本身不能作为换装证据", generated_prompt)
        self.assertEqual(
            align_calls,
            [
                (
                    "公园长椅上的生活照，穿浅蓝色衬衫和帆布鞋",
                    "拍张现在的照片",
                    "current_character",
                    False,
                )
            ],
        )

    async def test_final_appearance_snapshot_also_locks_group_character(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        prompt = runtime._apply_current_appearance_snapshot(
            "人物 A 与人物 B 在老街合影",
            "当前穿搭：白色方领短袖搭配浅蓝色牛仔短裤\n当前发型名称：高马尾",
            "group",
            source_request="重新换一套再合影",
            final_snapshot=True,
        )

        self.assertIn("人物 A造型（来自当前生活状态）", prompt)
        self.assertIn("本轮真实换装已经保存后的唯一最终造型", prompt)
        self.assertNotIn("重新换一套再合影", prompt)
        self.assertNotIn("只有用户当前原始请求明确要求", prompt)

    async def test_current_appearance_alignment_keeps_only_user_requested_changes(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        prompts = []

        async def rewrite(prompt, *, provider_id=""):
            prompts.append((prompt, provider_id))
            return json.dumps(
                {"prompt": "公园长椅上的自然生活照，傍晚逆光，半身构图"},
                ensure_ascii=False,
            )

        runtime._media_director_text_call = rewrite

        result = await runtime._align_current_appearance_scene_prompt(
            "公园长椅上的生活照，身穿另一套浅蓝色衬衫",
            "拍张现在的照片",
            "current_character",
        )

        self.assertEqual(result, "公园长椅上的自然生活照，傍晚逆光，半身构图")
        self.assertEqual(len(prompts), 1)
        self.assertIn("用户原始请求：拍张现在的照片", prompts[0][0])
        self.assertIn("工具生成的画面提示", prompts[0][0])

    async def test_current_appearance_alignment_falls_back_to_user_request(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})

        async def rewrite(*args, **kwargs):
            raise RuntimeError("provider unavailable")

        runtime._media_director_text_call = rewrite

        result = await runtime._align_current_appearance_scene_prompt(
            "窗边自拍，身穿工具自行补写的另一套衣服",
            "拍张窗边的照片",
            "current_character",
        )

        self.assertEqual(result, "拍张窗边的照片")
        self.assertNotIn("另一套衣服", result)

    async def test_life_image_generate_group_uses_structured_friend_profile(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        group_calls = []

        async def direct_image(*args, **kwargs):
            raise AssertionError(
                "group image requests should not ask the director again"
            )

        class ImageService:
            def can_edit_image(self):
                return True

            def first_character_reference_image(self):
                return "D:/ref/role.png"

            def friend_reference_options(self):
                return [{"profile_id": "profile:friend", "display_name": "示例好友"}]

            async def generate_group_image(
                self, prompt, participant_profile_ids, aspect_ratio=""
            ):
                group_calls.append((prompt, participant_profile_ids, aspect_ratio))
                return types.SimpleNamespace(path=Path("group.png"))

        runtime._direct_life_image_payload = direct_image
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "拍张你和示例好友的合影"
        prompt = "当前角色在左，示例好友在右，在书店窗边自然自拍"

        result = await runtime.life_image_generate(
            event,
            prompt,
            subject_route="group",
            participants=["profile:friend"],
            friend_outfit="米白色针织上衣搭配浅蓝色直筒牛仔裤和白色帆布鞋",
            friend_hair="黑色短发自然垂落，额前留轻薄碎发",
            friend_scene_category="public",
            friend_style_pool="outfit_styles",
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(len(group_calls), 1)
        group_prompt, participant_ids, aspect_ratio = group_calls[0]
        self.assertEqual(participant_ids, ["profile:friend"])
        self.assertEqual(aspect_ratio, "")
        self.assertTrue(group_prompt.startswith(prompt))
        self.assertIn("人物 B 本轮结构化造型（最高优先级）", group_prompt)
        self.assertIn("参考图造型与上述内容冲突时", group_prompt)
        context = runtime.friend_reference_injection_context()
        self.assertIn("示例好友：profile:friend", context)
        self.assertIn("subject_route 填 group", context)
        self.assertIn("当前角色写为人物 A、好友写为人物 B", context)
        self.assertIn("不要用一套未标注归属的穿搭描述两个人", context)
        self.assertIn("不要根据姓名或昵称猜测性别", context)

    async def test_group_image_requires_structured_friend_look_without_daily_state(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        group_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_group_image=lambda *args, **kwargs: (
                    group_calls.append((args, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("group.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(
            event,
            "人物 B 穿深灰色家居服，在客厅和人物 A 合影",
            subject_route="group",
            participants=["profile:friend"],
        )

        payload = json.loads(result)
        self.assertEqual(payload["status"], "needs_parameters")
        self.assertEqual(
            payload["required_parameters"],
            [
                "friend_scene_category",
                "friend_outfit",
                "friend_hair",
            ],
        )
        self.assertEqual(group_calls, [])
        self.assertFalse(
            runtime._current_friend_daily_look(
                event.unified_msg_origin, "profile:friend"
            )
        )

        partial_result = await runtime.life_image_generate(
            event,
            "客厅合影",
            subject_route="group",
            participants=["profile:friend"],
            friend_outfit="深灰色家居短袖搭配深色休闲长裤",
            friend_scene_category="home",
        )
        self.assertEqual(
            json.loads(partial_result)["required_parameters"],
            ["friend_hair"],
        )
        self.assertEqual(group_calls, [])

    async def test_group_image_reuses_friend_daily_look(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        root = Path(tempfile.mkdtemp())
        runtime.data_path = root / "daily_life.db"
        group_calls = []
        image_path = root / "group.png"
        image_path.write_bytes(b"x" * 2048)

        class ImageService:
            async def generate_group_image(
                self, prompt, participant_profile_ids, aspect_ratio=""
            ):
                group_calls.append(prompt)
                return types.SimpleNamespace(path=image_path)

        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        outfit = "深蓝色短袖衬衫搭配浅灰直筒长裤和白色休闲鞋"
        hair = "黑色短发自然梳理，额前保留轻薄碎发"

        first = await runtime.life_image_generate(
            event,
            "傍晚书店窗边合影",
            subject_route="group",
            participants=["profile:friend"],
            friend_outfit=outfit,
            friend_hair=hair,
            friend_scene_category="public",
            friend_style_pool="outfit_styles",
        )
        second = await runtime.life_image_generate(
            event,
            "走到书店另一排书架再拍一张",
            subject_route="group",
            participants=["profile:friend"],
            friend_scene_category="public",
        )
        changed_outfit = "浅灰色连帽卫衣搭配深蓝色运动长裤和白色运动鞋"
        third = await runtime.life_image_generate(
            event,
            "回到公园入口再拍一张",
            subject_route="group",
            participants=["profile:friend"],
            friend_outfit=changed_outfit,
            friend_scene_category="outdoor",
            friend_style_pool="outfit_styles",
            friend_outfit_decision="outdoor",
        )

        self.assertEqual(json.loads(first)["status"], "sent")
        self.assertEqual(json.loads(second)["status"], "sent")
        self.assertEqual(json.loads(third)["status"], "sent")
        self.assertEqual(len(group_calls), 3)
        self.assertTrue(all(outfit in prompt for prompt in group_calls[:2]))
        self.assertTrue(all(hair in prompt for prompt in group_calls))
        self.assertIn(changed_outfit, group_calls[2])
        current = runtime._current_friend_daily_look(
            event.unified_msg_origin, "profile:friend"
        )
        self.assertEqual(current["outfit"], changed_outfit)
        self.assertEqual(current["hair"], hair)
        self.assertTrue((root / "friend_looks.json").is_file())
        self.assertFalse(
            runtime._current_friend_daily_look(
                event.unified_msg_origin, "profile:another"
            )
        )

    async def test_friend_look_changes_homewear_before_outdoor_scene(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        root = Path(tempfile.mkdtemp())
        runtime.data_path = root / "daily_life.db"
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        home_look, source, missing = await runtime._prepare_friend_daily_look(
            event,
            "profile:friend",
            outfit="柔软家居上衣搭配棉质长裤和居家拖鞋",
            hair="黑色短发自然垂落",
            scene="夜晚客厅合影",
            scene_category="home",
            style_pool="sleep_styles",
            decision="change",
        )
        self.assertEqual(source, "本轮新建")
        self.assertEqual(missing, [])
        await runtime._remember_friend_daily_look(
            event.unified_msg_origin, "profile:friend", home_look
        )

        _outdoor_look, source, missing = await runtime._prepare_friend_daily_look(
            event,
            "profile:friend",
            scene="第二天在公园入口合影",
            scene_category="outdoor",
        )
        self.assertEqual(source, "场景需要更新")
        self.assertEqual(missing, ["friend_outfit"])

        _relabelled, source, missing = await runtime._prepare_friend_daily_look(
            event,
            "profile:friend",
            scene="第二天在公园入口合影",
            scene_category="outdoor",
            style_pool="outfit_styles",
        )
        self.assertEqual(source, "场景需要更新")
        self.assertEqual(missing, ["friend_outfit"])

        outdoor_look, source, missing = await runtime._prepare_friend_daily_look(
            event,
            "profile:friend",
            outfit="浅绿色衬衫搭配白色长裤和帆布鞋",
            scene="第二天在公园入口合影",
            scene_category="outdoor",
            style_pool="outfit_styles",
            decision="outdoor",
        )
        self.assertEqual(source, "本轮更新")
        self.assertEqual(missing, [])
        self.assertEqual(outdoor_look["hair"], home_look["hair"])
        await runtime._remember_friend_daily_look(
            event.unified_msg_origin, "profile:friend", outdoor_look
        )

        returned_home, source, missing = await runtime._prepare_friend_daily_look(
            event,
            "profile:friend",
            scene="回到客厅后继续合影",
            scene_category="home",
        )
        self.assertEqual(source, "场景沿用")
        self.assertEqual(missing, [])
        self.assertEqual(returned_home["outfit"], outdoor_look["outfit"])
        self.assertEqual(returned_home["style_pool"], "outfit_styles")
        self.assertEqual(returned_home["decision"], "keep")

    async def test_legacy_friend_look_is_rechecked_before_public_scene(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        runtime._life_friend_daily_looks = {
            runtime._friend_daily_look_key(
                event.unified_msg_origin, "profile:friend"
            ): {
                "date": time.strftime("%Y-%m-%d"),
                "outfit": "旧记录中的一套穿搭",
                "hair": "自然短发",
            }
        }
        runtime._life_friend_daily_looks_loaded = True

        _look, source, missing = await runtime._prepare_friend_daily_look(
            event,
            "profile:friend",
            scene="商场里合影",
            scene_category="public",
        )

        self.assertEqual(source, "场景需要更新")
        self.assertEqual(missing, ["friend_outfit"])

    async def test_group_image_failed_delivery_does_not_persist_friend_look(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        root = Path(tempfile.mkdtemp())
        runtime.data_path = root / "daily_life.db"
        image_path = root / "group.png"
        image_path.write_bytes(b"x" * 2048)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_group_image=lambda *args, **kwargs: async_return(
                    types.SimpleNamespace(path=image_path)
                )
            )
        )
        runtime.send_message_if_not_recalled = lambda *args, **kwargs: async_return(
            False
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(
            event,
            "雨后街边合影",
            subject_route="group",
            participants=["profile:friend"],
            friend_outfit="浅绿色衬衫搭配白色长裤和帆布鞋",
            friend_hair="自然黑色短发",
            friend_scene_category="outdoor",
            friend_style_pool="outfit_styles",
        )

        self.assertEqual(result, "原消息已撤回，已取消图片发送。")
        self.assertFalse(
            runtime._current_friend_daily_look(
                event.unified_msg_origin, "profile:friend"
            )
        )
        self.assertFalse((root / "friend_looks.json").exists())

    async def test_life_image_generate_uses_reference_when_extraction_marks_current_character(
        self,
    ):
        provider = Provider(
            [
                (
                    '{"identity_route":"不确定","contains_character":false,"needs_character_reference":false,'
                    '"subject":"被窝里的我和旁边的人","subject_kind":"character",'
                    '"scene":"深夜卧室床上","composition":"近景合影生活照",'
                    '"visible_scope":"半身","frame_logic":"手机近景拍到当前角色和身旁的人",'
                    '"lighting":"手机暖光"}'
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        generate_calls = []
        edit_calls = []

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        class ImageService:
            def can_edit_image(self):
                return True

            def first_character_reference_image(self):
                return "D:/ref/role.png"

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("scene.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path("role.png"))

        runtime.composer = Composer()
        runtime._character_appearance_profile = lambda event: async_return(
            "成年女性，整体纤细匀称，上半身曲线自然丰满"
        )
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "咱们合影照还没拍呢，拍张呗"
        prompt = (
            "深夜卧室关灯后，手机屏幕的暖色微光照亮被窝里并排躺着的两个人，"
            "女生穿着宽松睡衣用手捂住脸，旁边的男生只露出一侧肩膀和头发，"
            "搞怪而温馨的睡前生活照，镜头带有一点手抖的糊感和颗粒感"
        )

        result = await runtime.life_image_generate(event, prompt)

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [])
        self.assertEqual(len(edit_calls), 1)
        edited_prompt, reference_image, kwargs = edit_calls[0]
        self.assertIn("被窝里的我和旁边的人", edited_prompt)
        self.assertEqual(reference_image, "D:/ref/role.png")
        self.assertEqual(
            kwargs,
            {
                "preserve_reference_ratio": False,
                "identity_profile": "成年女性，整体纤细匀称，上半身曲线自然丰满",
            },
        )
        self.assertIn(prompt, provider.prompts[0])

    async def test_life_image_generate_falls_back_when_director_returns_empty_payload(
        self,
    ):
        provider = Provider(["{}"])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

        image_prompts = []
        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: (
                    image_prompts.append(prompt)
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜沙发上随手拍")

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(image_prompts, ["雨夜沙发上随手拍"])

    async def test_life_image_director_preserves_original_request_and_provider(self):
        provider = Provider(
            [
                '{"identity_route":"独立主体","contains_character":false,"needs_character_reference":false,'
                '"subject":"窗边人物","subject_kind":"person","scene":"测试市的窗边","composition":"竖版全身构图",'
                '"frame_logic":"完整展示人物和环境","continuity_constraints":"保持人物与场景关系"}'
            ],
            provider_id="image-director-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        default_provider = Provider([])
        runtime.context = Context(
            default_provider, providers={"image-director-model": provider}
        )
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "image_director_provider": "image-director-model"
                }
            }
        )
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                response = await provider.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(response, "completion_text", "")

        runtime.composer = Composer()
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    image_prompts.append(prompt)
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(
            event, "测试市窗边，红色外套，9:16竖版全身，保留手里的书"
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(default_provider.prompts, [])
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("用户明确要求（最高优先级）", image_prompts[0])
        self.assertIn("红色外套", image_prompts[0])
        self.assertIn("continuity_constraints", provider.prompts[0])
        self.assertEqual(provider.provider_id, "image-director-model")

    async def test_life_image_reverse_prompt_uses_current_message_image(self):
        vision_provider = Provider(
            [
                (
                    '{"title":"窗边热茶",'
                    '"prompt":"写实生活照，窗边桌面上有一杯热茶，暖色台灯，浅景深，手机随手拍质感",'
                    '"keywords":["热茶","窗边","暖色台灯","浅景深"],'
                    '"ratio":"4:3","usage":"文生图",'
                    '"analysis":{'
                    '"subject":{"main":"桌面热茶","attributes":["白瓷杯"]},'
                    '"environment":{"scene_type":"窗边桌面","background":"室内"},'
                    '"composition":{"shot_type":"近景","subject_placement":"画面中央"},'
                    '"lighting":{"source":"暖色台灯","quality":"柔和"},'
                    '"visible_text":{"content":"","excluded":["平台水印"]}}}'
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "image", "url": "https://example.com/tea.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(
            event,
            source_prompt="保留窗边暖光和随手拍质感",
            profile="生活照",
        )

        self.assertIn("标题：", result)
        self.assertIn("窗边热茶", result)
        self.assertIn("图片反推提示词：", result)
        self.assertIn("窗边桌面上有一杯热茶", result)
        self.assertIn("关键词：热茶、窗边、暖色台灯、浅景深", result)
        self.assertIn("比例：4:3", result)
        self.assertIn("适合：文生图", result)
        self.assertIn("画面拆解：", result)
        self.assertIn("主体：桌面热茶", result)
        self.assertIn("特征白瓷杯", result)
        self.assertIn("光线：来源暖色台灯，性质柔和", result)
        self.assertIn("可见文字：排除项平台水印", result)
        self.assertEqual(
            vision_provider.vision_prompts[0]["image"], "https://example.com/tea.png"
        )
        self.assertIn(
            "可复制、可直接用于生图的中文完整提示词",
            vision_provider.vision_prompts[0]["prompt"],
        )
        self.assertNotIn("260 到 420 字", vision_provider.vision_prompts[0]["prompt"])
        self.assertNotIn("详细程度：", vision_provider.vision_prompts[0]["prompt"])
        self.assertIn(
            "参考重点：保留窗边暖光和随手拍质感",
            vision_provider.vision_prompts[0]["prompt"],
        )
        self.assertIn("反推方案：生活照", vision_provider.vision_prompts[0]["prompt"])
        self.assertIn(
            "方案取舍：重点保留真实生活感", vision_provider.vision_prompts[0]["prompt"]
        )
        self.assertIn("无法确认的身份", vision_provider.vision_prompts[0]["prompt"])
        self.assertIn(
            "水印、平台界面、字幕", vision_provider.vision_prompts[0]["prompt"]
        )
        self.assertLess(
            vision_provider.vision_prompts[0]["prompt"].index("分析维度"),
            vision_provider.vision_prompts[0]["prompt"].index("【反推参考】"),
        )
        self.assertEqual(
            runtime._last_reverse_prompt_for_scope(event),
            "写实生活照，窗边桌面上有一杯热茶，暖色台灯，浅景深，手机随手拍质感",
        )
        self.assertEqual(
            runtime._last_reverse_reference_for_scope(event),
            "https://example.com/tea.png",
        )

    async def test_reverse_prompt_falls_back_to_current_default_provider(self):
        primary = Provider(
            [RuntimeError("测试指定视觉模型不可用")], provider_id="vision-model"
        )
        fallback = Provider(
            [
                json.dumps(
                    {
                        "title": "默认模型结果",
                        "prompt": "测试市窗边人物，柔和自然光，生活照质感",
                        "keywords": ["窗边", "自然光"],
                        "usage": "文生图",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="default-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(fallback, providers={"vision-model": primary})
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )
        provider_requests = []
        closed_sessions = []

        class Composer:
            async def _get_provider(self, provider_id=""):
                provider_requests.append(provider_id)
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                closed_sessions.append(session_id)

        runtime.composer = Composer()

        payload = await runtime._reverse_prompt_call_vision(
            "https://example.test/reference.png"
        )

        self.assertEqual(payload["title"], "默认模型结果")
        self.assertEqual(provider_requests, ["vision-model", ""])
        self.assertEqual(len(primary.vision_prompts), 1)
        self.assertEqual(len(fallback.vision_prompts), 1)
        self.assertEqual(len(closed_sessions), 2)
        self.assertTrue(closed_sessions[1].endswith("_fallback"))

    async def test_life_image_reverse_prompt_reuses_same_image_and_request(self):
        reverse_prompt = "窗边人物生活照，柔和自然光，手机随手拍质感"
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "title": "窗边生活照",
                        "prompt": reverse_prompt,
                        "keywords": ["窗边", "自然光", "生活照"],
                        "usage": "文生图",
                        "analysis": {
                            "style": {"genre": "生活照", "mood": "松弛"},
                            "camera": {"device_look": "手机随手拍"},
                        },
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.archive = LifeArchive(runtime.data_path)
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace())

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        source = Path(tempfile.mkdtemp()) / "portrait.jpg"
        source.write_bytes(b"\xff\xd8\xffsame-reverse-image")
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "image", "file": str(source)}]
        event.message_obj.message = event.message_items

        first = await runtime.life_image_reverse_prompt(
            event, source_prompt="保留自然光", profile="生活照"
        )
        second = await runtime.life_image_reverse_prompt(
            event, source_prompt="保留自然光", profile="生活照"
        )

        self.assertIn(reverse_prompt, first)
        self.assertEqual(second, first)
        self.assertEqual(len(vision_provider.vision_prompts), 1)
        row_count = runtime.archive._conn.execute(
            "SELECT COUNT(*) FROM reverse_prompts"
        ).fetchone()[0]
        self.assertEqual(row_count, 1)
        runtime.archive.close()

    async def test_life_image_reverse_prompt_uses_quoted_image(self):
        vision_provider = Provider(
            ["雨夜街道，霓虹灯反射在湿润路面，电影感构图"], provider_id="vision-model"
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            {
                "type": "reply",
                "data": {
                    "message": [
                        {"type": "image", "url": "https://example.com/rain.png"}
                    ]
                },
            }
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event)

        self.assertIn("雨夜街道", result)
        self.assertEqual(
            vision_provider.vision_prompts[0]["image"], "https://example.com/rain.png"
        )

    async def test_life_image_reverse_prompt_caches_quoted_local_image_for_followup_generation(
        self,
    ):
        reverse_prompt = "雨夜街道，霓虹灯反射在湿润路面，电影感构图"
        vision_provider = Provider(
            [json.dumps({"prompt": reverse_prompt}, ensure_ascii=False)],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.archive = LifeArchive(runtime.data_path)
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace())
        self._stub_media_director(runtime)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        source = Path(tempfile.mkdtemp()) / "compressed_quote.jpg"
        source.write_bytes(b"\xff\xd8\xffquoted-image")
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            {
                "type": "reply",
                "data": {"message": [{"type": "image", "file": str(source)}]},
            }
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event)
        cached_reference = runtime._last_reverse_reference_for_scope(event)
        source.unlink()

        self.assertIn(reverse_prompt, result)
        self.assertEqual(vision_provider.vision_prompts[0]["image"], str(source))
        self.assertNotEqual(cached_reference, str(source))
        self.assertTrue(Path(cached_reference).is_file())
        self.assertEqual(
            Path(cached_reference).parent, runtime.data_path.parent / "reverse"
        )
        cached_record = await runtime.archive.get_latest_reverse_prompt(
            event.unified_msg_origin
        )
        self.assertIsNotNone(cached_record)
        self.assertEqual(cached_record.prompt, reverse_prompt)
        self.assertEqual(cached_record.image_path, cached_reference)

        image_prompts = []

        class ImageService:
            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                image_prompts.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("life.png"))

            async def edit_image(self, prompt, reference_image, **kwargs):
                raise AssertionError(
                    "should not auto use cached reverse reference image"
                )

        runtime.media = types.SimpleNamespace(image=ImageService())

        generate_result = await runtime.life_image_generate(
            event, "", use_last_reverse_prompt=True
        )

        self.assertEqual(json.loads(generate_result)["status"], "sent")
        self.assertEqual(image_prompts, [(reverse_prompt, {})])
        runtime.archive.close()

    async def test_life_image_reverse_prompt_keeps_full_long_prompt(self):
        long_prompt = (
            "超详细人像反推，"
            + "主体、服装、光线、构图、材质、空间层次全部保留，" * 260
            + "结尾完整保留。"
        )
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "prompt": long_prompt,
                        "keywords": ["长提示词"],
                        "usage": "图生图参考",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "image", "url": "https://example.com/long.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event)

        self.assertIn(long_prompt, result)
        self.assertIn("结尾完整保留。", result)
        self.assertEqual(runtime._last_reverse_prompt_for_scope(event), long_prompt)

    async def test_life_image_reverse_prompt_uses_standard_text_chat_image_urls(self):
        class TextVisionProvider(Provider):
            async def image_chat(self, *args, **kwargs):
                raise AttributeError("image_chat unavailable")

            async def text_chat(
                self,
                prompt,
                session_id=None,
                system_prompt=None,
                image_urls=None,
                **kwargs,
            ):
                self.vision_prompts.append(
                    {
                        "prompt": prompt,
                        "image_urls": list(image_urls or []),
                        "session_id": session_id,
                    }
                )
                return await super().text_chat(
                    prompt, session_id=session_id, system_prompt=system_prompt, **kwargs
                )

        vision_provider = TextVisionProvider(
            [
                '{"prompt":"窗边人像，柔和自然光，真实生活照质感","keywords":["人像","窗边","自然光"],"usage":"文生图"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            {"type": "image", "url": "https://example.com/portrait.png"}
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event, profile="人像")

        self.assertIn("窗边人像", result)
        self.assertEqual(
            vision_provider.vision_prompts[0]["image_urls"],
            ["https://example.com/portrait.png"],
        )

    async def test_life_image_reverse_prompt_parses_json_code_block_with_curly_quotes(
        self,
    ):
        vision_provider = Provider(
            [
                """```json
{
  “title”: “雨夜街景”,
  “prompt”: “雨夜街道生活照，湿润路面反射霓虹灯光，行人撑伞经过，低角度街拍构图，电影感色彩和真实颗粒。”,
  “keywords”: “雨夜, 街道, 霓虹, 雨伞”,
  “ratio”: “16:9”,
  “usage”: “文生图”
}
```"""
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            {"type": "image", "url": "https://example.com/street.png"}
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event, profile="人像")

        self.assertIn("标题：", result)
        self.assertIn("雨夜街景", result)
        self.assertIn("雨夜街道生活照", result)
        self.assertIn("关键词：雨夜、街道、霓虹、雨伞", result)
        self.assertIn("比例：16:9", result)
        self.assertIn("适合：文生图", result)
        self.assertIn(
            "方案取舍：重点保留人物外观",
            vision_provider.vision_prompts[0]["prompt"],
        )

    async def test_life_image_reverse_prompt_requires_image(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.composer = types.SimpleNamespace(
            _get_provider=lambda provider_id="": async_return(
                runtime.context.get_using_provider()
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_reverse_prompt(event)

        self.assertEqual(result, "没有找到可反推的图片。")

    def test_photo_suite_count_and_plan_protocol(self):
        self.assertEqual(DailyLifeRuntime._photo_suite_count(None), 3)
        self.assertEqual(DailyLifeRuntime._photo_suite_count(1), 2)
        self.assertEqual(DailyLifeRuntime._photo_suite_count(4), 4)
        self.assertEqual(DailyLifeRuntime._photo_suite_count(20), 6)
        self.assertEqual(
            DailyLifeRuntime._photo_suite_parse_plan(
                '{"shots":[{"title":"全景","prompt":"完整全景"},'
                '{"title":"近景","prompt":"完整近景"}]}',
                2,
            ),
            [
                {"title": "全景", "prompt": "完整全景"},
                {"title": "近景", "prompt": "完整近景"},
            ],
        )
        self.assertEqual(
            DailyLifeRuntime._photo_suite_parse_plan(
                '{"shots":[{"title":"全景","prompt":"完整全景"}]}', 2
            ),
            [],
        )
        person_fallback = DailyLifeRuntime._photo_suite_fallback_plan(
            "雨后公园散步", 6, "current_character"
        )
        object_fallback = DailyLifeRuntime._photo_suite_fallback_plan(
            "窗边的一杯咖啡", 3, "object"
        )
        group_fallback = DailyLifeRuntime._photo_suite_fallback_plan(
            "深夜客厅合影，浅灰棉质睡裙", 3, "group"
        )
        self.assertEqual(len(person_fallback), 6)
        self.assertEqual(len({item["title"] for item in person_fallback}), 6)
        self.assertEqual(len(object_fallback), 3)
        self.assertTrue(
            all("不要拼图或多宫格" in item["prompt"] for item in person_fallback)
        )
        self.assertTrue(
            all(
                f"第 {index} 个明确的镜头、机位、姿势或动作变化" in item["prompt"]
                for index, item in enumerate(person_fallback, start=1)
            )
        )
        self.assertTrue(
            all("相同人物身份" in item["prompt"] for item in person_fallback)
        )
        self.assertTrue(
            all("相同主体外观" in item["prompt"] for item in object_fallback)
        )
        self.assertTrue(
            all(
                "未标注归属的单套服装、发型或体态默认只应用于人物 A" in item["prompt"]
                for item in group_fallback
            )
        )
        self.assertTrue(
            all(
                "人物参考图只用于确认各自身份和稳定外观" in item["prompt"]
                and "未明确时再选择符合当前场景的独立穿搭" in item["prompt"]
                for item in group_fallback
            )
        )
        self.assertTrue(
            all(
                "明确要求同款、情侣装或统一造型" in item["prompt"]
                for item in group_fallback
            )
        )
        self.assertEqual(
            person_fallback,
            DailyLifeRuntime._photo_suite_fallback_plan(
                "雨后公园散步", 6, "current_character"
            ),
        )
        self.assertNotEqual(
            [item["title"] for item in person_fallback[:3]],
            [item["title"] for item in object_fallback],
        )

    async def test_photo_suite_planner_uses_configured_timeout_and_logs_model(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": {
                    "image_director_provider": "image-director-model",
                    "photo_suite_planning_timeout_seconds": 45,
                }
            }
        )
        provider = types.SimpleNamespace(
            model_name="grok-4.5",
            provider_config={"id": "default-chat", "model": "grok-4.5"},
        )
        requested_providers = []
        runtime.get_text_provider = lambda provider_id="": (
            requested_providers.append(provider_id) or async_return(provider)
        )

        async def call_text_model(*args, **kwargs):
            return '{"shots":[]}'

        runtime.call_text_model = call_text_model
        waits = []

        async def timeout(coro, *, timeout):
            waits.append(timeout)
            coro.close()
            raise TimeoutError

        logs = []
        with (
            patch("core.runtime.channel.suite.asyncio.wait_for", timeout),
            patch(
                "core.runtime.channel.suite.logger.debug",
                side_effect=lambda message: logs.append(message),
            ),
        ):
            planned = await runtime._photo_suite_plan(
                Event(),
                "床边、枕边与半身侧拍三种生活镜头",
                3,
                subject_route="current_character",
            )

        self.assertEqual(waits, [45])
        self.assertEqual(requested_providers, ["image-director-model"])
        self.assertEqual(
            planned,
            runtime._photo_suite_fallback_plan(
                "床边、枕边与半身侧拍三种生活镜头",
                3,
                "current_character",
            ),
        )
        combined = "\n".join(logs)
        self.assertIn("套图镜头规划开始", combined)
        self.assertIn("模型=default-chat/grok-4.5", combined)
        self.assertIn("上限=45秒", combined)
        self.assertIn("套图镜头规划超时", combined)
        self.assertIn("耗时=", combined)
        self.assertIn("使用本地镜头方案", combined)

    async def test_photo_suite_group_planner_separates_person_attributes(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        captured = []
        runtime.get_text_provider = lambda provider_id="": async_return(object())

        async def call_text_model(provider, prompt, session_id, **kwargs):
            captured.append(prompt)
            return (
                '{"shots":['
                '{"title":"并肩","prompt":"人物 A 与人物 B 并肩坐着"},'
                '{"title":"互动","prompt":"人物 A 与人物 B 自然聊天"}'
                "]}"
            )

        runtime.call_text_model = call_text_model

        planned = await runtime._photo_suite_plan(
            Event(),
            "深夜客厅合影，浅灰棉质睡裙",
            2,
            subject_route="group",
        )

        self.assertEqual(len(planned), 2)
        self.assertIn("人物 A 是当前角色，人物 B 是好友", captured[0])
        self.assertIn("默认只应用于人物 A", captured[0])
        self.assertIn("人物参考图只用于确认各自身份和稳定外观", captured[0])
        self.assertIn("本轮画面要求中分别绑定", captured[0])
        self.assertIn("只有用户明确要求同款、情侣装或统一造型", captured[0])

    async def test_photo_suite_group_requires_and_records_structured_friend_look(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        root = Path(tempfile.mkdtemp())
        runtime.data_path = root / "daily_life.db"
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        missing_result = await runtime.life_photo_suite_generate(
            event,
            "周末咖啡店双人合影套图",
            subject_route="group",
            participants=["friend-1"],
        )

        self.assertEqual(json.loads(missing_result)["status"], "needs_parameters")
        self.assertEqual(scheduled, [])

        outfit = "浅蓝色牛仔外套搭配白色长裙和米色平底鞋"
        hair = "黑色中长发自然披肩"
        result = await runtime.life_photo_suite_generate(
            event,
            "周末咖啡店双人合影套图",
            subject_route="group",
            participants=["friend-1"],
            friend_outfit=outfit,
            friend_hair=hair,
            friend_scene_category="public",
            friend_style_pool="outfit_styles",
        )

        self.assertEqual(json.loads(result)["status"], "pending")
        manifests = list(
            (root / "generated" / "images" / "suites").glob("*/manifest.json")
        )
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["friend_look"]["outfit"], outfit)
        self.assertEqual(manifest["friend_look"]["hair"], hair)
        self.assertEqual(manifest["friend_look"]["scene_category"], "public")
        self.assertEqual(manifest["friend_look"]["style_pool"], "outfit_styles")
        self.assertNotIn("结构化造型（最高优先级）", manifest["prompt"])
        self.assertFalse(
            runtime._current_friend_daily_look(event.unified_msg_origin, "friend-1")
        )
        scheduled[0][2].close()

    async def test_photo_suite_records_character_profile_once_and_retry_reuses_it(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        root = Path(tempfile.mkdtemp())
        runtime.data_path = root / "daily_life.db"
        profile_calls = []
        runtime._character_appearance_profile = lambda event: (
            profile_calls.append(event)
            or async_return("整体纤细匀称，上半身曲线自然丰满")
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append(coro) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_photo_suite_generate(
            event,
            "窗边生活套图",
            count=2,
            subject_route="current_character",
        )
        retry_result = await runtime.life_photo_suite_generate(event, retry_indexes=[1])

        self.assertEqual(json.loads(result)["status"], "pending")
        self.assertEqual(json.loads(retry_result)["status"], "pending")
        manifests = list(
            (root / "generated" / "images" / "suites").glob("*/manifest.json")
        )
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["identity_profiles"],
            {"current_character": "整体纤细匀称，上半身曲线自然丰满"},
        )
        self.assertEqual(len(profile_calls), 1)
        for coro in scheduled:
            coro.close()

    async def test_photo_suite_snapshots_current_appearance_for_new_character_suite(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        root = Path(tempfile.mkdtemp())
        runtime.data_path = root / "daily_life.db"
        runtime._current_life_appearance_snapshot = lambda route: async_return(
            "当前穿搭：白色短袖搭配深蓝色长裤\n当前发型名称：低马尾"
        )
        align_calls = []

        async def align_scene(prompt, source_request, route, *, final_snapshot=False):
            align_calls.append((prompt, source_request, route, final_snapshot))
            return "公园里不同角度的生活套图"

        runtime._align_current_appearance_scene_prompt = align_scene
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append(coro) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "拍三张现在的生活照"

        result = await runtime.life_photo_suite_generate(
            event,
            "公园里不同角度的生活套图，换成工具自行补写的红色连衣裙",
            count=3,
            subject_route="current_character",
        )

        self.assertEqual(json.loads(result)["status"], "pending")
        manifests = list(
            (root / "generated" / "images" / "suites").glob("*/manifest.json")
        )
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertIn("白色短袖搭配深蓝色长裤", manifest["current_appearance"])
        self.assertEqual(manifest["source_request"], "拍三张现在的生活照")
        self.assertNotIn("红色连衣裙", manifest["prompt"])
        self.assertEqual(len(align_calls), 1)
        for coro in scheduled:
            coro.close()

    async def test_photo_suite_background_preserves_successes_and_retries_one_slot(
        self,
    ):
        root = Path(tempfile.mkdtemp())
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = root / "daily_life.db"
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "给我拍一套雨后公园散步照"
        planner_payload = json.dumps(
            {
                "shots": [
                    {"title": f"镜头 {index}", "prompt": f"雨后公园镜头{index}"}
                    for index in range(1, 5)
                ]
            },
            ensure_ascii=False,
        )
        active = 0
        max_active = 0
        failed_second = True
        prompt_calls = {}
        generation_options = []

        async def generate_image(prompt, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            prompt_calls[prompt] = prompt_calls.get(prompt, 0) + 1
            generation_options.append(dict(kwargs))
            try:
                await asyncio.sleep(0.01)
                if prompt.endswith("镜头2") and failed_second:
                    raise RuntimeError("第二张暂时失败")
                path = root / f"source-{len(prompt_calls)}-{prompt[-1]}.png"
                path.write_bytes(b"\x89PNG\r\n\x1a\n" + prompt.encode("utf-8"))
                return types.SimpleNamespace(path=path)
            finally:
                active -= 1

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_image=generate_image)
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="", **kwargs: (
            scheduled.append((label, key, coro)) or True
        )

        async def get_provider(provider_id=""):
            return object()

        model_calls = []

        async def call_text_model(provider, prompt, session_id, **kwargs):
            model_calls.append(prompt)
            if "镜头规划器" in prompt:
                return planner_payload
            return '{"reply_text":"这组拍好啦。"}'

        async def get_persona_text(scope=""):
            return "说话自然简短"

        expressed = []

        async def send_background_text(scope, text, **kwargs):
            expressed.append((scope, text))
            return True

        async def append_history(scope, text):
            return True

        runtime.get_text_provider = get_provider
        runtime.call_text_model = call_text_model
        runtime.get_persona_text = get_persona_text
        runtime.send_background_text = send_background_text
        runtime._append_assistant_history = append_history

        reaction_calls = []

        async def set_suite_reaction(**kwargs):
            reaction_calls.append(kwargs)

        event.bot = types.SimpleNamespace(set_msg_emoji_like=set_suite_reaction)
        event.message_id = "7301"
        event.message_obj.message_id = 7301
        reaction_tool = types.SimpleNamespace(name="life_photo_suite_generate")
        await runtime.note_tool_reaction_start(event, reaction_tool, {})

        result = await runtime.life_photo_suite_generate(
            event,
            "雨后公园散步，浅色风衣",
            count=4,
            subject_route="scene",
            resolution="2K",
        )
        await runtime.note_tool_reaction_result(event, reaction_tool, {}, result)
        await runtime.note_tool_reaction_agent_done(event, None)

        self.assertEqual(json.loads(result)["status"], "pending")
        self.assertEqual([item["emoji_id"] for item in reaction_calls], [125])
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(model_calls, [])
        self.assertEqual(prompt_calls, {})
        await scheduled[0][2]
        self.assertEqual([item["emoji_id"] for item in reaction_calls], [125, 79])
        self.assertLessEqual(max_active, 2)
        self.assertEqual(prompt_calls["雨后公园镜头2"], 2)
        manifests = list(
            (root / "generated" / "images" / "suites").glob("*/manifest.json")
        )
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["resolution"], "2K")
        self.assertTrue(generation_options)
        self.assertTrue(
            all(options == {"resolution": "2K"} for options in generation_options)
        )
        self.assertEqual(
            [shot["status"] for shot in manifest["shots"]],
            ["sent", "failed", "sent", "sent"],
        )
        self.assertFalse(manifests[0].with_suffix(".json.tmp").exists())
        self.assertEqual(runtime.context.sent_messages, [])
        first_chain = event.sent_messages[0]
        self.assertEqual(
            [Path(item["file"]).name for item in first_chain.items],
            ["01.png", "03.png", "04.png"],
        )

        failed_second = False
        retry_result = await runtime.life_photo_suite_generate(
            event, retry_indexes=[2], resolution="4K"
        )
        self.assertEqual(json.loads(retry_result)["status"], "pending")
        await scheduled[1][2]

        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["resolution"], "2K")
        self.assertTrue(
            all(options == {"resolution": "2K"} for options in generation_options)
        )
        self.assertEqual(prompt_calls["雨后公园镜头1"], 1)
        self.assertEqual(prompt_calls["雨后公园镜头2"], 3)
        self.assertEqual(prompt_calls["雨后公园镜头3"], 1)
        self.assertEqual(prompt_calls["雨后公园镜头4"], 1)
        self.assertEqual(
            [Path(item["file"]).name for item in event.sent_messages[1].items],
            ["02.png"],
        )
        self.assertEqual(len(expressed), 2)
        followup_prompts = [item for item in model_calls if "套图交付结果" in item]
        self.assertEqual(len(followup_prompts), 2)
        self.assertTrue(
            all("不预设固定话术或互动动作" in item for item in followup_prompts)
        )
        self.assertTrue(all("邀请对方挑一张" not in item for item in followup_prompts))
        self.assertEqual([item[1] for item in expressed], ["这组拍好啦。"] * 2)
        self.assertEqual(
            runtime._last_generated_life_image_path(event.unified_msg_origin),
            str(manifests[0].parent / "02.png"),
        )

    async def test_photo_suite_followup_fallback_does_not_force_selection(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        expressed = []

        async def get_provider(provider_id=""):
            return None

        async def call_text_model(*args, **kwargs):
            raise AssertionError("没有文字模型时不应发起补话请求")

        async def send_background_text(scope, text, **kwargs):
            expressed.append(text)
            return True

        async def append_history(scope, text):
            return True

        runtime.get_text_provider = get_provider
        runtime.call_text_model = call_text_model
        runtime.send_background_text = send_background_text
        runtime._append_assistant_history = append_history

        cases = [
            (1, 1, True),
            (3, 3, False),
            (2, 3, False),
            (0, 3, False),
        ]
        for sent_count, requested_count, is_retry in cases:
            await runtime._photo_suite_send_followup(
                event.unified_msg_origin,
                event,
                prompt="雨后公园散步",
                sent_count=sent_count,
                requested_count=requested_count,
                total_count=3,
                failed_indexes=[],
                is_retry=is_retry,
            )

        self.assertEqual(
            expressed,
            [
                "这张重新拍好了。",
                "这组都拍好了。",
                "先把拍好的这几张发给你。",
                "这次没拍出来，整组都没有生成成功。",
            ],
        )
        self.assertTrue(all("挑" not in text for text in expressed))

    async def test_photo_suite_group_generation_uses_friend_and_scene_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        calls = []

        async def generate_group_image(
            prompt,
            participants,
            aspect_ratio="",
            resolution="",
            scene_reference="",
            identity_profiles=None,
        ):
            calls.append(
                (
                    prompt,
                    participants,
                    aspect_ratio,
                    resolution,
                    scene_reference,
                    identity_profiles,
                )
            )
            return types.SimpleNamespace(path=Path("group.png"))

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(generate_group_image=generate_group_image)
        )
        manifest = {
            "subject_route": "group",
            "participants": ["friend-7"],
            "aspect_ratio": "3:4",
            "resolution": "4K",
            "reference_path": "stable-reference.png",
            "identity_profiles": {
                "current_character": "整体纤细匀称",
                "friend": "身形修长",
            },
        }

        await runtime._photo_suite_generate_asset(Event(), manifest, "同一场景侧面抓拍")

        self.assertEqual(
            calls[0][1:],
            (
                ["friend-7"],
                "3:4",
                "4K",
                "stable-reference.png",
                {"current_character": "整体纤细匀称"},
            ),
        )
        self.assertEqual(calls[0][0], "同一场景侧面抓拍")
        self.assertEqual(
            calls[0][5],
            {"current_character": "整体纤细匀称"},
        )

    async def test_photo_suite_current_character_keeps_prompt_without_reference(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        generated = []

        class ImageService:
            def can_edit_image(self):
                return False

            async def generate_image(self, prompt, **kwargs):
                generated.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("suite.png"))

        runtime.media = types.SimpleNamespace(image=ImageService())
        manifest = {
            "subject_route": "current_character",
            "participants": [],
            "aspect_ratio": "",
            "resolution": "1K",
            "reference_path": "",
            "identity_profiles": {},
        }

        await runtime._photo_suite_generate_asset(Event(), manifest, "雨后公园侧面抓拍")

        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0][0], "雨后公园侧面抓拍")
        self.assertEqual(
            generated[0][1],
            {
                "resolution": "1K",
            },
        )

    async def test_photo_suite_stabilizes_temporary_reference_in_task_directory(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        task_dir = Path(tempfile.mkdtemp())
        loaded = []

        async def load_reference(reference):
            loaded.append(reference)
            return b"\xff\xd8\xffreference", "image/jpeg"

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(_load_reference_image=load_reference)
        )

        stable = await runtime._photo_suite_stabilize_reference(
            "/tmp/astrbot-expiring-image", task_dir
        )

        self.assertEqual(loaded, ["/tmp/astrbot-expiring-image"])
        self.assertEqual(Path(stable), task_dir / "reference.jpg")
        self.assertEqual(Path(stable).read_bytes(), b"\xff\xd8\xffreference")

    async def test_photo_suite_retry_lookup_is_scoped_to_current_conversation(self):
        root = Path(tempfile.mkdtemp())
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = root / "daily_life.db"
        suite_root = root / "generated" / "images" / "suites"
        for task_id, scope in (("task-a", "scope-a"), ("task-b", "scope-b")):
            task_dir = suite_root / task_id
            task_dir.mkdir(parents=True)
            (task_dir / "manifest.json").write_text(
                json.dumps({"id": task_id, "scope": scope, "count": 4}),
                encoding="utf-8",
            )

        path, manifest = await runtime._photo_suite_latest_manifest("scope-a")

        self.assertEqual(path, suite_root / "task-a" / "manifest.json")
        self.assertEqual(manifest["id"], "task-a")

    async def test_photo_suite_multi_image_send_falls_back_to_ordered_single_images(
        self,
    ):
        root = Path(tempfile.mkdtemp())
        paths = []
        for index in range(1, 4):
            path = root / f"{index:02d}.png"
            path.write_bytes(b"image")
            paths.append(path)
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        calls = []

        async def send(scope, chain, **kwargs):
            calls.append([item["file"] for item in chain.items])
            if len(chain.items) > 1:
                raise RuntimeError("平台不支持多图消息链")
            return True

        runtime.send_message_if_not_recalled = send
        shots = [
            {"index": index, "path": str(path)}
            for index, path in enumerate(paths, start=1)
        ]

        sent = await runtime._photo_suite_send_images("scope", Event(), shots)

        self.assertEqual(sent, {1, 2, 3})
        self.assertEqual(calls[0], [str(path) for path in paths])
        self.assertEqual(calls[1:], [[str(path)] for path in paths])

    async def test_photo_suite_final_text_is_held_until_background_delivery(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        task_dir = Path(tempfile.mkdtemp())
        runtime._photo_suite_register_request(
            event.unified_msg_origin, "雨后散步套图", event, task_dir
        )
        event.set_result(event.chain_result(["我已经拍好啦。"]))

        self.assertTrue(runtime.hold_life_photo_suite_final_text(event))
        self.assertIsNone(event.get_result())

    async def test_gemini_image_edit_sends_reference_image_part(self):
        posted_payloads = []
        output_bytes = b"\x89PNG\r\n\x1a\noutput"

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(
                                                output_bytes
                                            ).decode("ascii"),
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }

            async def text(self):
                return ""

        class Session:
            closed = False

            def post(self, url, json=None, headers=None, proxy=None, timeout=None):
                posted_payloads.append(json)
                return Response()

        reference = Path(tempfile.mkdtemp()) / "reference.png"
        reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(
                    "gemini-key",
                    resolution="2K",
                    aspect_ratio="16:9",
                )
            }
        ).image_generation
        service = GeminiImageService(
            settings, Path(tempfile.mkdtemp()) / "daily_life.db"
        )
        service._get_session = lambda: async_return(Session())

        generated = await service.edit_image("改成咖啡店生活照", str(reference))

        self.assertTrue(generated.path.exists())
        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("改成咖啡店生活照", parts[0]["text"])
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(
            base64.b64decode(parts[1]["inlineData"]["data"]), reference.read_bytes()
        )
        image_config = posted_payloads[-1]["generationConfig"]["imageConfig"]
        self.assertEqual(image_config["imageSize"], "2K")
        self.assertEqual(image_config["aspectRatio"], "16:9")
        response_image_config = posted_payloads[-1]["generationConfig"][
            "responseFormat"
        ]["image"]
        self.assertEqual(response_image_config["imageSize"], "2K")
        self.assertEqual(response_image_config["aspectRatio"], "16:9")

    async def test_gemini_image_generation_always_attaches_character_reference(self):
        posted_payloads = []
        output_bytes = b"\x89PNG\r\n\x1a\noutput"

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(
                                                output_bytes
                                            ).decode("ascii"),
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }

            async def text(self):
                return ""

        class Session:
            closed = False

            def post(self, url, json=None, headers=None, proxy=None, timeout=None):
                posted_payloads.append(json)
                return Response()

        temp_dir = Path(tempfile.mkdtemp())
        character = temp_dir / "character.png"
        character_side = temp_dir / "character-side.png"
        character.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
        character_side.write_bytes(b"\x89PNG\r\n\x1a\nside")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(
                    "gemini-key",
                    character_reference_images=[
                        {"path": str(character), "name": "正面参考.png"},
                        {"path": str(character_side), "name": "侧面参考.png"},
                    ],
                    character_reference_policy="always",
                )
            }
        ).image_generation
        service = GeminiImageService(
            settings, Path(tempfile.mkdtemp()) / "daily_life.db"
        )
        service._get_session = lambda: async_return(Session())

        await service.generate_image("角色坐在窗边看雨")

        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("优先保持角色的脸部气质、体态和身份辨识度", parts[0]["text"])
        self.assertIn("参考图不锁定本轮服装、配饰、发型、妆容或美甲", parts[0]["text"])
        self.assertIn("画面要求明确指定的当天造型优先", parts[0]["text"])
        self.assertEqual(
            parts[1]["text"], "下面 2 张图是角色形象参考图组，用于保持角色外貌一致。"
        )
        self.assertIn("正面参考.png", parts[2]["text"])
        self.assertEqual(
            base64.b64decode(parts[3]["inlineData"]["data"]), character.read_bytes()
        )
        self.assertIn("侧面参考.png", parts[4]["text"])
        self.assertEqual(
            base64.b64decode(parts[5]["inlineData"]["data"]),
            character_side.read_bytes(),
        )

    async def test_gemini_image_generation_auto_waits_for_identity_route(self):
        temp_dir = Path(tempfile.mkdtemp())
        character = temp_dir / "character.png"
        character.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(
                    "gemini-key",
                    character_reference_images=[
                        {"path": str(character), "name": "角色参考.png"}
                    ],
                    character_reference_policy="auto",
                )
            }
        ).image_generation
        service = GeminiImageService(settings, temp_dir / "daily_life.db")
        route = (await service._request_routes("text"))[0]

        parts = await service._text_to_image_parts("陌生人在街边散步", route)

        self.assertEqual(len(parts), 1)
        self.assertNotIn("角色形象参考图", parts[0]["text"])
        self.assertNotIn("inlineData", parts[0])

    async def test_gemini_image_edit_keeps_scene_reference_and_character_reference_separate(
        self,
    ):
        posted_payloads = []
        output_bytes = b"\x89PNG\r\n\x1a\noutput"

        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(
                                                output_bytes
                                            ).decode("ascii"),
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }

            async def text(self):
                return ""

        class Session:
            closed = False

            def post(self, url, json=None, headers=None, proxy=None, timeout=None):
                posted_payloads.append(json)
                return Response()

        temp_dir = Path(tempfile.mkdtemp())
        scene = temp_dir / "scene.png"
        character = temp_dir / "character.png"
        scene.write_bytes(b"\x89PNG\r\n\x1a\nscene")
        character.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
        settings = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(
                    "gemini-key",
                    character_reference_images=[
                        {"path": str(character), "name": "角色参考.png"}
                    ],
                    character_reference_policy="always",
                )
            }
        ).image_generation
        service = GeminiImageService(
            settings, Path(tempfile.mkdtemp()) / "daily_life.db"
        )
        service._get_session = lambda: async_return(Session())

        await service.edit_image("保留姿势，换成咖啡店生活照", str(scene))

        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("优先保持角色的脸部气质、体态和身份辨识度", parts[0]["text"])
        self.assertIn("参考图不锁定本轮服装、配饰、发型、妆容或美甲", parts[0]["text"])
        self.assertEqual(
            base64.b64decode(parts[1]["inlineData"]["data"]), scene.read_bytes()
        )
        self.assertEqual(
            parts[2]["text"], "下面 1 张图是角色形象参考图组，用于保持角色外貌一致。"
        )
        self.assertIn("角色参考.png", parts[3]["text"])
        self.assertEqual(
            base64.b64decode(parts[4]["inlineData"]["data"]), character.read_bytes()
        )

    async def test_image_vision_uses_standard_text_chat_image_urls(self):
        class TextVisionProvider(Provider):
            def __init__(self):
                super().__init__(
                    [
                        '{"summary":"桌上放着一盘切好的水果","is_emoji_asset":false,'
                        '"label":"水果","description":"适合分享生活小吃","emotions":["日常"],'
                        '"sendable":false,"status":"rejected"}'
                    ],
                    provider_id="vision-model",
                )
                self.image_inputs = []
                self.legacy_inputs = []

            async def image_chat(self, prompt, image="", session_id=None, **kwargs):
                self.legacy_inputs.append(image)
                return await super().image_chat(
                    prompt, image=image, session_id=session_id, **kwargs
                )

            async def text_chat(
                self,
                prompt,
                session_id=None,
                system_prompt=None,
                image_urls=None,
                **kwargs,
            ):
                self.image_inputs.append(list(image_urls or []))
                return await super().text_chat(
                    prompt, session_id=session_id, system_prompt=system_prompt, **kwargs
                )

        vision_provider = TextVisionProvider()
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {
                "resolve_event_sender": staticmethod(
                    lambda event: async_return(event.get_sender_name())
                )
            },
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        tmp_root = Path(tempfile.mkdtemp())
        runtime.data_path = tmp_root / "daily_life.db"
        source_path = tmp_root / "fruit-text-vision.png"
        source_path.write_bytes(b"\x89PNG\r\n\x1a\nfruit-text-vision")
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-private-img-text-vision",
        )
        event.message_str = "看看这个"
        event.message_items = [{"type": "image", "path": str(source_path)}]
        event.message_obj.message = event.message_items

        runtime.note_structured_incoming_message(event)
        self.assertTrue(runtime.schedule_visual_context_from_event(event))
        await scheduled[0][2]

        context = runtime.format_structured_message_context(event)
        self.assertIn("看看这个 [图片：桌上放着一盘切好的水果]", context)
        cached_path = next((tmp_root / "emoji").iterdir())
        self.assertEqual(vision_provider.image_inputs, [[str(cached_path)]])
        self.assertEqual(vision_provider.legacy_inputs, [])

    async def test_image_vision_updates_private_structured(self):
        vision_provider = Provider(
            [
                '{"summary":"桌上放着一盘切好的水果","label":"水果","description":"适合分享生活小吃","emotions":["日常"],"status":"ready"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {
                "resolve_event_sender": staticmethod(
                    lambda event: async_return(event.get_sender_name())
                )
            },
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        tmp_root = Path(tempfile.mkdtemp())
        runtime.data_path = tmp_root / "daily_life.db"
        source_path = tmp_root / "fruit.png"
        source_path.write_bytes(b"\x89PNG\r\n\x1a\nfruit")

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-private-img",
        )
        event.message_str = "看看这个"
        event.message_items = [{"type": "image", "path": str(source_path)}]
        event.message_obj.message = event.message_items

        runtime.note_structured_incoming_message(event)
        self.assertTrue(runtime.schedule_visual_context_from_event(event))
        self.assertEqual(scheduled[0][0], "图片上下文识别")
        index = 0
        while index < len(scheduled):
            await scheduled[index][2]
            index += 1

        context = runtime.format_structured_message_context(event)
        self.assertIn("看看这个 [图片：桌上放着一盘切好的水果]", context)
        cached_path = next((tmp_root / "emoji").iterdir())
        self.assertEqual(vision_provider.vision_prompts[0]["image"], str(cached_path))
        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(assets, [])

    async def test_image_vision_resolves_image_component_path(self):
        vision_provider = Provider(
            [
                '{"summary":"桌上放着一杯热茶","label":"热茶","description":"适合记录生活片段","emotions":["日常"],"status":"ready"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {"vision_config": {"provider": "vision-model"}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {
                "resolve_event_sender": staticmethod(
                    lambda event: async_return(event.get_sender_name())
                )
            },
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        class CurrentImage:
            type = "Image"

            async def convert_to_file_path(self):
                return str(image_path)

        tmp_root = Path(tempfile.mkdtemp())
        runtime.data_path = tmp_root / "daily_life.db"
        image_path = tmp_root / "tea.png"
        image_path.write_bytes(b"tea-image")
        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-private-img-path",
        )
        event.message_str = "看看这个"
        event.message_items = [CurrentImage()]
        event.message_obj.message = event.message_items

        runtime.note_structured_incoming_message(event)
        self.assertTrue(runtime.schedule_visual_context_from_event(event))
        self.assertEqual(scheduled[0][0], "图片上下文识别")
        await scheduled[0][2]

        context = runtime.format_structured_message_context(event)
        self.assertIn("看看这个 [图片：桌上放着一杯热茶]", context)
        cached_path = next((tmp_root / "emoji").iterdir())
        self.assertEqual(vision_provider.vision_prompts[0]["image"], str(cached_path))
