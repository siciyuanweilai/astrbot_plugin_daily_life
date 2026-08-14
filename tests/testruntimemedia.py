import unittest

from core.runtime.spine.boot import SpineBootMixin
from runtimehelpers import (
    CORE_INTERNAL_SYSTEM_PROMPT,
    BackgroundTaskScheduler,
    Context,
    DailyLifeRuntime,
    DataManager,
    DayRecord,
    EmojiAssetRecord,
    EmotionArcRecord,
    Event,
    LifeSettings,
    LifeState,
    Path,
    Provider,
    ProviderRequest,
    RuntimeAsyncHelperMixin,
    SightClip,
    SightInsight,
    SightVault,
    TimelineItem,
    async_return,
    asyncio,
    base64,
    datetime,
    image_generation_config,
    json,
    os,
    tempfile,
    time,
    types,
)


class RuntimeMediaTest(unittest.TestCase):
    def test_hidden_context_can_include_expression_channel_when_voice_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 45,
                }
            }
        )
        data = DayRecord(
            date="2026-05-24",
            weather="测试市 晴 20°C",
            timeline=[
                TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")
            ],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertIn("<expression_channel>", text)
        self.assertIn("插件会在发送前用本地节奏算法判断是否转成语音", text)
        self.assertIn("文字始终是默认表达", text)
        self.assertIn("已收藏表情", text)
        self.assertIn("具体调用条件和参数以工具说明为准", text)
        self.assertNotIn("record_life_text_decision", text)
        self.assertNotIn("speak_life_voice", text)
        self.assertIn("[HiddenVoiceChance]", text)
        self.assertIn("[HiddenVoiceCadence]", text)
        self.assertIn("45.0%", text)

    def test_hidden_context_can_include_media_expression_when_image_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"image_generation_config": image_generation_config()}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("<expression_channel>", text)
        self.assertIn("[HiddenMediaExpression]", text)
        self.assertIn("对话意图、当下状态和表达自然度", text)
        self.assertIn("不要靠固定词触发", text)
        self.assertNotIn("关系边界", text)
        self.assertNotIn("画面尺度", text)
        self.assertNotIn("睡前、换衣", text)
        self.assertIn("已收藏表情", text)
        self.assertNotIn("[HiddenImageReference]", text)
        self.assertIn("当前图片参考状态", text)
        self.assertIn("当前消息和引用消息没有可用图片", text)
        self.assertIn("当前可用媒体表达：图片", text)
        self.assertNotIn("life_voice_generate", text)

    def test_media_expression_channel_does_not_mark_voice_switch_available(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"image_generation_config": image_generation_config()}
        )
        runtime.archive = DataManager()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        day = DayRecord(
            date="2026-05-24",
            weather="测试市 晴 20°C",
            timeline=[
                TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")
            ],
        )

        text = runtime.build_hidden_life_context(
            day,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            expression_event=event,
        )

        self.assertIn("<expression_channel>", text)
        self.assertFalse(runtime.note_voice_switch_text_result(event))

    def test_hidden_context_can_include_video_expression_when_video_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(),
                "video_generation_config": {
                    "enabled": True,
                    "api_keys": ["video-key"],
                },
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("当前可用媒体表达：图片、视频", text)
        self.assertIn("[HiddenMediaCadence]", text)

    def test_hidden_media_cadence_reports_recent_media(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"image_generation_config": image_generation_config()}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        runtime.note_life_media_sent(
            event,
            "图片",
            now=datetime.datetime.now() - datetime.timedelta(minutes=3),
        )

        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("[HiddenMediaCadence]", text)
        self.assertIn("发过图片", text)
        self.assertIn("连续 1 次", text)

    def test_hidden_expression_channel_can_be_disabled_for_text_chat(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_enabled": False,
                }
            }
        )

        text = runtime.build_hidden_expression_channel_hint(Event())

        self.assertEqual(text, "")

    def test_hidden_expression_channel_frontloads_voice_cadence(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 45,
                }
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        runtime._voice_switch_next_chain_limit = lambda: 3
        runtime._mark_voice_switch_channel(event, "语音", now=datetime.datetime.now())
        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("[HiddenVoiceCadence]", text)
        self.assertIn("可以自然接一条语音", text)
        self.assertIn("自然上限", text)

    def test_hidden_expression_channel_allows_group_and_private_when_voice_enabled(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                }
            }
        )
        first_group = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10001", group_id="10001"
        )
        second_group = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10002", group_id="10002"
        )
        first_private = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )
        second_private = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:654321", sender_id="654321"
        )

        self.assertIn(
            "<expression_channel>",
            runtime.build_hidden_expression_channel_hint(first_group),
        )
        self.assertIn(
            "<expression_channel>",
            runtime.build_hidden_expression_channel_hint(second_group),
        )
        self.assertIn(
            "<expression_channel>",
            runtime.build_hidden_expression_channel_hint(first_private),
        )
        self.assertIn(
            "<expression_channel>",
            runtime.build_hidden_expression_channel_hint(second_private),
        )

    def test_voice_switch_text_result_is_runtime_log_only(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                }
            }
        )
        runtime.archive = DataManager()
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        self.assertTrue(runtime.mark_voice_switch_available(event))
        self.assertTrue(runtime.note_voice_switch_text_result(event))
        self.assertFalse(runtime.note_voice_switch_text_result(event))
        self.assertEqual(runtime.archive.action_decisions, {})

    def test_voice_switch_text_result_does_not_log_text_decision(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 35,
                }
            }
        )
        runtime.archive = DataManager()
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        from core.runtime import messenger

        old_debug = messenger.logger.debug
        messenger.logger.debug = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
        try:
            runtime.mark_voice_switch_available(event)
            self.assertTrue(runtime.note_voice_switch_text_result(event))
        finally:
            messenger.logger.debug = old_debug

        self.assertEqual(messages, [])
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_channel"], "文字")

    def test_voice_switch_text_result_consumes_internal_reason_silently(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 35,
                }
            }
        )
        runtime.archive = DataManager()
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
        try:
            self.assertTrue(runtime.mark_voice_switch_available(event))
            runtime._voice_switch_round_store()[event.unified_msg_origin][
                "text_reason"
            ] = "我这轮内容有几句铺垫，文字更容易读清楚。"
            self.assertTrue(runtime.note_voice_switch_text_result(event))
        finally:
            messenger.logger.info = old_info

        self.assertEqual(messages, [])
        self.assertEqual(runtime.archive.action_decisions, {})

    def test_voice_switch_used_by_tool_does_not_emit_text_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                }
            }
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        self.assertTrue(runtime.mark_voice_switch_available(event))
        self.assertTrue(runtime.mark_voice_switch_used(event))
        self.assertFalse(runtime.note_voice_switch_text_result(event))


class RuntimeMediaAsyncTest(RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase):
    async def test_runtime_terminate_closes_media_tasks_before_resources(self):
        runtime = SpineBootMixin.__new__(SpineBootMixin)
        calls = []

        runtime.rhythm = types.SimpleNamespace(stop=lambda: calls.append("rhythm"))
        runtime._shutdown_chat_memory_batcher = lambda: async_return(
            calls.append("memory")
        )
        runtime._cancel_background_tasks = lambda: async_return(
            calls.append("background")
        )
        runtime.scope_state = types.SimpleNamespace(
            clear=lambda: calls.append("scope_state")
        )
        runtime._sight_flight = types.SimpleNamespace(
            close=lambda: async_return(calls.append("sight"))
        )
        runtime.weather_client = types.SimpleNamespace(
            close=lambda: async_return(calls.append("weather"))
        )
        runtime.media = types.SimpleNamespace(
            close=lambda: async_return(calls.append("media"))
        )
        runtime.close_memos_service = lambda: async_return(calls.append("memos"))
        runtime.archive = types.SimpleNamespace(close=lambda: calls.append("archive"))

        await runtime.terminate()

        self.assertEqual(
            calls,
            [
                "rhythm",
                "memory",
                "background",
                "scope_state",
                "sight",
                "weather",
                "media",
                "memos",
                "archive",
            ],
        )

    async def test_generate_life_image_asset_trusted_identity_skips_director_and_uses_reference(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        edit_calls = []
        generate_calls = []

        async def direct(event, prompt, **kwargs):
            raise AssertionError("trusted identity should skip image director")

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

        result = await runtime.generate_life_image_asset(
            None,
            "画面主体是当前角色本人，窗边晨光生活照",
            contains_character=True,
            trusted_identity=True,
            text_model="gpt-image-text",
            edit_model="gpt-image-edit",
        )

        self.assertEqual(result.path, Path("role.png"))
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [
                (
                    "画面主体是当前角色本人，窗边晨光生活照",
                    "D:/ref/role.png",
                    {
                        "preserve_reference_ratio": False,
                        "model": "gpt-image-edit",
                    },
                )
            ],
        )

    async def test_generate_life_image_asset_uses_text_model_without_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        generate_calls = []

        async def direct(event, prompt, **kwargs):
            return types.SimpleNamespace(
                contains_character=False,
                needs_character_reference=False,
            )

        class ImageService:
            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path("scene.png"))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())

        result = await runtime.generate_life_image_asset(
            None,
            "雨后街角的安静风景",
            text_model="gpt-image-text",
            edit_model="gpt-image-edit",
        )

        self.assertEqual(result.path, Path("scene.png"))
        self.assertEqual(
            generate_calls,
            [
                (
                    "雨后街角的安静风景",
                    {"model": "gpt-image-text"},
                )
            ],
        )

    async def test_edit_life_image_rewrites_policy_violation_once(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "edited.png"
        image_path.write_bytes(b"x")
        reference = Path(tempfile.mkdtemp()) / "reference.png"
        reference.write_bytes(b"ref")
        calls = []
        rewrites = []

        async def edit_image(prompt, reference_image, **kwargs):
            calls.append((prompt, reference_image, kwargs))
            if len(calls) == 1:
                raise RuntimeError(
                    'HTTP 400：{"error":{"code":"content_policy_violation"}}'
                )
            return types.SimpleNamespace(path=image_path)

        async def rewrite(event, prompt, *, reference=False):
            rewrites.append((prompt, reference))
            return "保留姿势，换成咖啡店生活照，自然生活化表达"

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(edit_image=edit_image)
        )
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(
            event, "保留姿势，换成咖啡店生活照", str(reference)
        )

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(
            calls,
            [
                (
                    "保留姿势，换成咖啡店生活照",
                    str(reference),
                    {"preserve_reference_ratio": True},
                ),
                (
                    "保留姿势，换成咖啡店生活照，自然生活化表达",
                    str(reference),
                    {"preserve_reference_ratio": True},
                ),
            ],
        )
        self.assertEqual(rewrites, [("保留姿势，换成咖啡店生活照", True)])

    async def test_edit_life_image_prefers_user_prompt_aspect_ratio(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        reference = Path(tempfile.mkdtemp()) / "reference.png"
        reference.write_bytes(b"ref")
        calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference_image, **kwargs: (
                    calls.append((prompt, reference_image, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "参考这张改成咖啡店生活照，横版 16:9"

        result = await runtime.edit_life_image(
            event, "改成咖啡店生活照", str(reference)
        )

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(
            calls,
            [
                (
                    event.message_str,
                    str(reference),
                    {"aspect_ratio": "16:9", "preserve_reference_ratio": False},
                )
            ],
        )

    async def test_recall_notice_cancels_life_image_send(self):
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
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="42"
        )
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": "42",
            "user_id": "10001",
        }

        self.assertTrue(runtime.note_recalled_message(recall_event))
        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertEqual(result, "原消息已撤回，已取消图片发送。")
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_send_with_source_event_uses_own_platform_adapter(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        event = Event(
            platform_name="weixin_oc",
            unified_msg_origin=(
                "shared-platform:FriendMessage:"
                "test-user@im.wechat"
            ),
        )
        chain = types.SimpleNamespace(items=[{"type": "image", "file": "life.png"}])

        sent = await runtime.send_message_if_not_recalled(
            event.unified_msg_origin,
            chain,
            source_event=event,
        )

        self.assertTrue(sent)
        self.assertEqual(event.sent_messages, [chain])
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_send_without_source_event_uses_unified_session(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        scope = "weixin-platform:FriendMessage:test-user@im.wechat"
        chain = types.SimpleNamespace(items=[{"type": "image", "file": "life.png"}])

        sent = await runtime.send_message_if_not_recalled(scope, chain)

        self.assertTrue(sent)
        self.assertEqual(runtime.context.sent_messages, [(scope, chain)])

    async def test_send_without_source_event_routes_duplicate_id_to_weixin(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        sent_by_weixin = []

        def metadata(platform_type):
            return types.SimpleNamespace(id="测试角色", name=platform_type)

        qq = types.SimpleNamespace(meta=lambda: metadata("aiocqhttp"))

        async def send_by_session(session, chain):
            sent_by_weixin.append((session, chain))

        weixin = types.SimpleNamespace(
            meta=lambda: metadata("weixin_oc"),
            send_by_session=send_by_session,
        )
        runtime.context.platform_manager = types.SimpleNamespace(
            get_insts=lambda: [qq, weixin]
        )
        scope = "测试角色:FriendMessage:test-user@im.wechat"
        chain = types.SimpleNamespace(items=[{"type": "image", "file": "life.png"}])

        sent = await runtime.send_message_if_not_recalled(scope, chain)

        self.assertTrue(sent)
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(sent_by_weixin), 1)
        self.assertEqual(sent_by_weixin[0][0].platform_name, "测试角色")
        self.assertEqual(sent_by_weixin[0][0].session_id, "test-user@im.wechat")
        self.assertIs(sent_by_weixin[0][1], chain)

    async def test_send_without_source_event_waits_for_weixin_adapter(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        qq_meta = types.SimpleNamespace(id="测试角色", name="aiocqhttp")
        runtime.context.platform_manager = types.SimpleNamespace(
            get_insts=lambda: [types.SimpleNamespace(meta=lambda: qq_meta)]
        )

        sent = await runtime.send_message_if_not_recalled(
            "测试角色:FriendMessage:test-user@im.wechat",
            types.SimpleNamespace(items=[{"type": "text", "text": "稍后联系"}]),
        )

        self.assertFalse(sent)
        self.assertEqual(runtime.context.sent_messages, [])

    async def test_recall_notice_clears_pending_result_and_runtime_context(self):
        runtime, _ = self._make_proactive_runtime([])
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="77",
        )
        event.message_str = "问一句问题"
        runtime.note_structured_incoming_message(event)
        runtime.note_proactive_activity(
            event, now=datetime.datetime(2026, 5, 24, 12, 0)
        )
        event.set_result(types.SimpleNamespace(chain=["准备回复"]))
        recall_event = Event(
            unified_msg_origin=event.unified_msg_origin, group_id="20001"
        )
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "group_recall",
            "group_id": "20001",
            "message_id": "77",
            "user_id": "123456",
        }

        self.assertTrue(runtime.note_recalled_message(recall_event))
        self.assertTrue(runtime.suppress_recalled_event_result(event))

        self.assertIsNone(event.get_result())
        self.assertEqual(
            list(runtime._structured_scope_messages(event.unified_msg_origin)), []
        )
        self.assertEqual(runtime._proactive_idle_candidates, {})

    async def test_recall_notice_matches_message_obj_raw_message_id(self):
        runtime, _ = self._make_proactive_runtime([])
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="")
        event.message_obj.raw_message = {"message_id": 1801792361}
        event.set_result(types.SimpleNamespace(chain=["准备回复"]))
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": 1801792361,
            "user_id": "10001",
        }

        self.assertEqual(runtime._event_message_id(event), "1801792361")
        self.assertTrue(runtime.note_recalled_message(recall_event))
        self.assertTrue(runtime.suppress_recalled_event_result(event))
        self.assertIsNone(event.get_result())

    async def test_recall_notice_stops_event_before_astrbot_history_save(self):
        runtime, _ = self._make_proactive_runtime([])
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="407090562"
        )
        event.set_result(types.SimpleNamespace(chain=["准备回复"]))
        recall_event = Event(unified_msg_origin=event.unified_msg_origin)
        recall_event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "message_id": "407090562",
            "user_id": "10001",
        }

        self.assertTrue(runtime.note_recalled_message(recall_event))
        self.assertTrue(runtime.stop_recalled_event_before_history(event))

        self.assertTrue(event.is_stopped())
        self.assertIsNone(event.get_result())

    async def test_edit_life_image_group_keeps_scene_reference_separate(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"emoji_config": {"collect_chat_emojis": True}}
        )
        runtime.archive = DataManager()
        group_calls = []

        async def resolve_reference(*args, **kwargs):
            return "D:/ref/scene.png"

        async def direct_image(*args, **kwargs):
            raise AssertionError("group image edits should not ask the director again")

        class ImageService:
            async def generate_group_image(
                self,
                prompt,
                participant_profile_ids,
                aspect_ratio="",
                **kwargs,
            ):
                group_calls.append(
                    (prompt, participant_profile_ids, aspect_ratio, kwargs)
                )
                return types.SimpleNamespace(path=Path("group-edit.png"))

        runtime._resolve_life_image_reference_async = resolve_reference
        runtime._direct_life_image_payload = direct_image
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "照着这张构图拍你和示例好友的合影"
        prompt = "保留窗边并肩坐着的构图，当前角色在左，示例好友在右"

        result = await runtime.edit_life_image(
            event,
            prompt,
            participants=["profile:friend"],
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(
            group_calls,
            [
                (
                    prompt,
                    ["profile:friend"],
                    "",
                    {"scene_reference": "D:/ref/scene.png"},
                )
            ],
        )

    async def test_media_director_marks_full_timeline_as_background(self):
        provider = Provider(
            [
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
                (
                    '{"subject":"雨天长椅上的我","scene":"街角小吃摊旁",'
                    '"composition":"半身生活照","lighting":"阴天柔光","outfit":"防雨外套和长裙",'
                    '"action":"拿着炸串看雨","weather_vibe":"细雨","mood":"慵懒满足","constraints":"真实抓拍"}'
                ),
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        runtime.archive.days[today] = DayRecord(
            date=today,
            weather="小雨 20°C",
            outfit="奶白睡裙",
            timeline=[
                TimelineItem(
                    time="13:20", activity="坐在雨边长椅吃炸串", status="慵懒满足"
                ),
                TimelineItem(
                    time="21:00", activity="洗完澡换睡裙准备睡前放松", status="困倦"
                ),
            ],
            state=LifeState(summary="坐在雨边长椅吃炸串，想吃完再溜达"),
        )
        await runtime.archive.save_emotion_arc(
            EmotionArcRecord(
                date=today,
                label="轻松但低能量",
                valence=25,
                intensity=68,
                evidence="刚恢复体力，只想慢慢吃完再走",
                influence="更适合低强度、慢节奏的画面",
                expires_at="2099-01-01 00:00:00",
            )
        )

        async def fixed_media_day():
            fixed_now = datetime.datetime.strptime(f"{today} 13:25", "%Y-%m-%d %H:%M")
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
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=Path("life.png"))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        await runtime.life_image_generate(event, "拍一张现在的生活照")

        prompt = provider.prompts[0]
        self.assertIn("当前活动：坐在雨边长椅吃炸串", prompt)
        self.assertIn("近期情绪脉络（短期状态参考）", prompt)
        self.assertIn("轻松但低能量", prompt)
        self.assertIn("更适合低强度、慢节奏的画面", prompt)
        self.assertIn("全天日程背景（连续性参考", prompt)
        self.assertLess(
            prompt.index("当前活动：坐在雨边长椅吃炸串"), prompt.index("全天日程背景")
        )
        self.assertIn("21:00 - 洗完澡换睡裙准备睡前放松", prompt)

    async def test_media_director_uses_recent_chat_as_scene_anchor(self):
        provider = Provider(
            [
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
                (
                    '{"subject":"餐桌旁的我","scene":"家里餐桌旁","composition":"随手生活照",'
                    '"lighting":"室内暖光","outfit":"居家外套","action":"把切好的水果推到镜头前",'
                    '"weather_vibe":"","mood":"自然催促","constraints":"不要回到刚进门或翻钥匙的旧场景"}'
                ),
            ]
        )
        scope = "aiocqhttp:FriendMessage:10001"
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.context.conversation_manager.current_ids[scope] = "current"
        runtime.context.conversation_manager.conversations[scope] = (
            types.SimpleNamespace(
                history=[
                    {
                        "role": "assistant",
                        "content": "快了，拐过弯就是。等会帮我拿包，我翻下钥匙。",
                    },
                    {
                        "role": "assistant",
                        "content": "水果切好了，快来吃，再不来我一个人全部解决掉。",
                    },
                    {"role": "user", "content": "拍张照看看"},
                ]
            )
        )
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
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(
                    types.SimpleNamespace(path=Path("life.png"))
                )
            )
        )
        event = Event(unified_msg_origin=scope)
        event.message_str = "拍张照看看"

        await runtime.life_image_generate(event, "拍一张现在的生活照")

        prompt = provider.prompts[0]
        self.assertIn("最近对话场景锚点", prompt)
        self.assertIn("水果切好了", prompt)
        self.assertIn("拍张照看看", prompt)
        self.assertLess(
            prompt.rindex("当前生活上下文"), prompt.rindex("最近对话场景锚点")
        )
        self.assertLess(
            prompt.rindex("最近对话场景锚点"), prompt.rindex("原始画面要求")
        )
        self.assertFalse(
            any(
                call[0] == "update_conversation"
                for call in runtime.context.conversation_manager.calls
            )
        )

    async def test_edit_life_image_uses_explicit_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(
            event, "改成咖啡店生活照", "https://example.com/ref.png"
        )

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(
            edit_calls,
            [
                (
                    "改成咖啡店生活照",
                    "https://example.com/ref.png",
                    {"preserve_reference_ratio": True},
                )
            ],
        )
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(event.sent_messages), 1)
        self.assertIn(
            {"type": "image", "file": "edited.png"},
            event.sent_messages[0].items,
        )

    async def test_edit_life_image_uses_current_message_image_when_reference_empty(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "image", "file": "D:/tmp/ref.png"}]
        event.message_obj.message = event.message_items
        runtime._life_media_last_images = {
            event.unified_msg_origin: "D:/tmp/last-generated.png"
        }

        result = await runtime.edit_life_image(
            event, "换成雨夜房间氛围", continue_last_result=True
        )

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(
            edit_calls,
            [
                (
                    "换成雨夜房间氛围",
                    "D:/tmp/ref.png",
                    {"preserve_reference_ratio": True},
                )
            ],
        )

    async def test_edit_life_image_resolves_current_image_component_path(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        resolved_path = str(Path(tempfile.mkdtemp()) / "current.png")

        class CurrentImage:
            type = "Image"

            async def convert_to_file_path(self):
                return resolved_path

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [CurrentImage()]
        event.message_obj.message = event.message_items

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(
            edit_calls,
            [("换成雨夜房间氛围", resolved_path, {"preserve_reference_ratio": True})],
        )

    async def test_edit_life_image_uses_quoted_image_when_reference_empty(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items
        runtime._life_media_last_images = {
            event.unified_msg_origin: "D:/tmp/last-generated.png"
        }

        result = await runtime.edit_life_image(
            event, "换成雨夜房间氛围", continue_last_result=True
        )

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(
            edit_calls,
            [
                (
                    "换成雨夜房间氛围",
                    "https://example.com/quoted.png",
                    {"preserve_reference_ratio": True},
                )
            ],
        )

    async def test_edit_life_image_resolves_quoted_image_component_path(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        resolved_path = str(Path(tempfile.mkdtemp()) / "quoted.png")

        class QuotedImage:
            type = "Image"

            async def convert_to_file_path(self):
                return resolved_path

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(type="reply", chain=[QuotedImage()])
        ]
        event.message_obj.message = event.message_items

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(
            edit_calls,
            [("换成雨夜房间氛围", resolved_path, {"preserve_reference_ratio": True})],
        )

    async def test_edit_life_image_requires_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertEqual(result, "请先发送或引用一张要参考的图片。")

    async def test_edit_life_image_continues_last_generated_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        with tempfile.TemporaryDirectory() as temp_dir:
            cached = Path(temp_dir) / "last-generated.png"
            cached.write_bytes(b"image")
            runtime._life_media_last_images = {event.unified_msg_origin: str(cached)}

            result = await runtime.edit_life_image(
                event,
                "不要外套",
                "https://example.com/old-reference.png",
                continue_last_result=True,
            )

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(
            edit_calls,
            [
                (
                    "不要外套",
                    str(cached),
                    {"preserve_reference_ratio": True},
                )
            ],
        )

    async def test_edit_life_image_uses_last_result_when_reference_is_omitted(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        with tempfile.TemporaryDirectory() as temp_dir:
            cached = Path(temp_dir) / "last-generated.png"
            cached.write_bytes(b"image")
            runtime._life_media_last_images = {event.unified_msg_origin: str(cached)}

            result = await runtime.edit_life_image(event, "再调亮一点")

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(edit_calls[0][1], str(cached))

    async def test_edit_life_image_replaces_expired_explicit_with_last_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        with tempfile.TemporaryDirectory() as temp_dir:
            cached = Path(temp_dir) / "last-generated.png"
            cached.write_bytes(b"image")
            expired = Path(temp_dir) / "deleted-astrbot-temp.png"
            runtime._life_media_last_images = {event.unified_msg_origin: str(cached)}

            result = await runtime.edit_life_image(event, "不要外套", str(expired))

        self.assertEqual(json.loads(result)["action"], "edit")
        self.assertEqual(edit_calls[0][1], str(cached))

    async def test_edit_life_image_does_not_reuse_another_scope_last_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        with tempfile.TemporaryDirectory() as temp_dir:
            cached = Path(temp_dir) / "other-scope.png"
            cached.write_bytes(b"image")
            runtime._life_media_last_images = {
                "aiocqhttp:GroupMessage:20002": str(cached)
            }

            result = await runtime.edit_life_image(
                event, "继续改", continue_last_result=True
            )

        self.assertEqual(result, "当前会话没有可继续修改的图片，请重新发送或引用原图。")

    async def test_edit_life_image_discards_deleted_cached_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        expired = str(Path(tempfile.mkdtemp()) / "deleted-generated.png")
        runtime._life_media_last_images = {event.unified_msg_origin: expired}

        result = await runtime.edit_life_image(
            event, "继续改", continue_last_result=True
        )

        self.assertEqual(result, "当前会话没有可继续修改的图片，请重新发送或引用原图。")
        self.assertNotIn(event.unified_msg_origin, runtime._life_media_last_images)

    async def test_edit_life_image_continuation_never_falls_back_to_generation(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        generate_calls = []
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    generate_calls.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                ),
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                ),
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(
            event,
            "继续改",
            continue_last_result=True,
            generate_without_reference=True,
        )

        self.assertEqual(result, "当前会话没有可继续修改的图片，请重新发送或引用原图。")
        self.assertEqual(generate_calls, [])
        self.assertEqual(edit_calls, [])

    async def test_edit_life_image_expired_explicit_never_falls_back_to_generation(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        generate_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    generate_calls.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        expired = str(Path(tempfile.mkdtemp()) / "deleted-astrbot-temp.png")

        result = await runtime.edit_life_image(
            event,
            "不要外套",
            expired,
            generate_without_reference=True,
        )

        self.assertEqual(result, "当前会话没有可继续修改的图片，请重新发送或引用原图。")
        self.assertEqual(generate_calls, [])

    async def test_edit_life_image_without_reference_requires_structured_route(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        generate_calls = []
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    generate_calls.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                ),
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                ),
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "窗边生活照"

        result = await runtime.edit_life_image(event, "窗边生活照")

        self.assertEqual(result, "请先发送或引用一张要参考的图片。")
        self.assertEqual(generate_calls, [])
        self.assertEqual(edit_calls, [])

    async def test_edit_life_image_can_generate_without_reference_from_structured_route(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        generate_calls = []
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    generate_calls.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("life.png")))
                ),
                edit_image=lambda prompt, reference, **kwargs: (
                    edit_calls.append((prompt, reference, kwargs))
                    or async_return(types.SimpleNamespace(path=Path("edited.png")))
                ),
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(
            event,
            "窗边生活照",
            generate_without_reference=True,
        )

        self.assertEqual(json.loads(result)["status"], "sent")
        self.assertEqual(generate_calls, [("窗边生活照", {})])
        self.assertEqual(edit_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(event.sent_messages), 1)
        self.assertIn(
            {"type": "image", "file": "life.png"},
            event.sent_messages[0].items,
        )

    async def test_life_group_video_generates_two_person_first_frame_in_background(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        root = Path(tempfile.mkdtemp())
        runtime.data_path = root / "daily_life.db"
        group_frame = root / "group-frame.png"
        group_frame.write_bytes(b"\x89PNG\r\n\x1a\ngroup")
        group_calls = []
        video_calls = []

        async def generate_group_image(
            prompt,
            participant_ids,
            aspect_ratio="",
            scene_reference="",
            reference_context="",
        ):
            group_calls.append((prompt, participant_ids, aspect_ratio, scene_reference))
            return types.SimpleNamespace(path=group_frame)

        async def generate_video(prompt, image_bytes=None, **kwargs):
            video_calls.append((prompt, image_bytes, kwargs))
            return types.SimpleNamespace(url="https://example.com/group.mp4")

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_group_image=generate_group_image,
                _load_reference_image=lambda reference: async_return(
                    (b"group-frame-bytes", "image/png")
                ),
            ),
            video=types.SimpleNamespace(generate_video=generate_video),
        )
        runtime._send_life_video_followup = lambda *args, **kwargs: async_return(False)
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        reaction_calls = []

        async def set_video_reaction(**kwargs):
            reaction_calls.append(kwargs)

        event = Event(
            bot=types.SimpleNamespace(set_msg_emoji_like=set_video_reaction),
            message_id="7302",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_obj.message_id = 7302
        reaction_tool = types.SimpleNamespace(name="life_video_generate")
        await runtime.note_tool_reaction_start(event, reaction_tool, {})

        result = await runtime.life_video_generate(
            event,
            "傍晚公园里并肩慢慢散步",
            subject_route="group",
            participants=["friend-1"],
            friend_outfit="深绿色针织开衫搭配白色长裤和棕色休闲鞋",
            friend_hair="自然黑色短发，额前轻薄碎发",
            friend_scene_category="outdoor",
            friend_style_pool="outfit_styles",
        )
        await runtime.note_tool_reaction_result(event, reaction_tool, {}, result)
        await runtime.note_tool_reaction_agent_done(event, None)

        self.assertEqual(json.loads(result)["status"], "pending")
        self.assertEqual([item["emoji_id"] for item in reaction_calls], [125])
        self.assertEqual(group_calls, [])
        self.assertEqual(video_calls, [])
        await scheduled[0][2]
        self.assertEqual([item["emoji_id"] for item in reaction_calls], [125, 79])
        self.assertEqual(group_calls[0][1], ["friend-1"])
        self.assertIn("双人视频首帧", group_calls[0][0])
        self.assertIn("人物 A 与人物 B 是两位既定且不同的人物", group_calls[0][0])
        self.assertIn("个体属性必须分别绑定到人物 A 或人物 B", group_calls[0][0])
        self.assertIn("不得把一个人的属性复制给另一个人", group_calls[0][0])
        self.assertIn("服从当前剧情和镜头设计", group_calls[0][0])
        self.assertIn("深绿色针织开衫", group_calls[0][0])
        self.assertIn("自然黑色短发", group_calls[0][0])
        self.assertNotIn("适当分开站位", group_calls[0][0])
        self.assertNotIn("减少脸部与身体互相遮挡", group_calls[0][0])
        self.assertEqual(video_calls[0][1], b"group-frame-bytes")
        self.assertIn("不得串脸、融合、增删或交换人物", video_calls[0][0])
        self.assertIn("个体属性必须分别绑定到人物 A 或人物 B", video_calls[0][0])
        self.assertIn("不得把一个人的属性复制给另一个人", video_calls[0][0])
        self.assertIn("服从当前剧情与镜头设计", video_calls[0][0])
        self.assertNotIn("动作保持低到中等幅度", video_calls[0][0])
        self.assertNotIn("避免人物交叉", video_calls[0][0])
        self.assertEqual(
            runtime._last_generated_life_image_path(event.unified_msg_origin),
            str(group_frame),
        )
        friend_look = runtime._current_friend_daily_look(
            event.unified_msg_origin, "friend-1"
        )
        self.assertIn("深绿色针织开衫", friend_look["outfit"])
        self.assertIn("自然黑色短发", friend_look["hair"])
        self.assertEqual(friend_look["scene_category"], "outdoor")
        self.assertEqual(friend_look["style_pool"], "outfit_styles")

    async def test_life_current_character_video_first_frame_keeps_directed_prompt(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        root = Path(tempfile.mkdtemp())
        frame = root / "character-frame.png"
        frame.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
        generated = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: (
                    generated.append((prompt, kwargs))
                    or async_return(types.SimpleNamespace(path=frame))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime._generate_life_video_first_frame(
            event.unified_msg_origin,
            "凌晨卧室里刚醒来的生活镜头",
            event,
        )

        self.assertEqual(result, str(frame))
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0][0], "凌晨卧室里刚醒来的生活镜头")

    async def test_life_group_video_uses_quoted_group_photo_without_friend_profile(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        loaded_refs = []
        video_calls = []

        async def fail_group_image(*args, **kwargs):
            raise AssertionError("现成合影应直接作为首帧，不应重新生成合影")

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_group_image=fail_group_image,
                _load_reference_image=lambda reference: (
                    loaded_refs.append(reference)
                    or async_return((b"quoted-group", "image/png"))
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: (
                    video_calls.append((prompt, image_bytes))
                    or async_return(
                        types.SimpleNamespace(url="https://example.com/group.mp4")
                    )
                )
            ),
        )
        runtime._send_life_video_followup = lambda *args, **kwargs: async_return(False)
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/group.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(
            event, "让这张合影轻轻动起来", subject_route="group"
        )

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(loaded_refs, ["https://example.com/group.png"])
        self.assertEqual(video_calls[0][1], b"quoted-group")
        self.assertIn("人物 A 与人物 B 是两位既定且不同的人物", video_calls[0][0])
        self.assertIn("服从当前剧情与镜头设计", video_calls[0][0])

    async def test_life_group_video_can_continue_last_image_in_current_scope(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        root = Path(tempfile.mkdtemp())
        last_group = root / "last-group.png"
        last_group.write_bytes(b"\x89PNG\r\n\x1a\nlast")
        loaded_refs = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_group_image=lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("沿用上一张合影时不应重新生成首帧")
                ),
                _load_reference_image=lambda reference: (
                    loaded_refs.append(reference)
                    or async_return((b"last-group", "image/png"))
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: async_return(
                    types.SimpleNamespace(url="https://example.com/group.mp4")
                )
            ),
        )
        runtime._send_life_video_followup = lambda *args, **kwargs: async_return(False)
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        runtime._remember_life_image_for_scope(event.unified_msg_origin, last_group)
        runtime._remember_life_image_for_scope(
            "aiocqhttp:FriendMessage:other", root / "other.png"
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )

        result = await runtime.life_video_generate(
            event,
            "继续把刚才的合影拍成视频",
            subject_route="group",
            continue_last_result=True,
        )

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(loaded_refs, [str(last_group)])

    async def test_life_group_video_does_not_reuse_last_image_without_explicit_continue(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        root = Path(tempfile.mkdtemp())
        last_group = root / "last-group.png"
        last_group.write_bytes(b"\x89PNG\r\n\x1a\nlast")
        new_group = root / "new-group.png"
        new_group.write_bytes(b"\x89PNG\r\n\x1a\nnew")
        loaded_refs = []
        group_calls = []

        async def generate_group_image(
            prompt, participants, aspect_ratio="", reference_context=""
        ):
            group_calls.append((prompt, participants, aspect_ratio))
            return types.SimpleNamespace(path=new_group)

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_group_image=generate_group_image,
                _load_reference_image=lambda reference: (
                    loaded_refs.append(reference)
                    or async_return((b"new-group", "image/png"))
                ),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: async_return(
                    types.SimpleNamespace(url="https://example.com/group.mp4")
                )
            ),
        )
        runtime._send_life_video_followup = lambda *args, **kwargs: async_return(False)
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        runtime._remember_life_image_for_scope(event.unified_msg_origin, last_group)
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )

        result = await runtime.life_video_generate(
            event,
            "一起拍个视频",
            subject_route="group",
            participants=["friend-1"],
            friend_outfit="深蓝色针织上衣搭配浅灰色休闲长裤和白色运动鞋",
            friend_hair="自然黑色短发，额前留轻薄碎发",
            friend_scene_category="public",
            friend_style_pool="outfit_styles",
        )

        self.assertEqual(json.loads(result)["status"], "pending")
        await scheduled[0][2]
        self.assertEqual(loaded_refs, [str(new_group)])
        self.assertEqual(len(group_calls), 1)
        self.assertEqual(group_calls[0][1], ["friend-1"])
        self.assertNotEqual(
            runtime._last_generated_life_image_path(event.unified_msg_origin),
            str(last_group),
        )

    async def test_life_group_video_requires_structured_look_for_new_first_frame(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append(coro) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_video_generate(
            event,
            "人物 B 穿灰色家居服，和人物 A 一起在客厅拍视频",
            subject_route="group",
            participants=["friend-1"],
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
        self.assertEqual(scheduled, [])
        self.assertFalse(
            runtime._current_friend_daily_look(event.unified_msg_origin, "friend-1")
        )

    async def test_runtime_terminate_continues_after_service_close_failure(self):
        runtime = SpineBootMixin.__new__(SpineBootMixin)
        calls = []

        async def fail_weather():
            calls.append("weather")
            raise RuntimeError("天气连接关闭失败")

        runtime.rhythm = types.SimpleNamespace(stop=lambda: calls.append("rhythm"))
        runtime._shutdown_chat_memory_batcher = lambda: async_return(
            calls.append("memory")
        )
        runtime._cancel_background_tasks = lambda: async_return(
            calls.append("background")
        )
        runtime.scope_state = types.SimpleNamespace(
            clear=lambda: calls.append("scope_state")
        )
        runtime.weather_client = types.SimpleNamespace(close=fail_weather)
        runtime.media = types.SimpleNamespace(
            close=lambda: async_return(calls.append("media"))
        )
        runtime.close_memos_service = lambda: async_return(calls.append("memos"))
        runtime.archive = types.SimpleNamespace(close=lambda: calls.append("archive"))

        await runtime.terminate()

        self.assertEqual(
            calls,
            [
                "rhythm",
                "memory",
                "background",
                "scope_state",
                "weather",
                "media",
                "memos",
                "archive",
            ],
        )

    async def test_life_group_video_rejects_multiple_friend_profiles(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append(coro) or True
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_video_generate(
            event,
            "三个人一起拍视频",
            subject_route="group",
            participants=["friend-1", "friend-2"],
        )

        self.assertEqual(result, "当前合影视频只能选择一位好友。")
        self.assertEqual(scheduled, [])

    async def test_life_group_video_failure_sends_generated_group_photo_first(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        root = Path(tempfile.mkdtemp())
        group_frame = root / "group-fallback.png"
        group_frame.write_bytes(b"\x89PNG\r\n\x1a\ngroup")

        async def fail_video(*args, **kwargs):
            raise RuntimeError("视频接口不可用")

        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_group_image=lambda *args, **kwargs: async_return(
                    types.SimpleNamespace(path=group_frame)
                ),
                _load_reference_image=lambda reference: async_return(
                    (b"group-frame", "image/png")
                ),
            ),
            video=types.SimpleNamespace(generate_video=fail_video),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        reaction_calls = []

        async def set_failed_video_reaction(**kwargs):
            reaction_calls.append(kwargs)

        event = Event(
            bot=types.SimpleNamespace(set_msg_emoji_like=set_failed_video_reaction),
            message_id="7303",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_obj.message_id = 7303
        reaction_tool = types.SimpleNamespace(name="life_video_generate")
        await runtime.note_tool_reaction_start(event, reaction_tool, {})

        result = await runtime.life_video_generate(
            event,
            "两个人在窗边挥挥手",
            subject_route="group",
            participants=["friend-1"],
            friend_outfit="米白色家居上衣搭配浅灰色休闲长裤",
            friend_hair="自然黑色短发",
            friend_scene_category="home",
            friend_style_pool="sleep_styles",
        )
        await runtime.note_tool_reaction_result(event, reaction_tool, {}, result)
        await runtime.note_tool_reaction_agent_done(event, None)
        await scheduled[0][2]

        self.assertEqual([item["emoji_id"] for item in reaction_calls], [125, 106])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(event.sent_messages), 2)
        self.assertIn(
            {"type": "image", "file": str(group_frame)},
            event.sent_messages[0].items,
        )
        self.assertEqual(
            event.sent_messages[1].items,
            ["视频没拍成，先把这张照片发你看。"],
        )

    async def test_life_voice_generate_sends_voice_message(self):
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
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append((text, emotion, emotion_category))
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "你快点睡。"
        runtime.context.config = {
            "provider_settings": {
                "identifier": True,
                "datetime_system_prompt": True,
            },
            "timezone": "Asia/Shanghai",
        }

        result = await runtime.life_voice_generate(
            event,
            "我困啦",
            emotion="困倦",
            emotion_category="neutral",
            user_requested=True,
        )

        self.assertIsNone(result)
        self.assertEqual(voice_calls, [("我困啦", "困倦", "neutral")])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(event.sent_messages), 1)
        self.assertTrue(
            any(
                getattr(item, "file", "") == "voice.mp3"
                for item in event.sent_messages[0].items
            )
        )
        history = runtime.context.conversation_manager.conversations[
            event.unified_msg_origin
        ].history
        self.assertEqual(history[-2]["role"], "user")
        self.assertEqual(
            history[-2]["content"][0], {"type": "text", "text": "你快点睡。"}
        )
        self.assertIn(
            "用户 ID：123456，昵称：平台名", history[-2]["content"][1]["text"]
        )
        self.assertIn("当前时间：", history[-2]["content"][1]["text"])
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_applies_chat_style_trace(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                },
                "chat_style_config": {
                    "casual_max_chars": 15,
                    "private_casual_max_chars": 15,
                },
            }
        )
        runtime.archive = DataManager()
        voice_calls = []
        trace_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append((text, emotion, emotion_category))
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        runtime.log_chat_style_trace = (
            lambda event, reply_text, context, changed=False: trace_calls.append(
                (reply_text, dict(context), changed)
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "付得起吗"

        result = await runtime.life_voice_generate(
            event,
            "付得起也不卖。赶紧闭眼，梦里啥都有，晚安！",
            emotion="困倦",
            emotion_category="neutral",
            user_requested=True,
        )

        self.assertIsNone(result)
        self.assertEqual(
            voice_calls[0][0], "付得起也不卖。赶紧闭眼，梦里啥都有，晚安！"
        )
        self.assertEqual(
            trace_calls[0][0], "付得起也不卖。赶紧闭眼，梦里啥都有，晚安！"
        )
        self.assertEqual(trace_calls[0][1], {"scope": "private"})
        self.assertFalse(trace_calls[0][2])
        self._assert_last_assistant_history(
            runtime,
            event.unified_msg_origin,
            "付得起也不卖。赶紧闭眼，梦里啥都有，晚安！",
        )

    async def test_life_voice_generate_does_not_duplicate_existing_user_history(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        scope = "aiocqhttp:FriendMessage:10001"
        runtime.context.conversation_manager.conversations[scope] = (
            types.SimpleNamespace(history=[{"role": "user", "content": "你快点睡。"}])
        )
        runtime.context.conversation_manager.current_ids[scope] = "current"
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(
                    types.SimpleNamespace(path=Path("voice.mp3"))
                )
            )
        )
        event = Event(unified_msg_origin=scope)
        event.message_str = "你快点睡。"

        result = await runtime.life_voice_generate(
            event,
            "我困啦",
            emotion="困倦",
            emotion_category="neutral",
            user_requested=True,
        )

        self.assertIsNone(result)
        history = runtime.context.conversation_manager.conversations[scope].history
        self.assertEqual(
            history,
            [
                {"role": "user", "content": "你快点睡。"},
                {"role": "assistant", "content": "我困啦"},
            ],
        )

    async def test_life_voice_generate_enriches_existing_user_image_history(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        scope = "aiocqhttp:FriendMessage:10001"
        runtime.context.conversation_manager.conversations[scope] = (
            types.SimpleNamespace(history=[{"role": "user", "content": "你看看这张"}])
        )
        runtime.context.conversation_manager.current_ids[scope] = "current"
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 100,
                }
            }
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(
                    types.SimpleNamespace(path=Path("voice.mp3"))
                )
            )
        )
        image_file = Path(tempfile.mkdtemp()) / "current.png"
        image_file.write_bytes(b"\x89PNG\r\n\x1a\ncurrent")
        image_path = str(image_file)
        event = Event(unified_msg_origin=scope)
        event.message_str = "你看看这张"
        event.message_items = [{"type": "image", "file": image_path}]
        event.message_obj.message = event.message_items

        result = await runtime.life_voice_generate(
            event,
            "看到了",
            emotion="轻松",
            emotion_category="happy",
            user_requested=True,
        )

        self.assertIsNone(result)
        history = runtime.context.conversation_manager.conversations[scope].history
        self.assertEqual(len(history), 2)
        self._assert_user_history_has_image(history[0], image_path)
        self.assertEqual(
            history[0]["content"][0], {"type": "text", "text": "你看看这张"}
        )
        self.assertEqual(history[1], {"role": "assistant", "content": "看到了"})

    async def test_life_voice_generate_suppresses_normal_success_summary_log(self):
        messages = []
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
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(
                    types.SimpleNamespace(path=Path("voice.mp3"))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
        try:
            await runtime.life_voice_generate(
                event,
                "我困啦",
                emotion="困倦",
                user_requested=True,
                decision_reason="这句更适合小声说出来。",
            )
        finally:
            messenger.logger.info = old_info

        self.assertFalse(any("语音智能切换裁定：语音" in item for item in messages))
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_resolves_agent_context_event(self):
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
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(
                    types.SimpleNamespace(path=Path("voice.mp3"))
                )
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        wrapped_event = types.SimpleNamespace(
            context=types.SimpleNamespace(event=event)
        )

        result = await runtime.life_voice_generate(
            wrapped_event,
            "我困啦",
            emotion="困倦",
            user_requested=True,
        )

        self.assertIsNone(result)
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")

    async def test_life_voice_generate_sends_when_voice_enabled(self):
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
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append(text)
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        result = await runtime.life_voice_generate(
            event,
            "我困啦",
            emotion="困倦",
            user_requested=True,
        )

        self.assertIsNone(result)
        self.assertEqual(voice_calls, ["我困啦"])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(event.sent_messages), 1)
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_logs_disabled_reason(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": False,
                }
            }
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(
                    types.SimpleNamespace(path=Path("voice.mp3"))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        from core.runtime import messenger

        old_debug = messenger.logger.debug
        messenger.logger.debug = lambda message, *args, **kwargs: messages.append(
            str(message)
        )
        try:
            await runtime.life_voice_generate(
                event,
                "我困啦",
                emotion="困倦",
                user_requested=True,
            )
        finally:
            messenger.logger.debug = old_debug

        self.assertTrue(any("语音请求裁定：文字" in item for item in messages))
        self.assertTrue(any("结果：被拦截" in item for item in messages))
        self.assertTrue(any("原因：语音生成未启用" in item for item in messages))

    async def test_life_voice_generate_rejects_non_explicit_request(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_enabled": True,
                }
            }
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append(text)
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIn("用户没有明确要求语音", result)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_allows_explicit_user_request_when_auto_switch_disabled(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_enabled": False,
                }
            }
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append((text, emotion, emotion_category))
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        result = await runtime.life_voice_generate(
            event, "我困啦", emotion="困倦", user_requested=True
        )

        self.assertIsNone(result)
        self.assertEqual(voice_calls, [("我困啦", "困倦", "")])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(event.sent_messages), 1)
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_non_explicit_request_does_not_use_voice_chain(
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
        runtime.archive = DataManager()
        voice_calls = []
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
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )
        runtime._mark_voice_switch_channel(event, "语音")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIn("用户没有明确要求语音", result)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_channel"], "文字")

    async def test_life_voice_generate_non_explicit_link_stays_text(self):
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
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append(text)
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )

        result = await runtime.life_voice_generate(
            event,
            "第三条是这个：https://example.com/news",
            emotion="轻松",
        )

        self.assertIn("用户没有明确要求语音", result)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    async def test_life_voice_generate_user_request_bypasses_cadence_gate(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "smart_switch_probability": 0,
                }
            }
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append(text)
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456"
        )
        runtime._mark_voice_switch_channel(event, "语音")

        result = await runtime.life_voice_generate(
            event, "我困啦", emotion="困倦", user_requested=True
        )

        self.assertIsNone(result)
        self.assertEqual(voice_calls, ["我困啦"])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(event.sent_messages), 1)
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")

    async def test_proactive_voice_probability_can_skip_voice(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                    "proactive_enabled": True,
                    "proactive_probability": 0,
                    "api_key": "sf-key",
                    "voice": "voice-1",
                }
            }
        )
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": (
                    voice_calls.append(text)
                    or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
                )
            )
        )
        runtime.archive = DataManager()
        runtime._apply_proactive_send_timing = lambda payload: async_return(None)
        runtime._send_proactive_emoji_if_needed = lambda scope, payload: async_return(
            None
        )
        runtime._mark_failed_proactive_contact = lambda *args, **kwargs: async_return(
            None
        )

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "我困啦",
            "闲时回复发送失败",
        )

        self.assertTrue(sent)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["我困啦"])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])

    def test_proactive_voice_probability_boundaries(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        self.assertFalse(
            runtime._proactive_voice_probability_hit(
                types.SimpleNamespace(proactive_probability=0)
            )
        )
        self.assertTrue(
            runtime._proactive_voice_probability_hit(
                types.SimpleNamespace(proactive_probability=100)
            )
        )
        self.assertTrue(
            runtime._proactive_voice_probability_hit(
                types.SimpleNamespace(proactive_probability="bad")
            )
        )

    async def test_collects_emoji_assets_and_uses_vision_provider(self):
        memory_provider = Provider([], provider_id="memory-model")
        vision_provider = Provider(
            [
                '{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇","围观"],"status":"ready"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            memory_provider,
            providers={
                "memory-model": memory_provider,
                "vision-model": vision_provider,
            },
        )
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"provider": "memory-model"},
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
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

        def run_now(coro, label="", key=""):
            scheduled.append((label, key, coro))
            return True

        runtime._schedule_background_task = run_now
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展群",
            message_id="m-img",
        )
        event.message_items = [
            {"type": "mface", "data": {"url": "https://example.com/peek.png"}}
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
            sender_name="阿林",
        )
        self.assertEqual(len(scheduled), 1)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(10, status="ready")
        self.assertEqual(assets[0].label, "探头")
        self.assertEqual(assets[0].description, "适合轻轻围观的小表情")
        self.assertEqual(assets[0].source_scope, "20001")
        self.assertEqual(assets[0].source_message_id, "m-img")
        self.assertEqual(memory_provider.vision_prompts, [])
        self.assertEqual(
            vision_provider.vision_prompts[0]["image"], "https://example.com/peek.png"
        )

    def test_visual_context_summary_keeps_maximum_meme_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        raw_summary = (
            "画面中是一个蓝发Q版女仆装的动漫角色（带有鲸鱼尾巴和耳朵），她委屈地含泪跪坐在地上，"
            "面前放着一个空碗。左侧有一只手指着她，上方气泡文字写着“你这个白吃token的大肥鱼”，"
            "她则微弱地辩解“我不是大肥鱼……”。后面是补充说明，用来验证过长内容会被直接按长度收束。"
            + "额外描述。"
            * 20
        )
        summary = runtime._visual_context_summary_from_payload({"summary": raw_summary})
        expected_prefix = " ".join(raw_summary.split())[
            : runtime.VISUAL_CONTEXT_SUMMARY_MAX_CHARS
        ].rstrip()

        self.assertIn("我不是大肥鱼", summary)
        self.assertFalse(summary.endswith("我不"))
        self.assertEqual(summary, f"{expected_prefix}...")
        self.assertLessEqual(len(summary), runtime.VISUAL_CONTEXT_SUMMARY_MAX_CHARS + 3)

    async def test_collects_emoji_assets_uses_standard_text_chat_image_urls(self):
        class TextVisionProvider(Provider):
            def __init__(self):
                super().__init__(
                    [
                        '{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇"],"status":"ready"}'
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
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
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-text-vision",
        )
        event.message_items = [
            {"type": "mface", "data": {"url": "https://example.com/text-vision.png"}}
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(10, status="ready")
        self.assertEqual(assets[0].label, "探头")
        self.assertEqual(
            vision_provider.image_inputs, [["https://example.com/text-vision.png"]]
        )
        self.assertEqual(vision_provider.legacy_inputs, [])

    async def test_collects_emoji_assets_resolves_image_component_path(self):
        memory_provider = Provider([], provider_id="memory-model")
        vision_provider = Provider(
            [
                '{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇"],"status":"ready"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            memory_provider,
            providers={
                "memory-model": memory_provider,
                "vision-model": vision_provider,
            },
        )
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"provider": "memory-model"},
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
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

        class StickerImage:
            type = "mface"
            file_id = "sticker-file"

            async def convert_to_file_path(self):
                return str(image_path)

        image_path = Path(tempfile.mkdtemp()) / "sticker.png"
        image_path.write_bytes(b"sticker-image")
        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-path",
        )
        event.message_items = [StickerImage()]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(
            event, now=datetime.datetime(2026, 5, 24, 12, 0), sender_name="阿林"
        )
        self.assertEqual(len(scheduled), 1)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(10, status="ready")
        self.assertEqual(assets[0].file_path, str(image_path))
        self.assertEqual(vision_provider.vision_prompts[0]["image"], str(image_path))

    async def test_plain_image_does_not_enter_emoji_assets(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
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
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展群",
            message_id="m-plain-img",
        )
        event.message_items = [
            {"type": "image", "url": "https://example.com/photo.png"}
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)

        self.assertEqual(scheduled, [])
        self.assertEqual(await runtime.archive.get_emoji_assets(10), [])

    async def test_review_image_is_rejected_when_vision_does_not_confirm_emoji(self):
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "summary": "普通照片",
                        "is_emoji_asset": False,
                        "label": "照片",
                        "description": "普通图片",
                        "emotions": ["日常"],
                        "sendable": False,
                        "confidence": 0.2,
                        "rejected_reason": "不是表情或贴纸",
                        "status": "rejected",
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
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
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-review-img",
        )
        event.message_items = [
            {
                "type": "image",
                "data": {
                    "raw_type": "sticker_image",
                    "url": "https://example.com/review.png",
                },
            }
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        self.assertEqual(len(scheduled), 1)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(assets[0].source_kind, "review")
        self.assertEqual(assets[0].status, "rejected")
        self.assertFalse(assets[0].sendable)
        self.assertEqual(
            await runtime.archive.get_emoji_assets(
                10, status="ready", sendable_only=True
            ),
            [],
        )

    async def test_review_image_becomes_sendable_after_vision_confirmation(self):
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "summary": "小人探头",
                        "is_emoji_asset": True,
                        "asset_type": "sticker",
                        "label": "探头",
                        "description": "适合轻轻围观",
                        "emotion_category": "neutral",
                        "emotions": ["好奇", "围观"],
                        "sendable": True,
                        "standalone_reaction": True,
                        "context_dependent": False,
                        "information_dominant": False,
                        "confidence": 0.86,
                        "status": "ready",
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
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
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-review-ok",
        )
        event.message_items = [
            {
                "type": "image",
                "data": {
                    "raw_type": "sticker_image",
                    "url": "https://example.com/review-ok.png",
                },
            }
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(
            10, status="ready", sendable_only=True
        )
        self.assertEqual(assets[0].source_kind, "verified")
        self.assertEqual(assets[0].asset_type, "sticker")
        self.assertAlmostEqual(assets[0].confidence, 0.86)
        self.assertTrue(assets[0].sendable)

    def test_plain_image_emoji_review_requires_independent_reaction(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        asset = EmojiAssetRecord(source_kind="review", status="reviewing")
        accepted = {
            "is_emoji_asset": True,
            "asset_type": "sticker",
            "label": "探头",
            "emotions": ["好奇"],
            "sendable": True,
            "standalone_reaction": True,
            "context_dependent": False,
            "information_dominant": False,
            "confidence": 0.85,
            "status": "ready",
        }

        self.assertEqual(runtime._emoji_asset_review_status(asset, accepted), "ready")
        rejected_cases = {
            "新闻画面": {"information_dominant": True},
            "依赖原始语境": {"context_dependent": True},
            "不能独立表达反应": {"standalone_reaction": False},
            "普通图片类型": {"asset_type": "other"},
            "置信度不足": {"confidence": 0.84},
        }
        for label, changes in rejected_cases.items():
            with self.subTest(label=label):
                payload = {**accepted, **changes}
                self.assertEqual(
                    runtime._emoji_asset_review_status(asset, payload), "rejected"
                )

    def test_trusted_emoji_does_not_require_plain_image_review_fields(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        asset = EmojiAssetRecord(source_kind="trusted", status="reviewing")
        payload = {
            "is_emoji_asset": True,
            "asset_type": "sticker",
            "label": "挥手",
            "emotions": ["打招呼"],
            "sendable": True,
            "status": "ready",
        }

        self.assertEqual(runtime._emoji_asset_review_status(asset, payload), "ready")

    async def test_plain_image_confirmed_by_vision_enters_emoji_assets(self):
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "summary": "小人探头看热闹",
                        "is_emoji_asset": True,
                        "asset_type": "sticker",
                        "label": "探头",
                        "description": "适合轻轻围观",
                        "emotion_category": "neutral",
                        "emotions": ["好奇", "围观"],
                        "sendable": True,
                        "standalone_reaction": True,
                        "context_dependent": False,
                        "information_dominant": False,
                        "confidence": 0.91,
                        "status": "ready",
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
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

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "incoming.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\nplain-image")

            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="m-private-plain-emoji",
            )
            event.message_str = "这个表情好欠"
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            await scheduled[0][2]

            assets = await runtime.archive.get_emoji_assets(
                10, status="ready", sendable_only=True
            )
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(cached_path.parent, tmp_root / "emoji")
            self.assertTrue(cached_path.is_file())
            self.assertEqual(assets[0].source_kind, "verified")
            self.assertEqual(assets[0].asset_type, "sticker")
            self.assertEqual(assets[0].label, "探头")
            self.assertTrue(assets[0].sendable)
            self.assertAlmostEqual(assets[0].confidence, 0.91)
            self.assertEqual(
                vision_provider.vision_prompts[0]["image"], str(cached_path)
            )

    async def test_plain_image_emoji_collection_respects_disabled_auto_collect(self):
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "summary": "小人探头看热闹",
                        "is_emoji_asset": True,
                        "asset_type": "sticker",
                        "label": "探头",
                        "description": "适合轻轻围观",
                        "emotion_category": "neutral",
                        "emotions": ["好奇", "围观"],
                        "sendable": True,
                        "standalone_reaction": True,
                        "context_dependent": False,
                        "information_dominant": False,
                        "confidence": 0.91,
                        "status": "ready",
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": False},
            }
        )
        runtime.archive = DataManager()

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

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "incoming.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\nplain-image")
            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="m-private-plain-emoji-disabled",
            )
            event.message_str = "这个表情好欠"
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            await scheduled[0][2]

            context = runtime.format_structured_message_context(event)
            self.assertIn("这个表情好欠 [图片：小人探头看热闹]", context)
            self.assertEqual(await runtime.archive.get_emoji_assets(10), [])

    async def test_group_plain_image_confirmed_by_vision_uses_stable_cache(self):
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "summary": "Q版角色在收银台前啃巧克力",
                        "is_emoji_asset": True,
                        "asset_type": "meme",
                        "label": "吃货满足",
                        "description": "适合表达吃东西或满足",
                        "emotion_category": "happy",
                        "emotions": ["开心", "满足", "想吃"],
                        "sendable": True,
                        "standalone_reaction": True,
                        "context_dependent": False,
                        "information_dominant": False,
                        "confidence": 0.95,
                        "status": "ready",
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
        )
        runtime.archive = DataManager()

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

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "group-image.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\ngroup-image")

            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:GroupMessage:20001",
                group_id="20001",
                group_name="测试",
                message_id="m-group-plain-emoji",
            )
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            self.assertEqual(scheduled[0][0], "图片上下文识别")
            await scheduled[0][2]

            assets = await runtime.archive.get_emoji_assets(
                10, status="ready", sendable_only=True
            )
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(cached_path.parent, tmp_root / "emoji")
            self.assertEqual(cached_path.read_bytes(), source_path.read_bytes())
            self.assertEqual(
                vision_provider.vision_prompts[0]["image"], str(cached_path)
            )
            self.assertEqual(assets[0].source_scope, "aiocqhttp:GroupMessage:20001")
            self.assertEqual(assets[0].source_message_id, "m-group-plain-emoji")

    async def test_plain_image_emoji_cache_tries_alternate_media_sources(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.config = LifeSettings.from_dict(
            {"emoji_config": {"collect_chat_emojis": True}}
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "incoming-file.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\nfallback-image")

            await runtime._save_plain_image_emoji_candidate(
                {
                    "is_emoji_asset": True,
                    "asset_type": "sticker",
                    "label": "贴贴",
                    "description": "适合表达亲近",
                    "emotion_category": "happy",
                    "emotions": ["亲近", "可爱"],
                    "sendable": True,
                    "standalone_reaction": True,
                    "context_dependent": False,
                    "information_dominant": False,
                    "confidence": 0.95,
                    "status": "ready",
                },
                image=str(tmp_root / "missing-primary.png"),
                fingerprint="plain-image-fallback",
                context_scope="aiocqhttp:FriendMessage:10001",
                context_message_key="m-fallback-image",
                cache_sources=[str(source_path)],
            )

            assets = await runtime.archive.get_emoji_assets(
                10, status="ready", sendable_only=True
            )
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(cached_path.parent, tmp_root / "emoji")
            self.assertEqual(cached_path.read_bytes(), source_path.read_bytes())
            self.assertEqual(assets[0].label, "贴贴")

    async def test_text_with_plain_image_confirmed_as_emoji_caches_inline_source(self):
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "summary": "粉发动漫女孩半眯着眼嫌弃",
                        "is_emoji_asset": True,
                        "asset_type": "meme",
                        "label": "嫌弃",
                        "description": "适合表达嫌弃和调侃",
                        "emotion_category": "angry",
                        "emotions": ["嫌弃", "无语"],
                        "sendable": True,
                        "standalone_reaction": True,
                        "context_dependent": False,
                        "information_dominant": False,
                        "confidence": 0.95,
                        "status": "ready",
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
        )
        runtime.archive = DataManager()

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

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            image_data = b"\x89PNG\r\n\x1a\ntext-with-image"
            image_source = (
                f"data:image/png;base64,{base64.b64encode(image_data).decode()}"
            )
            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="m-text-with-image-emoji",
            )
            event.message_str = "脸皮比墙还厚"
            event.message_items = [
                {"type": "text", "data": {"text": event.message_str}},
                {
                    "type": "image",
                    "data": {
                        "image": image_source,
                        "file_unique_id": "inline-emoji-source",
                    },
                },
            ]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            await scheduled[0][2]

            assets = await runtime.archive.get_emoji_assets(
                10, status="ready", sendable_only=True
            )
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(cached_path.parent, tmp_root / "emoji")
            self.assertEqual(cached_path.read_bytes(), image_data)
            self.assertEqual(assets[0].label, "嫌弃")
            self.assertEqual(
                vision_provider.vision_prompts[0]["image"], str(cached_path)
            )

    async def test_plain_image_vision_uses_readable_alternate_media_source(self):
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "summary": "两个人贴贴",
                        "is_emoji_asset": True,
                        "asset_type": "emoji",
                        "label": "贴贴",
                        "description": "适合表达亲近",
                        "emotion_category": "happy",
                        "emotions": ["亲近", "可爱"],
                        "sendable": True,
                        "standalone_reaction": True,
                        "context_dependent": False,
                        "information_dominant": False,
                        "confidence": 0.95,
                        "status": "ready",
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
        )
        runtime.archive = DataManager()

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

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "incoming-file.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\nreadable-file")

            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="m-readable-alternate",
            )
            event.message_items = [
                {
                    "type": "image",
                    "hash": "readable-alternate-hash",
                    "path": str(tmp_root / "missing-primary.png"),
                    "file": str(source_path),
                }
            ]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            await scheduled[0][2]

            assets = await runtime.archive.get_emoji_assets(
                10, status="ready", sendable_only=True
            )
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(
                vision_provider.vision_prompts[0]["image"], str(cached_path)
            )
            self.assertEqual(cached_path.read_bytes(), source_path.read_bytes())

    async def test_plain_image_confirmed_by_vision_needs_local_emoji_cache(self):
        vision_provider = Provider(
            [
                json.dumps(
                    {
                        "summary": "小人得意指向文字",
                        "is_emoji_asset": True,
                        "asset_type": "sticker",
                        "label": "最后还得找我",
                        "description": "适合得意吐槽",
                        "emotion_category": "happy",
                        "emotions": ["得意", "吐槽"],
                        "sendable": True,
                        "standalone_reaction": True,
                        "context_dependent": False,
                        "information_dominant": False,
                        "confidence": 0.93,
                        "status": "ready",
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
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
        )
        runtime.archive = DataManager()

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

        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "incoming.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\nplain-image")
            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="m-private-plain-emoji-no-cache",
            )
            event.message_str = "这个表情能收吗"
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            await scheduled[0][2]

            context = runtime.format_structured_message_context(event)
            self.assertIn("这个表情能收吗 [图片]", context)
            self.assertEqual(vision_provider.vision_prompts, [])
            self.assertEqual(await runtime.archive.get_emoji_assets(10), [])

    async def test_visual_media_prepare_preserves_temporary_gif_before_cleanup(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "media_image_temporary.gif"
            image_data = b"GIF89a-temporary-image"
            source_path.write_bytes(image_data)
            event = Event(
                unified_msg_origin="aiocqhttp:GroupMessage:20001",
                group_id="20001",
                message_id="m-temporary-gif",
            )
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            prepared = await runtime.prepare_visual_media_from_event(event)
            source_path.unlink()

            self.assertTrue(prepared)
            entries = event._daily_life_prepared_visual_media
            self.assertEqual(len(entries), 1)
            cached_path = Path(entries[0]["path"])
            self.assertEqual(cached_path.suffix, ".gif")
            self.assertEqual(cached_path.read_bytes(), image_data)

    async def test_visual_media_prepare_timeout_does_not_hold_message_pipeline(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime._VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS = 0.01

        async def slow_prepare(_event):
            await asyncio.sleep(1)
            return []

        runtime._build_prepared_visual_media = slow_prepare
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-slow-image",
        )
        event.message_items = [{"type": "image", "url": "https://example.com/a.png"}]
        event.message_obj.message = event.message_items

        prepared = await runtime.prepare_visual_media_from_event(event)

        self.assertFalse(prepared)
        self.assertEqual(event._daily_life_prepared_visual_media, [])

    async def test_visual_context_uses_prepared_copy_after_event_temp_cleanup(self):
        vision_provider = Provider(
            [
                '{"summary":"动图里有人挥手打招呼","is_emoji_asset":false,"status":"ready"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
        )
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return (
                    runtime.context.providers.get(provider_id)
                    or runtime.context.get_using_provider()
                )

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "media_image_temporary.gif"
            source_path.write_bytes(b"GIF89a-vision-image")
            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="m-prepared-vision",
            )
            event.message_str = "看看这个"
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            await runtime.prepare_visual_media_from_event(event)
            cached_path = Path(event._daily_life_prepared_visual_media[0]["path"])
            source_path.unlink()
            await runtime._collect_visual_context_background(event)

            context = runtime.format_structured_message_context(event)
            self.assertIn("看看这个 [图片：动图里有人挥手打招呼]", context)
            self.assertEqual(
                vision_provider.vision_prompts[0]["image"], str(cached_path)
            )
            self.assertTrue(cached_path.is_file())

    async def test_gif_visual_bridge_replaces_provider_image_with_text_summary(self):
        vision_provider = Provider(
            ['{"summary":"小猫伸出双爪比心","is_emoji_asset":true,"status":"ready"}'],
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

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id)

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "incoming-animation.jpg"
            source_path.write_bytes(b"GIF89a-animated-image")
            event = Event(
                sender_name="测试用户",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="m-gif-bridge",
            )
            event.message_str = "看看这个"
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            await runtime.prepare_visual_media_from_event(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            provider_copy = tmp_root / "provider-copy.png"
            provider_copy.write_bytes(b"\x89PNG\r\n\x1a\nconverted-first-frame")
            request = ProviderRequest(
                prompt="<attachment>",
                image_urls=[str(provider_copy)],
                extra_user_content_parts=[
                    types.SimpleNamespace(
                        type="text",
                        text=f"[Image Attachment: path {provider_copy}]",
                    )
                ],
            )
            visual_task = asyncio.create_task(scheduled[0][2])

            bridged = await runtime.bridge_animated_visual_for_llm_request(
                event, request
            )
            await visual_task

            self.assertTrue(bridged)
            self.assertEqual(request.image_urls, [])
            texts = [
                str(getattr(part, "text", ""))
                for part in request.extra_user_content_parts
            ]
            self.assertEqual(len(texts), 1)
            self.assertIn("小猫伸出双爪比心", texts[0])
            self.assertNotIn("Image Attachment", texts[0])
            self.assertEqual(len(vision_provider.vision_prompts), 1)

    async def test_static_image_does_not_use_gif_visual_bridge(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "incoming.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\nplain-image")
            event = Event(message_id="m-static-bridge")
            event.message_items = [{"type": "image", "path": str(source_path)}]
            event.message_obj.message = event.message_items
            await runtime.prepare_visual_media_from_event(event)
            request = ProviderRequest(image_urls=[str(source_path)])

            bridged = await runtime.bridge_animated_visual_for_llm_request(
                event, request
            )

            self.assertFalse(bridged)
            self.assertEqual(request.image_urls, [str(source_path)])

    async def test_file_component_uses_async_get_file_without_reading_file_property(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        video_path = str(Path(tempfile.mkdtemp()) / "async-video.mp4")

        class AsyncFileVideo:
            type = "Video"
            file_ = ""
            url = "https://example.com/async-video.mp4"
            name = "async-video.mp4"

            def __init__(self):
                self.get_file_calls = 0

            @property
            def file(self):
                raise AssertionError("异步上下文不应读取 .file")

            async def get_file(self):
                self.get_file_calls += 1
                return video_path

        component = AsyncFileVideo()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            message_id="m-async-file-video",
        )
        event.message_items = [component]
        event.message_obj.message = event.message_items

        sync_clips = runtime._sight_clips_from_event(event)
        async_clips = await runtime._sight_clips_from_event_async(event)
        media_payload = await runtime._media_payload_from_item_async(component)

        self.assertEqual(sync_clips[0].source, component.url)
        self.assertEqual(len(async_clips), 1)
        self.assertEqual(async_clips[0].source, video_path)
        self.assertEqual(media_payload["path"], video_path)
        self.assertEqual(component.get_file_calls, 2)

    def test_group_upload_id_is_restored_from_raw_file_message_metadata(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._sight_upload_ids = {}
        notice = Event(
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
        )
        notice.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "group_upload",
            "group_id": 20001,
            "user_id": 10001,
            "file": {
                "id": "68395339a5caab4c3ce9167110fd2eff",
                "name": "same-video.mp4",
                "size": 5000457,
            },
        }
        self.assertTrue(runtime.note_sight_group_upload_event(notice))

        component = types.SimpleNamespace(
            type="file", name="same-video.mp4", url="https://example.com/upload.mp4"
        )
        event = Event(
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            message_id="m-group-file",
        )
        event.message_items = [component]
        event.message_obj.message = event.message_items
        event.message_obj.raw_message = {
            "message_type": "group",
            "group_id": 20001,
            "user_id": 10001,
            "message": [
                {
                    "type": "file",
                    "data": {
                        "file": "same-video.mp4",
                        "file_id": "/temporary-upload-id",
                        "file_size": "5000457",
                        "url": "https://example.com/upload.mp4",
                    },
                }
            ],
        }

        clip = runtime._sight_clips_from_event(event)[0]

        self.assertEqual(clip.metadata["file_size"], "5000457")
        self.assertEqual(
            clip.metadata["platform_id"], "68395339a5caab4c3ce9167110fd2eff"
        )

    async def test_recent_video_context_filters_source_metadata_details(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-clean-context",
        )
        video_event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin=event.unified_msg_origin,
            message_id="m-video-old-cache",
        )
        video_event.message_items = [{"type": "video", "file": "D:/tmp/old-cache.mp4"}]
        video_event.message_obj.message = video_event.message_items
        await runtime._sight_vault_for_runtime().upsert(
            SightInsight(
                clip=runtime._sight_clips_from_event(video_event)[0],
                summary="视频里在讲旅行准备",
                details=[
                    "完整文字来源：必剪转写，共 428 字，已参与音频主线提炼",
                    "文字内容预览：这是转写预览",
                    "画面内容来源：时间线抽帧，6 个时间点",
                    "音频主线：讨论目的地和集合时间",
                    "街边有人经过",
                ],
            )
        )

        context = await runtime.format_recent_sight_context(event)

        self.assertIn("视频里在讲旅行准备", context)
        self.assertIn("讨论目的地和集合时间", context)
        self.assertIn("街边有人经过", context)
        self.assertNotIn("完整文字来源", context)
        self.assertNotIn("文字内容预览", context)
        self.assertNotIn("画面内容来源", context)
        self.assertNotIn("必剪转写，共", context)

    async def test_recent_video_context_uses_professional_digest(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-professional-context",
        )
        insight = await runtime._sight_vault_for_runtime().upsert(
            SightInsight(
                clip=SightClip(
                    scope=event.unified_msg_origin,
                    message_id="m-video-professional-cache",
                    source="https://www.bilibili.com/video/BV-test",
                    name="雨夜城市观察",
                    origin="bilibili",
                ),
                summary="普通视频理解摘要",
                details=["普通细节"],
                note="内置浅摘要不应优先进入上下文",
                metadata={"title": "雨夜城市观察", "author": "作者"},
            )
        )
        markdown = """# 雨夜城市观察 - 作者

## 背景概述
视频主要围绕雨夜城市路口的行人、灯光和交通节奏展开。

## 核心论点
- **重点**：雨夜环境让画面里的等待和移动更明显。
- 路口灯光、地面反光和人群速度共同构成主要信息。

![00:12 关键帧](D:/tmp/frame.jpg)
"""

        cached = await runtime._cache_sight_note_markdown(
            insight, markdown, style="professional"
        )
        context = await runtime.format_recent_sight_context(event)

        self.assertIn("professional_digest", cached.metadata)
        self.assertIn("专业总结：背景概述", context)
        self.assertIn("雨夜环境让画面里的等待和移动更明显", context)
        self.assertNotIn("内置浅摘要不应优先进入上下文", context)
        self.assertNotIn("![00:12", context)
        self.assertNotIn("# 雨夜城市观察", context)

    async def test_collects_emoji_assets_copies_local_file_to_plugin_cache(self):
        vision_provider = Provider(
            [
                '{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇"],"status":"ready"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            vision_provider, providers={"vision-model": vision_provider}
        )
        runtime.config = LifeSettings.from_dict(
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": True},
            }
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

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "incoming.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")

            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:GroupMessage:20001",
                group_id="20001",
                group_name="看展群",
                message_id="m-img-local",
            )
            event.message_items = [{"type": "mface", "path": str(source_path)}]
            event.message_obj.message = event.message_items

            await runtime.maybe_collect_emoji_assets_from_event(event)
            self.assertEqual(scheduled[0][0], "表情素材缓存与识别")
            await scheduled[0][2]

            assets = await runtime.archive.get_emoji_assets(10, status="ready")
            cached_path = Path(assets[0].file_path)
            self.assertTrue(cached_path.is_file())
            self.assertEqual(cached_path.parent, tmp_root / "emoji")
            self.assertEqual(cached_path.read_bytes(), source_path.read_bytes())
            self.assertEqual(
                vision_provider.vision_prompts[0]["image"], str(cached_path)
            )

    async def test_cleanup_emoji_asset_cache_removes_only_unreferenced_files(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            cache_dir = tmp_root / "emoji"
            cache_dir.mkdir()
            referenced = cache_dir / "referenced.png"
            orphan = cache_dir / "orphan.png"
            referenced.write_bytes(b"\x89PNG\r\n\x1a\nreferenced")
            orphan.write_bytes(b"\x89PNG\r\n\x1a\norphan")
            old_time = (
                datetime.datetime.now() - datetime.timedelta(days=2)
            ).timestamp()
            os.utime(orphan, (old_time, old_time))

            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash="referenced",
                    file_path=str(referenced),
                    label="还在使用",
                    status="ready",
                )
            )

            deleted = await runtime.cleanup_emoji_asset_cache()

            self.assertEqual(deleted, 1)
            self.assertTrue(referenced.exists())
            self.assertFalse(orphan.exists())

    async def test_cleanup_emoji_asset_cache_keeps_fresh_orphan_file(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            cache_dir = tmp_root / "emoji"
            cache_dir.mkdir()
            orphan = cache_dir / "fresh.png"
            orphan.write_bytes(b"\x89PNG\r\n\x1a\nfresh")

            deleted = await runtime.cleanup_emoji_asset_cache()

            self.assertEqual(deleted, 0)
            self.assertTrue(orphan.exists())

    async def test_failed_emoji_asset_is_not_rescheduled_by_same_image(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
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
        runtime._schedule_background_task = lambda coro, label="", key="": False

        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="failed-hash",
                file_path="https://example.com/failed.png",
                status="failed",
            )
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-failed",
        )
        event.message_items = [
            {"type": "mface", "url": "https://example.com/failed.png"}
        ]
        event.message_obj.message = event.message_items
        runtime._media_fingerprint = lambda payload: "failed-hash"

        await runtime.maybe_collect_emoji_assets_from_event(event)

        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].status, "failed")

    async def test_emoji_asset_records_message_id_and_source_url_separately(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.config = LifeSettings.from_dict(
            {"emoji_config": {"collect_chat_emojis": True}}
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {
                "resolve_event_sender": staticmethod(
                    lambda event: async_return(event.get_sender_name())
                )
            },
        )()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-url",
        )
        event.message_items = [
            {"type": "mface", "data": {"url": "https://example.com/source.png"}}
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)

        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(assets[0].source_message_id, "m-img-url")
        self.assertEqual(assets[0].source_url, "https://example.com/source.png")
        for _, _, coro in scheduled:
            coro.close()

    async def test_auto_collect_emoji_assets_can_be_disabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.config = LifeSettings.from_dict(
            {"emoji_config": {"collect_chat_emojis": False}}
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {
                "resolve_event_sender": staticmethod(
                    lambda event: async_return(event.get_sender_name())
                )
            },
        )()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": (
            scheduled.append((label, key, coro)) or True
        )
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-disabled",
        )
        event.message_items = [
            {"type": "mface", "data": {"url": "https://example.com/source.png"}}
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)

        self.assertEqual(await runtime.archive.get_emoji_assets(10), [])
        self.assertEqual(scheduled, [])

    async def test_emoji_cache_uses_configured_size_limit(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.config = LifeSettings.from_dict({"emoji_config": {"max_size_mb": 1}})

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            source_path = tmp_root / "large.png"
            source_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024))

            cached = await runtime._cache_emoji_asset_path(
                {"path": str(source_path)}, "large-limit"
            )

            self.assertIsNone(cached)
            self.assertFalse(any((tmp_root / "emoji").iterdir()))

    async def test_maintain_emoji_assets_marks_missing_and_prunes_over_limit(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.data_path = Path(tempfile.gettempdir()) / "daily_life_test.db"
        runtime.config = LifeSettings.from_dict({"emoji_config": {"max_ready": 2}})

        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="missing",
                file_path=str(Path(tempfile.gettempdir()) / "not-exists.png"),
                status="ready",
            )
        )
        for index in range(4):
            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash=f"ready-{index}",
                    file_path=f"https://example.com/{index}.png",
                    label=f"表情{index}",
                    status="ready",
                    used_count=index,
                )
            )

        result = await runtime.maintain_emoji_assets()

        assets = await runtime.archive.get_emoji_assets(limit=0)
        self.assertEqual(result["missing_marked"], 1)
        self.assertEqual(result["deleted_records"], 2)
        self.assertEqual(len([item for item in assets if item.status == "ready"]), 2)
        self.assertEqual(
            (await runtime.archive.get_emoji_asset_by_hash("missing")).status, "missing"
        )

    async def test_maintain_emoji_assets_prunes_stale_inactive_records(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.data_path = Path(tempfile.gettempdir()) / "daily_life_test.db"
        runtime.config = LifeSettings.from_dict(
            {"emoji_config": {"inactive_record_keep_days": 7}}
        )
        old_time = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="old-failed",
                file_path="",
                status="failed",
                rejected_reason="bad",
                created_at=old_time,
                updated_at=old_time,
            )
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                file_hash="ready-kept",
                file_path="https://example.com/ready.png",
                status="ready",
                sendable=True,
            )
        )

        result = await runtime.maintain_emoji_assets()

        self.assertEqual(result["deleted_inactive_records"], 1)
        self.assertIsNone(await runtime.archive.get_emoji_asset_by_hash("old-failed"))
        self.assertIsNotNone(
            await runtime.archive.get_emoji_asset_by_hash("ready-kept")
        )

    async def test_revalidate_review_emoji_assets_classifies_once(self):
        provider = Provider(
            [
                json.dumps(
                    {
                        "is_emoji_asset": True,
                        "asset_type": "sticker",
                        "label": "探头",
                        "emotions": ["好奇"],
                        "sendable": True,
                        "standalone_reaction": True,
                        "context_dependent": False,
                        "information_dominant": False,
                        "confidence": 0.93,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "is_emoji_asset": True,
                        "asset_type": "reaction",
                        "label": "新闻提醒",
                        "emotions": ["惊讶"],
                        "sendable": True,
                        "standalone_reaction": False,
                        "context_dependent": True,
                        "information_dominant": True,
                        "confidence": 0.96,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                ),
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.config = LifeSettings.from_dict(
            {"emoji_config": {"review_batch_size": 2}}
        )

        async def get_provider(_self):
            return provider

        async def close_session(_self, _session_id):
            return None

        runtime._get_vision_provider = types.MethodType(get_provider, runtime)
        runtime.close_text_session = types.MethodType(close_session, runtime)
        with tempfile.TemporaryDirectory() as tmpdir:
            for file_hash in ("old-sticker", "old-news"):
                path = Path(tmpdir) / f"{file_hash}.png"
                path.write_bytes(b"image")
                await runtime.archive.upsert_emoji_asset(
                    EmojiAssetRecord(
                        file_hash=file_hash,
                        file_path=str(path),
                        source_kind="review",
                        asset_type="image",
                        label="旧素材",
                        emotions=["旧标签"],
                        confidence=0.9,
                        sendable=True,
                        status="ready",
                    )
                )

            self.assertEqual(await runtime._revalidate_review_emoji_assets(), 2)

        accepted = await runtime.archive.get_emoji_asset_by_hash("old-sticker")
        rejected = await runtime.archive.get_emoji_asset_by_hash("old-news")
        self.assertEqual(accepted.source_kind, "verified")
        self.assertEqual(accepted.status, "ready")
        self.assertEqual(rejected.source_kind, "review")
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(rejected.rejected_reason, "不是可独立使用的聊天反应")
        self.assertEqual(await runtime._revalidate_review_emoji_assets(), 0)

    async def test_revalidate_review_emoji_assets_preserves_on_invalid_result(self):
        provider = Provider(["not-json"], provider_id="vision-model")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.config = LifeSettings.from_dict({})

        async def get_provider(_self):
            return provider

        async def close_session(_self, _session_id):
            return None

        runtime._get_vision_provider = types.MethodType(get_provider, runtime)
        runtime.close_text_session = types.MethodType(close_session, runtime)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "old-review.png"
            path.write_bytes(b"image")
            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash="old-review",
                    file_path=str(path),
                    source_kind="review",
                    label="旧素材",
                    emotions=["旧标签"],
                    sendable=True,
                    status="ready",
                )
            )
            self.assertEqual(await runtime._revalidate_review_emoji_assets(), 0)

        saved = await runtime.archive.get_emoji_asset_by_hash("old-review")
        self.assertEqual(saved.source_kind, "review")
        self.assertEqual(saved.status, "ready")
        self.assertTrue(saved.sendable)

    async def test_maintain_sight_cache_removes_stale_files_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
            runtime.data_path = Path(tmpdir) / "daily_life.db"
            runtime.config = LifeSettings.from_dict(
                {"sight_config": {"sight_cache_keep_days": 1}}
            )

            cache_dir = runtime._sight_cache_dir()
            stale_file = cache_dir / "frames" / "stale" / "frame_01_00_03.jpg"
            fresh_file = cache_dir / "frames" / "fresh" / "frame_01_00_03.jpg"
            stale_audio = cache_dir / "audio" / "old.wav"
            stale_transcript = cache_dir / "transcripts" / "old.json"
            stale_asr_model = cache_dir / "asr" / "models" / "model.bin"
            stale_file.parent.mkdir(parents=True, exist_ok=True)
            fresh_file.parent.mkdir(parents=True, exist_ok=True)
            stale_audio.parent.mkdir(parents=True, exist_ok=True)
            stale_transcript.parent.mkdir(parents=True, exist_ok=True)
            stale_asr_model.parent.mkdir(parents=True, exist_ok=True)
            stale_file.write_bytes(b"stale")
            fresh_file.write_bytes(b"fresh")
            stale_audio.write_bytes(b"audio")
            stale_transcript.write_text("{}", encoding="utf-8")
            stale_asr_model.write_bytes(b"model")

            now = time.time()
            old_time = now - 3 * 86400
            os.utime(stale_file, (old_time, old_time))
            os.utime(stale_audio, (old_time, old_time))
            os.utime(stale_transcript, (old_time, old_time))
            os.utime(stale_asr_model, (old_time, old_time))
            os.utime(fresh_file, (now, now))

            result = await runtime.maintain_sight_cache()

            self.assertGreaterEqual(result["deleted_files"], 1)
            self.assertFalse(stale_file.exists())
            self.assertFalse(stale_audio.exists())
            self.assertFalse(stale_transcript.exists())
            self.assertTrue(stale_asr_model.exists())
            self.assertTrue(fresh_file.exists())

    def test_maintain_plugin_file_cache_removes_generated_and_reverse_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
            runtime.data_path = Path(tmpdir) / "daily_life.db"
            runtime.config = LifeSettings.from_dict(
                {
                    "storage_config": {
                        "generated_media_keep_days": 1,
                        "reverse_cache_keep_days": 1,
                    }
                }
            )
            cleanup_calls = []

            class Archive:
                async def cleanup_reverse_prompts(self, keep_days):
                    cleanup_calls.append(keep_days)
                    return 1

            runtime.archive = Archive()
            root = Path(tmpdir)
            stale_generated = root / "generated" / "images" / "old.png"
            fresh_generated = root / "generated" / "videos" / "new.mp4"
            stale_reverse = root / "reverse" / "reverse_reference_old.png"
            for path in (stale_generated, fresh_generated, stale_reverse):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"cache")

            now = time.time()
            old_time = now - 3 * 86400
            for path in (stale_generated, stale_reverse):
                os.utime(path, (old_time, old_time))
            os.utime(fresh_generated, (now, now))

            result = asyncio.run(runtime.maintain_plugin_file_cache())

            self.assertEqual(result["deleted_files"], 2)
            self.assertEqual(result["deleted_reverse_rows"], 1)
            self.assertEqual(cleanup_calls, [1])
            self.assertFalse(stale_generated.exists())
            self.assertFalse(stale_reverse.exists())
            self.assertTrue(fresh_generated.exists())

    def test_maintain_plugin_file_cache_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
            runtime.data_path = Path(tmpdir) / "daily_life.db"
            runtime.config = LifeSettings.from_dict(
                {
                    "storage_config": {
                        "generated_media_keep_days": 0,
                        "reverse_cache_keep_days": 0,
                    }
                }
            )
            stale_generated = Path(tmpdir) / "generated" / "images" / "old.png"
            stale_reverse = Path(tmpdir) / "reverse" / "reverse_reference_old.png"
            for path in (stale_generated, stale_reverse):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"cache")
                old_time = time.time() - 10 * 86400
                os.utime(path, (old_time, old_time))

            result = asyncio.run(runtime.maintain_plugin_file_cache())

            self.assertEqual(result["deleted_files"], 0)
            self.assertTrue(stale_generated.exists())
            self.assertTrue(stale_reverse.exists())

    async def test_vision_provider_unset_uses_current_default_provider(self):
        default_provider = Provider(
            [
                '{"label":"默认识别","description":"默认模型识别的小表情","emotions":["轻松"],"status":"ready"}'
            ],
            provider_id="default-model",
        )
        memory_provider = Provider([], provider_id="memory-model")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            default_provider,
            providers={"memory-model": memory_provider},
        )
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"provider": "memory-model"},
                "vision_config": {"provider": ""},
                "emoji_config": {"collect_chat_emojis": True},
            }
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
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展群",
            message_id="m-img-default",
        )
        event.message_items = [
            {"type": "mface", "url": "https://example.com/default.png"}
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        await scheduled[0][2]

        self.assertEqual(
            default_provider.vision_prompts[0]["image"],
            "https://example.com/default.png",
        )
        self.assertEqual(memory_provider.vision_prompts, [])

    async def test_memory_awareness_skips_empty_visibility_and_decision_shells(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        await runtime.archive.save_day(DayRecord(date="2026-05-24"))
        meta = {
            "session_id": "aiocqhttp:GroupMessage:20001",
            "message_id": "m1",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "20001",
            "group_name": "测试群",
            "date": "2026-05-24",
            "is_group": "true",
        }

        await runtime._save_memory_awareness_records({"worth_saving": False}, meta)
        await runtime._save_memory_awareness_records(
            {
                "visibility": {
                    "level": "seen",
                    "attention_level": 0,
                    "psychological_freshness": 0,
                },
                "action_decision": {"action": "skip_memory", "confidence": 0},
            },
            meta,
        )

        self.assertEqual(await runtime.archive.get_recent_message_visibility(10), [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(10), [])
        self.assertEqual(await runtime.archive.get_recent_group_environments(10), [])
        await runtime._append_memory_decision_log(
            {
                "visibility": {"level": "seen"},
                "action_decision": {"action": "skip_memory"},
            },
            meta,
            datetime.datetime(2026, 5, 24, 12, 0),
        )
        self.assertEqual((await runtime.archive.get_day("2026-05-24")).state_log, [])

    async def test_memory_awareness_keeps_effective_visibility_and_decision_results(
        self,
    ):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        meta = {
            "session_id": "aiocqhttp:GroupMessage:20001",
            "message_id": "m1",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "20001",
            "group_name": "测试群",
            "date": "2026-05-24",
            "is_group": "true",
        }
        payload = {
            "visibility": {
                "level": "seen_but_ignored",
                "attention_level": 32,
                "psychological_freshness": 58,
                "reason": "看见了但状态不想展开",
            },
            "action_decision": {
                "action": "observe",
                "reason": "先观察，不急着接话",
                "reply_strategy": "等话题自然落点",
            },
        }

        await runtime._save_memory_awareness_records(payload, meta)

        visibility = await runtime.archive.get_recent_message_visibility(10)
        decisions = await runtime.archive.get_recent_action_decisions(10)
        self.assertEqual(visibility[0].visibility, "seen_but_ignored")
        self.assertEqual(visibility[0].reason, "看见了但状态不想展开")
        self.assertEqual(decisions[0].action, "observe")
        self.assertEqual(decisions[0].reply_strategy, "等话题自然落点")

    async def test_proactive_send_can_attach_selected_emoji_asset(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 1, "reason": "这个表情适合轻轻围观"}'],
            provider_id="proactive-model",
        )
        await runtime.archive.upsert_emoji_asset(
            EmojiAssetRecord(
                id=1,
                file_hash="emoji-1",
                file_path="https://example.com/peek.png",
                label="探头",
                description="适合轻轻围观",
                emotions=["好奇", "围观"],
                status="ready",
            )
        )

        sent = await runtime._send_proactive_message(
            "aiocqhttp:FriendMessage:10001",
            "我先探头看一眼。",
            "闲时回复发送失败",
            send_payload={
                "expression_intent": {
                    "emotion": "好奇",
                    "emoji_intent": "轻轻围观",
                    "action_intent": "探头",
                    "send_emoji": True,
                    "reason": "表情比补一句解释更自然",
                }
            },
        )

        self.assertTrue(sent)
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertEqual(
            runtime.context.sent_messages[0][1].items, ["我先探头看一眼。"]
        )
        self.assertEqual(
            runtime.context.sent_messages[1][1].items,
            [{"type": "image", "url": "https://example.com/peek.png"}],
        )
        assets = await runtime.archive.get_emoji_assets(10, status="ready")
        self.assertEqual(assets[0].used_count, 1)
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("候选表情", provider.prompts[0])

    async def test_proactive_send_uses_cached_local_emoji_asset(self):
        runtime, provider = self._make_proactive_runtime(
            ['{"emoji_id": 1, "reason": "这个表情适合轻轻围观"}'],
            provider_id="proactive-model",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cached_path = Path(tmpdir) / "emoji" / "peek.png"
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            cached_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")
            await runtime.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    id=1,
                    file_hash="emoji-1",
                    file_path=str(cached_path),
                    label="探头",
                    description="适合轻轻围观",
                    emotions=["好奇", "围观"],
                    status="ready",
                )
            )

            sent = await runtime._send_proactive_message(
                "aiocqhttp:FriendMessage:10001",
                "我先探头看一眼。",
                "闲时回复发送失败",
                send_payload={
                    "expression_intent": {
                        "emotion": "好奇",
                        "emoji_intent": "轻轻围观",
                        "action_intent": "探头",
                        "send_emoji": True,
                        "reason": "表情比补一句解释更自然",
                    }
                },
            )

            self.assertTrue(sent)
            self.assertEqual(
                runtime.context.sent_messages[1][1].items,
                [{"type": "image", "file": str(cached_path)}],
            )
            self.assertEqual(len(provider.prompts), 1)
            self.assertIn("候选表情", provider.prompts[0])

    async def test_injection_media_expression_does_not_create_voice_round(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config(),
                "state_config": {"enabled": False},
            }
        )
        runtime.archive = DataManager()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅蓝外套",
                timeline=[
                    TimelineItem(time="12:00", activity="在家整理资料", status="平静")
                ],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(
            ("2026-05-24", False)
        )
        runtime.maybe_collect_emoji_assets_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime.maybe_capture_commitment_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime.maybe_capture_chat_memory_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime._build_injection_memos_context = lambda event, message="": async_return(
            ""
        )
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: (
            async_return({})
        )

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "看看你现在在干嘛"
        req = type(
            "Request",
            (),
            {"prompt": "你好", "system_prompt": "", "session_id": "chat_session"},
        )()

        await runtime.inject_life_context(req, event)

        self.assertIn("[HiddenMediaExpression]", req.system_prompt)
        self.assertNotIn("life_voice_generate", req.system_prompt)
        self.assertFalse(runtime.note_voice_switch_text_result(event))
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

    async def test_injection_adds_visual_anchor_when_request_has_image(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({"state_config": {"enabled": False}})
        runtime.archive = DataManager()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅蓝外套",
                timeline=[
                    TimelineItem(time="12:00", activity="在家整理资料", status="平静")
                ],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(
            ("2026-05-24", False)
        )
        runtime.maybe_capture_commitment_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime.maybe_capture_chat_memory_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime._build_injection_memos_context = lambda event, message="": async_return(
            ""
        )
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: (
            async_return({})
        )

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "这照片是你吗"
        req = ProviderRequest(
            prompt="这照片是你吗",
            system_prompt="",
            session_id="chat_session",
            image_urls=["D:/tmp/quoted.png"],
        )

        await runtime.inject_life_context(req, event)

        anchors = [
            part
            for part in req.extra_user_content_parts
            if "HiddenVisualInputRule" in str(getattr(part, "text", ""))
        ]
        self.assertEqual(len(anchors), 1)
        self.assertTrue(getattr(anchors[0], "_no_save", False))
        self.assertIn("必须以本轮图片和图片说明为准", anchors[0].text)
        self.assertIn("不能替代图片事实", anchors[0].text)
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

    async def test_injection_does_not_add_visual_anchor_without_image(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({"state_config": {"enabled": False}})
        runtime.archive = DataManager()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅蓝外套",
                timeline=[
                    TimelineItem(time="12:00", activity="在家整理资料", status="平静")
                ],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(
            ("2026-05-24", False)
        )
        runtime.maybe_capture_commitment_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime.maybe_capture_chat_memory_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime._build_injection_memos_context = lambda event, message="": async_return(
            ""
        )
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: (
            async_return({})
        )

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "今天在干嘛"
        req = ProviderRequest(
            prompt="今天在干嘛", system_prompt="", session_id="chat_session"
        )

        await runtime.inject_life_context(req, event)

        self.assertFalse(
            any(
                "HiddenVisualInputRule" in str(getattr(part, "text", ""))
                for part in req.extra_user_content_parts
            )
        )
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

    async def test_injection_adds_video_anchor_when_request_has_video(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({"state_config": {"enabled": False}})
        runtime.archive = DataManager()
        await runtime.archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅蓝外套",
                timeline=[
                    TimelineItem(time="12:00", activity="在家整理资料", status="平静")
                ],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(
            ("2026-05-24", False)
        )
        runtime.maybe_capture_commitment_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime.maybe_capture_chat_memory_from_event = (
            lambda event, now, sender_name="": async_return(None)
        )
        runtime._build_injection_memos_context = lambda event, message="": async_return(
            ""
        )
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: (
            async_return({})
        )

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "这个视频讲什么"
        event.message_items = [{"type": "video", "file": "D:/tmp/classroom.mp4"}]
        event.message_obj.message = event.message_items
        req = ProviderRequest(
            prompt="这个视频讲什么", system_prompt="", session_id="chat_session"
        )

        await runtime.inject_life_context(req, event)

        anchors = [
            part
            for part in req.extra_user_content_parts
            if "HiddenVideoInputRule" in str(getattr(part, "text", ""))
        ]
        self.assertEqual(len(anchors), 1)
        self.assertTrue(getattr(anchors[0], "_no_save", False))
        self.assertIn(
            "必须基于近期视频理解或调用 life_video_understand", anchors[0].text
        )
        self.assertIn(
            "不要因为字幕、水印、标题或画面线索再调用联网搜索", anchors[0].text
        )
        self.assertIn("出处、原视频、作者、链接", anchors[0].text)
        await asyncio.gather(*list(runtime._background_scheduler.tasks))
