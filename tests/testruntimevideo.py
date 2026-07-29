import unittest

from runtimehelpers import (
    BiliMetadata,
    BiliTarget,
    Context,
    CORE_INTERNAL_SYSTEM_PROMPT,
    DailyLifeRuntime,
    DataManager,
    DayRecord,
    EmotionArcRecord,
    Event,
    GeminiImageService,
    LifeSettings,
    Path,
    PersonaManager,
    Provider,
    RuntimeAsyncHelperMixin,
    SegmentPart,
    SemanticSegmentPlan,
    SightBrief,
    SightClip,
    SightInsight,
    SightTextResult,
    SightVault,
    SiliconFlowVoiceService,
    TimelineItem,
    TranscriptResult,
    async_return,
    asyncio,
    datetime,
    html_renderer,
    json,
    patch,
    random,
    tempfile,
    types,
)
from core.sight.flight import SightFlight
from core.sight.note import (
    PROFESSIONAL_NOTE_CACHE_SCHEMA,
    professional_note_prompt_key,
)
from core.sight.reader import SightReader


class RuntimeVideoAsyncTest(RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _set_voice_expression_plan(
        runtime,
        event,
        *,
        emotion="轻松亲近",
        emotion_category="happy",
        confidence=0.9,
        reason="整轮语义更适合直接说出来",
    ):
        reply_text = runtime._voice_switch_reply_text_from_event(event)
        setattr(
            event,
            runtime._SEMANTIC_SEGMENT_PLAN_ATTR,
            SemanticSegmentPlan(
                (SegmentPart(reply_text),),
                channel="voice",
                emotion=emotion,
                emotion_category=emotion_category,
                confidence=confidence,
                reason=reason,
            ),
        )

    async def test_sight_flight_close_cancels_inflight_work(self):
        flight = SightFlight()
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.Event().wait()

        waiter = asyncio.create_task(flight.run("video", work))
        await started.wait()
        await flight.close()
        result = await asyncio.gather(waiter, return_exceptions=True)

        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertEqual(flight._tasks, {})

    async def test_sight_completed_keys_expire_and_stay_bounded(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"video_cache_ttl_hours": 1, "video_cache_max_items": 8}}
        )
        runtime._sight_completed_keys = {}

        with patch("core.sight.identity.time.monotonic", return_value=100.0):
            for index in range(12):
                runtime._mark_sight_completed_key(f"video-{index}")
            self.assertLessEqual(len(runtime._sight_completed_keys), 8)
            self.assertTrue(runtime._sight_completed_key_is_fresh("video-11"))

        with patch("core.sight.identity.time.monotonic", return_value=4001.0):
            self.assertFalse(runtime._sight_completed_key_is_fresh("video-11"))
            self.assertEqual(runtime._sight_completed_keys, {})

    async def test_life_video_generate_runs_in_background_and_sends_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "first-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        video_path = Path(tempfile.mkdtemp()) / "life.mp4"
        video_path.write_bytes(b"v" * 4096)
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=image_path)
                ),
                _load_reference_image=lambda reference: async_return(
                    (b"first-frame", "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: (
                    video_calls.append((prompt, image_bytes))
                    or async_return(types.SimpleNamespace(url=str(video_path)))
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "书店门口短视频")

        self.assertEqual(json.loads(result)["status"], "pending")
        self.assertNotIn("视频生成已开始", result)
        self.assertEqual(scheduled[0][0], "生活视频生成")
        await scheduled[0][2]

        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertEqual(video_calls[0][1], b"first-frame")
        self.assertIn(
            {"type": "video", "file": str(video_path)},
            runtime.context.sent_messages[0][1].items,
        )
        structured = list(runtime._structured_scope_messages(event.unified_msg_origin))
        self.assertTrue(structured)
        self.assertIn("[视频已发送：4.0 KB，耗时", structured[-1].content)
        cadence = runtime._media_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_media"], "视频")
        self.assertEqual(cadence["consecutive"], 1)
        self.assertFalse(
            any(
                call[0] == "update_conversation"
                for call in runtime.context.conversation_manager.calls
            )
        )

    async def test_life_video_generate_resolves_agent_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "first-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=image_path)
                ),
                _load_reference_image=lambda reference: async_return(
                    (b"first-frame", "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: async_return(
                    types.SimpleNamespace(url="https://example.com/life.mp4")
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")
        wrapped_event = types.SimpleNamespace(
            context=types.SimpleNamespace(event=event)
        )

        result = await runtime.life_video_generate(wrapped_event, "咖喱店短视频")

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertEqual(len(runtime.context.sent_messages), 1)

    async def test_life_video_generate_uses_directed_prompt_and_reference_image(self):
        provider = Provider(
            [
                (
                    '{"image":"傍晚书店门口的半身生活镜头",'
                    '"continuity":"保持上一张生活图里的人物身份、浅蓝外套、书店门口构图和主体位置",'
                    '"camera":"半身中近景，镜头缓慢推近，主体保持在画面中央偏右",'
                    '"motion":"手里的纸袋轻轻晃动，雨丝在路灯下微微发亮",'
                    '"sound":"街边细雨声和纸袋摩擦声"}'
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        runtime.archive.days[today] = DayRecord(
            date=today,
            weather="小雨",
            outfit="浅蓝外套",
            timeline=[TimelineItem(time="18:20", activity="从书店出来", status="轻松")],
        )
        await runtime.archive.save_emotion_arc(
            EmotionArcRecord(
                date=today,
                label="有点疲惫但心情不错",
                valence=30,
                intensity=64,
                evidence="雨天从书店出来，状态放松但体力一般",
                influence="视频动作更适合轻微、缓慢、生活化",
                expires_at="2099-01-01 00:00:00",
            )
        )

        async def fixed_media_day():
            fixed_now = datetime.datetime.strptime(f"{today} 18:25", "%Y-%m-%d %H:%M")
            return runtime.archive.days[today], fixed_now, False

        runtime._media_director_current_day = fixed_media_day

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
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                _load_reference_image=lambda reference: async_return(
                    (b"image-bytes", "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: (
                    video_calls.append((prompt, image_bytes))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")
        event.message_items = [{"type": "image", "file": "D:/tmp/current.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "傍晚从书店门口走出来")

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertIn("画面：傍晚书店门口的半身生活镜头", video_calls[0][0])
        self.assertIn("连续性：保持上一张生活图里的人物身份", video_calls[0][0])
        self.assertIn("镜头：半身中近景，镜头缓慢推近", video_calls[0][0])
        self.assertIn("动态：手里的纸袋轻轻晃动", video_calls[0][0])
        self.assertIn("continuity", provider.prompts[0])
        self.assertIn("camera", provider.prompts[0])
        self.assertIn("根据剧情、用户要求和画面内容决定", provider.prompts[0])
        self.assertIn("保持原图主体", provider.prompts[0])
        self.assertIn("嘴唇轻微自然开合", provider.prompts[0])
        self.assertIn("不要为了字段完整强行添加背景声或人声", provider.prompts[0])
        self.assertIn(
            "只有用户要求、原始剧情或人物互动确实需要时才写台词", provider.prompts[0]
        )
        self.assertIn("旁白、画外音和解说只有用户明确要求", provider.prompts[0])
        self.assertIn("近期情绪脉络（短期状态参考）", provider.prompts[0])
        self.assertIn("有点疲惫但心情不错", provider.prompts[0])
        self.assertIn("视频动作更适合轻微、缓慢、生活化", provider.prompts[0])
        self.assertEqual(video_calls[0][1], b"image-bytes")

    async def test_life_video_generate_uses_quoted_image_as_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        loaded_refs = []
        video_calls = []
        quoted_frame = (
            b"\x89PNG\r\n\x1a\n"
            + (13).to_bytes(4, "big")
            + b"IHDR"
            + (9).to_bytes(4, "big")
            + (16).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
            + (0).to_bytes(4, "big")
        )

        async def fail_generate_image(prompt):
            raise AssertionError("引用图片应直接作为视频首帧，不应自动生成首帧")

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=fail_generate_image,
                _reference_image_aspect_ratio=GeminiImageService._reference_image_aspect_ratio,
                _load_reference_image=lambda reference: (
                    loaded_refs.append(reference)
                    or async_return((quoted_frame, "image/png"))
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: (
                    video_calls.append((prompt, image_bytes, kwargs))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "把这张转成视频")

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(loaded_refs, ["https://example.com/quoted.png"])
        self.assertEqual(
            video_calls, [("把这张转成视频", quoted_frame, {"aspect_ratio": "9:16"})]
        )

    async def test_life_video_generate_prompt_ratio_overrides_reference_image_ratio(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        quoted_frame = (
            b"\x89PNG\r\n\x1a\n"
            + (13).to_bytes(4, "big")
            + b"IHDR"
            + (9).to_bytes(4, "big")
            + (16).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
            + (0).to_bytes(4, "big")
        )
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: (_ for _ in ()).throw(
                    AssertionError("引用图片不应自动生成首帧")
                ),
                _reference_image_aspect_ratio=GeminiImageService._reference_image_aspect_ratio,
                _load_reference_image=lambda reference: async_return(
                    (quoted_frame, "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: (
                    video_calls.append((prompt, image_bytes, kwargs))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "把这张转成横版 16:9 视频")

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(
            video_calls,
            [("把这张转成横版 16:9 视频", quoted_frame, {"aspect_ratio": "16:9"})],
        )

    async def test_life_video_generate_prompt_duration_overrides_config(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        quoted_frame = (
            b"\x89PNG\r\n\x1a\n"
            + (13).to_bytes(4, "big")
            + b"IHDR"
            + (9).to_bytes(4, "big")
            + (16).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
            + (0).to_bytes(4, "big")
        )
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: (_ for _ in ()).throw(
                    AssertionError("引用图片不应自动生成首帧")
                ),
                _reference_image_aspect_ratio=GeminiImageService._reference_image_aspect_ratio,
                _load_reference_image=lambda reference: async_return(
                    (quoted_frame, "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: (
                    video_calls.append((prompt, image_bytes, kwargs))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(
            event, "把这张转成横版 16:9，做成5秒视频"
        )

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(
            video_calls,
            [
                (
                    "把这张转成横版 16:9，做成5秒视频",
                    quoted_frame,
                    {"aspect_ratio": "16:9", "duration": 5},
                )
            ],
        )

    def test_video_prompt_duration_seconds_parses_common_forms(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        self.assertEqual(runtime._video_prompt_duration_seconds("5 秒视频"), 5)
        self.assertEqual(runtime._video_prompt_duration_seconds("8s短视频"), 8)
        self.assertEqual(runtime._video_prompt_duration_seconds("12秒"), 12)
        self.assertEqual(runtime._video_prompt_duration_seconds("60秒视频"), 15)
        self.assertEqual(runtime._video_prompt_duration_seconds("时长：12"), 0)
        self.assertEqual(
            runtime._video_prompt_duration_seconds(
                "第1格【0-1.5秒】开场\n第2格【1.5-3秒】奔跑"
            ),
            3,
        )
        self.assertEqual(
            runtime._video_prompt_duration_seconds(
                "第9格【12.5-15秒】：坐在电车里微笑"
            ),
            15,
        )
        self.assertEqual(runtime._video_prompt_duration_seconds("普通短视频"), 0)

    async def test_life_video_generate_uses_full_event_storyboard_directly(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        quoted_frame = (
            b"\x89PNG\r\n\x1a\n"
            + (13).to_bytes(4, "big")
            + b"IHDR"
            + (9).to_bytes(4, "big")
            + (16).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
            + (0).to_bytes(4, "big")
        )
        video_calls = []
        runtime._direct_life_video_prompt = lambda event, prompt: (_ for _ in ()).throw(
            AssertionError("完整分镜不应再走视频智能提取")
        )
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: (_ for _ in ()).throw(
                    AssertionError("引用图片不应自动生成首帧")
                ),
                _reference_image_aspect_ratio=GeminiImageService._reference_image_aspect_ratio,
                _load_reference_image=lambda reference: async_return(
                    (quoted_frame, "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: (
                    video_calls.append((prompt, image_bytes, kwargs))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        full_prompt = (
            "/ [图片] 转视频\n"
            "第1格【0-1.5秒】：手机屏幕特写，显示清晨闹钟，金黄色温暖阳光穿过蕾丝窗帘洒下。\n"
            "第2格【1.5-3秒】：日本高中女生急忙从木质桌子上抓起棕色皮革制服包。\n"
            "第9格【12.5-15秒】：中近景，女孩坐在复古电车车厢里，靠着窗户温柔微笑。"
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = full_prompt
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(
            event, "English summary that lost most storyboard details"
        )

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(
            video_calls,
            [(full_prompt, quoted_frame, {"aspect_ratio": "9:16", "duration": 15})],
        )

    async def test_life_video_first_frame_uses_character_reference_route(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        first_frame = Path(tempfile.mkdtemp()) / "video-first-frame.png"
        first_frame.write_bytes(b"\x89PNG\r\n\x1a\nframe")
        generate_calls = []
        edit_calls = []
        rewrite_calls = []
        loaded_refs = []
        video_calls = []

        async def direct_image(event, prompt, **kwargs):
            return types.SimpleNamespace(
                prompt=f"导演整理：{prompt}",
                contains_character=True,
                needs_character_reference=True,
            )

        class ImageService:
            def can_edit_image(self):
                return True

            def first_character_reference_image(self):
                return "D:/ref/role.png"

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                raise AssertionError("视频首帧需要角色参考图时不应走文生图")

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                if len(edit_calls) == 1:
                    raise RuntimeError(
                        'HTTP 400：{"error":{"code":"content_policy_violation"}}'
                    )
                return types.SimpleNamespace(path=first_frame)

            async def _load_reference_image(self, reference):
                loaded_refs.append(reference)
                return b"edited-first-frame", "image/png"

        runtime._direct_life_image_payload = direct_image
        runtime._character_appearance_profile = lambda event: async_return(
            "成年女性，整体纤细匀称，上半身曲线自然丰满"
        )
        runtime._direct_life_video_prompt = lambda event, prompt: async_return(
            f"视频导演：{prompt}"
        )
        runtime._rewrite_life_image_prompt_for_policy_retry = (
            lambda event, prompt, **kwargs: (
                rewrite_calls.append((prompt, kwargs))
                or async_return(f"{prompt}，自然生活化表达")
            )
        )
        runtime.media = types.SimpleNamespace(
            image=ImageService(),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: (
                    video_calls.append((prompt, image_bytes, kwargs))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_video_generate(
            event, "角色本人深夜卧室短视频，竖版 9:16"
        )

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [
                (
                    "导演整理：角色本人深夜卧室短视频，竖版 9:16",
                    "D:/ref/role.png",
                    {
                        "aspect_ratio": "9:16",
                        "preserve_reference_ratio": False,
                        "identity_profile": "成年女性，整体纤细匀称，上半身曲线自然丰满",
                    },
                ),
                (
                    "导演整理：角色本人深夜卧室短视频，竖版 9:16，自然生活化表达",
                    "D:/ref/role.png",
                    {
                        "aspect_ratio": "9:16",
                        "preserve_reference_ratio": False,
                        "identity_profile": "成年女性，整体纤细匀称，上半身曲线自然丰满",
                    },
                ),
            ],
        )
        self.assertEqual(
            rewrite_calls,
            [("导演整理：角色本人深夜卧室短视频，竖版 9:16", {"reference": True})],
        )
        self.assertEqual(loaded_refs, [str(first_frame)])
        self.assertEqual(
            video_calls,
            [
                (
                    "视频导演：角色本人深夜卧室短视频，竖版 9:16",
                    b"edited-first-frame",
                    {"aspect_ratio": "9:16"},
                )
            ],
        )

    async def test_life_video_prompt_allows_optional_director_fields(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        prompt = runtime._media_video_prompt_from_payload(
            {
                "image": "雨夜窗边半身镜头",
                "motion": "雨滴在玻璃上缓慢滑落，人物轻轻眨眼",
            }
        )

        self.assertEqual(
            prompt,
            "画面：雨夜窗边半身镜头。动态：雨滴在玻璃上缓慢滑落，人物轻轻眨眼。",
        )
        self.assertNotIn("连续性：", prompt)
        self.assertNotIn("声音：", prompt)

    async def test_life_video_generate_ignores_previous_image_when_message_has_no_image(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "fresh-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfresh")
        loaded_refs = []
        image_prompts = []
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: (
                    image_prompts.append(prompt)
                    or async_return(types.SimpleNamespace(path=image_path))
                ),
                _load_reference_image=lambda reference: (
                    loaded_refs.append(reference)
                    or async_return((b"fresh-frame", "image/png"))
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: (
                    video_calls.append((prompt, image_bytes))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                ),
            ),
        )
        runtime._life_media_last_images = {
            "aiocqhttp:GroupMessage:20001": "D:/tmp/old.png"
        }
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(loaded_refs, [str(image_path)])
        self.assertEqual(video_calls[0][1], b"fresh-frame")
        self.assertNotIn("D:/tmp/old.png", loaded_refs)
        self.assertTrue(image_prompts)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertIn(
            {
                "type": "video",
                "url": "https://example.com/life.mp4",
                "file": "https://example.com/life.mp4",
            },
            runtime.context.sent_messages[0][1].items,
        )

    async def test_life_video_generate_reports_error_when_video_fails(self):
        provider = Provider(
            [
                '{"subject":"窗边的人","scene":"雨夜窗边","composition":"半身生活照"}',
                (
                    '{"image":"雨夜窗边",'
                    '"continuity":"保持首帧里的人物、睡衣和窗边构图",'
                    '"camera":"半身近景，镜头轻轻推近",'
                    '"motion":"人物轻轻眨眼，雨滴沿玻璃滑落",'
                    '"sound":"窗外雨声和很轻的室内背景声"}'
                ),
                '{"reply_text":"这段视频没跑出来，我先不硬凑了。"}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider,
            persona_manager=PersonaManager(prompt="我是一个夜里说话会放轻声音的人。"),
        )
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.archive.days["2026-05-24"] = DayRecord(
            date="2026-05-24",
            outfit="奶油色睡衣",
            timeline=[
                TimelineItem(time="23:50", activity="窝在被窝里准备睡觉", status="困")
            ],
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

            async def _get_persona(self, umo=""):
                return "我是一个夜里说话会放轻声音的人。"

        runtime.composer = Composer()
        image_path = Path(tempfile.mkdtemp()) / "fallback-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfallback")

        async def fail_video(prompt, image_bytes=None):
            raise RuntimeError("TimeoutError")

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=image_path)
                ),
                _load_reference_image=lambda reference: async_return(
                    (b"fallback-frame", "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=fail_video,
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertEqual(
            runtime.context.sent_messages[0][1].items,
            [{"type": "image", "file": str(image_path)}],
        )
        self.assertEqual(
            runtime.context.sent_messages[1][1].items,
            ["这段视频没跑出来，我先不硬凑了。"],
        )
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("视频未发送", provider.prompts[-1])
        self.assertIn("已经发送一张图片", provider.prompts[-1])
        self.assertIn("不要输出接口、模型、任务、报错", provider.prompts[-1])

    async def test_life_video_generate_accepts_image_only_director_result(self):
        provider = Provider(
            [
                '{"subject":"窗边的人","scene":"雨夜窗边","composition":"半身生活照"}',
                '{"image":"雨夜窗边"}',
                '{"reply_text":"拍好了，雨夜窗边这一段还挺安静的。"}',
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
        image_path = Path(tempfile.mkdtemp()) / "fallback-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfallback")
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=image_path)
                ),
                _load_reference_image=lambda reference: async_return(
                    (b"fallback-frame", "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: (
                    video_calls.append((prompt, image_bytes))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                ),
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(video_calls, [("画面：雨夜窗边。", b"fallback-frame")])
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertIn(
            {
                "type": "video",
                "url": "https://example.com/life.mp4",
                "file": "https://example.com/life.mp4",
            },
            runtime.context.sent_messages[0][1].items,
        )
        self.assertEqual(
            runtime.context.sent_messages[1][1].items,
            ["拍好了，雨夜窗边这一段还挺安静的。"],
        )
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("视频发送结果", provider.prompts[-1])

    async def test_life_video_generate_uses_local_fallback_when_failure_llm_empty(self):
        provider = Provider(["{}", ""])
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
        image_calls = []
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: (
                    image_calls.append(prompt)
                    or async_return(types.SimpleNamespace(path=Path("first-frame.png")))
                ),
                _load_reference_image=lambda reference: async_return(
                    (b"first-frame", "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: (
                    video_calls.append((prompt, image_bytes))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/life.mp4")
                    )
                ),
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(image_calls, [])
        self.assertEqual(video_calls, [])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(
            runtime.context.sent_messages[0][1].items,
            ["刚才没发出去，晚点再试试。"],
        )
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("视频未发送", provider.prompts[-1])

    async def test_life_video_final_text_is_held_until_background_send(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        runtime._register_life_video_request(
            event.unified_msg_origin, "窗边短视频", event
        )
        event.set_result(event.chain_result(["视频生成要稍微等等，我已经开始跑了。"]))

        self.assertTrue(runtime.hold_life_video_final_text(event))
        result = event.get_result()
        self.assertIsNone(result)

    async def test_life_video_generate_sends_video_before_followup_text(self):
        provider = Provider(['{"reply_text":"拍好啦，这段夜色很贴她现在的状态。"}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "first-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        video_path = Path(tempfile.mkdtemp()) / "life.mp4"
        video_path.write_bytes(b"v" * 2048)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(
                    prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT
                )
                return getattr(resp, "completion_text", "")

            async def _get_persona(self, umo=""):
                return "我是一个深夜说话会放轻的人。"

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=image_path)
                ),
                _load_reference_image=lambda reference: async_return(
                    (b"first-frame", "image/png")
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: async_return(
                    types.SimpleNamespace(url=str(video_path))
                )
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_video_generate(event, "深夜窗边短视频")

        self.assertEqual(json.loads(result)["response_timing"], "after_delivery")
        await scheduled[0][2]
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertIn(
            {"type": "video", "file": str(video_path)},
            runtime.context.sent_messages[0][1].items,
        )
        self.assertEqual(
            runtime.context.sent_messages[1][1].items,
            ["拍好啦，这段夜色很贴她现在的状态。"],
        )
        self.assertIn("已发送视频", provider.prompts[-1])
        self.assertIn("成品交付后的回应", provider.prompts[-1])
        self.assertIn("不描述内部流程或生成过程", provider.prompts[-1])
        self.assertIn('"reply_text"', provider.prompts[-1])
        self.assertNotIn(
            "等待、稍后、正在、开始生成、后台、任务、接口、发送成功",
            provider.prompts[-1],
        )

    def test_life_video_reply_parser_accepts_only_strict_reply_json(self):
        parse = DailyLifeRuntime._parse_life_video_reply_text

        self.assertEqual(
            parse('{"reply_text":"这段晚风拍得还挺舒服。"}'),
            "这段晚风拍得还挺舒服。",
        )
        self.assertEqual(
            parse('```json\n{"reply_text":"图片你先看。"}\n```'),
            "图片你先看。",
        )
        self.assertEqual(
            parse(
                "我看到视频没发出去但图已经到了，想轻轻吐槽一下。\n\n"
                "啊这视频卡壳了没发出去，有点烦…图片你先看。"
            ),
            "",
        )
        self.assertEqual(parse('我先想想语气。\n{"reply_text":"真正回复"}'), "")
        self.assertEqual(parse('{"reply_text":"真正回复","reason":"语气说明"}'), "")
        self.assertEqual(parse("真正回复"), "")
        self.assertEqual(parse('{"reply_text":""}'), "")
        self.assertEqual(
            DailyLifeRuntime._life_video_failure_fallback_text(True),
            "视频没拍成，先把这张照片发你看。",
        )
        self.assertEqual(
            DailyLifeRuntime._life_video_failure_fallback_text(False),
            "刚才没发出去，晚点再试试。",
        )

    async def test_voice_switch_before_send_uses_local_structure_for_text_decision(
        self,
    ):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "ZBrush 遮罩怎么扩大？"
        event.set_result(
            types.SimpleNamespace(
                chain=[
                    types.SimpleNamespace(
                        text="你得去右侧工具栏找 Tool -> Masking，里面有个 Grow 按钮。\n"
                        "按住 Ctrl + Alt 点击它，再按你想绑定的键。"
                    )
                ]
            )
        )

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(provider.prompts, [])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("英文名词或参数", item["text_reason"])

    async def test_voice_switch_before_send_keeps_link_as_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "第三条链接发我"
        event.set_result(
            types.SimpleNamespace(
                chain=[
                    types.SimpleNamespace(text="第三条是这个：https://example.com/news")
                ]
            )
        )

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("链接", item["text_reason"])

    async def test_voice_switch_before_send_keeps_text_without_voice_expression_plan(
        self,
    ):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 35,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "外面好玩吗？"
        event.set_result(
            types.SimpleNamespace(
                chain=[
                    types.SimpleNamespace(
                        text="我刚从外面绕了一圈回来，雨停了，路上人不多，空气还可以，等会儿先把东西放好再说。"
                    )
                ]
            )
        )

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(provider.prompts, [])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertTrue(item["pre_send_checked"])
        self.assertIn("没有明确的语音表达需要", item["text_reason"])

    async def test_voice_switch_before_send_can_replace_text_with_voice(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.context.config = {
            "provider_settings": {
                "identifier": True,
                "datetime_system_prompt": True,
            },
            "timezone": "Asia/Shanghai",
        }
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append((text, emotion, emotion_category))
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "还没到吗？"
        reply = "别催啦，我马上到。"
        runtime.context.conversation_manager.conversations[event.unified_msg_origin] = (
            types.SimpleNamespace(history=[{"role": "assistant", "content": reply}])
        )
        runtime.context.conversation_manager.current_ids[event.unified_msg_origin] = (
            "current"
        )
        event.set_result(
            types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)])
        )

        self._set_voice_expression_plan(runtime, event)
        setattr(
            event,
            runtime._SEMANTIC_SEGMENT_PENDING_ATTR,
            [SegmentPart("别催啦，"), SegmentPart("我马上到。")],
        )
        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "轻松亲近", "happy")])
        self.assertEqual(getattr(event, runtime._SEMANTIC_SEGMENT_PENDING_ATTR), [])
        result = event.get_result()
        self.assertTrue(
            any(getattr(item, "file", "") == "voice.mp3" for item in result.chain)
        )
        self.assertFalse(runtime.note_voice_switch_text_result(event))
        history = runtime.context.conversation_manager.conversations[
            event.unified_msg_origin
        ].history
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(
            history[0]["content"][0], {"type": "text", "text": "还没到吗？"}
        )
        self.assertIn("用户 ID：10001", history[0]["content"][1]["text"])
        self.assertEqual(history[1], {"role": "assistant", "content": reply})
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_voice_switch_keeps_active_tool_preface_as_text(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        class Runner:
            def done(self):
                return False

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append((text, emotion, emotion_category))
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        reply = "我这就帮你转成视频，稍等我一下。"
        event.set_result(event.chain_result([reply]))
        runtime.mark_voice_switch_available(event)

        from core.runtime.voice import preface as voice_preface_module

        old_follow_up = voice_preface_module._astrbot_follow_up
        voice_preface_module._astrbot_follow_up = types.SimpleNamespace(
            _ACTIVE_AGENT_RUNNERS={event.unified_msg_origin: Runner()}
        )
        try:
            changed = await runtime.apply_voice_switch_before_send(event)
        finally:
            voice_preface_module._astrbot_follow_up = old_follow_up

        self.assertFalse(changed)
        self.assertEqual(voice_calls, [])
        self.assertEqual(event.get_result().chain, [reply])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertTrue(item["pre_send_checked"])
        self.assertIn("工具还在执行中", item["text_reason"])

    async def test_voice_switch_before_send_enriches_existing_user_image_history(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(
                    types.SimpleNamespace(path=Path("voice.mp3"))
                )
            )
        )
        scope = "aiocqhttp:FriendMessage:10001"
        image_file = Path(tempfile.mkdtemp()) / "voice-switch.png"
        image_file.write_bytes(b"\x89PNG\r\n\x1a\nvoice-switch")
        image_path = str(image_file)
        reply = "看见啦，好可爱。"
        runtime.context.conversation_manager.conversations[scope] = (
            types.SimpleNamespace(
                history=[
                    {"role": "user", "content": "你看看这张"},
                    {"role": "assistant", "content": reply},
                ]
            )
        )
        runtime.context.conversation_manager.current_ids[scope] = "current"
        event = Event(unified_msg_origin=scope, sender_id="10001")
        event.message_str = "你看看这张"
        event.message_items = [{"type": "image", "file": image_path}]
        event.message_obj.message = event.message_items
        event.set_result(
            types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)])
        )

        self._set_voice_expression_plan(runtime, event)
        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        history = runtime.context.conversation_manager.conversations[scope].history
        self.assertEqual(len(history), 2)
        self._assert_user_history_has_image(history[0], image_path)
        self.assertEqual(
            history[0]["content"][0], {"type": "text", "text": "你看看这张"}
        )
        self.assertEqual(history[1], {"role": "assistant", "content": reply})

    async def test_voice_switch_short_wrapped_text_can_still_use_voice(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append((text, emotion, emotion_category))
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "那就这么定了？"
        reply = "嗯，\n雨天就这点好，节奏一下慢下来\n你那边呢，还在发呆没"
        event.set_result(
            types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)])
        )

        self._set_voice_expression_plan(runtime, event)
        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "轻松亲近", "happy")])

    async def test_voice_switch_short_clipped_reply_can_use_angry_tone(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append((text, emotion, emotion_category))
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "还没到吗？"
        reply = "别催啦，我马上到！"
        event.set_result(
            types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)])
        )

        self._set_voice_expression_plan(
            runtime,
            event,
            emotion="语气稍冲",
            emotion_category="angry",
        )
        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "语气稍冲", "angry")])

    async def test_voice_switch_soft_drooping_reply_can_use_sad_tone(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append((text, emotion, emotion_category))
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "你还好吗？"
        reply = "有点累了…我先缓缓。"
        event.set_result(
            types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)])
        )

        self._set_voice_expression_plan(
            runtime,
            event,
            emotion="低低慢声",
            emotion_category="sad",
        )
        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "低低慢声", "sad")])

    async def test_voice_switch_probability_gate_keeps_text(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 20,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append(text)
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "还没到吗？"
        event.set_result(
            types.SimpleNamespace(
                chain=[types.SimpleNamespace(text="别催啦，我马上到。")]
            )
        )

        self._set_voice_expression_plan(runtime, event)
        old_random = random.random
        random.random = lambda: 0.9
        try:
            runtime.mark_voice_switch_available(event)
            changed = await runtime.apply_voice_switch_before_send(event)
        finally:
            random.random = old_random

        self.assertFalse(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("语音留到更需要", item["text_reason"])

    async def test_voice_switch_can_continue_short_voice_chain_after_recent_voice(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append(text)
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 3
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "还在外面吗？"
        event.set_result(
            types.SimpleNamespace(
                chain=[types.SimpleNamespace(text="嗯，雨停了我就往回走。")]
            )
        )
        runtime._mark_voice_switch_channel(event, "语音")

        self._set_voice_expression_plan(runtime, event)
        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, ["嗯，雨停了我就往回走。"])
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["consecutive_voice"], 2)

    async def test_voice_switch_stops_after_voice_chain_limit(self):
        provider = Provider(
            [
                '{"channel":"voice","reason":"我还想顺着刚才的语气接一句。",'
                '"emotion":"轻松","emotion_category":"happy","confidence":0.91}'
            ]
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append(text)
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 2
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "还没到吗？"
        event.set_result(
            types.SimpleNamespace(chain=[types.SimpleNamespace(text="马上，别催啦。")])
        )
        for _ in range(2):
            runtime._mark_voice_switch_channel(event, "语音")

        self._set_voice_expression_plan(runtime, event)
        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(voice_calls, [])
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("连续发了几条语音", item["text_reason"])

    async def test_voice_switch_before_send_does_not_call_llm_decision(self):
        class Composer:
            async def _get_provider(self, provider_id=""):
                raise AssertionError("发送前本地裁定不应请求 provider")

            async def _call_llm_text(
                self,
                provider_obj,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                raise AssertionError("发送前本地裁定不应调用大语言模型")

            async def _cleanup_conversation(self, session_id):
                raise AssertionError("发送前本地裁定不应创建临时会话")

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "ZBrush 遮罩怎么扩大？"
        event.set_result(
            types.SimpleNamespace(
                chain=[
                    types.SimpleNamespace(
                        text="Tool -> Masking 里点 Grow，再自己绑定快捷键。"
                    )
                ]
            )
        )

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("英文名词或参数", item["text_reason"])

    async def test_voice_switch_before_send_drops_text_when_record_chain_has_caption(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.composer = types.SimpleNamespace()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(
                    types.SimpleNamespace(path=Path("voice.mp3"))
                )
            )
        )
        reply = "有要急事吗？要是没什么事，我可先睡了。"
        runtime._record_message_chain = lambda path: types.SimpleNamespace(
            chain=[
                types.SimpleNamespace(type="record", file=str(path), text=reply),
                types.SimpleNamespace(text=reply),
            ]
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001"
        )
        event.message_str = "在吗"
        event.set_result(
            types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)])
        )

        self._set_voice_expression_plan(runtime, event)
        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        result = event.get_result()
        self.assertEqual(len(result.chain), 1)
        self.assertEqual(getattr(result.chain[0], "type", ""), "record")
        self.assertIsNone(getattr(result.chain[0], "text", None))

    async def test_voice_generation_routes_emotion_to_voice_and_speed(self):
        posted_payloads = []

        class Response:
            status = 200
            headers = {"Content-Type": "audio/mpeg"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def read(self):
                return b"voice-bytes"

        class Session:
            closed = False

            def post(self, url, headers=None, json=None, timeout=None):
                posted_payloads.append(json)
                return Response()

        settings = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "api_key": "sf-key",
                    "voice": "voice-neutral",
                    "emotion_voice_map": "happy: voice-happy\nsad: voice-sad\n无奈中带点宠溺: voice-soft",
                    "emotion_speed_map": "happy: 1.35\nsad: 0.75\nneutral: 1.0\n无奈中带点宠溺: 0.95",
                }
            }
        ).voice_generation
        service = SiliconFlowVoiceService(settings, Path(tempfile.mkdtemp()))
        service._get_session = lambda: async_return(Session())

        await service.synthesize("好耶，今天很开心")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-neutral")
        self.assertEqual(posted_payloads[-1]["speed"], 1.0)
        self.assertEqual(posted_payloads[-1]["response_format"], "wav")
        self.assertNotIn("sample_rate", posted_payloads[-1])
        self.assertNotIn("gain", posted_payloads[-1])

        await service.synthesize("好耶，今天很开心", emotion="开心")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-neutral")
        self.assertEqual(posted_payloads[-1]["speed"], 1.0)

        await service.synthesize(
            "好耶，今天很开心", emotion="开心", emotion_category="happy"
        )

        self.assertEqual(posted_payloads[-1]["voice"], "voice-happy")
        self.assertEqual(posted_payloads[-1]["speed"], 1.35)

        await service.synthesize("我还好", emotion="难过")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-neutral")
        self.assertEqual(posted_payloads[-1]["speed"], 1.0)

        await service.synthesize("我还好", emotion="难过", emotion_category="sad")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-sad")
        self.assertEqual(posted_payloads[-1]["speed"], 0.75)

        route = service._voice_route("无奈中带点宠溺")
        self.assertEqual(route["emotion"], "无奈中带点宠溺")
        self.assertEqual(route["voice"], "voice-soft")
        self.assertEqual(route["speed"], 0.95)

        await service.synthesize("行了行了，听到没", emotion="无奈中带点宠溺")

        self.assertEqual(posted_payloads[-1]["voice"], "voice-soft")
        self.assertEqual(posted_payloads[-1]["speed"], 0.95)

        unknown_route = service._voice_route("困得有点撒娇")
        self.assertEqual(unknown_route["emotion"], "困得有点撒娇")
        self.assertEqual(unknown_route["emotion_category"], "")
        self.assertEqual(unknown_route["voice"], "voice-neutral")
        self.assertEqual(unknown_route["speed"], 1.0)

        category_route = service._voice_route("慵懒治愈", "happy")
        self.assertEqual(category_route["emotion"], "慵懒治愈")
        self.assertEqual(category_route["emotion_category"], "happy")
        self.assertEqual(category_route["voice"], "voice-happy")
        self.assertEqual(category_route["speed"], 1.35)

        await service.synthesize(
            "慢慢醒一下", emotion="慵懒治愈", emotion_category="happy"
        )

        self.assertEqual(posted_payloads[-1]["voice"], "voice-happy")
        self.assertEqual(posted_payloads[-1]["speed"], 1.35)

        no_category_settings = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "api_key": "sf-key",
                    "voice": "voice-default",
                    "emotion_voice_map": "neutral: voice-neutral",
                    "emotion_speed_map": "neutral: 0.7",
                }
            }
        ).voice_generation
        no_category_service = SiliconFlowVoiceService(
            no_category_settings, Path(tempfile.mkdtemp())
        )
        no_category_route = no_category_service._voice_route("慵懒治愈")

        self.assertEqual(no_category_route["emotion"], "慵懒治愈")
        self.assertEqual(no_category_route["emotion_category"], "")
        self.assertEqual(no_category_route["voice"], "voice-default")
        self.assertEqual(no_category_route["speed"], 1.0)

    async def test_video_sight_updates_private_structured_context(self):
        vision_provider = Provider(
            [
                '{"summary":"雨夜街边有人撑伞走过","details":["青石板有积水","灯笼光偏暖"]}',
                '{"summary":"镜头转到古镇小巷深处","details":["远处有店铺招牌"]}',
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
        runtime._sight_vault = SightVault(runtime.archive)
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

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-private-video",
        )
        event.message_str = "看看这个视频"
        event.message_items = [{"type": "video", "file": "D:/tmp/rain-town.mp4"}]
        event.message_obj.message = event.message_items

        runtime.note_structured_incoming_message(event)
        prepared_path = Path(tempfile.mkdtemp()) / "rain-town.mp4"
        prepared_path.write_bytes(b"fake-video")
        with (
            patch(
                "core.sight.bridge.prepare_sample_video_source",
                lambda *args, **kwargs: async_return(prepared_path),
            ),
            patch(
                "core.sight.bridge.extract_video_frames",
                lambda source, cache_dir, max_frames=3, **kwargs: async_return(
                    [Path("frame-1.jpg"), Path("frame-2.jpg")]
                ),
            ),
        ):
            self.assertTrue(runtime.schedule_video_context_from_event(event))
            self.assertEqual(scheduled[0][0], "视频上下文理解")
            await scheduled[0][2]

        context = runtime.format_structured_message_context(event)
        self.assertIn("看看这个视频 [视频：雨夜街边有人撑伞走过", context)
        self.assertEqual(
            [item["image"] for item in vision_provider.vision_prompts],
            ["frame-1.jpg", "frame-2.jpg"],
        )
        recent = await runtime._sight_vault_for_runtime().recent(
            event.unified_msg_origin
        )
        self.assertIn("镜头转到古镇小巷深处", recent[0].summary)

    async def test_sight_async_clips_resolve_quoted_video_component(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        video_path = str(Path(tempfile.mkdtemp()) / "quoted.mp4")

        class QuotedVideo:
            type = "Video"
            file = "file-id-placeholder"
            path = ""
            name = "quoted.mp4"

            async def convert_to_file_path(self):
                return video_path

        class Reply:
            type = "Reply"
            id = "quoted-message-id"
            chain = [QuotedVideo()]

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            message_id="m-quoted-video-question",
        )
        event.message_str = "这是什么？"
        event.message_items = [Reply()]
        event.message_obj.message = event.message_items

        clips = await runtime._sight_clips_from_event_async(event)

        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].source, video_path)
        self.assertEqual(clips[0].origin, "quote")
        self.assertEqual(clips[0].text, "这是什么？")

    def test_sight_clip_cache_key_ignores_message_id_for_same_video(self):
        first = SightClip(
            scope="group", message_id="m-1", source="https://example.com/video.mp4"
        )
        second = SightClip(
            scope="group", message_id="m-2", source="https://example.com/video.mp4"
        )

        self.assertEqual(first.key, second.key)

    def test_sight_clip_cache_key_keeps_different_video_sources_separate(self):
        first = SightClip(
            scope="group", message_id="m-1", source="https://example.com/one.mp4"
        )
        second = SightClip(
            scope="group", message_id="m-2", source="https://example.com/two.mp4"
        )

        self.assertNotEqual(first.key, second.key)

    def test_sight_clip_inflight_key_uses_name_and_size_when_upload_ids_change(self):
        first = SightClip(
            scope="group",
            message_id="m-1",
            source="https://example.com/one-upload.mp4",
            file_id="upload-id-1",
            name="辛苦啦.mp4",
            metadata={"file_size": "5000457"},
        )
        second = SightClip(
            scope="group",
            message_id="m-2",
            source="https://example.com/two-upload.mp4",
            file_id="upload-id-2",
            name="辛苦啦.mp4",
            metadata={"file_size": "5000457"},
        )

        self.assertEqual(first.key, second.key)

    async def test_sight_cache_matches_resource_alias_beyond_recent_twenty(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"video_cache_max_items": 60}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive, max_items=60)
        scope = "aiocqhttp:FriendMessage:10001"
        cached_clip = SightClip(
            scope=scope,
            message_id="m-original",
            source="D:/tmp/original.mp4",
            file_id="original-file-id",
            name="辛苦啦.mp4",
            metadata={
                "content_fingerprint": "content-sha256",
                "file_size": "5000457",
            },
        )
        await runtime._sight_vault.upsert(
            SightInsight(clip=cached_clip, summary="已经理解过的视频")
        )
        for index in range(25):
            clip = SightClip(
                scope=scope,
                message_id=f"m-other-{index}",
                source=f"D:/tmp/other-{index}.mp4",
            )
            await runtime._sight_vault.upsert(
                SightInsight(clip=clip, summary=f"其他视频 {index}")
            )

        current = SightClip(
            scope=scope,
            message_id="m-current",
            file_id="changed-file-id",
            name="辛苦啦.mp4",
            metadata={"file_size": "5000457"},
        )
        result = await runtime._cached_sight_insight_for_clip(current)

        self.assertIsNotNone(result)
        self.assertEqual(result.summary, "已经理解过的视频")
        self.assertEqual(result.clip.message_id, "m-current")

    async def test_sight_resource_alias_cache_remains_isolated_by_scope(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        cached_clip = SightClip(
            scope="aiocqhttp:GroupMessage:10001",
            message_id="m-group",
            name="辛苦啦.mp4",
            metadata={"file_size": "5000457"},
        )
        await runtime._sight_vault.upsert(
            SightInsight(clip=cached_clip, summary="群聊里的视频")
        )
        private_clip = SightClip(
            scope="aiocqhttp:FriendMessage:10001",
            message_id="m-private",
            name="辛苦啦.mp4",
            metadata={"file_size": "5000457"},
        )

        result = await runtime._cached_sight_insight_for_clip(private_clip)

        self.assertIsNone(result)

    async def test_sight_cache_does_not_merge_different_content_fingerprints(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        scope = "aiocqhttp:FriendMessage:10001"
        cached_clip = SightClip(
            scope=scope,
            message_id="m-first",
            name="同名视频.mp4",
            metadata={
                "content_fingerprint": "first-content",
                "file_size": "5000457",
            },
        )
        await runtime._sight_vault.upsert(
            SightInsight(clip=cached_clip, summary="第一个视频")
        )
        different_clip = SightClip(
            scope=scope,
            message_id="m-second",
            name="同名视频.mp4",
            metadata={
                "content_fingerprint": "different-content",
                "file_size": "5000457",
            },
        )

        result = await runtime._cached_sight_insight_for_clip(different_clip)

        self.assertIsNone(result)

    async def test_life_video_understand_uses_recent_when_event_has_no_video(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-question",
        )
        video_event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin=event.unified_msg_origin,
            message_id="m-video-source",
        )
        video_event.message_items = [{"type": "video", "file": "D:/tmp/cafe.mp4"}]
        video_event.message_obj.message = video_event.message_items

        clip = runtime._sight_clips_from_event(video_event)[0]
        await runtime._sight_vault_for_runtime().upsert(
            SightInsight(
                clip=clip,
                summary="视频里是咖啡店窗边的暖光场景",
                details=["桌上有咖啡杯"],
            )
        )

        result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：视频里是咖啡店窗边的暖光场景", result)
        self.assertIn("不要因为字幕、水印、标题或画面线索再调用联网搜索", result)
        self.assertIn("出处、原视频、作者、链接", result)

    async def test_sight_note_sent_suppresses_duplicate_followup_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        event = Event(message_id="m-video-note-followup")
        result = types.SimpleNamespace(
            chain=[types.SimpleNamespace(text="我已经整理成图发给你了。")],
            result_content_type="LLM_RESULT",
        )
        event.set_result(result)

        runtime._mark_sight_note_sent(event)

        self.assertTrue(runtime.suppress_sight_note_followup(event))
        self.assertIsNone(event.get_result())

    async def test_bili_auto_summary_sends_t2i_image(self):
        provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "概述",
                                "time": "00:12",
                                "paragraphs": ["视频讲了雨夜咖啡店。"],
                                "bullets": [],
                                "quotes": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"}
        )
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-auto",
        )
        event.message_str = "https://www.bilibili.com/video/BV1aa411c7mD"
        frame_path = Path(tempfile.mkdtemp()) / "bili-frame-12.png"
        frame_path.write_bytes(b"\x89PNG\r\n\x1a\nframe")

        async def understand(event_arg, clip, force=False, purpose="chat"):
            return SightInsight(
                clip=clip,
                summary="视频讲了雨夜咖啡店",
                details=["窗边有人端起咖啡"],
                transcript="视频讲了雨夜咖啡店。",
                metadata={
                    **dict(clip.metadata),
                    "frames": [
                        {
                            "path": str(frame_path),
                            "label": "00:12",
                            "second": 12,
                            "note": "窗边有人端起咖啡",
                        }
                    ],
                },
            )

        runtime._understand_sight_clip = understand
        html_renderer.calls.clear()
        html_renderer.result = "https://example.com/bili-note.png"
        with patch(
            "core.sight.bridge.fetch_bili_metadata",
            lambda *args, **kwargs: async_return(
                BiliMetadata(
                    title="真实标题",
                    author="真实作者",
                    duration=88,
                    bvid="BV1aa411c7mD",
                    cid=100,
                )
            ),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        self.assertEqual(len(runtime.context.sent_messages), 1)
        scope, chain = runtime.context.sent_messages[0]
        self.assertEqual(scope, event.unified_msg_origin)
        self.assertEqual(
            chain.items, [{"type": "image", "url": "https://example.com/bili-note.png"}]
        )
        self.assertEqual(len(html_renderer.calls), 1)
        self.assertIn("# 真实标题 - 真实作者", html_renderer.calls[0]["text"])
        self.assertNotIn("![00:12 关键帧]", html_renderer.calls[0]["text"])
        self.assertIn("⏱ 00:12", html_renderer.calls[0]["text"])
        self.assertNotIn(str(frame_path), html_renderer.calls[0]["text"])
        self.assertIn("视频标题：\n真实标题", provider.prompts[-1])
        self.assertIn("视频作者：\n真实作者", provider.prompts[-1])
        self.assertNotIn("可引用关键帧", provider.prompts[-1])
        self.assertNotIn("Content-[", provider.prompts[-1])
        self.assertNotIn("Screenshot-[", provider.prompts[-1])
        self.assertNotIn(str(frame_path), provider.prompts[-1])

    async def test_sight_note_uses_remote_t2i_when_frame_images_are_embedded(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            Provider(), config={"t2i_strategy": "local", "t2i_active_template": "base"}
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            frame_path = Path(tmpdir) / "frame.png"
            frame_path.write_bytes(b"\x89PNG\r\n\x1a\nframe")
            html_renderer.calls.clear()
            html_renderer.result = "https://example.com/frame-note.png"

            result = await runtime._render_sight_note_image(
                "aiocqhttp:FriendMessage:10001",
                f"# 视频总结\n\n## 00:12 关键段落\n\n![00:12 关键帧]({frame_path})",
            )

        self.assertEqual(result, "https://example.com/frame-note.png")
        self.assertTrue(html_renderer.calls[0]["use_network"])
        self.assertIn("data:image/png;base64,", html_renderer.calls[0]["text"])

    async def test_sight_note_embeds_local_images_off_event_loop(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            Provider(), config={"t2i_strategy": "local", "t2i_active_template": "base"}
        )
        calls = []

        async def run_in_thread(func, *args):
            calls.append((func, args))
            return func(*args)

        html_renderer.calls.clear()
        html_renderer.result = "https://example.com/frame-note.png"
        with (
            patch(
                "core.sight.bridge.embed_local_markdown_images",
                return_value="\n\n# 视频总结\n\ndata:image/png;base64,AAAA",
            ) as embed,
            patch("core.sight.bridge.asyncio.to_thread", side_effect=run_in_thread),
        ):
            result = await runtime._render_sight_note_image(
                "aiocqhttp:FriendMessage:10001",
                "# 视频总结",
            )

        self.assertEqual(result, "https://example.com/frame-note.png")
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], embed)
        self.assertEqual(calls[0][1], ("\n\n# 视频总结",))

    async def test_bili_auto_summary_removes_unknown_author_when_author_missing(self):
        provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "概述",
                                "paragraphs": ["视频讲了雨夜咖啡店。"],
                                "bullets": [],
                                "quotes": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"}
        )
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-no-author",
        )
        event.message_str = "https://www.bilibili.com/video/BV1aa411c7mD"

        async def understand(event_arg, clip, force=False, purpose="chat"):
            return SightInsight(
                clip=clip,
                summary="视频讲了雨夜咖啡店",
                transcript="视频讲了雨夜咖啡店。",
                metadata=dict(clip.metadata),
            )

        runtime._understand_sight_clip = understand
        html_renderer.calls.clear()
        html_renderer.result = "https://example.com/bili-note.png"
        with patch(
            "core.sight.bridge.fetch_bili_metadata",
            lambda *args, **kwargs: async_return(
                BiliMetadata(title="真实标题", author="", bvid="BV1aa411c7mD")
            ),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        self.assertIn("# 真实标题", html_renderer.calls[0]["text"])
        self.assertNotIn("未知作者", html_renderer.calls[0]["text"])

    async def test_bili_auto_summary_failure_notifies_retry(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        provider = Provider(
            [
                '{"segments":[{"text":"B站视频自动总结失败：",'
                '"relation":"lead","pause":"short"},'
                '{"text":"没有抽取到可用视频画面",'
                '"relation":"continue","pause":"normal"}]}'
            ]
        )
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "sight_config": {"bili_auto_summary": True},
                "chat_style_config": {"enabled": True},
            }
        )
        runtime._semantic_segment_init_state()
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-failed",
        )
        event.message_str = "https://www.bilibili.com/video/BV1aa411c7mD"

        async def understand(event_arg, clip, force=False, purpose="chat"):
            return SightInsight(
                clip=clip,
                summary="已收到视频，但暂时没有可确认的内容信息。",
                status="failed",
                error="没有抽取到可用视频画面",
            )

        runtime._understand_sight_clip = understand
        with patch(
            "core.sight.bridge.fetch_bili_metadata",
            lambda *args, **kwargs: async_return(None),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(provider.prompts, [])
        self.assertIn(
            "B站视频自动总结失败：没有抽取到可用视频画面",
            str(runtime.context.sent_messages[0][1].items[0]),
        )
        messages = list(runtime._structured_scope_messages(event.unified_msg_origin))
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].is_bot)
        self.assertIn(
            "B站视频自动总结失败：没有抽取到可用视频画面", messages[0].content
        )

    async def test_bili_auto_summary_uses_visual_evidence_without_transcript(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "河边散步",
                                "time": "00:12",
                                "paragraphs": ["画面里有人沿着河边散步。"],
                                "evidence": ["[画面 00:12] 河边步道与散步的人"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-no-transcript",
        )
        event.message_str = "https://www.bilibili.com/video/BV1aa411c7mD"

        async def understand(event_arg, clip, force=False, purpose="chat"):
            insight = SightInsight(
                clip=clip,
                summary="画面里有人沿着河边散步。",
                frame_notes=["00:12 河边步道与散步的人"],
                metadata=dict(clip.metadata),
            )
            return await runtime._sight_vault_for_runtime().upsert(insight)

        runtime._understand_sight_clip = understand
        with (
            patch(
                "core.sight.bridge.fetch_bili_metadata",
                lambda *args, **kwargs: async_return(None),
            ),
            patch("core.sight.bridge.logger.warning") as warning,
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        recent = await runtime._sight_vault_for_runtime().recent(
            event.unified_msg_origin
        )
        self.assertEqual(len(runtime.context.sent_messages), 1)
        warning.assert_not_called()
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].summary, "画面里有人沿着河边散步。")
        self.assertEqual(recent[0].frame_notes, ["00:12 河边步道与散步的人"])

    async def test_bili_auto_summary_empty_media_defaults_to_failed_and_retryable(self):
        provider = Provider(
            [
                '{"summary":"该视频未包含任何可确认的音频、字幕或画面内容，无法获取具体信息。","details":["未检测到音频主线信息"]}',
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "概述",
                                "paragraphs": ["没有可确认内容。"],
                                "bullets": [],
                                "quotes": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"}
        )
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-empty-media",
        )
        event.message_str = "https://www.bilibili.com/video/BV1aa411c7mD"
        html_renderer.calls.clear()
        prepared_path = Path(tempfile.mkdtemp()) / "bili-empty.mp4"
        prepared_path.write_bytes(b"fake-video")

        with (
            patch(
                "core.sight.reader.transcribe_bcut",
                lambda *args, **kwargs: async_return(None),
            ),
            patch(
                "core.sight.reader.transcribe_local",
                lambda *args, **kwargs: async_return(None),
            ),
            patch(
                "core.sight.bridge.prepare_sample_video_source",
                lambda *args, **kwargs: async_return(prepared_path),
            ),
            patch(
                "core.sight.bridge.extract_video_frames",
                lambda source, cache_dir, max_frames=3, **kwargs: async_return([]),
            ),
            patch(
                "core.sight.bridge.fetch_bili_metadata",
                lambda *args, **kwargs: async_return(
                    BiliMetadata(
                        title="真实标题", author="真实作者", bvid="BV1aa411c7mD"
                    )
                ),
            ),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        recent = await runtime._sight_vault_for_runtime().recent(
            event.unified_msg_origin
        )
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertIn(
            "B站视频自动总结失败：没有抽取到可用视频画面",
            str(runtime.context.sent_messages[0][1].items[0]),
        )
        self.assertEqual(html_renderer.calls, [])
        self.assertEqual(provider.prompts, [])
        self.assertEqual(recent[0].status, "failed")
        self.assertIn("没有抽取到可用视频画面", recent[0].error)
        self.assertIsNone(
            await runtime._cached_sight_insight_for_clip(
                runtime._sight_clips_from_event(
                    event, explicit="https://www.bilibili.com/video/BV1aa411c7mD"
                )[0]
            )
        )

    async def test_bili_auto_summary_continues_without_frames_when_transcript_exists(
        self,
    ):
        provider = Provider(
            [
                '{"summary":"视频主要讲雨夜咖啡店的布置。","details":["音频里介绍了窗边灯光和咖啡"]}',
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "概述",
                                "paragraphs": ["视频主要讲雨夜咖啡店的布置。"],
                                "bullets": [],
                                "quotes": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"}
        )
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-audio-only",
        )
        event.message_str = "https://www.bilibili.com/video/BV1aa411c7mD"
        html_renderer.calls.clear()
        html_renderer.result = "https://example.com/bili-note.png"
        prepared_path = Path(tempfile.mkdtemp()) / "prepared.mp4"
        prepared_path.write_bytes(b"fake-video")

        def fail_sample(*args, **kwargs):
            raise TimeoutError()

        with (
            patch(
                "core.sight.bridge.resolve_bili_target",
                lambda *args, **kwargs: async_return(
                    BiliTarget(
                        bvid="BV1aa411c7mD",
                        url="https://www.bilibili.com/video/BV1aa411c7mD",
                    )
                ),
            ),
            patch(
                "core.sight.bridge.prepare_sample_video_source",
                lambda *args, **kwargs: async_return(prepared_path),
            ),
            patch(
                "core.sight.reader.transcribe_bcut",
                lambda *args, **kwargs: async_return(
                    TranscriptResult(
                        full_text="视频主要讲雨夜咖啡店的布置，音频里介绍了窗边灯光和咖啡。",
                        source="必剪转写",
                    )
                ),
            ),
            patch(
                "core.sight.bridge.extract_video_frames",
                fail_sample,
            ),
            patch(
                "core.sight.bridge.fetch_bili_metadata",
                lambda *args, **kwargs: async_return(
                    BiliMetadata(
                        title="真实标题", author="真实作者", bvid="BV1aa411c7mD"
                    )
                ),
            ),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        recent = await runtime._sight_vault_for_runtime().recent(
            event.unified_msg_origin
        )
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(
            runtime.context.sent_messages[0][1].items,
            [{"type": "image", "url": "https://example.com/bili-note.png"}],
        )
        self.assertEqual(recent[0].status, "ready")
        self.assertEqual(recent[0].frame_notes, [])
        self.assertIn("雨夜咖啡店", recent[0].transcript)
        self.assertEqual(recent[0].error, "")
        self.assertEqual(len(html_renderer.calls), 1)
        self.assertIn("# 真实标题 - 真实作者", html_renderer.calls[0]["text"])

    async def test_bili_auto_summary_ignores_stale_failed_cache(self):
        provider = Provider(
            [
                json.dumps(
                    {
                        "sections": [
                            {
                                "title": "概述",
                                "paragraphs": ["重新理解成功。"],
                                "bullets": [],
                                "quotes": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"}
        )
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-cache",
        )
        event.message_str = "https://www.bilibili.com/video/BV1aa411c7mD"
        stale_clip = SightInsight(
            clip=runtime._sight_clips_from_event(
                event, explicit="https://www.bilibili.com/video/BV1aa411c7mD"
            )[0],
            summary="旧失败",
            status="failed",
            error="没有抽取到可用视频画面",
        )
        await runtime._sight_vault_for_runtime().upsert(stale_clip)
        calls = []

        async def understand(event_arg, clip, force=False, purpose="chat"):
            calls.append(force)
            return SightInsight(
                clip=clip,
                summary="重新理解成功",
                details=["不复用旧失败缓存"],
                transcript="重新理解成功。",
                metadata={"title": "新结果"},
            )

        runtime._understand_sight_clip = understand
        html_renderer.calls.clear()
        html_renderer.result = "https://example.com/bili-note.png"
        with patch(
            "core.sight.bridge.fetch_bili_metadata",
            lambda *args, **kwargs: async_return(None),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        self.assertEqual(calls, [False])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(
            runtime.context.sent_messages[0][1].items,
            [{"type": "image", "url": "https://example.com/bili-note.png"}],
        )

    async def test_bili_auto_summary_reuses_ready_insight_and_note_markdown(self):
        provider = Provider([])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"}
        )
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                raise AssertionError("重复自动总结不应重新生成专业总结")

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-repeat",
        )
        event.message_str = "https://www.bilibili.com/video/BV1aa411c7mD"
        clip = SightClip(
            scope=event.unified_msg_origin,
            message_id="old-message",
            source="https://www.bilibili.com/video/BV1aa411c7mD",
            name="缓存视频",
            origin="bilibili",
            metadata={
                "title": "缓存视频",
                "professional_note_schema": PROFESSIONAL_NOTE_CACHE_SCHEMA,
                "professional_note_prompt_key": professional_note_prompt_key(),
                "notes": {"professional": "# 缓存视频\n\n## 概述\n已经总结过。"},
            },
        )
        await runtime._sight_vault_for_runtime().upsert(
            SightInsight(
                clip=clip,
                summary="已经总结过",
                transcript="已经总结过。",
                metadata=dict(clip.metadata),
            )
        )

        calls = []

        async def understand(event_arg, clip_arg, force=False, purpose="chat"):
            calls.append(force)
            return await runtime._cached_sight_insight_for_clip(clip_arg)

        runtime._understand_sight_clip = understand
        html_renderer.calls.clear()
        html_renderer.result = "https://example.com/cached-bili-note.png"
        with patch(
            "core.sight.bridge.fetch_bili_metadata",
            lambda *args, **kwargs: async_return(None),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        self.assertEqual(calls, [False])
        self.assertEqual(provider.prompts, [])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(
            runtime.context.sent_messages[0][1].items,
            [{"type": "image", "url": "https://example.com/cached-bili-note.png"}],
        )
        self.assertIn("# 缓存视频", html_renderer.calls[0]["text"])

    async def test_bili_auto_summary_rewrites_cached_note_title_from_current_metadata(
        self,
    ):
        provider = Provider([])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"}
        )
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                raise AssertionError("命中缓存时不应重新生成专业总结")

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-title-refresh",
        )
        event.message_str = "https://www.bilibili.com/video/BV1Khj96zEN3"
        cached_clip = SightClip(
            scope=event.unified_msg_origin,
            message_id="old-message",
            source="https://www.bilibili.com/video/BV1Khj96zEN3",
            name="BV1Khj96zEN3",
            origin="bilibili",
            metadata={
                "title": "BV1Khj96zEN3",
                "professional_note_schema": PROFESSIONAL_NOTE_CACHE_SCHEMA,
                "professional_note_prompt_key": professional_note_prompt_key(),
                "notes": {"professional": "# BV1Khj96zEN3\n\n## 概述\n已经总结过。"},
            },
        )
        await runtime._sight_vault_for_runtime().upsert(
            SightInsight(
                clip=cached_clip,
                summary="已经总结过",
                transcript="已经总结过。",
                metadata=dict(cached_clip.metadata),
            )
        )

        calls = []

        async def understand(event_arg, clip_arg, force=False, purpose="chat"):
            calls.append(force)
            return await runtime._cached_sight_insight_for_clip(clip_arg)

        runtime._understand_sight_clip = understand
        html_renderer.calls.clear()
        html_renderer.result = "https://example.com/title-refresh.png"
        metadata = BiliMetadata(
            title="核准追诉24人！低龄未成年人严重暴力犯罪依法追究刑责！",
            author="央视频",
            bvid="BV1Khj96zEN3",
            cid=39358696079,
        )
        with patch(
            "core.sight.bridge.fetch_bili_metadata",
            lambda *args, **kwargs: async_return(metadata),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1Khj96zEN3",
                    url="https://www.bilibili.com/video/BV1Khj96zEN3",
                ),
            )

        self.assertEqual(calls, [False])
        self.assertEqual(provider.prompts, [])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertIn(
            "# 核准追诉24人！低龄未成年人严重暴力犯罪依法追究刑责！ - 央视频",
            html_renderer.calls[0]["text"],
        )
        self.assertNotIn("# BV1Khj96zEN3", html_renderer.calls[0]["text"])

    async def test_bili_auto_summary_schedule_suppresses_default_llm(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": True}}
        )
        runtime._recalled_messages = {}
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-schedule",
        )
        event.message_str = "分享一个 https://www.bilibili.com/video/BV1bb411c7mE"

        self.assertTrue(runtime.schedule_bili_summary_from_event(event))

        self.assertTrue(event.call_llm)
        self.assertEqual(scheduled[0][0], "B站视频总结")
        scheduled[0][2].close()

    async def test_bili_auto_summary_setting_can_disable_schedule(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"bili_auto_summary": False}}
        )
        runtime._recalled_messages = {}
        runtime._schedule_background_task = lambda coro, label="", key="": True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-bili-disabled",
        )
        event.message_str = "https://www.bilibili.com/video/BV1cc411c7mF"

        self.assertFalse(runtime.schedule_bili_summary_from_event(event))

    async def test_video_understanding_total_timeout_returns_failed_insight(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"total_timeout_seconds": 60}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}

        async def prepare(event, clip):
            return {
                "source_note": "source note",
                "text_result": SightTextResult(
                    transcript="prepared transcript",
                    transcript_source="prepared source",
                ),
                "metadata": {"title": "prepared title"},
                "error": "",
                "source_path": None,
            }

        async def never_finish(event, clip, **kwargs):
            await asyncio.sleep(999)
            return SightInsight(clip=clip, summary="不会返回")

        runtime._prepare_sight_clip_material = prepare
        runtime._finalize_prepared_sight_clip = never_finish
        runtime._sight_total_timeout_seconds = lambda: 0.01
        clip = SightClip(source="D:/tmp/video.mp4")
        event = Event()

        insight = await runtime._understand_sight_clip_with_timeout(event, clip)

        self.assertEqual(insight.status, "failed")
        self.assertIn("视频理解超时", insight.error)
        self.assertFalse(event.call_llm)

    async def test_video_material_preparation_runs_audio_and_video_in_parallel(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        reader = SightReader(runtime)
        runtime._sight_reader = reader
        audio_started = asyncio.Event()
        video_started = asyncio.Event()
        source_path = runtime.data_path.parent / "video.mp4"
        source_path.write_bytes(b"video")

        async def prepare_audio(source):
            audio_started.set()
            await asyncio.wait_for(video_started.wait(), timeout=0.5)
            return None

        async def read_audio(event, clip, audio_path):
            return SightTextResult(
                transcript="并发准备完成", transcript_source="测试转写"
            )

        async def prepare_video(*args, **kwargs):
            video_started.set()
            await asyncio.wait_for(audio_started.wait(), timeout=0.5)
            return source_path

        reader.prepare_audio = prepare_audio
        reader.read_prepared_audio = read_audio
        event = Event()
        clip = SightClip(source="D:/tmp/video.mp4")

        with patch("core.sight.bridge.prepare_sample_video_source", prepare_video):
            prepared = await runtime._prepare_sight_clip_material(event, clip)

        self.assertTrue(audio_started.is_set())
        self.assertTrue(video_started.is_set())
        self.assertEqual(prepared["source_path"], source_path)
        self.assertEqual(prepared["text_result"].transcript, "并发准备完成")

    async def test_bili_material_uses_configured_audio_transcription(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        reader = SightReader(runtime)
        runtime._sight_reader = reader
        source_path = runtime.data_path.parent / "video.mp4"
        source_path.write_bytes(b"video")
        calls = []

        async def prepare_audio(source):
            calls.append(("prepare", source))
            return None

        async def read_audio(event, clip, audio_path):
            calls.append(("transcribe", audio_path))
            return SightTextResult(
                transcript="ASR 转写内容", transcript_source="本地ASR"
            )

        reader.prepare_audio = prepare_audio
        reader.read_prepared_audio = read_audio
        clip = SightClip(
            source="https://www.bilibili.com/video/BV1M5TY6tErE",
            metadata={"platform": "bilibili", "bvid": "BV1M5TY6tErE", "cid": 456},
        )

        with patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(source_path),
        ):
            prepared = await runtime._prepare_sight_clip_material(Event(), clip)

        self.assertEqual(prepared["text_result"].transcript, "ASR 转写内容")
        self.assertEqual(calls[0][0], "prepare")
        self.assertEqual(calls[1], ("transcribe", None))

    async def test_professional_evidence_path_skips_chat_brief(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}

        class Brief:
            async def summarize(self, *args, **kwargs):
                raise AssertionError("专业证据路径不应调用普通视频简介")

        runtime._sight_brief = Brief()
        clip = SightClip(source="D:/tmp/video.mp4")
        insight = await runtime._finalize_sight_insight(
            Event(),
            clip,
            source_note="测试视频",
            frame_notes=["00:03：画面里出现一张图表"],
            text_result=SightTextResult(
                transcript="讲解者说明这张图表反映了季度变化。",
                transcript_source="测试转写",
            ),
            metadata={},
            error="",
            purpose="professional",
        )

        self.assertEqual(insight.status, "ready")
        self.assertFalse(insight.metadata["brief_ready"])
        self.assertEqual(insight.note, "")

    async def test_timeout_resume_reuses_completed_visual_evidence(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}
        clip = SightClip(source="D:/tmp/video.mp4")
        source_path = runtime.data_path.parent / "prepared.mp4"
        source_path.write_bytes(b"video")
        runtime._save_sight_prepare_cache(
            clip,
            source_note="测试视频",
            source_path=str(source_path),
            frame_notes=["00:05：已经完成的画面证据"],
            text_result=SightTextResult(
                transcript="已经完成的音频证据", transcript_source="测试转写"
            ),
            metadata={"visual_complete": True},
            error="",
        )

        async def fail_extract(*args, **kwargs):
            raise AssertionError("恢复时不应重复抽帧")

        with patch("core.sight.bridge.extract_video_frames", fail_extract):
            insight = await runtime._resume_sight_summary_after_timeout(
                Event(), clip, purpose="professional"
            )

        self.assertIsNotNone(insight)
        assert insight is not None
        self.assertTrue(
            any("已经完成的画面证据" in item for item in insight.frame_notes)
        )

    async def test_video_understanding_total_timeout_resumes_prepared_summary(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"total_timeout_seconds": 60}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}
        runtime._sight_total_timeout_seconds = lambda: 0.01
        clip = SightClip(source="D:/tmp/video.mp4")
        event = Event()

        async def prepare(event_arg, clip_arg):
            text_result = SightTextResult(
                transcript="prepared transcript", transcript_source="prepared source"
            )
            runtime._save_sight_prepare_cache(
                clip_arg,
                source_note="source note",
                source_path="",
                frame_notes=["00:01 frame note"],
                text_result=text_result,
                metadata={"title": "prepared title"},
                error="",
            )
            return {
                "source_note": "source note",
                "text_result": text_result,
                "metadata": {"title": "prepared title"},
                "error": "",
                "source_path": None,
            }

        async def slow_finalize(event_arg, clip_arg, **kwargs):
            await asyncio.sleep(999)

        class Brief(SightBrief):
            async def summarize(
                self, clip_arg, *, transcript="", frame_notes=None, metadata=None
            ):
                return "resumed summary", ["resumed detail"]

        runtime._prepare_sight_clip_material = prepare
        runtime._finalize_prepared_sight_clip = slow_finalize
        runtime._sight_brief = Brief(runtime)

        insight = await runtime._understand_sight_clip_with_timeout(event, clip)

        self.assertEqual(insight.status, "ready")
        self.assertEqual(insight.note, "resumed summary")
        self.assertIn("prepared transcript", insight.transcript)
        self.assertFalse(runtime._sight_prepare_cache_path(clip).exists())

    async def test_video_understanding_total_timeout_starts_after_material_prepare(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.config = LifeSettings.from_dict(
            {"sight_config": {"total_timeout_seconds": 60}}
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}
        runtime._sight_total_timeout_seconds = lambda: 0.01
        clip = SightClip(source="D:/tmp/video.mp4")
        event = Event()
        calls: list[str] = []

        async def prepare(event_arg, clip_arg):
            calls.append("prepare:start")
            await asyncio.sleep(0.02)
            calls.append("prepare:end")
            return {
                "source_note": "source note",
                "text_result": SightTextResult(
                    transcript="prepared transcript",
                    transcript_source="prepared source",
                ),
                "metadata": {"title": "prepared title"},
                "error": "",
                "source_path": None,
            }

        async def finalize(event_arg, clip_arg, **kwargs):
            calls.append("finalize")
            return await runtime._finalize_sight_insight(
                event_arg,
                clip_arg,
                source_note=kwargs["source_note"],
                frame_notes=kwargs.get("frame_notes", []),
                text_result=kwargs["text_result"],
                metadata=kwargs["metadata"],
                error=kwargs["error"],
            )

        runtime._prepare_sight_clip_material = prepare
        runtime._finalize_prepared_sight_clip = finalize
        runtime._sight_brief = type(
            "Brief",
            (SightBrief,),
            {
                "summarize": lambda self, clip_arg, **kwargs: async_return(
                    ("prepared summary", ["prepared detail"])
                )
            },
        )(runtime)

        insight = await runtime._understand_sight_clip_with_timeout(event, clip)

        self.assertEqual(calls, ["prepare:start", "prepare:end", "finalize"])
        self.assertEqual(insight.status, "ready")
        self.assertEqual(insight.note, "prepared summary")

    async def test_bili_login_commands_are_private_only(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path("D:/tmp/daily_life.db")
        group_event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001"
        )

        login_results = []
        async for result in runtime.bili_login(group_event):
            login_results.append(result)

        self.assertEqual(login_results, ["B站登录请在私聊里使用。"])
        self.assertEqual(
            await runtime.bili_logout(group_event), "B站登录请在私聊里使用。"
        )
        self.assertEqual(
            await runtime.bili_status(group_event), "B站登录请在私聊里使用。"
        )

    async def test_life_video_understand_reuses_same_video_insight_in_scope(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        scope = "aiocqhttp:FriendMessage:10001"
        saved_event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin=scope,
            message_id="m-video-source",
        )
        saved_event.message_items = [{"type": "video", "file": "D:/tmp/reuse.mp4"}]
        saved_event.message_obj.message = saved_event.message_items
        await runtime._sight_vault_for_runtime().upsert(
            SightInsight(
                clip=runtime._sight_clips_from_event(saved_event)[0],
                summary="视频里有人在雨夜街边撑伞走过",
                details=["雨夜街边有人撑伞走过"],
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin=scope,
            message_id="m-video-current",
        )
        event.message_str = "这个视频讲什么"
        event.message_items = [{"type": "video", "file": "D:/tmp/reuse.mp4"}]
        event.message_obj.message = event.message_items

        async def fail_sample(*args, **kwargs):
            raise AssertionError("不应重复抽帧")

        prepared_path = Path(tempfile.mkdtemp()) / "reuse.mp4"
        prepared_path.write_bytes(b"fake-video")
        with (
            patch(
                "core.sight.bridge.prepare_sample_video_source",
                lambda *args, **kwargs: async_return(prepared_path),
            ),
            patch("core.sight.bridge.extract_video_frames", fail_sample),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：视频里有人在雨夜街边撑伞走过", result)

    async def test_video_sight_dedupes_concurrent_same_video_work(self):
        vision_provider = Provider(
            ['{"summary":"雨夜街边有人撑伞走过","details":["路面有积水"]}'],
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
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-dedupe",
        )
        event.message_items = [{"type": "video", "file": "D:/tmp/dedupe.mp4"}]
        event.message_obj.message = event.message_items
        clip = runtime._sight_clips_from_event(event)[0]
        calls = 0
        prepared_path = Path(tempfile.mkdtemp()) / "dedupe.mp4"
        prepared_path.write_bytes(b"fake-video")

        async def sample_once(source, cache_dir, max_frames=8, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return [Path("frame-1.jpg")]

        with (
            patch(
                "core.sight.reader.transcribe_bcut",
                lambda *args, **kwargs: async_return(None),
            ),
            patch(
                "core.sight.reader.transcribe_local",
                lambda *args, **kwargs: async_return(None),
            ),
            patch(
                "core.sight.bridge.prepare_sample_video_source",
                lambda *args, **kwargs: async_return(prepared_path),
            ),
            patch("core.sight.bridge.extract_video_frames", sample_once),
        ):
            first, second = await asyncio.gather(
                runtime._understand_sight_clip(event, clip),
                runtime._understand_sight_clip(event, clip),
            )

        self.assertEqual(calls, 1)
        self.assertEqual(first.summary, second.summary)
        self.assertIn("雨夜街边有人撑伞走过", first.summary)

    async def test_video_sight_uses_standard_text_chat_image_input(self):
        class TextVisionProvider(Provider):
            def __init__(self):
                super().__init__(
                    [
                        '{"summary":"教室里有人问班里有没有喜欢的人","details":["画面里有学生","字幕在画面下方"]}'
                    ],
                    provider_id="vision-model",
                )
                self.image_inputs = []

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
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-standard-vision",
        )
        event.message_str = "这个视频是什么？"
        event.message_items = [{"type": "video", "file": "D:/tmp/classroom.mp4"}]
        event.message_obj.message = event.message_items
        prepared_path = Path(tempfile.mkdtemp()) / "classroom.mp4"
        prepared_path.write_bytes(b"fake-video")

        with (
            patch(
                "core.sight.bridge.prepare_sample_video_source",
                lambda *args, **kwargs: async_return(prepared_path),
            ),
            patch(
                "core.sight.bridge.extract_video_frames",
                lambda source, cache_dir, max_frames=3, **kwargs: async_return(
                    [Path("frame-1.jpg")]
                ),
            ),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：教室里有人问班里有没有喜欢的人", result)
        self.assertEqual(vision_provider.image_inputs, [["frame-1.jpg"]])

    async def test_life_video_understand_fails_without_frame_notes(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(
                    lambda provider_id="": async_return(None)
                ),
                "_cleanup_conversation": staticmethod(
                    lambda session_id: async_return(None)
                ),
            },
        )()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-empty",
        )
        event.message_str = "这个视频是什么？"
        event.message_items = [{"type": "video", "file": "D:/tmp/empty.mp4"}]
        event.message_obj.message = event.message_items

        prepared_path = Path(tempfile.mkdtemp()) / "empty.mp4"
        prepared_path.write_bytes(b"fake-video")
        with (
            patch(
                "core.sight.bridge.prepare_sample_video_source",
                lambda *args, **kwargs: async_return(prepared_path),
            ),
            patch(
                "core.sight.bridge.extract_video_frames",
                lambda source, cache_dir, max_frames=3, **kwargs: async_return([]),
            ),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解失败：没有抽取到可用视频画面", result)
        recent = await runtime._sight_vault_for_runtime().recent(
            event.unified_msg_origin
        )
        self.assertEqual(recent[0].status, "failed")
        self.assertEqual(await runtime.format_recent_sight_context(event), "")

    async def test_video_sight_passes_download_limit_to_frame_sampler(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "sight_config": {
                    "video_download_max_mb": 128,
                    "video_download_timeout_seconds": 360,
                }
            }
        )
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(
                    lambda provider_id="": async_return(None)
                ),
                "_cleanup_conversation": staticmethod(
                    lambda session_id: async_return(None)
                ),
            },
        )()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-download-limit",
        )
        event.message_items = [
            {"type": "video", "file": "https://example.com/video.mp4"}
        ]
        event.message_obj.message = event.message_items
        seen: dict[str, int] = {}

        async def prepare(source, cache_dir, **kwargs):
            seen["max_video_mb"] = kwargs.get("max_video_mb")
            seen["download_timeout_seconds"] = kwargs.get("download_timeout_seconds")
            return None

        with (
            patch(
                "core.sight.reader.transcribe_bcut",
                lambda *args, **kwargs: async_return(None),
            ),
            patch(
                "core.sight.reader.transcribe_local",
                lambda *args, **kwargs: async_return(None),
            ),
            patch("core.sight.bridge.prepare_sample_video_source", prepare),
        ):
            await runtime.life_video_understand(event)

        self.assertEqual(seen["max_video_mb"], 128)
        self.assertEqual(seen["download_timeout_seconds"], 360)

    async def test_life_video_understand_uses_audio_transcript_without_frames(self):
        from core.sight import TranscriptResult, TranscriptSegment

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(
                    lambda provider_id="": async_return(None)
                ),
                "_cleanup_conversation": staticmethod(
                    lambda session_id: async_return(None)
                ),
            },
        )()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-text-reader",
        )
        event.message_str = "这个视频是什么？"
        event.message_items = [{"type": "video", "file": "D:/tmp/cat.mp4"}]
        event.message_obj.message = event.message_items

        with (
            patch(
                "core.sight.reader.transcribe_bcut",
                lambda *args, **kwargs: async_return(
                    TranscriptResult(
                        language="zh",
                        full_text="先展示了一只橘猫趴在窗边，后面有人把杯子推到镜头前。",
                        segments=(
                            TranscriptSegment(
                                start=0, end=1, text="先展示了一只橘猫趴在窗边"
                            ),
                        ),
                        metadata={"title": "雨天窗边的猫", "segments": 1},
                        source="必剪转写",
                    )
                ),
            ),
            patch(
                "core.sight.bridge.extract_video_frames",
                lambda source, cache_dir, max_frames=3, **kwargs: async_return([]),
            ),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：雨天窗边的猫：先展示了一只橘猫趴在窗边", result)
        recent = await runtime._sight_vault_for_runtime().recent(
            event.unified_msg_origin
        )
        self.assertEqual(recent[0].status, "ready")
        self.assertEqual(recent[0].transcript_source, "必剪转写")
        context = await runtime.format_recent_sight_context(event)
        self.assertIn("雨天窗边的猫", context)
        self.assertIn("先展示了一只橘猫趴在窗边", context)
        self.assertIn("不要因为字幕、水印、标题或画面线索再调用联网搜索", context)

    async def test_life_video_understand_summarizes_with_internal_model(self):
        from core.sight import TranscriptResult, TranscriptSegment

        provider = Provider(
            [
                '{"summary":"橘猫趴在窗边看雨，镜头前有人递来一只杯子。",'
                '"details":["窗边有一只橘猫","有人把杯子推到镜头前"]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": Composer._get_provider,
                "_call_llm_text": Composer._call_llm_text,
                "_cleanup_conversation": Composer._cleanup_conversation,
            },
        )()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-internal-summary",
        )
        event.message_str = "帮我看这个视频"
        event.message_items = [{"type": "video", "file": "D:/tmp/cat.mp4"}]
        event.message_obj.message = event.message_items

        with (
            patch(
                "core.sight.reader.transcribe_bcut",
                lambda *args, **kwargs: async_return(
                    TranscriptResult(
                        language="zh",
                        full_text="橘猫趴在窗边。有人把杯子推到镜头前。",
                        segments=(
                            TranscriptSegment(start=0, end=1, text="橘猫趴在窗边。"),
                        ),
                        metadata={"title": "雨天窗边的猫"},
                        source="必剪转写",
                    )
                ),
            ),
            patch(
                "core.sight.bridge.extract_video_frames",
                lambda source, cache_dir, max_frames=3, **kwargs: async_return([]),
            ),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：橘猫趴在窗边看雨", result)
        recent = await runtime._sight_vault_for_runtime().recent(
            event.unified_msg_origin
        )
        self.assertEqual(recent[0].note_source, "内置摘要")
        self.assertIn("有人把杯子推到镜头前", "；".join(recent[0].details))
        self.assertIn("音频主线", provider.prompts[0])
        self.assertIn("音频转写内容", provider.prompts[0])
        self.assertLess(
            provider.prompts[0].index("输出 JSON"),
            provider.prompts[0].index("【视频内容】"),
        )
        self.assertLess(
            provider.prompts[0].index("【视频内容】"),
            provider.prompts[0].index("音频转写内容"),
        )
        self.assertNotIn("来源信息", provider.prompts[0])
        self.assertNotIn("来源：", provider.prompts[0])

    async def test_life_video_understand_builds_audio_outline_before_fusion(self):
        from core.sight import TranscriptResult, TranscriptSegment

        provider = Provider(
            [
                '{"summary":"先讲旅行准备，后面提到目的地和集合时间。","details":["旅行准备","集合时间"]}',
                '{"summary":"音频里在讲旅行准备、目的地和集合时间，画面只是街景补充。",'
                '"details":["音频主线完整进入融合","画面只作为场景参考"]}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(
                self,
                provider_arg,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id="",
            ):
                return (
                    await provider_arg.text_chat(prompt, session_id)
                ).completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": Composer._get_provider,
                "_call_llm_text": Composer._call_llm_text,
                "_cleanup_conversation": Composer._cleanup_conversation,
            },
        )()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-audio-outline",
        )
        event.message_str = "帮我看这个视频"
        event.message_items = [{"type": "video", "file": "D:/tmp/trip.mp4"}]
        event.message_obj.message = event.message_items
        transcript = " ".join(
            [f"第{i}段讲旅行准备、目的地和集合时间" for i in range(420)]
        )

        prepared_path = Path(tempfile.mkdtemp()) / "travel.mp4"
        prepared_path.write_bytes(b"fake-video")
        with (
            patch(
                "core.sight.reader.transcribe_bcut",
                lambda *args, **kwargs: async_return(
                    TranscriptResult(
                        language="zh",
                        full_text=transcript,
                        segments=(TranscriptSegment(start=0, end=1, text="旅行准备"),),
                        source="必剪转写",
                    )
                ),
            ),
            patch(
                "core.sight.bridge.prepare_sample_video_source",
                lambda *args, **kwargs: async_return(prepared_path),
            ),
            patch(
                "core.sight.bridge.extract_video_frames",
                lambda source, cache_dir, max_frames=3, **kwargs: async_return(
                    [Path("frame-1.jpg")]
                ),
            ),
            patch.object(
                runtime,
                "_describe_sight_frames",
                lambda clip, frames: async_return((["00:01：街边有人经过"], [])),
            ),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：", result)
        self.assertIn("旅行准备", result)
        self.assertIn("目的地和集合时间", result)
        audio_prompt_index = next(
            i
            for i, prompt in enumerate(provider.prompts)
            if "请只根据视频音频转写提炼音频主线" in prompt
        )
        fusion_prompt_index = next(
            i
            for i, prompt in enumerate(provider.prompts)
            if "请把视频音频主线、转写摘录和时间线画面整理成聊天可用的视频理解结果。"
            in prompt
        )
        self.assertLess(audio_prompt_index, fusion_prompt_index)
        self.assertTrue(all("来源：" not in prompt for prompt in provider.prompts))
        self.assertTrue(all("来源信息" not in prompt for prompt in provider.prompts))
