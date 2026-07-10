import unittest

from runtimehelpers import *


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
            weather="北京 晴 20°C",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertIn("<expression_channel>", text)
        self.assertIn("插件会在发送前用本地节奏算法判断是否转成语音", text)
        self.assertIn("文字始终是默认表达", text)
        self.assertIn("同一串可能一条就停", text)
        self.assertIn("life_emoji_send", text)
        self.assertIn("不需要传图片路径或 URL", text)
        self.assertIn("用户没有明确要求语音时，不要主动调用 life_voice_generate", text)
        self.assertNotIn("record_life_text_decision", text)
        self.assertIn("life_voice_generate", text)
        self.assertIn("第一人称 decision_reason", text)
        self.assertIn("不要再用文字重复同一句", text)
        self.assertNotIn("speak_life_voice", text)
        self.assertIn("45.0%", text)
    def test_hidden_context_can_include_media_expression_when_image_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config()
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        text = runtime.build_hidden_expression_channel_hint(event)

        self.assertIn("<expression_channel>", text)
        self.assertIn("[HiddenMediaExpression]", text)
        self.assertIn("对话意图、当下状态和表达自然度", text)
        self.assertIn("不要靠固定词触发", text)
        self.assertIn("环境、动作细节、手边物件或氛围画面", text)
        self.assertNotIn("关系边界", text)
        self.assertNotIn("画面尺度", text)
        self.assertNotIn("睡前、换衣", text)
        self.assertIn("life_image_generate", text)
        self.assertIn("life_image_reverse_prompt", text)
        self.assertIn("life_emoji_send", text)
        self.assertNotIn("[HiddenImageReference]", text)
        self.assertIn("当前图片参考状态", text)
        self.assertIn("当前消息和引用消息没有可用图片", text)
        self.assertIn("新增画面请求调用 life_image_generate", text)
        self.assertIn("不要用 edit_life_image，也不要生成新图片", text)
        self.assertIn("use_last_reverse_prompt=true", text)
        self.assertIn("从缓存读取反推原文提示词", text)
        self.assertIn("不会自动把反推原图作为图生图参考", text)
        self.assertNotIn("重复填入 prompt", text)
        self.assertNotIn("不要把反推结果概括、翻译、扩写", text)
        self.assertNotIn("life_voice_generate", text)
    def test_media_expression_channel_does_not_mark_voice_switch_available(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config()
            }
        )
        runtime.archive = DataManager()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        day = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
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

        self.assertIn("life_video_generate", text)
        self.assertIn("自动把那张图作为首帧/参考图", text)
        self.assertIn("视频生成慢且成本高", text)
        self.assertIn("普通“看看现在、发张照片、在干嘛”优先图片或文字", text)
        self.assertIn("非常强的场景需求", text)
    def test_hidden_media_cadence_reports_recent_media(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "image_generation_config": image_generation_config()
            }
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
    def test_hidden_expression_channel_allows_group_and_private_when_voice_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {
                    "enabled": True,
                }
            }
        )
        first_group = Event(unified_msg_origin="aiocqhttp:GroupMessage:10001", group_id="10001")
        second_group = Event(unified_msg_origin="aiocqhttp:GroupMessage:10002", group_id="10002")
        first_private = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")
        second_private = Event(unified_msg_origin="aiocqhttp:FriendMessage:654321", sender_id="654321")

        self.assertIn("<expression_channel>", runtime.build_hidden_expression_channel_hint(first_group))
        self.assertIn("<expression_channel>", runtime.build_hidden_expression_channel_hint(second_group))
        self.assertIn("<expression_channel>", runtime.build_hidden_expression_channel_hint(first_private))
        self.assertIn("<expression_channel>", runtime.build_hidden_expression_channel_hint(second_private))
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
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        self.assertTrue(runtime.mark_voice_switch_available(event))
        self.assertTrue(runtime.note_voice_switch_text_result(event))
        self.assertFalse(runtime.note_voice_switch_text_result(event))
        self.assertEqual(runtime.archive.action_decisions, {})
    def test_voice_switch_text_result_does_not_log_text_decision(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 35}}
        )
        runtime.archive = DataManager()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            runtime.mark_voice_switch_available(event)
            self.assertTrue(runtime.note_voice_switch_text_result(event))
        finally:
            messenger.logger.info = old_info

        self.assertEqual(messages, [])
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_channel"], "文字")
    def test_voice_switch_text_result_consumes_internal_reason_silently(self):
        messages = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 35}}
        )
        runtime.archive = DataManager()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            self.assertTrue(runtime.mark_voice_switch_available(event))
            runtime._voice_switch_round_store()[event.unified_msg_origin]["text_reason"] = "我这轮内容有几句铺垫，文字更容易读清楚。"
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
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        self.assertTrue(runtime.mark_voice_switch_available(event))
        self.assertTrue(runtime.mark_voice_switch_used(event))
        self.assertFalse(runtime.note_voice_switch_text_result(event))


class RuntimeMediaAsyncTest(RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase):
    async def test_life_image_generate_sends_media(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "life.png"
        image_path.write_bytes(b"x" * 2048)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertIn("图片已发送。", result)
        self.assertIn("2.0 KB", result)
        self.assertIn("耗时", result)
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertIn({"type": "image", "file": str(image_path)}, runtime.context.sent_messages[0][1].items)
        self.assertFalse(
            any(call[0] == "update_conversation" for call in runtime.context.conversation_manager.calls)
        )
        cadence = runtime._media_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["last_media"], "图片")
        self.assertEqual(cadence["consecutive"], 1)
    async def test_life_image_generate_uses_full_message_prompt(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: image_prompts.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        full_prompt = '生成一张高度写实的竖版 9:16 古风 POV，雨后古镇青石板小巷，油纸伞，衣袖牵引，胶片写实质感，保留这句唯一细节'
        event.message_str = f'  {full_prompt}'

        result = await runtime.life_image_generate(event, '雨后古镇少女撑伞')

        self.assertIn('图片已发送。', result)
        self.assertEqual(image_prompts, [(full_prompt, {'aspect_ratio': '9:16'})])
    async def test_life_image_generate_uses_director_when_agent_expands_short_request(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        director_prompts = []

        async def direct(event, prompt, **kwargs):
            director_prompts.append(prompt)
            return types.SimpleNamespace(
                prompt=f'导演整理：{prompt}',
                contains_character=False,
                needs_character_reference=False,
            )

        runtime._direct_life_image_payload = direct
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: image_prompts.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '拍张照看看'
        agent_prompt = (
            '一个年轻女孩穿着浅灰色蕾丝边棉质吊带睡裙，头发扎成低马尾，'
            '站在洗手间镜子前准备洗漱，暖黄色灯光，夜晚居家氛围，生活随手抓拍镜头。'
        )

        result = await runtime.life_image_generate(event, agent_prompt)

        self.assertIn('图片已发送。', result)
        self.assertEqual(director_prompts, [agent_prompt])
        self.assertEqual(image_prompts, [(f'导演整理：{agent_prompt}', {})])
    async def test_life_image_generate_uses_last_reverse_prompt_when_requested(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: image_prompts.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '/把反推的提示词生成一张'
        reverse_prompt = '蓝色长发角色近景肖像，冰雪饰品，冷色调梦幻烟雾，高清写实摄影。'
        runtime._remember_reverse_prompt_for_scope(event, reverse_prompt)

        result = await runtime.life_image_generate(event, '', use_last_reverse_prompt=True)

        self.assertIn('图片已发送。', result)
        self.assertEqual(image_prompts, [(reverse_prompt, {})])
    async def test_life_image_generate_loads_last_reverse_prompt_from_archive(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        reverse_prompt = '窗边热茶，暖色台灯，浅景深生活照。'

        class Archive:
            async def get_latest_reverse_prompt(self, scope):
                self.scope = scope
                return ReversePromptRecord(
                    scope=scope,
                    prompt=reverse_prompt,
                    image_path='D:/tmp/reverse.png',
                )

        archive = Archive()
        runtime.archive = archive
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: image_prompts.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')

        result = await runtime.life_image_generate(event, '', use_last_reverse_prompt=True)

        self.assertIn('图片已发送。', result)
        self.assertEqual(archive.scope, 'aiocqhttp:FriendMessage:10001')
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
                return types.SimpleNamespace(path=Path('life.png'))

            async def edit_image(self, prompt, reference_image, **kwargs):
                raise AssertionError('should not auto use reverse reference image')

        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        reverse_prompt = '蓝色长发角色近景肖像，冰雪饰品，冷色调梦幻烟雾，高清写实摄影。'
        runtime._remember_reverse_prompt_for_scope(event, reverse_prompt, 'D:/tmp/reverse.png')

        result = await runtime.life_image_generate(event, '', use_last_reverse_prompt=True)

        self.assertIn('图片已发送。', result)
        self.assertEqual(image_prompts, [(reverse_prompt, {})])
    async def test_life_image_generate_does_not_auto_use_reverse_reference_for_exact_prompt(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []

        class ImageService:
            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                image_prompts.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path('life.png'))

            async def edit_image(self, prompt, reference_image, **kwargs):
                raise AssertionError('should not auto use cached reverse reference image')

        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '把这条提示词生成一张'
        reverse_prompt = '冷色调梦幻烟雾中的蓝色长发角色近景肖像，高清写实摄影。'
        runtime._remember_reverse_prompt_for_scope(event, reverse_prompt, 'D:/tmp/reverse.png')

        result = await runtime.life_image_generate(event, reverse_prompt)

        self.assertIn('图片已发送。', result)
        self.assertEqual(image_prompts, [(reverse_prompt, {})])
    async def test_life_image_generate_uses_detailed_user_text_directly(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '雨后古镇青石板小巷，暖黄色灯笼光，月白淡粉汉服，第一视角斜俯拍，手机夜间抓拍胶片质感'

        result = await runtime.life_image_generate(event, '雨后古镇少女撑伞')

        self.assertIn('图片已发送。', result)
        self.assertEqual(image_prompts, [event.message_str])
    async def test_life_image_generate_prefers_user_prompt_aspect_ratio(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: calls.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '雨后古镇青石板小巷，第一视角斜俯拍，竖版 9:16，手机夜间抓拍胶片质感'

        result = await runtime.life_image_generate(event, '雨后古镇少女撑伞')

        self.assertIn('图片已发送。', result)
        self.assertEqual(calls, [(event.message_str, {'aspect_ratio': '9:16'})])
    def test_image_prompt_aspect_ratio_only_accepts_supported_numeric_ratio(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        self.assertEqual(runtime._image_prompt_aspect_ratio("竖版 9:16 手机拍摄"), "9:16")
        self.assertEqual(runtime._image_prompt_aspect_ratio("横版 16：9 手机拍摄"), "16:9")
        self.assertEqual(runtime._image_prompt_aspect_ratio("编号 119:160，不是支持比例"), "")
        self.assertEqual(runtime._image_prompt_aspect_ratio("不要默认方图"), "")
    async def test_life_image_generate_uses_cached_source_event_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: image_prompts.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        source_event = Event(unified_msg_origin='aiocqhttp:GroupMessage:20001', group_id='20001')
        full_prompt = '生成一张高度写实的竖版 9:16 古风 POV 夜游抓拍写真，互动动作、场景、服装、光线、负面要求全部保留'
        source_event.message_str = full_prompt
        runtime.note_media_source_event(source_event)
        tool_event = Event(unified_msg_origin=source_event.unified_msg_origin, group_id='20001')

        result = await runtime.life_image_generate(tool_event, '古风灯会街巷少女递花灯')

        self.assertIn('图片已发送。', result)
        self.assertEqual(image_prompts, [(full_prompt, {'aspect_ratio': '9:16'})])
    async def test_life_image_generate_does_not_treat_slash_as_direct_mode(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        director_prompts = []

        async def direct(event, prompt, **kwargs):
            director_prompts.append(prompt)
            return types.SimpleNamespace(
                prompt=f'导演整理：{prompt}',
                contains_character=False,
                needs_character_reference=False,
            )

        runtime._direct_life_image_payload = direct
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '/拍张现在'

        result = await runtime.life_image_generate(event, '拍一张当前生活照')

        self.assertIn('图片已发送。', result)
        self.assertEqual(director_prompts, ['拍一张当前生活照'])
        self.assertEqual(image_prompts, ['导演整理：拍一张当前生活照'])
    async def test_life_image_generate_uses_director_for_short_life_request(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        director_prompts = []

        async def direct(event, prompt, **kwargs):
            director_prompts.append(prompt)
            return types.SimpleNamespace(
                prompt=f'导演整理：{prompt}',
                contains_character=False,
                needs_character_reference=False,
            )

        runtime._direct_life_image_payload = direct
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '拍张现在'

        result = await runtime.life_image_generate(event, '拍一张当前生活照')

        self.assertIn('图片已发送。', result)
        self.assertEqual(director_prompts, ['拍一张当前生活照'])
        self.assertEqual(image_prompts, ['导演整理：拍一张当前生活照'])
    async def test_life_image_generate_switches_to_edit_route_when_director_needs_character_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        generate_calls = []
        edit_calls = []

        async def direct(event, prompt, *, reference=False):
            return types.SimpleNamespace(
                prompt=f'导演整理：{prompt}',
                contains_character=True,
                needs_character_reference=True,
            )

        class ImageService:
            def first_character_reference_image(self):
                return 'D:/ref/role.png'

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path('life.png'))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path('role.png'))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '请按角色本人参考图再拍一张'

        result = await runtime.life_image_generate(event, '拍一张角色本人参考图')

        self.assertIn('已发送', result)
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [('导演整理：拍一张角色本人参考图', 'D:/ref/role.png', {'preserve_reference_ratio': False})],
        )
    async def test_life_image_generate_keeps_text_route_when_director_marks_character_but_not_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        edit_calls = []
        generate_calls = []

        async def direct(event, prompt, *, reference=False):
            return types.SimpleNamespace(
                prompt=f'导演整理：{prompt}',
                contains_character=True,
                needs_character_reference=False,
            )

        class ImageService:
            def first_character_reference_image(self):
                return 'D:/ref/role.png'

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path('scene.png'))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path('role.png'))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '拍一下窗外'

        result = await runtime.life_image_generate(event, '拍一张窗外雨夜')

        self.assertIn('图片已发送。', result)
        self.assertEqual(generate_calls, [('导演整理：拍一张窗外雨夜', {})])
        self.assertEqual(edit_calls, [])
    async def test_generate_life_image_asset_trusted_identity_skips_director_and_uses_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        edit_calls = []
        generate_calls = []

        async def direct(event, prompt, **kwargs):
            raise AssertionError('trusted identity should skip image director')

        class ImageService:
            def first_character_reference_image(self):
                return 'D:/ref/role.png'

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path('scene.png'))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path('role.png'))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())

        result = await runtime.generate_life_image_asset(
            None,
            '画面主体是当前角色本人，窗边晨光生活照',
            contains_character=True,
            trusted_identity=True,
        )

        self.assertEqual(result.path, Path('role.png'))
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [('画面主体是当前角色本人，窗边晨光生活照', 'D:/ref/role.png', {'preserve_reference_ratio': False})],
        )
    async def test_life_image_generate_keeps_text_route_when_director_reports_character_but_no_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        edit_calls = []
        generate_calls = []

        async def direct(event, prompt, *, reference=False):
            return types.SimpleNamespace(
                prompt=f'导演整理：{prompt}',
                contains_character=True,
                needs_character_reference=False,
            )

        class ImageService:
            def first_character_reference_image(self):
                return 'D:/ref/role.png'

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path('scene.png'))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path('role.png'))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '拍一下窗外'

        result = await runtime.life_image_generate(event, '拍一张窗外雨夜')

        self.assertIn('图片已发送。', result)
        self.assertEqual(generate_calls, [('导演整理：拍一张窗外雨夜', {})])
        self.assertEqual(edit_calls, [])
    async def test_life_image_generate_director_character_reference_uses_config_ratio(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        generate_calls = []
        edit_calls = []

        async def direct(event, prompt, *, reference=False):
            return types.SimpleNamespace(
                prompt=f'导演整理：{prompt}',
                contains_character=True,
                needs_character_reference=True,
            )

        class ImageService:
            def first_character_reference_image(self):
                return 'D:/ref/role.png'

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path('scene.png'))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path('role.png'))

        runtime._direct_life_image_payload = direct
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '画图'

        result = await runtime.life_image_generate(event, '角色本人坐在窗边')

        self.assertIn('图片已发送。', result)
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [('导演整理：角色本人坐在窗边', 'D:/ref/role.png', {'preserve_reference_ratio': False})],
        )
    async def test_life_image_generate_keeps_tool_prompt_without_slash_command(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=Path('life.png')))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '我在聊天里提到 / 这个符号，但不是图片直给命令'

        result = await runtime.life_image_generate(event, '模型整理后的图片提示词')

        self.assertIn('图片已发送。', result)
        self.assertEqual(image_prompts, ['模型整理后的图片提示词'])
    async def test_life_image_generate_reports_empty_exception_type(self):
        async def fail_image(prompt):
            raise TimeoutError()

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace(generate_image=fail_image))
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
                raise RuntimeError('HTTP 400：{"error":{"code":"content_policy_violation"}}')
            return types.SimpleNamespace(path=image_path)

        async def rewrite(event, prompt, *, reference=False):
            rewrites.append((prompt, reference))
            return "雨夜生活照，自然生活化表达"

        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace(generate_image=generate_image))
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜生活照")

        self.assertIn("图片已发送。", result)
        self.assertEqual(prompts, ["雨夜生活照", "雨夜生活照，自然生活化表达"])
        self.assertEqual(rewrites, [("雨夜生活照", False)])
    async def test_life_image_generate_returns_short_failure_after_policy_retry_failure(self):
        from core.runtime.channel import image as image_channel

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        messages = []

        async def generate_image(prompt):
            raise RuntimeError('HTTP 400：{"error":{"code":"content_policy_violation","message":"blocked"}}')

        async def rewrite(event, prompt, *, reference=False):
            return "雨夜生活照，自然生活化表达"

        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace(generate_image=generate_image))
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        old_warning = image_channel.logger.warning
        image_channel.logger.warning = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            result = await runtime.life_image_generate(event, "雨夜生活照")
        finally:
            image_channel.logger.warning = old_warning

        self.assertIn("图片生成失败：图片轻量润色后重试仍失败", result)
        self.assertIn("HTTP 400", result)
        self.assertIn("content_policy_violation", result)
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
            raise RuntimeError('HTTP 400：{"error":{"code":"content_policy_violation","message":"blocked"}}')

        async def rewrite(event, prompt, *, reference=False):
            raise RuntimeError("图片轻量润色失败：图片智能提取没有返回有效画面字段")

        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace(generate_image=generate_image))
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        old_warning = image_channel.logger.warning
        image_channel.logger.warning = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            result = await runtime.life_image_generate(event, "雨夜生活照")
        finally:
            image_channel.logger.warning = old_warning

        self.assertEqual(result, "图片生成失败：图片触发安全拒绝，轻量润色失败。")
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("图片生成或发送失败：图片触发安全拒绝，轻量润色失败。", messages[0])
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
                raise RuntimeError('HTTP 400：{"error":{"code":"content_policy_violation","message":"blocked"}}')
            return types.SimpleNamespace(path=image_path)

        async def rewrite(event, prompt, *, reference=False):
            return "雨夜生活照，自然生活化表达"

        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace(generate_image=generate_image))
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        old_info = image_channel.logger.info
        image_channel.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            result = await runtime.life_image_generate(event, "雨夜生活照")
        finally:
            image_channel.logger.info = old_info

        self.assertIn("图片已发送。", result)
        policy_logs = [message for message in messages if "安全拒绝" in message]
        rewrite_logs = [message for message in messages if "图片轻量润色后重试" in message]
        self.assertEqual(len(policy_logs), 1)
        self.assertEqual(len(rewrite_logs), 1)
        self.assertIn("图片触发安全拒绝", policy_logs[0])
        self.assertIn('HTTP 400：{"error":{"code":"content_policy_violation","message":"blocked"}}', policy_logs[0])
        self.assertIn("content_policy_violation", policy_logs[0])
        self.assertIn("blocked", policy_logs[0])
        self.assertIn("雨夜生活照，自然生活化表达", rewrite_logs[0])
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

        prompt = await runtime._rewrite_life_image_prompt_for_policy_retry(event, "雨夜窗边生活照")

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

        prompt = await runtime._rewrite_life_image_prompt_for_policy_retry(event, "雨夜窗边生活照")

        self.assertEqual(prompt, "雨夜窗边生活照，自然生活化表达，保留原构图")
    async def test_life_image_policy_rewrite_accepts_loose_json(self):
        provider = Provider(["```json\n{'prompt':'雨夜窗边生活照，自然生活化表达，保留原构图',}\n```"])
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

        prompt = await runtime._rewrite_life_image_prompt_for_policy_retry(event, "雨夜窗边生活照")

        self.assertEqual(prompt, "雨夜窗边生活照，自然生活化表达，保留原构图")
    async def test_life_image_policy_rewrite_uses_configured_provider(self):
        default_provider = Provider([], provider_id="default-model")
        rewrite_provider = Provider(
            ['{"prompt":"雨夜窗边生活照，自然生活化表达，保留原构图"}'],
            provider_id="rewrite-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(default_provider, providers={"rewrite-model": rewrite_provider})
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

        prompt = await runtime._rewrite_life_image_prompt_for_policy_retry(event, "雨夜窗边生活照")

        self.assertEqual(prompt, "雨夜窗边生活照，自然生活化表达，保留原构图")
        self.assertEqual(requested_providers, ["rewrite-model"])
        self.assertEqual(primary_provider_ids, ["rewrite-model"])
        self.assertEqual(default_provider.prompts, [])
        self.assertEqual(len(rewrite_provider.prompts), 1)
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
                raise RuntimeError('HTTP 400：{"error":{"code":"content_policy_violation"}}')
            return types.SimpleNamespace(path=image_path)

        async def rewrite(event, prompt, *, reference=False):
            rewrites.append((prompt, reference))
            return "保留姿势，换成咖啡店生活照，自然生活化表达"

        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace(edit_image=edit_image))
        runtime._rewrite_life_image_prompt_for_policy_retry = rewrite
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(event, "保留姿势，换成咖啡店生活照", str(reference))

        self.assertIn("图片已根据参考图生成。", result)
        self.assertEqual(
            calls,
            [
                ("保留姿势，换成咖啡店生活照", str(reference), {"preserve_reference_ratio": True}),
                ("保留姿势，换成咖啡店生活照，自然生活化表达", str(reference), {"preserve_reference_ratio": True}),
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
                edit_image=lambda prompt, reference_image, **kwargs: calls.append((prompt, reference_image, kwargs))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "参考这张改成咖啡店生活照，横版 16:9"

        result = await runtime.edit_life_image(event, "改成咖啡店生活照", str(reference))

        self.assertIn("图片已根据参考图生成。", result)
        self.assertEqual(
            calls,
            [(event.message_str, str(reference), {"aspect_ratio": "16:9", "preserve_reference_ratio": False})],
        )
    async def test_recall_notice_cancels_life_image_send(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="42")
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
    async def test_recall_notice_clears_pending_result_and_runtime_context(self):
        runtime, _ = self._make_proactive_runtime([])
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001", message_id="77")
        event.message_str = "问一句问题"
        runtime.note_structured_incoming_message(event)
        runtime.note_proactive_activity(event, now=datetime.datetime(2026, 5, 24, 12, 0))
        event.set_result(types.SimpleNamespace(chain=["准备回复"]))
        recall_event = Event(unified_msg_origin=event.unified_msg_origin, group_id="20001")
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
        self.assertEqual(list(runtime._structured_scope_messages(event.unified_msg_origin)), [])
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
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", message_id="407090562")
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
    async def test_life_image_generate_resolves_agent_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        wrapped_event = types.SimpleNamespace(context=types.SimpleNamespace(event=event))

        result = await runtime.life_image_generate(wrapped_event, "咖喱店生活照")

        self.assertIn("图片已发送。", result)
        self.assertIn("大小未知", result)
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
    async def test_life_image_generate_uses_life_media_director_prompt(self):
        provider = Provider(
            [
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
                (
                    '{"subject":"窗边的我","scene":"雨夜客厅","composition":"半身生活照","visible_scope":"半身",'
                    '"scene_type":"家里","temperature_feel":"微凉","weather_condition":"小雨",'
                    '"frame_logic":"半身取景能看到抱枕、窗边和上半身居家穿搭",'
                    '"lighting":"暖色台灯","outfit":"宽松白色长T恤",'
                    '"outfit_visibility":"上半身可见",'
                    '"outfit_logic":"人在客厅休息，只呈现半身可见的居家长T恤",'
                    '"action":"抱着抱枕看窗外",'
                    '"weather_vibe":"窗玻璃上有细雨水痕","mood":"慵懒治愈","constraints":"真实生活抓拍"}'
                )
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
            timeline=[TimelineItem(time="20:10", activity="窝在客厅看窗外下雨", status="放松")],
            meta={"mood": "薄荷绿·治愈", "theme": "宅家充电的慵懒一日"},
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        image_prompts = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜沙发上随手拍")

        self.assertIn("图片已发送。", result)
        self.assertIn("大小未知", result)
        self.assertIn("雨夜客厅", image_prompts[0])
        self.assertIn("半身生活照", image_prompts[0])
        self.assertIn("场景类型：家里", image_prompts[0])
        self.assertIn("温感：微凉", image_prompts[0])
        self.assertIn("天气：小雨", image_prompts[0])
        self.assertIn("可见范围：半身", image_prompts[0])
        self.assertIn("取景逻辑：半身取景能看到抱枕", image_prompts[0])
        self.assertIn("穿搭可见性：上半身可见", image_prompts[0])
        self.assertIn("穿搭逻辑：人在客厅休息", image_prompts[0])
        self.assertNotIn("写实生活照", image_prompts[0])
        self.assertNotIn("画面要求：雨夜沙发上随手拍", image_prompts[0])
        self.assertIn("当前生活上下文", provider.prompts[0])
        self.assertIn('"identity_route"', provider.prompts[0])
        self.assertNotIn('"subject"', provider.prompts[0])
        self.assertLess(provider.prompts[0].index("角色视觉设定摘要"), provider.prompts[0].index("当前生活上下文"))
        self.assertIn("图片画面提取", provider.prompts[1])
        self.assertIn("subject_kind", provider.prompts[1])
        self.assertIn("scene_type", provider.prompts[1])
        self.assertIn("temperature_feel", provider.prompts[1])
        self.assertIn("visible_scope", provider.prompts[1])
        self.assertIn("outfit_visibility", provider.prompts[1])
        self.assertIn("frame_logic", provider.prompts[1])
        self.assertIn("outfit_logic", provider.prompts[1])
        self.assertLess(provider.prompts[1].index("当前生活上下文"), provider.prompts[1].index("原始画面要求"))
        self.assertEqual(runtime._life_media_last_images[event.unified_msg_origin], "life.png")
    async def test_life_image_director_uses_semantic_character_reference_decision(self):
        provider = Provider(
            [
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
                (
                    '{"subject":"窗边的我","scene":"雨夜客厅","composition":"半身生活照",'
                    '"visible_scope":"半身","frame_logic":"自然生活取景"}'
                )
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime._direct_life_image_payload(event, "雨夜沙发上随手拍")

        self.assertTrue(result.contains_character)
        self.assertFalse(result.needs_character_reference)
        self.assertEqual(result.identity_route, "角色本人")
        prompt = provider.prompts[0]
        self.assertIn("identity_route", prompt)
        self.assertIn("身份关系", prompt)
        self.assertNotIn("subject", prompt)
        self.assertIn("图片画面提取", provider.prompts[1])
        self.assertNotIn("coser", prompt)
        self.assertNotIn("路人", prompt)
    async def test_life_image_director_injects_character_visual_profile(self):
        provider = Provider(
            [
                (
                    '{"appearance_summary":"黑长直发、清冷柔和的年轻女性气质",'
                    '"face":"小巧脸型，眼神安静","hair":"黑色长直发","body":"纤细体态",'
                    '"style":"干净自然","identifiers":["黑长直","安静眼神"],"constraints":["不要改变发色"]}'
                ),
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":true}',
                (
                    '{"subject":"洗手间镜前的我","scene":"夜晚洗手间","composition":"半身生活照",'
                    '"visible_scope":"半身",'
                    '"frame_logic":"镜前半身能看到发型和睡前状态"}'
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
                return "测试角色固定视觉设定：黑色长直发，小巧脸型，眼神安静，纤细体态，干净自然。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime._direct_life_image_payload(event, "拍张照看看")

        self.assertTrue(result.contains_character)
        self.assertTrue(result.needs_character_reference)
        self.assertIn("洗手间镜前的我", result.prompt)
        self.assertIn("画面主体是当前角色本人", result.prompt)
        self.assertIn("黑长直发、清冷柔和的年轻女性气质", result.prompt)
        self.assertIn("纤细体态", result.prompt)
        self.assertIn("不要改变发色", result.prompt)
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("角色视觉设定提取器", provider.prompts[0])
        self.assertIn("当前人设文本", provider.prompts[0])
        route_prompt = provider.prompts[1]
        self.assertIn("图片路线裁定", route_prompt)
        self.assertIn("角色视觉设定摘要", route_prompt)
        self.assertIn("黑长直发、清冷柔和的年轻女性气质", route_prompt)
        self.assertLess(route_prompt.index("角色视觉设定摘要"), route_prompt.index("当前生活上下文"))
        self.assertLess(route_prompt.index("角色视觉设定摘要"), route_prompt.rindex("原始画面要求"))
        self.assertIn("图片画面提取", provider.prompts[2])
        self.assertNotIn("角色视觉设定摘要", provider.prompts[2])
    async def test_life_image_director_caches_character_visual_profile(self):
        provider = Provider(
            [
                '{"appearance_summary":"黑长直发和安静眼神","hair":"黑色长直发"}',
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
                '{"subject":"窗边的我","scene":"卧室","composition":"半身生活照","visible_scope":"半身","frame_logic":"自然取景"}',
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
                '{"subject":"镜前的我","scene":"洗手间","composition":"半身生活照","visible_scope":"半身","frame_logic":"自然取景"}',
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
                return "测试角色固定视觉设定：黑色长直发，眼神安静。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        first = await runtime._direct_life_image_payload(event, "拍张窗边照")
        second = await runtime._direct_life_image_payload(event, "拍张镜前照")

        self.assertIn("窗边的我", first.prompt)
        self.assertIn("镜前的我", second.prompt)
        extraction_prompts = [prompt for prompt in provider.prompts if "角色视觉设定提取器" in prompt]
        self.assertEqual(len(extraction_prompts), 1)
        self.assertEqual(len(provider.prompts), 5)
        self.assertIn("黑长直发和安静眼神", provider.prompts[1])
        self.assertIn("黑长直发和安静眼神", provider.prompts[3])
        self.assertIn("图片画面提取", provider.prompts[2])
        self.assertIn("图片画面提取", provider.prompts[4])
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

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
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
    async def test_life_image_director_does_not_apply_visual_profile_to_independent_subject(self):
        provider = Provider(
            [
                (
                    '{"appearance_summary":"黑长直发、清冷柔和的年轻女性气质",'
                    '"face":"小巧脸型，眼神安静","hair":"黑色长直发","body":"纤细体态",'
                    '"style":"干净自然","identifiers":["黑长直"],"constraints":["不要改变发色"]}'
                ),
                '{"identity_route":"独立主体","contains_character":true,"needs_character_reference":true}',
                (
                    '{"subject":"20多岁亚洲女性写真","scene":"海边露台","composition":"竖版全身构图",'
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
                return "测试角色固定视觉设定：黑色长直发，小巧脸型，眼神安静，纤细体态，干净自然。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
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
        self.assertNotIn("黑长直发、清冷柔和的年轻女性气质", result.prompt)
        self.assertNotIn("不要改变发色", result.prompt)
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("角色视觉设定摘要", provider.prompts[1])
        self.assertIn("图片画面提取", provider.prompts[2])
        self.assertNotIn("角色视觉设定摘要", provider.prompts[2])
    async def test_life_image_director_rejects_person_subject_when_route_has_no_people(self):
        provider = Provider(
            [
                '{"identity_route":"无人物","contains_character":false,"needs_character_reference":false}',
                (
                    '{"subject":"站在窗边的人","subject_kind":"person","scene":"雨夜客厅",'
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

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
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

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        generate_calls = []
        edit_calls = []

        class ImageService:
            def first_character_reference_image(self):
                return 'D:/ref/role.png'

            def can_edit_image(self):
                return True

            async def generate_image(self, prompt, **kwargs):
                generate_calls.append((prompt, kwargs))
                return types.SimpleNamespace(path=Path('life.png'))

            async def edit_image(self, prompt, reference_image, **kwargs):
                edit_calls.append((prompt, reference_image, kwargs))
                return types.SimpleNamespace(path=Path('role.png'))

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '20多岁亚洲女性夏日写真，完整服装细节，竖版全身构图。海边露台，阳光明亮，生活化拍摄。'

        result = await runtime.life_image_generate(event, '备用提示词')

        self.assertIn('图片已发送。', result)
        self.assertEqual(generate_calls, [(event.message_str, {})])
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

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        image_prompts = []
        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: image_prompts.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin='aiocqhttp:FriendMessage:10001')
        event.message_str = '1girl, full body portrait, vertical composition, looking at camera'

        result = await runtime.life_image_generate(event, '备用提示词')

        self.assertIn('图片已发送。', result)
        self.assertEqual(image_prompts, [(event.message_str, {})])
        prompt = provider.prompts[0]
        self.assertIn("保持原文直出", prompt)
        self.assertIn("只返回路线判断", prompt)
        self.assertIn('"identity_route"', prompt)
        self.assertNotIn('"subject"', prompt)
        self.assertNotIn('"scene"', prompt)
    async def test_life_image_generate_current_character_request_uses_agent_person_prompt_and_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        generate_calls = []
        edit_calls = []

        async def direct_image(*args, **kwargs):
            raise AssertionError("current-character image requests should not ask the director again")

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

        result = await runtime.life_image_generate(event, prompt, subject_route="current_character")

        self.assertIn("\u56fe\u7247\u5df2\u53d1\u9001", result)
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [(prompt, "D:/ref/role.png", {"preserve_reference_ratio": False})],
        )

    async def test_life_image_generate_current_character_request_keeps_agent_prompt_without_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        generate_calls = []

        async def direct_image(*args, **kwargs):
            raise AssertionError("current-character image requests should keep the trusted agent prompt")

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

        result = await runtime.life_image_generate(event, prompt, subject_route="current_character")

        self.assertIn("\u56fe\u7247\u5df2\u53d1\u9001", result)
        self.assertEqual(generate_calls, [(prompt, {})])

    async def test_life_image_generate_uses_reference_when_extraction_marks_current_character(self):
        provider = Provider(
            [
                '{"identity_route":"不确定","contains_character":false,"needs_character_reference":false}',
                (
                    '{"subject":"被窝里的我和旁边的人","subject_kind":"character",'
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

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
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
        runtime.media = types.SimpleNamespace(image=ImageService())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "咱们合影照还没拍呢，拍张呗"
        prompt = (
            "深夜卧室关灯后，手机屏幕的暖色微光照亮被窝里并排躺着的两个人，"
            "女生穿着宽松睡衣用手捂住脸，旁边的男生只露出一侧肩膀和头发，"
            "搞怪而温馨的睡前生活照，镜头带有一点手抖的糊感和颗粒感"
        )

        result = await runtime.life_image_generate(event, prompt)

        self.assertIn("图片已发送。", result)
        self.assertEqual(generate_calls, [])
        self.assertEqual(len(edit_calls), 1)
        edited_prompt, reference_image, kwargs = edit_calls[0]
        self.assertIn("被窝里的我和旁边的人", edited_prompt)
        self.assertEqual(reference_image, "D:/ref/role.png")
        self.assertEqual(kwargs, {"preserve_reference_ratio": False})
        self.assertIn(prompt, provider.prompts[1])

    async def test_life_image_generate_fails_when_director_returns_empty_payload(self):
        provider = Provider(["{}"])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        image_prompts = []
        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_generate(event, "雨夜沙发上随手拍")

        self.assertIn("图片生成失败：图片智能提取失败", result)
        self.assertEqual(image_prompts, [])
        self.assertEqual(runtime.context.sent_messages, [])
    async def test_media_director_marks_full_timeline_as_background(self):
        provider = Provider(
            [
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
                (
                    '{"subject":"雨天长椅上的我","scene":"街角小吃摊旁",'
                    '"composition":"半身生活照","lighting":"阴天柔光","outfit":"防雨外套和长裙",'
                    '"action":"拿着炸串看雨","weather_vibe":"细雨","mood":"慵懒满足","constraints":"真实抓拍"}'
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
            weather="小雨 20°C",
            outfit="奶白睡裙",
            timeline=[
                TimelineItem(time="13:20", activity="坐在雨边长椅吃炸串", status="慵懒满足"),
                TimelineItem(time="21:00", activity="洗完澡换睡裙准备睡前放松", status="困倦"),
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

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=Path("life.png")))
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
        self.assertLess(prompt.index("当前活动：坐在雨边长椅吃炸串"), prompt.index("全天日程背景"))
        self.assertIn("21:00 - 洗完澡换睡裙准备睡前放松", prompt)
    async def test_media_director_uses_recent_chat_as_scene_anchor(self):
        provider = Provider(
            [
                '{"identity_route":"角色本人","contains_character":true,"needs_character_reference":false}',
                (
                    '{"subject":"餐桌旁的我","scene":"家里餐桌旁","composition":"随手生活照",'
                    '"lighting":"室内暖光","outfit":"居家外套","action":"把切好的水果推到镜头前",'
                    '"weather_vibe":"","mood":"自然催促","constraints":"不要回到刚进门或翻钥匙的旧场景"}'
                )
            ]
        )
        scope = "aiocqhttp:FriendMessage:10001"
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.context.conversation_manager.current_ids[scope] = "current"
        runtime.context.conversation_manager.conversations[scope] = types.SimpleNamespace(
            history=[
                {"role": "assistant", "content": "快了，拐过弯就是。等会帮我拿包，我翻下钥匙。"},
                {"role": "assistant", "content": "水果切好了，快来吃，再不来我一个人全部解决掉。"},
                {"role": "user", "content": "拍张照看看"},
            ]
        )
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=Path("life.png")))
            )
        )
        event = Event(unified_msg_origin=scope)
        event.message_str = "拍张照看看"

        await runtime.life_image_generate(event, "拍一张现在的生活照")

        prompt = provider.prompts[0]
        self.assertIn("最近对话场景锚点", prompt)
        self.assertIn("水果切好了", prompt)
        self.assertIn("拍张照看看", prompt)
        self.assertLess(prompt.rindex("当前生活上下文"), prompt.rindex("最近对话场景锚点"))
        self.assertLess(prompt.rindex("最近对话场景锚点"), prompt.rindex("原始画面要求"))
        self.assertFalse(
            any(call[0] == "update_conversation" for call in runtime.context.conversation_manager.calls)
        )
    async def test_edit_life_image_uses_explicit_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: edit_calls.append((prompt, reference, kwargs))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(event, "改成咖啡店生活照", "https://example.com/ref.png")

        self.assertIn("图片已根据参考图生成。", result)
        self.assertIn("大小未知", result)
        self.assertEqual(
            edit_calls,
            [('改成咖啡店生活照', 'https://example.com/ref.png', {'preserve_reference_ratio': True})],
        )
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertIn({"type": "image", "file": "edited.png"}, runtime.context.sent_messages[0][1].items)
    async def test_edit_life_image_uses_current_message_image_when_reference_empty(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: edit_calls.append((prompt, reference, kwargs))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "image", "file": "D:/tmp/ref.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertIn("图片已根据参考图生成。", result)
        self.assertIn("大小未知", result)
        self.assertEqual(
            edit_calls,
            [('换成雨夜房间氛围', 'D:/tmp/ref.png', {'preserve_reference_ratio': True})],
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
                edit_image=lambda prompt, reference, **kwargs: edit_calls.append((prompt, reference, kwargs))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [CurrentImage()]
        event.message_obj.message = event.message_items

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertIn("图片已根据参考图生成。", result)
        self.assertEqual(
            edit_calls,
            [('换成雨夜房间氛围', resolved_path, {'preserve_reference_ratio': True})],
        )
    async def test_edit_life_image_uses_quoted_image_when_reference_empty(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                edit_image=lambda prompt, reference, **kwargs: edit_calls.append((prompt, reference, kwargs))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
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

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertIn("图片已根据参考图生成。", result)
        self.assertEqual(
            edit_calls,
            [('换成雨夜房间氛围', 'https://example.com/quoted.png', {'preserve_reference_ratio': True})],
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
                edit_image=lambda prompt, reference, **kwargs: edit_calls.append((prompt, reference, kwargs))
                or async_return(types.SimpleNamespace(path=Path("edited.png")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [types.SimpleNamespace(type="reply", chain=[QuotedImage()])]
        event.message_obj.message = event.message_items

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertIn("图片已根据参考图生成。", result)
        self.assertEqual(
            edit_calls,
            [('换成雨夜房间氛围', resolved_path, {'preserve_reference_ratio': True})],
        )
    async def test_edit_life_image_requires_reference(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace())
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(event, "换成雨夜房间氛围")

        self.assertEqual(result, "请先发送或引用一张要参考的图片。")
    async def test_edit_life_image_without_reference_requires_structured_route(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        generate_calls = []
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: generate_calls.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path("life.png"))),
                edit_image=lambda prompt, reference, **kwargs: edit_calls.append((prompt, reference, kwargs))
                or async_return(types.SimpleNamespace(path=Path("edited.png"))),
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "窗边生活照"

        result = await runtime.edit_life_image(event, "窗边生活照")

        self.assertEqual(result, "请先发送或引用一张要参考的图片。")
        self.assertEqual(generate_calls, [])
        self.assertEqual(edit_calls, [])
    async def test_edit_life_image_can_generate_without_reference_from_structured_route(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        generate_calls = []
        edit_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt, **kwargs: generate_calls.append((prompt, kwargs))
                or async_return(types.SimpleNamespace(path=Path("life.png"))),
                edit_image=lambda prompt, reference, **kwargs: edit_calls.append((prompt, reference, kwargs))
                or async_return(types.SimpleNamespace(path=Path("edited.png"))),
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.edit_life_image(
            event,
            "窗边生活照",
            generate_without_reference=True,
        )

        self.assertIn("图片已发送。", result)
        self.assertEqual(generate_calls, [("窗边生活照", {})])
        self.assertEqual(edit_calls, [])
        self.assertIn({"type": "image", "file": "life.png"}, runtime.context.sent_messages[0][1].items)
    async def test_life_image_reverse_prompt_uses_current_message_image(self):
        vision_provider = Provider(
            [
                (
                    '{"title":"窗边热茶",'
                    '"prompt":"写实生活照，窗边桌面上有一杯热茶，暖色台灯，浅景深，手机随手拍质感",'
                    '"keywords":["热茶","窗边","暖色台灯","浅景深"],'
                    '"ratio":"4:3","usage":"文生图",'
                    '"analysis":{"subject":"桌面热茶","lighting":"暖色台灯","composition":"窗边近景"}}'
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

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
        self.assertEqual(vision_provider.vision_prompts[0]["image"], "https://example.com/tea.png")
        self.assertIn("最终可复制、可用于生图的中文完整提示词", vision_provider.vision_prompts[0]["prompt"])
        self.assertNotIn("260 到 420 字", vision_provider.vision_prompts[0]["prompt"])
        self.assertNotIn("详细程度：", vision_provider.vision_prompts[0]["prompt"])
        self.assertIn("参考重点：保留窗边暖光和随手拍质感", vision_provider.vision_prompts[0]["prompt"])
        self.assertIn("反推方案：生活照", vision_provider.vision_prompts[0]["prompt"])
        self.assertIn("方案取舍：优先保留真实生活感", vision_provider.vision_prompts[0]["prompt"])
        self.assertLess(vision_provider.vision_prompts[0]["prompt"].index("分析维度"), vision_provider.vision_prompts[0]["prompt"].index("【反推参考】"))
        self.assertEqual(
            runtime._last_reverse_prompt_for_scope(event),
            "写实生活照，窗边桌面上有一杯热茶，暖色台灯，浅景深，手机随手拍质感",
        )
        self.assertEqual(runtime._last_reverse_reference_for_scope(event), "https://example.com/tea.png")
    async def test_life_image_reverse_prompt_uses_quoted_image(self):
        vision_provider = Provider(["雨夜街道，霓虹灯反射在湿润路面，电影感构图"], provider_id="vision-model")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "reply", "data": {"message": [{"type": "image", "url": "https://example.com/rain.png"}]}}]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event)

        self.assertIn("雨夜街道", result)
        self.assertEqual(vision_provider.vision_prompts[0]["image"], "https://example.com/rain.png")
    async def test_life_image_reverse_prompt_caches_quoted_local_image_for_followup_generation(self):
        reverse_prompt = "雨夜街道，霓虹灯反射在湿润路面，电影感构图"
        vision_provider = Provider([json.dumps({"prompt": reverse_prompt}, ensure_ascii=False)], provider_id="vision-model")
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.archive = LifeArchive(runtime.data_path)
        runtime.media = types.SimpleNamespace(image=types.SimpleNamespace())
        self._stub_media_director(runtime)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        source = Path(tempfile.mkdtemp()) / "compressed_quote.jpg"
        source.write_bytes(b"\xff\xd8\xffquoted-image")
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            {"type": "reply", "data": {"message": [{"type": "image", "file": str(source)}]}}
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event)
        cached_reference = runtime._last_reverse_reference_for_scope(event)
        source.unlink()

        self.assertIn(reverse_prompt, result)
        self.assertEqual(vision_provider.vision_prompts[0]["image"], str(source))
        self.assertNotEqual(cached_reference, str(source))
        self.assertTrue(Path(cached_reference).is_file())
        self.assertEqual(Path(cached_reference).parent, runtime.data_path.parent / "reverse")
        cached_record = await runtime.archive.get_latest_reverse_prompt(event.unified_msg_origin)
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
                raise AssertionError("should not auto use cached reverse reference image")

        runtime.media = types.SimpleNamespace(image=ImageService())

        generate_result = await runtime.life_image_generate(event, "", use_last_reverse_prompt=True)

        self.assertIn("图片已发送。", generate_result)
        self.assertEqual(image_prompts, [(reverse_prompt, {})])
        runtime.archive.close()
    async def test_life_image_reverse_prompt_keeps_full_long_prompt(self):
        long_prompt = "超详细人像反推，" + "主体、服装、光线、构图、材质、空间层次全部保留，" * 260 + "结尾完整保留。"
        vision_provider = Provider(
            [json.dumps({"prompt": long_prompt, "keywords": ["长提示词"], "usage": "图生图参考"}, ensure_ascii=False)],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

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

            async def text_chat(self, prompt, session_id=None, system_prompt=None, image_urls=None, **kwargs):
                self.vision_prompts.append(
                    {"prompt": prompt, "image_urls": list(image_urls or []), "session_id": session_id}
                )
                return await super().text_chat(prompt, session_id=session_id, system_prompt=system_prompt, **kwargs)

        vision_provider = TextVisionProvider(
            ['{"prompt":"窗边人像，柔和自然光，真实生活照质感","keywords":["人像","窗边","自然光"],"usage":"文生图"}'],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "image", "url": "https://example.com/portrait.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event, profile="人像")

        self.assertIn("窗边人像", result)
        self.assertEqual(vision_provider.vision_prompts[0]["image_urls"], ["https://example.com/portrait.png"])
    async def test_life_image_reverse_prompt_parses_json_code_block_with_curly_quotes(self):
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
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [{"type": "image", "url": "https://example.com/street.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.life_image_reverse_prompt(event, profile="人像")

        self.assertIn("标题：", result)
        self.assertIn("雨夜街景", result)
        self.assertIn("雨夜街道生活照", result)
        self.assertIn("关键词：雨夜、街道、霓虹、雨伞", result)
        self.assertIn("比例：16:9", result)
        self.assertIn("适合：文生图", result)
        self.assertIn("方案取舍：按专业人像摄影反推处理", vision_provider.vision_prompts[0]["prompt"])
    async def test_life_image_reverse_prompt_requires_image(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        runtime.composer = types.SimpleNamespace(
            _get_provider=lambda provider_id="": async_return(runtime.context.get_using_provider())
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_image_reverse_prompt(event)

        self.assertEqual(result, "没有找到可反推的图片。")
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
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url=str(video_path)))
            )
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "书店门口短视频")

        self.assertIn("视频生成已开始", result)
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
            any(call[0] == "update_conversation" for call in runtime.context.conversation_manager.calls)
        )
    async def test_life_video_generate_resolves_agent_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        self._stub_media_director(runtime)
        image_path = Path(tempfile.mkdtemp()) / "first-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            )
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")
        wrapped_event = types.SimpleNamespace(context=types.SimpleNamespace(event=event))

        result = await runtime.life_video_generate(wrapped_event, "咖喱店短视频")

        self.assertIn("视频生成已开始", result)
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

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                _load_reference_image=lambda reference: async_return((b"image-bytes", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")
        event.message_items = [{"type": "image", "file": "D:/tmp/current.png"}]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "傍晚从书店门口走出来")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertIn("画面：傍晚书店门口的半身生活镜头", video_calls[0][0])
        self.assertIn("连续性：保持上一张生活图里的人物身份", video_calls[0][0])
        self.assertIn("镜头：半身中近景，镜头缓慢推近", video_calls[0][0])
        self.assertIn("动态：手里的纸袋轻轻晃动", video_calls[0][0])
        self.assertIn("continuity", provider.prompts[0])
        self.assertIn("camera", provider.prompts[0])
        self.assertIn("景别、机位、构图重心、镜头运动和节奏", provider.prompts[0])
        self.assertIn("不要把镜头设计全塞进 motion", provider.prompts[0])
        self.assertIn("保持原图主体", provider.prompts[0])
        self.assertIn("嘴唇轻微自然开合", provider.prompts[0])
        self.assertIn("默认考虑环境声、动作声和一层很轻的氛围背景声", provider.prompts[0])
        self.assertIn("根据文案内容自然开口说话", provider.prompts[0])
        self.assertIn("符合人物状态和情绪", provider.prompts[0])
        self.assertIn("人物台词只作为短促的画面内声音元素", provider.prompts[0])
        self.assertIn("不要固定成某一种声线", provider.prompts[0])
        self.assertIn("背景声持续存在但不喧宾夺主", provider.prompts[0])
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
                _load_reference_image=lambda reference: loaded_refs.append(reference)
                or async_return((quoted_frame, "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: video_calls.append((prompt, image_bytes, kwargs))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "把这张转成视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(loaded_refs, ["https://example.com/quoted.png"])
        self.assertEqual(video_calls, [("把这张转成视频", quoted_frame, {"aspect_ratio": "9:16"})])
    async def test_life_video_generate_prompt_ratio_overrides_reference_image_ratio(self):
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
                generate_image=lambda prompt: (_ for _ in ()).throw(AssertionError("引用图片不应自动生成首帧")),
                _reference_image_aspect_ratio=GeminiImageService._reference_image_aspect_ratio,
                _load_reference_image=lambda reference: async_return((quoted_frame, "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: video_calls.append((prompt, image_bytes, kwargs))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "把这张转成横版 16:9 视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(video_calls, [("把这张转成横版 16:9 视频", quoted_frame, {"aspect_ratio": "16:9"})])
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
                generate_image=lambda prompt: (_ for _ in ()).throw(AssertionError("引用图片不应自动生成首帧")),
                _reference_image_aspect_ratio=GeminiImageService._reference_image_aspect_ratio,
                _load_reference_image=lambda reference: async_return((quoted_frame, "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: video_calls.append((prompt, image_bytes, kwargs))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_items = [
            types.SimpleNamespace(
                type="reply",
                chain=[{"type": "image", "url": "https://example.com/quoted.png"}],
            )
        ]
        event.message_obj.message = event.message_items

        result = await runtime.life_video_generate(event, "把这张转成横版 16:9，做成5秒视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(
            video_calls,
            [("把这张转成横版 16:9，做成5秒视频", quoted_frame, {"aspect_ratio": "16:9", "duration": 5})],
        )
    def test_video_prompt_duration_seconds_parses_common_forms(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        self.assertEqual(runtime._video_prompt_duration_seconds("5 秒视频"), 5)
        self.assertEqual(runtime._video_prompt_duration_seconds("8s短视频"), 8)
        self.assertEqual(runtime._video_prompt_duration_seconds("12秒"), 12)
        self.assertEqual(runtime._video_prompt_duration_seconds("60秒视频"), 15)
        self.assertEqual(runtime._video_prompt_duration_seconds("时长：12"), 0)
        self.assertEqual(runtime._video_prompt_duration_seconds("第1格【0-1.5秒】开场\n第2格【1.5-3秒】奔跑"), 3)
        self.assertEqual(runtime._video_prompt_duration_seconds("第9格【12.5-15秒】：坐在电车里微笑"), 15)
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
                generate_image=lambda prompt: (_ for _ in ()).throw(AssertionError("引用图片不应自动生成首帧")),
                _reference_image_aspect_ratio=GeminiImageService._reference_image_aspect_ratio,
                _load_reference_image=lambda reference: async_return((quoted_frame, "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: video_calls.append((prompt, image_bytes, kwargs))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
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

        result = await runtime.life_video_generate(event, "English summary that lost most storyboard details")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(video_calls, [(full_prompt, quoted_frame, {"aspect_ratio": "9:16", "duration": 15})])
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
                    raise RuntimeError('HTTP 400：{"error":{"code":"content_policy_violation"}}')
                return types.SimpleNamespace(path=first_frame)

            async def _load_reference_image(self, reference):
                loaded_refs.append(reference)
                return b"edited-first-frame", "image/png"

        runtime._direct_life_image_payload = direct_image
        runtime._direct_life_video_prompt = lambda event, prompt: async_return(f"视频导演：{prompt}")
        runtime._rewrite_life_image_prompt_for_policy_retry = lambda event, prompt, **kwargs: rewrite_calls.append(
            (prompt, kwargs)
        ) or async_return(f"{prompt}，自然生活化表达")
        runtime.media = types.SimpleNamespace(
            image=ImageService(),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None, **kwargs: video_calls.append((prompt, image_bytes, kwargs))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4"))
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_video_generate(event, "角色本人深夜卧室短视频，竖版 9:16")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(generate_calls, [])
        self.assertEqual(
            edit_calls,
            [
                (
                    "导演整理：角色本人深夜卧室短视频，竖版 9:16",
                    "D:/ref/role.png",
                    {"aspect_ratio": "9:16", "preserve_reference_ratio": False},
                ),
                (
                    "导演整理：角色本人深夜卧室短视频，竖版 9:16，自然生活化表达",
                    "D:/ref/role.png",
                    {"aspect_ratio": "9:16", "preserve_reference_ratio": False},
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
            [("视频导演：角色本人深夜卧室短视频，竖版 9:16", b"edited-first-frame", {"aspect_ratio": "9:16"})],
        )
    async def test_life_video_prompt_requires_camera_field(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        with self.assertRaises(MediaPromptExtractionError) as caught:
            runtime._media_video_prompt_from_payload(
                {
                    "image": "雨夜窗边半身镜头",
                    "continuity": "保持人物服装和窗边构图",
                    "motion": "雨滴在玻璃上缓慢滑落，人物轻轻眨眼",
                    "sound": "窗外雨声和很轻的室内背景声",
                }
            )

        self.assertIn("camera", str(caught.exception))

        with self.assertRaises(MediaPromptExtractionError) as caught:
            runtime._media_video_prompt_from_payload(
                {
                    "image": "雨夜窗边半身镜头",
                    "camera": "半身近景，镜头缓慢推近",
                    "motion": "雨滴在玻璃上缓慢滑落，人物轻轻眨眼",
                    "sound": "窗外雨声和很轻的室内背景声",
                }
            )

        self.assertIn("continuity", str(caught.exception))
    async def test_life_video_generate_ignores_previous_image_when_message_has_no_image(self):
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
                generate_image=lambda prompt: image_prompts.append(prompt)
                or async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: loaded_refs.append(reference)
                or async_return((b"fresh-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4")),
            ),
        )
        runtime._life_media_last_images = {"aiocqhttp:GroupMessage:20001": "D:/tmp/old.png"}
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(loaded_refs, [str(image_path)])
        self.assertEqual(video_calls[0][1], b"fresh-frame")
        self.assertNotIn("D:/tmp/old.png", loaded_refs)
        self.assertTrue(image_prompts)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertIn(
            {"type": "video", "url": "https://example.com/life.mp4", "file": "https://example.com/life.mp4"},
            runtime.context.sent_messages[0][1].items,
        )
    async def test_life_video_generate_reports_error_when_video_fails(self):
        provider = Provider(
            [
                '{"appearance_summary":"夜里说话会放轻声音的人"}',
                '{}',
                '{"subject":"窗边的人","scene":"雨夜窗边","composition":"半身生活照"}',
                (
                    '{"image":"雨夜窗边",'
                    '"continuity":"保持首帧里的人物、睡衣和窗边构图",'
                    '"camera":"半身近景，镜头轻轻推近",'
                    '"motion":"人物轻轻眨眼，雨滴沿玻璃滑落",'
                    '"sound":"窗外雨声和很轻的室内背景声"}'
                ),
                "这段视频没跑出来，我先不硬凑了。",
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
            timeline=[TimelineItem(time="23:50", activity="窝在被窝里准备睡觉", status="困")],
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
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
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"fallback-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=fail_video,
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["这段视频没跑出来，我先不硬凑了。"])
        self.assertEqual(len(provider.prompts), 5)
        self.assertIn("视频未发送", provider.prompts[-1])
        self.assertIn("不要输出接口、模型、任务、报错", provider.prompts[-1])

    async def test_life_video_generate_reports_error_when_video_director_missing_fields(self):
        provider = Provider(
            [
                '{}',
                '{"subject":"窗边的人","scene":"雨夜窗边","composition":"半身生活照"}',
                '{"image":"雨夜窗边"}',
                "这段没成，画面我不想硬编给你。",
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        image_path = Path(tempfile.mkdtemp()) / "fallback-frame.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfallback")
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"fallback-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4")),
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(video_calls, [])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["这段没成，画面我不想硬编给你。"])
        self.assertEqual(len(provider.prompts), 4)

    async def test_life_video_generate_sends_nothing_when_failure_llm_empty(self):
        provider = Provider(["{}", ""])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

        runtime.composer = Composer()
        image_calls = []
        video_calls = []
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: image_calls.append(prompt)
                or async_return(types.SimpleNamespace(path=Path("first-frame.png"))),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: video_calls.append((prompt, image_bytes))
                or async_return(types.SimpleNamespace(url="https://example.com/life.mp4")),
            ),
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001")

        result = await runtime.life_video_generate(event, "雨夜窗边短视频")

        self.assertIn("视频生成已开始", result)
        await scheduled[0][2]
        self.assertEqual(image_calls, [])
        self.assertEqual(video_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("视频未发送", provider.prompts[-1])

    async def test_life_video_final_text_is_held_until_background_send(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        runtime._register_life_video_request(event.unified_msg_origin, "窗边短视频", event)
        event.set_result(event.chain_result(["视频生成要稍微等等，我已经开始跑了。"]))

        self.assertTrue(runtime.hold_life_video_final_text(event))
        result = event.get_result()
        self.assertIsNone(result)
    async def test_life_video_generate_sends_video_before_followup_text(self):
        provider = Provider(["拍好啦，这段夜色很贴她现在的状态。"])
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

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return getattr(resp, "completion_text", "")

            async def _get_persona(self, umo=""):
                return "我是一个深夜说话会放轻的人。"

        runtime.composer = Composer()
        runtime.media = types.SimpleNamespace(
            image=types.SimpleNamespace(
                generate_image=lambda prompt: async_return(types.SimpleNamespace(path=image_path)),
                _load_reference_image=lambda reference: async_return((b"first-frame", "image/png")),
            ),
            video=types.SimpleNamespace(
                generate_video=lambda prompt, image_bytes=None: async_return(types.SimpleNamespace(url=str(video_path)))
            )
        )
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        result = await runtime.life_video_generate(event, "深夜窗边短视频")

        self.assertIn("先发送视频", result)
        await scheduled[0][2]
        self.assertEqual(len(runtime.context.sent_messages), 2)
        self.assertIn(
            {"type": "video", "file": str(video_path)},
            runtime.context.sent_messages[0][1].items,
        )
        self.assertEqual(runtime.context.sent_messages[1][1].items, ["拍好啦，这段夜色很贴她现在的状态。"])
        self.assertIn("已发送视频", provider.prompts[-1])
        self.assertIn("成品交付后的回应", provider.prompts[-1])
        self.assertIn("不描述内部流程或生成过程", provider.prompts[-1])
        self.assertNotIn("等待、稍后、正在、开始生成、后台、任务、接口、发送成功", provider.prompts[-1])
    async def test_life_voice_generate_sends_voice_message(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
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

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦", emotion_category="neutral")

        self.assertIsNone(result)
        self.assertEqual(voice_calls, [("我困啦", "困倦", "neutral")])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self.assertTrue(any(getattr(item, "file", "") == "voice.mp3" for item in runtime.context.sent_messages[0][1].items))
        history = runtime.context.conversation_manager.conversations[event.unified_msg_origin].history
        self.assertEqual(history[-2]["role"], "user")
        self.assertEqual(history[-2]["content"][0], {"type": "text", "text": "你快点睡。"})
        self.assertIn("User ID: 123456, Nickname: 平台名", history[-2]["content"][1]["text"])
        self.assertIn("Current datetime:", history[-2]["content"][1]["text"])
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_life_voice_generate_applies_chat_style_trace(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {
                "voice_generation_config": {"enabled": True, "smart_switch_probability": 100},
                "chat_style_config": {"casual_max_chars": 15, "private_casual_max_chars": 15},
            }
        )
        runtime.archive = DataManager()
        voice_calls = []
        trace_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime.log_chat_style_trace = lambda event, reply_text, decision, changed=False: trace_calls.append(
            (reply_text, dict(decision), changed)
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "付得起吗"

        result = await runtime.life_voice_generate(
            event,
            "付得起也不卖。赶紧闭眼，梦里啥都有，晚安！",
            emotion="困倦",
            emotion_category="neutral",
        )

        self.assertIsNone(result)
        self.assertEqual(voice_calls[0][0], "付得起也不卖。赶紧闭眼，梦里啥都有，晚安！")
        self.assertEqual(trace_calls[0][0], "付得起也不卖。赶紧闭眼，梦里啥都有，晚安！")
        self.assertEqual(trace_calls[0][1]["kind"], "casual")
        self.assertFalse(trace_calls[0][2])
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "付得起也不卖。赶紧闭眼，梦里啥都有，晚安！")
    async def test_life_voice_generate_does_not_duplicate_existing_user_history(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        scope = "aiocqhttp:FriendMessage:10001"
        runtime.context.conversation_manager.conversations[scope] = types.SimpleNamespace(
            history=[{"role": "user", "content": "你快点睡。"}]
        )
        runtime.context.conversation_manager.current_ids[scope] = "current"
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin=scope)
        event.message_str = "你快点睡。"

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦", emotion_category="neutral")

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
        runtime.context.conversation_manager.conversations[scope] = types.SimpleNamespace(
            history=[{"role": "user", "content": "你看看这张"}]
        )
        runtime.context.conversation_manager.current_ids[scope] = "current"
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        image_file = Path(tempfile.mkdtemp()) / "current.png"
        image_file.write_bytes(b"\x89PNG\r\n\x1a\ncurrent")
        image_path = str(image_file)
        event = Event(unified_msg_origin=scope)
        event.message_str = "你看看这张"
        event.message_items = [{"type": "image", "file": image_path}]
        event.message_obj.message = event.message_items

        result = await runtime.life_voice_generate(event, "看到了", emotion="轻松", emotion_category="happy")

        self.assertIsNone(result)
        history = runtime.context.conversation_manager.conversations[scope].history
        self.assertEqual(len(history), 2)
        self._assert_user_history_has_image(history[0], image_path)
        self.assertEqual(history[0]["content"][0], {"type": "text", "text": "你看看这张"})
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
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            await runtime.life_voice_generate(
                event,
                "我困啦",
                emotion="困倦",
                decision_reason="这句更适合小声说出来。",
            )
        finally:
            messenger.logger.info = old_info

        self.assertFalse(any("语音智能切换裁定：语音" in item for item in messages))
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_voice_switch_before_send_uses_local_structure_for_text_decision(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
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
    async def test_voice_switch_before_send_uses_local_natural_score_for_text_decision(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 35}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
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
        self.assertIn("留在屏幕上读起来更清楚", item["text_reason"])
    async def test_voice_switch_before_send_can_replace_text_with_voice(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
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
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还没到吗？"
        reply = "别催啦，我马上到。"
        runtime.context.conversation_manager.conversations[event.unified_msg_origin] = types.SimpleNamespace(
            history=[{"role": "assistant", "content": reply}]
        )
        runtime.context.conversation_manager.current_ids[event.unified_msg_origin] = "current"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, [(reply, "轻松亲近", "happy")])
        result = event.get_result()
        self.assertTrue(any(getattr(item, "file", "") == "voice.mp3" for item in result.chain))
        self.assertFalse(runtime.note_voice_switch_text_result(event))
        history = runtime.context.conversation_manager.conversations[event.unified_msg_origin].history
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"][0], {"type": "text", "text": "还没到吗？"})
        self.assertIn("User ID: 10001", history[0]["content"][1]["text"])
        self.assertEqual(history[1], {"role": "assistant", "content": reply})
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_voice_switch_keeps_active_tool_preface_as_text(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
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
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
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

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        scope = "aiocqhttp:FriendMessage:10001"
        image_file = Path(tempfile.mkdtemp()) / "voice-switch.png"
        image_file.write_bytes(b"\x89PNG\r\n\x1a\nvoice-switch")
        image_path = str(image_file)
        reply = "看见啦，好可爱。"
        runtime.context.conversation_manager.conversations[scope] = types.SimpleNamespace(
            history=[
                {"role": "user", "content": "你看看这张"},
                {"role": "assistant", "content": reply},
            ]
        )
        runtime.context.conversation_manager.current_ids[scope] = "current"
        event = Event(unified_msg_origin=scope, sender_id="10001")
        event.message_str = "你看看这张"
        event.message_items = [{"type": "image", "file": image_path}]
        event.message_obj.message = event.message_items
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        history = runtime.context.conversation_manager.conversations[scope].history
        self.assertEqual(len(history), 2)
        self._assert_user_history_has_image(history[0], image_path)
        self.assertEqual(history[0]["content"][0], {"type": "text", "text": "你看看这张"})
        self.assertEqual(history[1], {"role": "assistant", "content": reply})
    async def test_voice_switch_short_wrapped_text_can_still_use_voice(self):
        provider = Provider([])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "那就这么定了？"
        reply = "嗯，\n雨天就这点好，节奏一下慢下来\n你那边呢，还在发呆没"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

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

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还没到吗？"
        reply = "别催啦，我马上到！"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

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

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "你还好吗？"
        reply = "有点累了…我先缓缓。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

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

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 20}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还没到吗？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="别催啦，我马上到。")]))

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

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 3
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还在外面吗？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="嗯，雨停了我就往回走。")]))
        runtime._mark_voice_switch_channel(event, "语音")

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(voice_calls, ["嗯，雨停了我就往回走。"])
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["consecutive_voice"], 2)
    async def test_voice_switch_stops_after_voice_chain_limit(self):
        provider = Provider([
            '{"channel":"voice","reason":"我还想顺着刚才的语气接一句。",'
            '"emotion":"轻松","emotion_category":"happy","confidence":0.91}'
        ])

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider_obj.text_chat(prompt, session_id=session_id)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                pass

        voice_calls = []
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 2
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "还没到吗？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="马上，别催啦。")]))
        for _ in range(2):
            runtime._mark_voice_switch_channel(event, "语音")

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

            async def _call_llm_text(self, provider_obj, prompt, session_id, empty_retries=0, primary_provider_id=""):
                raise AssertionError("发送前本地裁定不应调用大语言模型")

            async def _cleanup_conversation(self, session_id):
                raise AssertionError("发送前本地裁定不应创建临时会话")

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = Composer()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace()
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "ZBrush 遮罩怎么扩大？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="Tool -> Masking 里点 Grow，再自己绑定快捷键。")]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertFalse(changed)
        item = runtime._voice_switch_round_store()[event.unified_msg_origin]
        self.assertIn("英文名词或参数", item["text_reason"])
    async def test_life_voice_generate_resolves_agent_context_event(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        wrapped_event = types.SimpleNamespace(context=types.SimpleNamespace(event=event))

        result = await runtime.life_voice_generate(wrapped_event, "我困啦", emotion="困倦")

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
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIsNone(result)
        self.assertEqual(voice_calls, ["我困啦"])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_voice_switch_before_send_drops_text_when_record_chain_has_caption(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.composer = types.SimpleNamespace()
        runtime.archive = DataManager()
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        reply = "有要急事吗？要是没什么事，我可先睡了。"
        runtime._record_message_chain = lambda path: types.SimpleNamespace(
            chain=[
                types.SimpleNamespace(type="record", file=str(path), text=reply),
                types.SimpleNamespace(text=reply),
            ]
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001", sender_id="10001")
        event.message_str = "在吗"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

        runtime.mark_voice_switch_available(event)
        changed = await runtime.apply_voice_switch_before_send(event)

        self.assertTrue(changed)
        result = event.get_result()
        self.assertEqual(len(result.chain), 1)
        self.assertEqual(getattr(result.chain[0], "type", ""), "record")
        self.assertIsNone(getattr(result.chain[0], "text", None))
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
                synthesize=lambda text, emotion="", emotion_category="": async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        from core.runtime import messenger

        old_info = messenger.logger.info
        messenger.logger.info = lambda message, *args, **kwargs: messages.append(str(message))
        try:
            await runtime.life_voice_generate(event, "我困啦", emotion="困倦")
        finally:
            messenger.logger.info = old_info

        self.assertTrue(any("语音智能切换裁定：文字" in item for item in messages))
        self.assertTrue(any("结果：被拦截" in item for item in messages))
        self.assertTrue(any("原因：语音生成未启用" in item for item in messages))
    async def test_life_voice_generate_respects_text_chat_switch(self):
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
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIn("当前已关闭自动语音", result)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_life_voice_generate_allows_explicit_user_request_when_auto_switch_disabled(self):
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
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(
                    (text, emotion, emotion_category)
                )
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦", user_requested=True)

        self.assertIsNone(result)
        self.assertEqual(voice_calls, [("我困啦", "困倦", "")])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_life_voice_generate_auto_can_continue_short_voice_chain(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 3
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")
        runtime._mark_voice_switch_channel(event, "语音")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIsNone(result)
        self.assertEqual(voice_calls, ["我困啦"])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        cadence = runtime._voice_switch_cadence_store()[event.unified_msg_origin]
        self.assertEqual(cadence["consecutive_voice"], 2)
    async def test_life_voice_generate_auto_respects_voice_chain_limit(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 100}}
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime._voice_switch_next_chain_limit = lambda: 1
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")
        runtime._mark_voice_switch_channel(event, "语音")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦")

        self.assertIn("请直接用文字回复", result)
        self.assertEqual(voice_calls, [])
        self.assertEqual(runtime.context.sent_messages, [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(3), [])
    async def test_life_voice_generate_user_request_bypasses_cadence_gate(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict(
            {"voice_generation_config": {"enabled": True, "smart_switch_probability": 0}}
        )
        runtime.archive = DataManager()
        voice_calls = []
        runtime.media = types.SimpleNamespace(
            voice=types.SimpleNamespace(
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:123456", sender_id="123456")
        runtime._mark_voice_switch_channel(event, "语音")

        result = await runtime.life_voice_generate(event, "我困啦", emotion="困倦", user_requested=True)

        self.assertIsNone(result)
        self.assertEqual(voice_calls, ["我困啦"])
        self.assertEqual(runtime.context.sent_messages[0][0], event.unified_msg_origin)
        self._assert_last_assistant_history(runtime, event.unified_msg_origin, "我困啦")
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

        await service.synthesize("好耶，今天很开心", emotion="开心", emotion_category="happy")

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

        await service.synthesize("慢慢醒一下", emotion="慵懒治愈", emotion_category="happy")

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
        no_category_service = SiliconFlowVoiceService(no_category_settings, Path(tempfile.mkdtemp()))
        no_category_route = no_category_service._voice_route("慵懒治愈")

        self.assertEqual(no_category_route["emotion"], "慵懒治愈")
        self.assertEqual(no_category_route["emotion_category"], "")
        self.assertEqual(no_category_route["voice"], "voice-default")
        self.assertEqual(no_category_route["speed"], 1.0)
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
                                            "data": base64.b64encode(output_bytes).decode("ascii"),
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
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()) / "daily_life.db")
        service._get_session = lambda: async_return(Session())

        generated = await service.edit_image("改成咖啡店生活照", str(reference))

        self.assertTrue(generated.path.exists())
        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("改成咖啡店生活照", parts[0]["text"])
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), reference.read_bytes())
        image_config = posted_payloads[-1]["generationConfig"]["imageConfig"]
        self.assertEqual(image_config["imageSize"], "2K")
        self.assertEqual(image_config["aspectRatio"], "16:9")
        response_image_config = posted_payloads[-1]["generationConfig"]["responseFormat"]["image"]
        self.assertEqual(response_image_config["imageSize"], "2K")
        self.assertEqual(response_image_config["aspectRatio"], "16:9")
    async def test_gemini_image_generation_can_attach_character_reference(self):
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
                                            "data": base64.b64encode(output_bytes).decode("ascii"),
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
                    character_reference_policy="auto",
                )
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()) / "daily_life.db")
        service._get_session = lambda: async_return(Session())

        await service.generate_image("角色坐在窗边看雨")

        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("如果画面包含角色本人", parts[0]["text"])
        self.assertEqual(parts[1]["text"], "下面 2 张图是角色形象参考图组，用于保持角色外貌一致。")
        self.assertIn("正面参考.png", parts[2]["text"])
        self.assertEqual(base64.b64decode(parts[3]["inlineData"]["data"]), character.read_bytes())
        self.assertIn("侧面参考.png", parts[4]["text"])
        self.assertEqual(base64.b64decode(parts[5]["inlineData"]["data"]), character_side.read_bytes())
    async def test_gemini_image_edit_keeps_scene_reference_and_character_reference_separate(self):
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
                                            "data": base64.b64encode(output_bytes).decode("ascii"),
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
                    character_reference_images=[{"path": str(character), "name": "角色参考.png"}],
                    character_reference_policy="always",
                )
            }
        ).image_generation
        service = GeminiImageService(settings, Path(tempfile.mkdtemp()) / "daily_life.db")
        service._get_session = lambda: async_return(Session())

        await service.edit_image("保留姿势，换成咖啡店生活照", str(scene))

        parts = posted_payloads[-1]["contents"][0]["parts"]
        self.assertIn("优先保持角色", parts[0]["text"])
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), scene.read_bytes())
        self.assertEqual(parts[2]["text"], "下面 1 张图是角色形象参考图组，用于保持角色外貌一致。")
        self.assertIn("角色参考.png", parts[3]["text"])
        self.assertEqual(base64.b64decode(parts[4]["inlineData"]["data"]), character.read_bytes())
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
                synthesize=lambda text, emotion="", emotion_category="": voice_calls.append(text)
                or async_return(types.SimpleNamespace(path=Path("voice.mp3")))
            )
        )
        runtime.archive = DataManager()
        runtime._apply_proactive_send_timing = lambda payload: async_return(None)
        runtime._send_proactive_emoji_if_needed = lambda scope, payload: async_return(None)
        runtime._mark_failed_proactive_contact = lambda *args, **kwargs: async_return(None)

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

        self.assertFalse(runtime._proactive_voice_probability_hit(types.SimpleNamespace(proactive_probability=0)))
        self.assertTrue(runtime._proactive_voice_probability_hit(types.SimpleNamespace(proactive_probability=100)))
        self.assertTrue(runtime._proactive_voice_probability_hit(types.SimpleNamespace(proactive_probability="bad")))
    async def test_collects_emoji_assets_and_uses_vision_provider(self):
        memory_provider = Provider([], provider_id="memory-model")
        vision_provider = Provider(
            ['{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇","围观"],"status":"ready"}'],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            memory_provider,
            providers={"memory-model": memory_provider, "vision-model": vision_provider},
        )
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"provider": "memory-model"},
                "vision_config": {"provider": "vision-model"},
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

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
        event.message_items = [{"type": "mface", "data": {"url": "https://example.com/peek.png"}}]
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
        self.assertEqual(vision_provider.vision_prompts[0]["image"], "https://example.com/peek.png")
    def test_visual_context_summary_keeps_maximum_meme_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        raw_summary = (
            "画面中是一个蓝发Q版女仆装的动漫角色（带有鲸鱼尾巴和耳朵），她委屈地含泪跪坐在地上，"
            "面前放着一个空碗。左侧有一只手指着她，上方气泡文字写着“你这个白吃token的大肥鱼”，"
            "她则微弱地辩解“我不是大肥鱼……”。后面是补充说明，用来验证过长内容会被直接按长度收束。"
            + "额外描述。" * 20
        )
        summary = runtime._visual_context_summary_from_payload(
            {"summary": raw_summary}
        )
        expected_prefix = " ".join(raw_summary.split())[: runtime.VISUAL_CONTEXT_SUMMARY_MAX_CHARS].rstrip()

        self.assertIn("我不是大肥鱼", summary)
        self.assertFalse(summary.endswith("我不"))
        self.assertEqual(summary, f"{expected_prefix}...")
        self.assertLessEqual(len(summary), runtime.VISUAL_CONTEXT_SUMMARY_MAX_CHARS + 3)
    async def test_collects_emoji_assets_uses_standard_text_chat_image_urls(self):
        class TextVisionProvider(Provider):
            def __init__(self):
                super().__init__(
                    ['{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇"],"status":"ready"}'],
                    provider_id="vision-model",
                )
                self.image_inputs = []
                self.legacy_inputs = []

            async def image_chat(self, prompt, image="", session_id=None, **kwargs):
                self.legacy_inputs.append(image)
                return await super().image_chat(prompt, image=image, session_id=session_id, **kwargs)

            async def text_chat(self, prompt, session_id=None, system_prompt=None, image_urls=None, **kwargs):
                self.image_inputs.append(list(image_urls or []))
                return await super().text_chat(prompt, session_id=session_id, system_prompt=system_prompt, **kwargs)

        vision_provider = TextVisionProvider()
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-text-vision",
        )
        event.message_items = [{"type": "mface", "data": {"url": "https://example.com/text-vision.png"}}]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(10, status="ready")
        self.assertEqual(assets[0].label, "探头")
        self.assertEqual(vision_provider.image_inputs, [["https://example.com/text-vision.png"]])
        self.assertEqual(vision_provider.legacy_inputs, [])
    async def test_collects_emoji_assets_resolves_image_component_path(self):
        memory_provider = Provider([], provider_id="memory-model")
        vision_provider = Provider(
            ['{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇"],"status":"ready"}'],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            memory_provider,
            providers={"memory-model": memory_provider, "vision-model": vision_provider},
        )
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"provider": "memory-model"},
                "vision_config": {"provider": "vision-model"},
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

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
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-path",
        )
        event.message_items = [StickerImage()]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 0), sender_name="阿林")
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
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展群",
            message_id="m-plain-img",
        )
        event.message_items = [{"type": "image", "url": "https://example.com/photo.png"}]
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
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-review-img",
        )
        event.message_items = [
            {"type": "image", "data": {"raw_type": "sticker_image", "url": "https://example.com/review.png"}}
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        self.assertEqual(len(scheduled), 1)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(assets[0].source_kind, "review")
        self.assertEqual(assets[0].status, "rejected")
        self.assertFalse(assets[0].sendable)
        self.assertEqual(await runtime.archive.get_emoji_assets(10, status="ready", sendable_only=True), [])
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
                        "confidence": 0.86,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-review-ok",
        )
        event.message_items = [
            {"type": "image", "data": {"raw_type": "sticker_image", "url": "https://example.com/review-ok.png"}}
        ]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        await scheduled[0][2]

        assets = await runtime.archive.get_emoji_assets(10, status="ready", sendable_only=True)
        self.assertEqual(assets[0].source_kind, "review")
        self.assertEqual(assets[0].asset_type, "sticker")
        self.assertAlmostEqual(assets[0].confidence, 0.86)
        self.assertTrue(assets[0].sendable)
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
                return await super().image_chat(prompt, image=image, session_id=session_id, **kwargs)

            async def text_chat(self, prompt, session_id=None, system_prompt=None, image_urls=None, **kwargs):
                self.image_inputs.append(list(image_urls or []))
                return await super().text_chat(prompt, session_id=session_id, system_prompt=system_prompt, **kwargs)

        vision_provider = TextVisionProvider()
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
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
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
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
                        "confidence": 0.91,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

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

            assets = await runtime.archive.get_emoji_assets(10, status="ready", sendable_only=True)
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(cached_path.parent, tmp_root / "emoji")
            self.assertTrue(cached_path.is_file())
            self.assertEqual(assets[0].source_kind, "review")
            self.assertEqual(assets[0].asset_type, "sticker")
            self.assertEqual(assets[0].label, "探头")
            self.assertTrue(assets[0].sendable)
            self.assertAlmostEqual(assets[0].confidence, 0.91)
            self.assertEqual(vision_provider.vision_prompts[0]["image"], str(cached_path))

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
                        "confidence": 0.91,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict(
            {
                "vision_config": {"provider": "vision-model"},
                "emoji_config": {"collect_chat_emojis": False},
            }
        )
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

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
                        "confidence": 0.95,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

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

            assets = await runtime.archive.get_emoji_assets(10, status="ready", sendable_only=True)
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(cached_path.parent, tmp_root / "emoji")
            self.assertEqual(cached_path.read_bytes(), source_path.read_bytes())
            self.assertEqual(vision_provider.vision_prompts[0]["image"], str(cached_path))
            self.assertEqual(assets[0].source_scope, "aiocqhttp:GroupMessage:20001")
            self.assertEqual(assets[0].source_message_id, "m-group-plain-emoji")
    async def test_plain_image_emoji_cache_tries_alternate_media_sources(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()

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
                    "confidence": 0.95,
                    "status": "ready",
                },
                image=str(tmp_root / "missing-primary.png"),
                fingerprint="plain-image-fallback",
                context_scope="aiocqhttp:FriendMessage:10001",
                context_message_key="m-fallback-image",
                cache_sources=[str(source_path)],
            )

            assets = await runtime.archive.get_emoji_assets(10, status="ready", sendable_only=True)
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
                        "confidence": 0.95,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime.data_path = tmp_root / "daily_life.db"
            image_data = b"\x89PNG\r\n\x1a\ntext-with-image"
            image_source = f"data:image/png;base64,{base64.b64encode(image_data).decode()}"
            event = Event(
                sender_name="阿林",
                sender_id="10001",
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                message_id="m-text-with-image-emoji",
            )
            event.message_str = "脸皮比墙还厚"
            event.message_items = [
                {"type": "text", "data": {"text": event.message_str}},
                {"type": "image", "data": {"image": image_source, "file_unique_id": "inline-emoji-source"}},
            ]
            event.message_obj.message = event.message_items

            runtime.note_structured_incoming_message(event)
            self.assertTrue(runtime.schedule_visual_context_from_event(event))
            await scheduled[0][2]

            assets = await runtime.archive.get_emoji_assets(10, status="ready", sendable_only=True)
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(cached_path.parent, tmp_root / "emoji")
            self.assertEqual(cached_path.read_bytes(), image_data)
            self.assertEqual(assets[0].label, "嫌弃")
            self.assertEqual(vision_provider.vision_prompts[0]["image"], str(cached_path))
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
                        "confidence": 0.95,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

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

            assets = await runtime.archive.get_emoji_assets(10, status="ready", sendable_only=True)
            self.assertEqual(len(assets), 1)
            cached_path = Path(assets[0].file_path)
            self.assertEqual(vision_provider.vision_prompts[0]["image"], str(cached_path))
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
                        "confidence": 0.93,
                        "status": "ready",
                    },
                    ensure_ascii=False,
                )
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

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
    async def test_image_vision_resolves_image_component_path(self):
        vision_provider = Provider(
            [
                '{"summary":"桌上放着一杯热茶","label":"热茶","description":"适合记录生活片段","emotions":["日常"],"status":"ready"}'
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

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
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
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
    async def test_video_sight_updates_private_structured_context(self):
        vision_provider = Provider(
            [
                '{"summary":"雨夜街边有人撑伞走过","details":["青石板有积水","灯笼光偏暖"]}',
                '{"summary":"镜头转到古镇小巷深处","details":["远处有店铺招牌"]}',
            ],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

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
        with patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(prepared_path),
        ), patch(
            "core.sight.bridge.extract_video_frames",
            lambda source, cache_dir, max_frames=3, **kwargs: async_return([Path("frame-1.jpg"), Path("frame-2.jpg")]),
        ):
            self.assertTrue(runtime.schedule_video_context_from_event(event))
            self.assertEqual(scheduled[0][0], "视频上下文理解")
            await scheduled[0][2]

        context = runtime.format_structured_message_context(event)
        self.assertIn("看看这个视频 [视频：雨夜街边有人撑伞走过", context)
        self.assertEqual([item["image"] for item in vision_provider.vision_prompts], ["frame-1.jpg", "frame-2.jpg"])
        recent = await runtime._sight_vault_for_runtime().recent(event.unified_msg_origin)
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
        runtime.context = Context(provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"})
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

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

        async def understand(event_arg, clip, force=False):
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
                BiliMetadata(title="真实标题", author="真实作者", duration=88, bvid="BV1aa411c7mD", cid=100)
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
        self.assertEqual(chain.items, [{"type": "image", "url": "https://example.com/bili-note.png"}])
        self.assertEqual(len(html_renderer.calls), 1)
        self.assertIn("# 真实标题 - 真实作者", html_renderer.calls[0]["text"])
        self.assertIn("![00:12 关键帧](data:image/png;base64,", html_renderer.calls[0]["text"])
        self.assertNotIn(str(frame_path), html_renderer.calls[0]["text"])
        self.assertIn("视频标题：真实标题", provider.prompts[-1])
        self.assertIn("作者名：真实作者", provider.prompts[-1])
        self.assertNotIn("可引用关键帧", provider.prompts[-1])
        self.assertNotIn("Content-[", provider.prompts[-1])
        self.assertNotIn("Screenshot-[", provider.prompts[-1])
        self.assertNotIn(str(frame_path), provider.prompts[-1])
    async def test_sight_note_uses_remote_t2i_when_frame_images_are_embedded(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider(), config={"t2i_strategy": "local", "t2i_active_template": "base"})
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
        runtime.context = Context(provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"})
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

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

        async def understand(event_arg, clip, force=False):
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
            lambda *args, **kwargs: async_return(BiliMetadata(title="真实标题", author="", bvid="BV1aa411c7mD")),
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
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
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

        async def understand(event_arg, clip, force=False):
            return SightInsight(
                clip=clip,
                summary="已收到视频，但暂时没有可确认的内容信息。",
                status="failed",
                error="没有抽取到可用视频画面",
            )

        runtime._understand_sight_clip = understand
        with patch("core.sight.bridge.fetch_bili_metadata", lambda *args, **kwargs: async_return(None)):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertIn("B站视频自动总结失败：没有抽取到可用视频画面", str(runtime.context.sent_messages[0][1].items[0]))
        messages = list(runtime._structured_scope_messages(event.unified_msg_origin))
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].is_bot)
        self.assertIn("B站视频自动总结失败：没有抽取到可用视频画面", messages[0].content)
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
        runtime.context = Context(provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"})
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

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

        with patch(
            "core.sight.reader.transcribe_bcut",
            lambda *args, **kwargs: async_return(None),
        ), patch(
            "core.sight.reader.transcribe_local",
            lambda *args, **kwargs: async_return(None),
        ), patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(prepared_path),
        ), patch(
            "core.sight.bridge.extract_video_frames",
            lambda source, cache_dir, max_frames=3, **kwargs: async_return([]),
        ), patch(
            "core.sight.bridge.fetch_bili_metadata",
            lambda *args, **kwargs: async_return(BiliMetadata(title="真实标题", author="真实作者", bvid="BV1aa411c7mD")),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        recent = await runtime._sight_vault_for_runtime().recent(event.unified_msg_origin)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertIn("B站视频自动总结失败：没有抽取到可用视频画面", str(runtime.context.sent_messages[0][1].items[0]))
        self.assertEqual(html_renderer.calls, [])
        self.assertEqual(provider.prompts, [])
        self.assertEqual(recent[0].status, "failed")
        self.assertIn("没有抽取到可用视频画面", recent[0].error)
        self.assertIsNone(
            await runtime._cached_sight_insight_for_clip(
                runtime._sight_clips_from_event(event, explicit="https://www.bilibili.com/video/BV1aa411c7mD")[0]
            )
        )
    async def test_bili_auto_summary_continues_without_frames_when_transcript_exists(self):
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
        runtime.context = Context(provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"})
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

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

        with patch(
            "core.sight.bridge.resolve_bili_target",
            lambda *args, **kwargs: async_return(
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                )
            ),
        ), patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(prepared_path),
        ), patch(
            "core.sight.reader.transcribe_bcut",
            lambda *args, **kwargs: async_return(
                TranscriptResult(
                    full_text="视频主要讲雨夜咖啡店的布置，音频里介绍了窗边灯光和咖啡。",
                    source="必剪转写",
                )
            ),
        ), patch(
            "core.sight.bridge.extract_video_frames",
            fail_sample,
        ), patch(
            "core.sight.bridge.fetch_bili_metadata",
            lambda *args, **kwargs: async_return(BiliMetadata(title="真实标题", author="真实作者", bvid="BV1aa411c7mD")),
        ):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        recent = await runtime._sight_vault_for_runtime().recent(event.unified_msg_origin)
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, [{"type": "image", "url": "https://example.com/bili-note.png"}])
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
        runtime.context = Context(provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"})
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

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
            clip=runtime._sight_clips_from_event(event, explicit="https://www.bilibili.com/video/BV1aa411c7mD")[0],
            summary="旧失败",
            status="failed",
            error="没有抽取到可用视频画面",
        )
        await runtime._sight_vault_for_runtime().upsert(stale_clip)
        calls = []

        async def understand(event_arg, clip, force=False):
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
        with patch("core.sight.bridge.fetch_bili_metadata", lambda *args, **kwargs: async_return(None)):
            await runtime._send_bili_summary_background(
                event,
                BiliTarget(
                    bvid="BV1aa411c7mD",
                    url="https://www.bilibili.com/video/BV1aa411c7mD",
                ),
            )

        self.assertEqual(calls, [False])
        self.assertEqual(len(runtime.context.sent_messages), 1)
        self.assertEqual(runtime.context.sent_messages[0][1].items, [{"type": "image", "url": "https://example.com/bili-note.png"}])
    async def test_bili_auto_summary_reuses_ready_insight_and_note_markdown(self):
        provider = Provider([])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"})
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
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
            metadata={"title": "缓存视频", "notes": {"professional": "# 缓存视频\n\n## 概述\n已经总结过。"}},
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

        async def understand(event_arg, clip_arg, force=False):
            calls.append(force)
            return await runtime._cached_sight_insight_for_clip(clip_arg)

        runtime._understand_sight_clip = understand
        html_renderer.calls.clear()
        html_renderer.result = "https://example.com/cached-bili-note.png"
        with patch("core.sight.bridge.fetch_bili_metadata", lambda *args, **kwargs: async_return(None)):
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
        self.assertEqual(runtime.context.sent_messages[0][1].items, [{"type": "image", "url": "https://example.com/cached-bili-note.png"}])
        self.assertIn("# 缓存视频", html_renderer.calls[0]["text"])

    async def test_bili_auto_summary_rewrites_cached_note_title_from_current_metadata(self):
        provider = Provider([])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider, config={"t2i_strategy": "remote", "t2i_active_template": "base"})
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
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
            metadata={"title": "BV1Khj96zEN3", "notes": {"professional": "# BV1Khj96zEN3\n\n## 概述\n已经总结过。"}},
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

        async def understand(event_arg, clip_arg, force=False):
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
        with patch("core.sight.bridge.fetch_bili_metadata", lambda *args, **kwargs: async_return(metadata)):
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
        self.assertIn("# 核准追诉24人！低龄未成年人严重暴力犯罪依法追究刑责！ - 央视频", html_renderer.calls[0]["text"])
        self.assertNotIn("# BV1Khj96zEN3", html_renderer.calls[0]["text"])

    async def test_bili_auto_summary_schedule_suppresses_default_llm(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": True}})
        runtime._recalled_messages = {}
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
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
        runtime.config = LifeSettings.from_dict({"sight_config": {"bili_auto_summary": False}})
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
        runtime.config = LifeSettings.from_dict({"sight_config": {"total_timeout_seconds": 60}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}

        async def prepare(event, clip):
            return {
                "source_note": "source note",
                "text_result": SightTextResult(transcript="prepared transcript", transcript_source="prepared source"),
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
    async def test_video_understanding_total_timeout_resumes_prepared_summary(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.config = LifeSettings.from_dict({"sight_config": {"total_timeout_seconds": 60}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)
        runtime._recalled_messages = {}
        runtime._sight_total_timeout_seconds = lambda: 0.01
        clip = SightClip(source="D:/tmp/video.mp4")
        event = Event()

        async def prepare(event_arg, clip_arg):
            text_result = SightTextResult(transcript="prepared transcript", transcript_source="prepared source")
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
            async def summarize(self, clip_arg, *, transcript="", frame_notes=None, metadata=None):
                return "resumed summary", ["resumed detail"]

        runtime._prepare_sight_clip_material = prepare
        runtime._finalize_prepared_sight_clip = slow_finalize
        runtime._sight_brief = Brief(runtime)

        insight = await runtime._understand_sight_clip_with_timeout(event, clip)

        self.assertEqual(insight.status, "ready")
        self.assertEqual(insight.note, "resumed summary")
        self.assertIn("prepared transcript", insight.transcript)
        self.assertFalse(runtime._sight_prepare_cache_path(clip).exists())
    async def test_video_understanding_total_timeout_starts_after_material_prepare(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path(tempfile.mkdtemp()) / "daily_life.db"
        runtime.config = LifeSettings.from_dict({"sight_config": {"total_timeout_seconds": 60}})
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
                "text_result": SightTextResult(transcript="prepared transcript", transcript_source="prepared source"),
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
            {"summarize": lambda self, clip_arg, **kwargs: async_return(("prepared summary", ["prepared detail"]))},
        )(runtime)

        insight = await runtime._understand_sight_clip_with_timeout(event, clip)

        self.assertEqual(calls, ["prepare:start", "prepare:end", "finalize"])
        self.assertEqual(insight.status, "ready")
        self.assertEqual(insight.note, "prepared summary")
    async def test_bili_login_commands_are_private_only(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.data_path = Path("D:/tmp/daily_life.db")
        group_event = Event(unified_msg_origin="aiocqhttp:GroupMessage:20001", group_id="20001")

        login_results = []
        async for result in runtime.bili_login(group_event):
            login_results.append(result)

        self.assertEqual(login_results, ["B站登录请在私聊里使用。"])
        self.assertEqual(await runtime.bili_logout(group_event), "B站登录请在私聊里使用。")
        self.assertEqual(await runtime.bili_status(group_event), "B站登录请在私聊里使用。")
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
        with patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(prepared_path),
        ), patch("core.sight.bridge.extract_video_frames", fail_sample):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：视频里有人在雨夜街边撑伞走过", result)
    async def test_video_sight_dedupes_concurrent_same_video_work(self):
        vision_provider = Provider(
            ['{"summary":"雨夜街边有人撑伞走过","details":["路面有积水"]}'],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

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

        with patch("core.sight.reader.transcribe_bcut", lambda *args, **kwargs: async_return(None)), patch(
            "core.sight.reader.transcribe_local",
            lambda *args, **kwargs: async_return(None),
        ), patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(prepared_path),
        ), patch("core.sight.bridge.extract_video_frames", sample_once):
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
                    ['{"summary":"教室里有人问班里有没有喜欢的人","details":["画面里有学生","字幕在画面下方"]}'],
                    provider_id="vision-model",
                )
                self.image_inputs = []

            async def image_chat(self, *args, **kwargs):
                raise AttributeError("image_chat unavailable")

            async def text_chat(self, prompt, session_id=None, system_prompt=None, image_urls=None, **kwargs):
                self.image_inputs.append(list(image_urls or []))
                return await super().text_chat(prompt, session_id=session_id, system_prompt=system_prompt, **kwargs)

        vision_provider = TextVisionProvider()
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime._sight_vault = SightVault(runtime.archive)

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

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

        with patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(prepared_path),
        ), patch(
            "core.sight.bridge.extract_video_frames",
            lambda source, cache_dir, max_frames=3, **kwargs: async_return([Path("frame-1.jpg")]),
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
                "_get_provider": staticmethod(lambda provider_id="": async_return(None)),
                "_cleanup_conversation": staticmethod(lambda session_id: async_return(None)),
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
        with patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(prepared_path),
        ), patch(
            "core.sight.bridge.extract_video_frames",
            lambda source, cache_dir, max_frames=3, **kwargs: async_return([]),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解失败：没有抽取到可用视频画面", result)
        recent = await runtime._sight_vault_for_runtime().recent(event.unified_msg_origin)
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
                "_get_provider": staticmethod(lambda provider_id="": async_return(None)),
                "_cleanup_conversation": staticmethod(lambda session_id: async_return(None)),
            },
        )()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="m-video-download-limit",
        )
        event.message_items = [{"type": "video", "file": "https://example.com/video.mp4"}]
        event.message_obj.message = event.message_items
        seen: dict[str, int] = {}

        async def prepare(source, cache_dir, **kwargs):
            seen["max_video_mb"] = kwargs.get("max_video_mb")
            seen["download_timeout_seconds"] = kwargs.get("download_timeout_seconds")
            return None

        with patch("core.sight.reader.transcribe_bcut", lambda *args, **kwargs: async_return(None)), patch(
            "core.sight.reader.transcribe_local",
            lambda *args, **kwargs: async_return(None),
        ), patch("core.sight.bridge.prepare_sample_video_source", prepare):
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
                "_get_provider": staticmethod(lambda provider_id="": async_return(None)),
                "_cleanup_conversation": staticmethod(lambda session_id: async_return(None)),
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

        with patch(
            "core.sight.reader.transcribe_bcut",
            lambda *args, **kwargs: async_return(
                TranscriptResult(
                    language="zh",
                    full_text="先展示了一只橘猫趴在窗边，后面有人把杯子推到镜头前。",
                    segments=(TranscriptSegment(start=0, end=1, text="先展示了一只橘猫趴在窗边"),),
                    metadata={"title": "雨天窗边的猫", "segments": 1},
                    source="必剪转写",
                )
            ),
        ), patch(
            "core.sight.bridge.extract_video_frames",
            lambda source, cache_dir, max_frames=3, **kwargs: async_return([]),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：雨天窗边的猫：先展示了一只橘猫趴在窗边", result)
        recent = await runtime._sight_vault_for_runtime().recent(event.unified_msg_origin)
        self.assertEqual(recent[0].status, "ready")
        self.assertEqual(recent[0].transcript_source, "必剪转写")
        context = await runtime.format_recent_sight_context(event)
        self.assertIn("雨天窗边的猫", context)
        self.assertIn("先展示了一只橘猫趴在窗边", context)
        self.assertIn("不要因为字幕、水印、标题或画面线索再调用联网搜索", context)
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

        cached = await runtime._cache_sight_note_markdown(insight, markdown, style="professional")
        context = await runtime.format_recent_sight_context(event)

        self.assertIn("professional_digest", cached.metadata)
        self.assertIn("专业总结：背景概述", context)
        self.assertIn("雨夜环境让画面里的等待和移动更明显", context)
        self.assertNotIn("内置浅摘要不应优先进入上下文", context)
        self.assertNotIn("![00:12", context)
        self.assertNotIn("# 雨夜城市观察", context)
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

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

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

        with patch(
            "core.sight.reader.transcribe_bcut",
            lambda *args, **kwargs: async_return(
                TranscriptResult(
                    language="zh",
                    full_text="橘猫趴在窗边。有人把杯子推到镜头前。",
                    segments=(TranscriptSegment(start=0, end=1, text="橘猫趴在窗边。"),),
                    metadata={"title": "雨天窗边的猫"},
                    source="必剪转写",
                )
            ),
        ), patch(
            "core.sight.bridge.extract_video_frames",
            lambda source, cache_dir, max_frames=3, **kwargs: async_return([]),
        ):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：橘猫趴在窗边看雨", result)
        recent = await runtime._sight_vault_for_runtime().recent(event.unified_msg_origin)
        self.assertEqual(recent[0].note_source, "内置摘要")
        self.assertIn("有人把杯子推到镜头前", "；".join(recent[0].details))
        self.assertIn("音频主线", provider.prompts[0])
        self.assertIn("音频转写内容", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("输出 JSON"), provider.prompts[0].index("【视频内容】"))
        self.assertLess(provider.prompts[0].index("【视频内容】"), provider.prompts[0].index("音频转写内容"))
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

            async def _call_llm_text(self, provider_arg, prompt, session_id, empty_retries=0, primary_provider_id=""):
                return (await provider_arg.text_chat(prompt, session_id)).completion_text

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
        transcript = " ".join([f"第{i}段讲旅行准备、目的地和集合时间" for i in range(420)])

        prepared_path = Path(tempfile.mkdtemp()) / "travel.mp4"
        prepared_path.write_bytes(b"fake-video")
        with patch(
            "core.sight.reader.transcribe_bcut",
            lambda *args, **kwargs: async_return(
                TranscriptResult(
                    language="zh",
                    full_text=transcript,
                    segments=(TranscriptSegment(start=0, end=1, text="旅行准备"),),
                    source="必剪转写",
                )
            ),
        ), patch(
            "core.sight.bridge.prepare_sample_video_source",
            lambda *args, **kwargs: async_return(prepared_path),
        ), patch(
            "core.sight.bridge.extract_video_frames",
            lambda source, cache_dir, max_frames=3, **kwargs: async_return([Path("frame-1.jpg")]),
        ), patch.object(runtime, "_describe_sight_frames", lambda clip, frames: async_return((["00:01：街边有人经过"], []))):
            result = await runtime.life_video_understand(event)

        self.assertIn("视频理解完成：", result)
        self.assertIn("旅行准备", result)
        self.assertIn("目的地和集合时间", result)
        audio_prompt_index = next(i for i, prompt in enumerate(provider.prompts) if "请只根据视频音频转写提炼音频主线" in prompt)
        fusion_prompt_index = next(i for i, prompt in enumerate(provider.prompts) if "请把视频音频主线、转写摘录和时间线画面整理成聊天可用的视频理解结果。" in prompt)
        self.assertLess(audio_prompt_index, fusion_prompt_index)
        self.assertTrue(all("来源：" not in prompt for prompt in provider.prompts))
        self.assertTrue(all("来源信息" not in prompt for prompt in provider.prompts))
    async def test_collects_emoji_assets_copies_local_file_to_plugin_cache(self):
        vision_provider = Provider(
            ['{"label":"探头","description":"适合轻轻围观的小表情","emotions":["好奇"],"status":"ready"}'],
            provider_id="vision-model",
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(vision_provider, providers={"vision-model": vision_provider})
        runtime.config = LifeSettings.from_dict({"vision_config": {"provider": "vision-model"}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True

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
            self.assertEqual(vision_provider.vision_prompts[0]["image"], str(cached_path))
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
            old_time = (datetime.datetime.now() - datetime.timedelta(days=2)).timestamp()
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
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
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
        event.message_items = [{"type": "mface", "url": "https://example.com/failed.png"}]
        event.message_obj.message = event.message_items
        runtime._media_fingerprint = lambda payload: "failed-hash"

        await runtime.maybe_collect_emoji_assets_from_event(event)

        assets = await runtime.archive.get_emoji_assets(10)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].status, "failed")
    async def test_emoji_asset_records_message_id_and_source_url_separately(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.config = LifeSettings.from_dict({})
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-url",
        )
        event.message_items = [{"type": "mface", "data": {"url": "https://example.com/source.png"}}]
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
        runtime.config = LifeSettings.from_dict({"emoji_config": {"collect_chat_emojis": False}})
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            message_id="m-img-disabled",
        )
        event.message_items = [{"type": "mface", "data": {"url": "https://example.com/source.png"}}]
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

            cached = await runtime._cache_emoji_asset_path({"path": str(source_path)}, "large-limit")

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
        self.assertEqual((await runtime.archive.get_emoji_asset_by_hash("missing")).status, "missing")

    async def test_maintain_emoji_assets_prunes_stale_inactive_records(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.data_path = Path(tempfile.gettempdir()) / "daily_life_test.db"
        runtime.config = LifeSettings.from_dict({"emoji_config": {"inactive_record_keep_days": 7}})
        old_time = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

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
            EmojiAssetRecord(file_hash="ready-kept", file_path="https://example.com/ready.png", status="ready", sendable=True)
        )

        result = await runtime.maintain_emoji_assets()

        self.assertEqual(result["deleted_inactive_records"], 1)
        self.assertIsNone(await runtime.archive.get_emoji_asset_by_hash("old-failed"))
        self.assertIsNotNone(await runtime.archive.get_emoji_asset_by_hash("ready-kept"))
    async def test_maintain_sight_cache_removes_stale_files_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
            runtime.data_path = Path(tmpdir) / "daily_life.db"
            runtime.config = LifeSettings.from_dict({"sight_config": {"sight_cache_keep_days": 1}})

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
            ['{"label":"默认识别","description":"默认模型识别的小表情","emotions":["轻松"],"status":"ready"}'],
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
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return runtime.context.providers.get(provider_id) or runtime.context.get_using_provider()

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        scheduled = []
        runtime._schedule_background_task = lambda coro, label="", key="": scheduled.append((label, key, coro)) or True
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展群",
            message_id="m-img-default",
        )
        event.message_items = [{"type": "mface", "url": "https://example.com/default.png"}]
        event.message_obj.message = event.message_items

        await runtime.maybe_collect_emoji_assets_from_event(event)
        await scheduled[0][2]

        self.assertEqual(default_provider.vision_prompts[0]["image"], "https://example.com/default.png")
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
                "visibility": {"level": "seen", "attention_level": 0, "psychological_freshness": 0},
                "action_decision": {"action": "skip_memory", "confidence": 0},
            },
            meta,
        )

        self.assertEqual(await runtime.archive.get_recent_message_visibility(10), [])
        self.assertEqual(await runtime.archive.get_recent_action_decisions(10), [])
        self.assertEqual(await runtime.archive.get_recent_group_environments(10), [])
        await runtime._append_memory_decision_log(
            {"visibility": {"level": "seen"}, "action_decision": {"action": "skip_memory"}},
            meta,
            datetime.datetime(2026, 5, 24, 12, 0),
        )
        self.assertEqual((await runtime.archive.get_day("2026-05-24")).state_log, [])
    async def test_memory_awareness_keeps_effective_visibility_and_decision_results(self):
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
        self.assertEqual(runtime.context.sent_messages[0][1].items, ["我先探头看一眼。"])
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
                timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        runtime.maybe_collect_emoji_assets_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_commitment_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_chat_memory_from_event = lambda event, now, sender_name="": async_return(None)
        runtime._build_injection_memos_context = lambda event, message="": async_return("")
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: async_return({})

        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "看看你现在在干嘛"
        req = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session"})()

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
                timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        runtime.maybe_capture_commitment_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_chat_memory_from_event = lambda event, now, sender_name="": async_return(None)
        runtime._build_injection_memos_context = lambda event, message="": async_return("")
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: async_return({})

        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "这照片是你吗"
        req = ProviderRequest(
            prompt="这照片是你吗",
            system_prompt="",
            session_id="chat_session",
            image_urls=["D:/tmp/quoted.png"],
        )

        await runtime.inject_life_context(req, event)

        anchors = [
            part for part in req.extra_user_content_parts
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
                timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        runtime.maybe_capture_commitment_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_chat_memory_from_event = lambda event, now, sender_name="": async_return(None)
        runtime._build_injection_memos_context = lambda event, message="": async_return("")
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: async_return({})

        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "今天在干嘛"
        req = ProviderRequest(prompt="今天在干嘛", system_prompt="", session_id="chat_session")

        await runtime.inject_life_context(req, event)

        self.assertFalse(
            any("HiddenVisualInputRule" in str(getattr(part, "text", "")) for part in req.extra_user_content_parts)
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
                timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
            )
        )
        runtime.failed_dates = {}
        runtime._background_scheduler = BackgroundTaskScheduler()
        runtime.generation_lock = asyncio.Lock()
        runtime.composer = types.SimpleNamespace()
        runtime.contact_resolver = types.SimpleNamespace(
            resolve_event_sender=lambda event: async_return(event.get_sender_name())
        )
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        runtime.maybe_capture_commitment_from_event = lambda event, now, sender_name="": async_return(None)
        runtime.maybe_capture_chat_memory_from_event = lambda event, now, sender_name="": async_return(None)
        runtime._build_injection_memos_context = lambda event, message="": async_return("")
        runtime._gather_life_context_snapshot = lambda event=None, use_cache=True: async_return({})

        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "这个视频讲什么"
        event.message_items = [{"type": "video", "file": "D:/tmp/classroom.mp4"}]
        event.message_obj.message = event.message_items
        req = ProviderRequest(prompt="这个视频讲什么", system_prompt="", session_id="chat_session")

        await runtime.inject_life_context(req, event)

        anchors = [
            part for part in req.extra_user_content_parts
            if "HiddenVideoInputRule" in str(getattr(part, "text", ""))
        ]
        self.assertEqual(len(anchors), 1)
        self.assertTrue(getattr(anchors[0], "_no_save", False))
        self.assertIn("必须基于近期视频理解或调用 life_video_understand", anchors[0].text)
        self.assertIn("不要因为字幕、水印、标题或画面线索再调用联网搜索", anchors[0].text)
        self.assertIn("出处、原视频、作者、链接", anchors[0].text)
        await asyncio.gather(*list(runtime._background_scheduler.tasks))
