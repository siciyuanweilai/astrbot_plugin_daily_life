import unittest

from runtimehelpers import *


class RuntimeMemoryTest(unittest.TestCase):
    def test_hidden_context_can_include_world_memory(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            weather_info=WeatherInfo(temp=20, temp_desc="舒适"),
            outfit="浅蓝外套和白裙子",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            world_context="[HiddenPlaces]\n- 常去咖啡店：出现 2 次；写手帐",
        )

        self.assertIn("[HiddenWorldMemory]", text)
        self.assertIn("常去咖啡店", text)
    def test_hidden_world_memory_includes_pronoun_boundary(self):
        from core.life.surroundings import format_hidden_world_context

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        world_context = format_hidden_world_context(
            [
                {
                    "id": "10001",
                    "name": "小林",
                    "relationship_story": "她平时会记得我想去看展。",
                }
            ],
            [],
            [],
        )
        data = DayRecord(
            date="2026-05-24",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            world_context=world_context,
        )

        self.assertIn("称谓边界：人设线索优先", text)
        self.assertIn("不要把旧叙事里的他/她当成性别依据", text)
        self.assertIn("关系叙事：她平时会记得我想去看展。", text)
    def test_hidden_context_can_include_commitments(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            weather="北京 晴 20°C",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
            commitments=[CommitmentRecord(id=3, content="下次继续聊世界观设定", kind="followup", time_window="next_chat")],
        )

        self.assertIn("[HiddenCommitmentHint]", text)
        self.assertIn("下次继续聊世界观设定", text)
    def test_hidden_experience_context_can_include_pending_memory_corrections(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        text = runtime._format_hidden_experience_context(
            memory_corrections=[
                MemoryCorrectionRecord(
                    target_type="relationship",
                    target_id="10001",
                    correction="这条还没应用，应该继续提示。",
                    applied=False,
                )
            ]
        )

        self.assertIn("[HiddenMemoryCorrections]", text)
        self.assertIn("这条还没应用", text)


class RuntimeMemoryAsyncTest(RuntimeAsyncHelperMixin, unittest.IsolatedAsyncioTestCase):
    async def test_injection_snapshot_uses_only_unapplied_memory_corrections(self):
        runtime, _ = self._make_proactive_runtime([])
        await runtime.archive.save_memory_correction(
            MemoryCorrectionRecord(
                target_type="relationship",
                target_id="applied",
                correction="已应用纠错不应继续注入。",
                applied=True,
            )
        )
        await runtime.archive.save_memory_correction(
            MemoryCorrectionRecord(
                target_type="relationship",
                target_id="pending",
                correction="未应用纠错仍需提示。",
                applied=False,
            )
        )

        snapshot = await runtime._gather_life_context_snapshot(use_cache=False)

        corrections = snapshot["memory_corrections"]
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0].target_id, "pending")
    async def test_maybe_capture_commitment_from_event_uses_llm_json(self):
        provider = Provider(
            [
                '{"has_commitment":true,"content":"明天提醒我买牛奶","kind":"reminder",'
                '"trigger_date":"2026-05-25","trigger_time":"","time_window":"",'
                '"people":[],"place":"","confidence":0.91}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "bot_identity_aliases": ["小助手"],
                "memos_config": {"enabled": True, "api_key": "key", "sync_selected_memory": True},
            }
        )
        runtime.memos = runtime._create_memos_service()
        scheduled = []

        def schedule(coro, label="", key=""):
            scheduled.append((label, key, coro))
            coro.close()
            return True

        runtime._schedule_background_task = schedule
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林")
        event.message_str = "明天提醒我买牛奶"

        saved = await runtime.maybe_capture_commitment_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertIsNotNone(saved)
        self.assertEqual(saved.content, "明天提醒我买牛奶")
        self.assertEqual(saved.trigger_date, "2026-05-25")
        self.assertIn("阿林", saved.people)
        self.assertEqual(scheduled[0][0], "MemOS 精选条目同步")
        self.assertTrue(scheduled[0][1].startswith("memos_items_"))
        self.assertIn("JSON 输出要求", provider.prompts[0])
        self.assertIn("缺少明确依据时使用空字符串", provider.prompts[0])
        self.assertIn("current_role=当前角色", provider.prompts[0])
        self.assertIn("speaker=消息发送者", provider.prompts[0])
        self.assertIn("perspective=当前角色第一人称", provider.prompts[0])
        self.assertIn("message_owner=speaker", provider.prompts[0])
        self.assertIn("visible_output=最终格式结果", provider.prompts[0])
        self.assertIn("人物称谓与性别规则", provider.prompts[0])
        self.assertIn("人物边界", provider.prompts[0])
        self.assertIn("当前角色：小助手", provider.prompts[0])
        self.assertIn("记录视角：当前角色第一人称", provider.prompts[0])
        self.assertIn("- 当前角色：小助手", provider.prompts[0])
        self.assertIn("- 消息发送者：阿林", provider.prompts[0])
        self.assertIn("当前人设线索：我是女生。 阿林是我的男死党", provider.prompts[0])
        self.assertIn("称谓依据：当前人设线索", provider.prompts[0])
        self.assertIn("字段中涉及人物称谓时按人物边界判断", provider.prompts[0])
        self.assertIn("隐藏推理第一句以“我”开头", provider.prompts[0])
        self.assertIn("内容范围=我此刻看到、想到、犹豫、决定和感受到的内容", provider.prompts[0])
        self.assertIn("最终可见输出=用户要求的格式结果，与隐藏推理分离", provider.prompts[0])
        self.assertIn(CORE_REASONING_ANTI_PATTERN_RULE, provider.prompts[0])
        self.assertIn("隐藏推理风格=当前角色第一人称短句", provider.prompts[0])
        self.assertNotIn("禁用开头或句式", provider.prompts[0])
        self.assertNotIn("隐藏推理也必须站在“我”的角色视角判断", provider.prompts[0])
        self.assertNotIn("服务端隐藏推理的第一句必须以“我”开头", provider.prompts[0])
        self.assertNotIn("不得把内心独白", provider.prompts[0])
        self.assertNotIn("不要使用复数主语", provider.prompts[0])
        self.assertIn("隐藏推理只写我此刻的感受和判断；普通情绪表达不是未来约定。", provider.prompts[0])
        self.assertNotIn("我看到对方说想我了", provider.prompts[0])
        self.assertIn("current_role=当前角色", provider.system_prompts[0])
        self.assertIn("speaker=消息发送者", provider.system_prompts[0])
        self.assertIn("perspective=当前角色第一人称", provider.system_prompts[0])
        self.assertIn("message_owner=speaker", provider.system_prompts[0])
        self.assertIn("最终可见输出与隐藏推理分离", provider.system_prompts[0])
        self.assertIn(CORE_REASONING_ANTI_PATTERN_RULE, provider.system_prompts[0])
        self.assertNotIn("隐藏推理第一句必须从“我”开始", provider.system_prompts[0])
        self.assertNotIn("禁止把隐藏推理", provider.system_prompts[0])
        self.assertNotIn("主语只用“我”", provider.system_prompts[0])
        self.assertNotIn("我们首先分析用户输入", provider.prompts[0])
        self.assertNotIn("好的，我需要判断", provider.prompts[0])
        self.assertNotIn("我们根据规则", provider.prompts[0])
        self.assertNotIn("我们分析当前情况", provider.prompts[0])
        self.assertNotIn("当前角色是我（深蓝）", provider.prompts[0])
        self.assertNotIn("我（深蓝）", provider.prompts[0])
        self.assertNotIn("我们分析当前情况", provider.system_prompts[0])
        self.assertNotIn("当前角色是我（深蓝）", provider.system_prompts[0])
        self.assertTrue(provider.prompts[0].startswith("隐藏推理口吻"))
        self.assertLess(provider.prompts[0].index("JSON 输出要求"), provider.prompts[0].index("【刚看到的聊天】"))
        self.assertLess(provider.prompts[0].index("说话人：阿林"), provider.prompts[0].index("当前日期时间：2026-05-24 12:00"))
        self.assertLess(provider.prompts[0].index("人物边界"), provider.prompts[0].index("日期换算：今天=2026-05-24"))
        self.assertGreater(provider.prompts[0].index("我刚看到的内容："), provider.prompts[0].index("【刚看到的聊天】"))
        self.assertIn("可见文本内容：明天提醒我买牛奶", provider.prompts[0])
        self.assertIn("真实图片组件：0 个", provider.prompts[0])
    async def test_commitment_capture_repairs_non_pure_json_reply(self):
        provider = Provider(
            [
                '我先判断这条是未来提醒。\n{"has_commitment":true,"content":"明天提醒我买牛奶","kind":"reminder",'
                '"trigger_date":"2026-05-25","trigger_time":"","time_window":"",'
                '"people":[],"place":"","confidence":0.91}',
                '{"has_commitment":true,"content":"明天提醒我买牛奶","kind":"reminder",'
                '"trigger_date":"2026-05-25","trigger_time":"","time_window":"","people":[],"place":"","confidence":0.91}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "阿林是我的男死党。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()
        event = Event(sender_name="阿林")
        event.message_str = "明天提醒我买牛奶"

        saved = await runtime.maybe_capture_commitment_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertIsNotNone(saved)
        self.assertEqual(saved.content, "明天提醒我买牛奶")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("最终回复只能是一个 JSON 对象", provider.prompts[0])
        self.assertIn("第一个非空字符必须是 {", provider.prompts[1])
        self.assertIn("请把下面内容改写为严格 JSON 对象本体", provider.prompts[1])
        self.assertTrue(provider.prompts[1].startswith("隐藏推理口吻"))
        self.assertLess(provider.prompts[1].index("请把下面内容改写为严格 JSON 对象本体"), provider.prompts[1].index("【待修复 JSON】"))
    async def test_capture_prompts_treat_image_sent_text_as_text_only(self):
        provider = Provider(['{"has_commitment":false}', '{"worth_saving":false}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"min_message_length": 1},
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("测试用户乙"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。测试用户乙是我的男死党。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "测试用户乙是我的男死党。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="测试用户乙", sender_id="100000002")
        event.message_str = "[图片已发送]\n为什么带上这句回复"
        event.message_items = [{"type": "text", "data": {"text": event.message_str}}]

        commitment = await runtime.maybe_capture_commitment_from_event(
            event,
            now=datetime.datetime(2026, 6, 25, 18, 16),
        )
        summary = await runtime.maybe_capture_chat_memory_from_event(
            event,
            now=datetime.datetime(2026, 6, 25, 18, 16),
        )

        self.assertIsNone(commitment)
        self.assertIsNone(summary)
        self.assertEqual(len(provider.prompts), 2)
        for prompt in provider.prompts:
            self.assertIn("真实图片组件：0 个", prompt)
            self.assertIn("文本组件：1 个", prompt)
            self.assertIn("可见文本内容：[图片已发送]\n为什么带上这句回复", prompt)
            self.assertIn("不要把可见文本里的“[图片已发送]”“图片已发送”等字样当成图片", prompt)
    async def test_capture_skips_framework_command_events(self):
        provider = Provider(['{"has_commitment":true}', '{"worth_saving":true}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"min_message_length": 1},
            }
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()
        runtime.composer = type(
            "Composer",
            (),
            {
                "_get_provider": staticmethod(lambda provider_id="": async_return(provider)),
                "_call_llm_text": staticmethod(lambda provider, prompt, session_id, **kwargs: async_return("{}")),
                "_cleanup_conversation": staticmethod(lambda session_id: async_return(None)),
                "learn_preferences_from_payload": staticmethod(lambda *args, **kwargs: async_return([])),
            },
        )()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")

        class CommandFilter:
            pass

        class Handler:
            event_filters = [CommandFilter()]

        event.message_str = "生活 状态"
        event.set_extra("activated_handlers", [Handler()])

        commitment = await runtime.maybe_capture_commitment_from_event(event)
        summary = await runtime.maybe_capture_chat_memory_from_event(event)

        self.assertIsNone(commitment)
        self.assertIsNone(summary)
        self.assertEqual(provider.prompts, [])
    async def test_maybe_capture_chat_memory_from_event_saves_summary_and_relationship_point(self):
        provider = Provider(
            [
                '{"worth_saving":true,"brief":"阿林说周末想去展览馆",'
                '"long_summary":"阿林提到周末想一起看展，顺便去书店。",'
                '"people":["阿林"],'
                '"relationship_points":["阿林是男性死党，最近对看展很感兴趣"]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "周末我们去展览馆看展吧，结束后还能顺路逛书店。"

        saved = await runtime.maybe_capture_chat_memory_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        relationships = await runtime.archive.get_recent_relationships(10)
        summaries = await runtime.archive.get_recent_chat_summaries(10)
        self.assertIsNotNone(saved)
        self.assertEqual(summaries[0].brief, "阿林说周末想去展览馆")
        self.assertEqual(relationships[0].name, "阿林")
        self.assertEqual(relationships[0].platform, "aiocqhttp")
        self.assertEqual(relationships[0].user_id, "10001")
        self.assertEqual(relationships[0].memory_points[0].content, "阿林是男性死党，最近对看展很感兴趣")
        self.assertIn("通用记忆原则", provider.prompts[0])
        self.assertIn("群聊信息必须先判断归属", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("通用记忆原则"), provider.prompts[0].index("【眼前内容】"))
        self.assertIn("身份边界", provider.prompts[0])
        self.assertIn("当前角色：我", provider.prompts[0])
        self.assertIn("记录视角：当前角色第一人称", provider.prompts[0])
        self.assertIn("消息发送者/对方：阿林", provider.prompts[0])
        self.assertIn("对方 profile_id：10001", provider.prompts[0])
        self.assertLess(provider.prompts[0].index("人物边界"), provider.prompts[0].index("当前日期时间：2026-05-24 12:00"))
        self.assertLess(provider.prompts[0].index("当前日期时间：2026-05-24 12:00"), provider.prompts[0].index("我刚看到对方发来的内容："))
        self.assertGreater(provider.prompts[0].index("我刚看到对方发来的内容："), provider.prompts[0].index("【眼前内容】"))
        self.assertIn("可见文本内容：周末我们去展览馆看展吧，结束后还能顺路逛书店。", provider.prompts[0])
        self.assertIn("真实图片组件：0 个", provider.prompts[0])
        self.assertIn('"category": "activity|outfit|hair|social|sleep|place|style|other"', provider.prompts[0])
        self.assertIn('"target_type": "schedule|outfit|hair|sleep|social|activity|place|style|relationship|memory|other"', provider.prompts[0])
    async def test_chat_memory_natural_life_adjustment_affects_next_daily_prompt(self):
        provider = Provider(
            [
                '{"worth_saving":true,"brief":"用户希望这几天多休息",'
                '"long_summary":"用户自然纠偏，希望这几天让角色多休息，不要安排高强度活动。",'
                '"people":["阿林"],'
                '"life_adjustments":[{"scope":"short_term","target_type":"sleep","content":"这几天让她多休息","reason":"用户自然提出短期恢复目标","priority":88,"confidence":0.92}]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict({"memory_config": {"min_message_length": 1}})
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "测试人格"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "阿林是熟悉的朋友。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "这几天让她多休息，别安排太满。"

        saved = await runtime.maybe_capture_chat_memory_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        slots = await runtime.archive.get_focus_slots(10, active_only=False)
        evidence = await runtime.archive.get_memory_evidence(target_type="focus", limit=10)
        self.assertIsNotNone(saved)
        self.assertEqual(slots[0].label, "这几天让她多休息")
        self.assertEqual(slots[0].priority, 88)
        self.assertEqual(slots[0].expires_at, "2026-05-31")
        self.assertIn("短期恢复目标", slots[0].reason)
        self.assertEqual(evidence[0].evidence_type, "correction")
        self.assertIn("短期恢复目标", evidence[0].summary)
    async def test_chat_memory_capture_repairs_non_pure_json_reply(self):
        provider = Provider(
            [
                '我看到阿林在认真约我。\n{"worth_saving":true,"brief":"阿林说周末想去展览馆",'
                '"long_summary":"阿林提到周末想一起看展。","people":["阿林"],'
                '"relationship_points":["阿林最近对看展很感兴趣"]}',
                '{"worth_saving":true,"brief":"阿林说周末想去展览馆",'
                '"long_summary":"阿林提到周末想一起看展。","people":["阿林"],'
                '"relationship_points":["阿林最近对看展很感兴趣"]}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "阿林是我的男死党。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "周末我们去展览馆看展吧。"

        saved = await runtime.maybe_capture_chat_memory_from_event(
            event,
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertIsNotNone(saved)
        self.assertEqual(saved.brief, "阿林说周末想去展览馆")
        self.assertIn("最终回复只能是一个 JSON 对象", provider.prompts[0])
        repair_prompts = [prompt for prompt in provider.prompts if "请把下面内容改写为严格 JSON 对象本体" in prompt]
        self.assertEqual(len(repair_prompts), 1)
        self.assertIn("第一个非空字符必须是 {", repair_prompts[0])
        self.assertTrue(repair_prompts[0].startswith("隐藏推理口吻"))
        self.assertLess(repair_prompts[0].index("请把下面内容改写为严格 JSON 对象本体"), repair_prompts[0].index("【待修复 JSON】"))
    async def test_chat_memory_calibrates_summary_before_persisting(self):
        provider = Provider(
            [
                '{"worth_saving":true,'
                '"brief":"阿林问我是不是只看到这些内容，像是在确认我有没有认真看她发的东西。",'
                '"long_summary":"阿林发来确认，像是在问我有没有认真看她刚发的资料。",'
                '"people":["阿林"],'
                '"subjective_impression":{"subjective_name":"阿林","tags":["会催我看资料"],'
                '"relationship_story":"她会用调侃语气确认我有没有认真看内容。",'
                '"impression_delta":"这次她又来确认我看没看完整。"},'
                '"relationship_points":["阿林会主动确认我有没有认真看她发的内容。"],'
                '"memory_targets":[{"profile_id":"10001","name":"阿林","subjective_name":"阿林",'
                '"relationship_story":"她会确认我有没有看她发的资料。",'
                '"points":["会确认我有没有认真看她发的内容。"],'
                '"note":"这次又确认我有没有看完整","source":"speaker"}]}',
                '{"needs_revision":true,"reason":"人设线索是男死党，记忆摘要和关系内容用了女性称谓。",'
                '"revised":{"brief":"阿林问我是不是只看到这些内容，像是在确认我有没有认真看他发的东西。",'
                '"long_summary":"阿林发来确认，像是在问我有没有认真看他刚发的资料。",'
                '"subjective_impression":{"subjective_name":"阿林","tags":["会催我看资料"],'
                '"relationship_story":"他会用调侃语气确认我有没有认真看内容。",'
                '"impression_delta":"这次他又来确认我看没看完整。"},'
                '"relationship_points":["阿林会主动确认我有没有认真看他发的内容。"],'
                '"memory_targets":[{"profile_id":"10001","name":"阿林","subjective_name":"阿林",'
                '"relationship_story":"他会确认我有没有看他发的资料。",'
                '"points":["会确认我有没有认真看他发的内容。"],'
                '"note":"这次又确认我有没有看完整","source":"speaker"}]}}',
                '{"needs_revision":false,"reason":"","relationship_story":""}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，会把有意思的资料发给我看。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，会把有意思的资料发给我看。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "你只看到这些内容吗？"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 22, 14, 58))

        relationship = await runtime.archive.get_relationship("10001")
        self.assertIsNotNone(saved)
        self.assertIn("看他发的东西", saved.brief)
        self.assertNotIn("她", saved.brief)
        self.assertIn("认真看他发的内容", relationship.memory_points[0].content)
        self.assertNotIn("她", relationship.relationship_story)
        self.assertIn("待审阅聊天记忆", provider.prompts[1])
        self.assertIn("这不是文本清洗，不要机械替换字词", provider.prompts[1])
    async def test_chat_memory_calibrates_target_memory_before_persisting(self):
        provider = Provider(
            [
                '{"worth_saving":true,'
                '"brief":"阿林提到柏舟又发了资料",'
                '"long_summary":"阿林在群里提到柏舟发资料的事。",'
                '"people":["阿林","柏舟"],'
                '"memory_targets":[{"profile_id":"name:柏舟","name":"柏舟",'
                '"persona_hint":"柏舟是我的男同学，常帮我整理资料。",'
                '"subjective_name":"柏舟","subjective_tags":["会整理资料"],'
                '"relationship_story":"她经常帮我整理资料。",'
                '"points":["柏舟会把她整理好的资料发给我。"],'
                '"note":"这次她又整理了资料","source":"mentioned_person"}]}',
                '{"needs_revision":true,"reason":"目标人设线索是男同学，目标记忆使用了女性称谓。",'
                '"revised":{"relationship_story":"他经常帮我整理资料。",'
                '"points":["柏舟会把他整理好的资料发给我。"],'
                '"note":"这次他又整理了资料"}}',
                '{"needs_revision":false,"reason":"","revised":{}}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()
        memos_items = []
        runtime.schedule_memos_selected_items = (
            lambda meta, items, **kwargs: memos_items.extend(items) or True
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。柏舟是我的男同学，常帮我整理资料。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "柏舟是我的男同学，常帮我整理资料。" if name == "柏舟" else ""

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="资料群",
            message_id="m-target-calibration",
        )
        event.message_str = "柏舟又把资料整理好了，回头我转给你。"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 22, 15, 20))

        relationship = await runtime.archive.get_relationship("name:柏舟")
        self.assertIsNotNone(relationship)
        self.assertIn("他经常帮我整理资料", relationship.relationship_story)
        self.assertIn("把他整理好的资料", relationship.memory_points[0].content)
        self.assertNotIn("她", relationship.relationship_story)
        self.assertFalse(any("她" in item for item in memos_items))
        self.assertTrue(any("把他整理好的资料" in item for item in memos_items))
        self.assertIn("待审阅目标记忆", provider.prompts[1])
        self.assertIn("不要机械替换字词", provider.prompts[1])
    async def test_chat_memory_calibrates_skipped_impression_before_persisting(self):
        provider = Provider(
            [
                '{"worth_saving":false,'
                '"subjective_impression":{"subjective_name":"阿林","tags":["爱催我"],'
                '"relationship_story":"她总会顺手催我看资料。",'
                '"impression_delta":"她又催我看资料。"}}',
                '{"needs_revision":true,"reason":"人设线索是男死党，主观印象用了女性称谓。",'
                '"revised":{"subjective_impression":{"subjective_name":"阿林","tags":["爱催我"],'
                '"relationship_story":"他总会顺手催我看资料。",'
                '"impression_delta":"他又催我看资料。"}}}',
                '{"needs_revision":false,"reason":"","revised":{}}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("阿林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，会把有意思的资料发给我看。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "阿林是我的男死党，性格爽朗，会把有意思的资料发给我看。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "你又没认真看吧。"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 22, 15, 30))

        relationship = await runtime.archive.get_relationship("10001")
        self.assertIsNone(saved)
        self.assertIsNotNone(relationship)
        self.assertIn("他总会顺手催我看资料", relationship.relationship_story)
        self.assertIn("他又催我看资料", relationship.notes[-1].content)
        self.assertNotIn("她", relationship.relationship_story)
        self.assertIn("待审阅聊天记忆", provider.prompts[1])
    async def test_group_chat_memory_uses_sender_id_not_group_session(self):
        provider = Provider(
            [
                '{"worth_saving":true,"brief":"Alice discussed Diablo",'
                '"long_summary":"Alice discussed Diablo updates.",'
                '"people":["Alice"],'
                '"relationship_points":["Alice likes Diablo"]}',
                '{"worth_saving":true,"brief":"Bob discussed Zelda",'
                '"long_summary":"Bob discussed Zelda updates.",'
                '"people":["Bob"],'
                '"relationship_points":["Bob likes Zelda"]}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        group_umo = "aiocqhttp:GroupMessage:20001"
        alice = Event(sender_name="Alice", sender_id="10001", unified_msg_origin=group_umo)
        alice.message_str = "I want to keep watching Diablo updates."
        bob = Event(sender_name="Bob", sender_id="10002", unified_msg_origin=group_umo)
        bob.message_str = "I want to keep watching Zelda updates."

        await runtime.maybe_capture_chat_memory_from_event(alice, now=datetime.datetime(2026, 5, 24, 12, 0))
        await runtime.maybe_capture_chat_memory_from_event(bob, now=datetime.datetime(2026, 5, 24, 12, 1))

        relationships = {item.id: item for item in await runtime.archive.get_recent_relationships(10)}
        self.assertNotIn("20001", relationships)
        self.assertEqual(set(relationships), {"10001", "10002"})
        self.assertEqual(relationships["10001"].name, "Alice")
        self.assertEqual(relationships["10001"].user_id, "10001")
        self.assertEqual(relationships["10001"].memory_points[0].content, "Alice likes Diablo")
        self.assertEqual(relationships["10002"].name, "Bob")
        self.assertEqual(relationships["10002"].user_id, "10002")
        self.assertEqual(relationships["10002"].memory_points[0].content, "Bob likes Zelda")
    async def test_group_chat_memory_splits_mentioned_member_profile_and_records_decision(self):
        provider = Provider(
            [
                '{"worth_saving":true,'
                '"brief":"Alice 提到 Bob 最近在准备看展",'
                '"long_summary":"群聊里 Alice 说 Bob 最近在准备看展，还想找人一起去。",'
                '"people":["Alice","Bob"],'
                '"visibility":{"level":"focused","attention_level":80,"priority":"normal","is_directed_at_bot":false,"reason":"包含可沉淀的群友近况"},'
                '"group_environment":{"atmosphere":"平稳","topic":"Bob 准备看展","topic_owner":"target_user_topic","active_users":2,'
                '"is_multithread":false,"is_spam":false,"is_repetition":false,"is_discussing_bot":false,'
                '"suitable_to_join":"observe","bot_watch_state":"peek","participation_desire":35,"complexity_score":42,'
                '"understanding_confidence":88,"deep_analysis_needed":false,"summary":"群里在聊 Bob 的近况"},'
                '"action_decision":{"action":"save_memory","reason":"这是 Bob 的稳定近况，不应挂到 Alice 身上","confidence":0.92,'
                '"scene_type":"群友档案","topic_owner":"target_user_topic","understanding":"understood","deep_analysis":false,'
                '"inner_monologue":"这条是 Bob 的信息，不能挂到 Alice 身上。","reply_strategy":"观察为主，必要时轻轻接话。"},'
                '"relationship_points":[],'
                '"memory_targets":[{"profile_id":"name:Bob","name":"Bob","points":["Bob 最近在准备看展，可能想找人一起去。"],'
                '"note":"被 Alice 提到正在准备看展","source":"mentioned_person"}]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="Alice",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展小群",
            message_id="m1",
        )
        event.message_str = "Bob 最近在准备看展，好像还想找人一起去。"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 0))

        relationships = {item.id: item for item in await runtime.archive.get_recent_relationships(10)}
        environments = await runtime.archive.get_recent_group_environments(10)
        decisions = await runtime.archive.get_recent_action_decisions(10)
        visibility = await runtime.archive.get_recent_message_visibility(10)
        self.assertIsNotNone(saved)
        self.assertIn("10001", relationships)
        self.assertIn("name:Bob", relationships)
        self.assertEqual(relationships["10001"].memory_points, [])
        self.assertEqual(relationships["name:Bob"].memory_points[0].content, "Bob 最近在准备看展，可能想找人一起去。")
        self.assertEqual(environments[0].group_name, "看展小群")
        self.assertEqual(environments[0].topic_owner, "target_user_topic")
        self.assertEqual(environments[0].participation_desire, 35)
        self.assertEqual(environments[0].complexity_score, 42)
        self.assertEqual(environments[0].understanding_confidence, 88)
        self.assertEqual(decisions[0].action, "save_memory")
        self.assertIn("不应挂到 Alice", decisions[0].reason)
        self.assertIn("不能挂到 Alice", decisions[0].inner_monologue)
        self.assertIn("观察为主", decisions[0].reply_strategy)
        self.assertEqual(visibility[0].visibility, "focused")
    async def test_seen_but_ignored_leaves_subjective_trace_without_summary(self):
        provider = Provider(
            [
                '{"worth_saving":false,'
                '"visibility":{"level":"seen_but_ignored","attention_level":32,"priority":"low","is_directed_at_bot":false,'
                '"freshness":"recent","psychological_freshness":58,"reactivated_from_id":0,'
                '"reactivation_hint":"如果后面有人继续接这个话题再回看","reason":"扫到了但此刻不想接普通闲聊"},'
                '"group_environment":{"atmosphere":"平稳","topic":"随口闲聊","topic_owner":"shared_group_topic","active_users":2,'
                '"is_multithread":false,"is_spam":false,"is_repetition":false,"is_discussing_bot":false,'
                '"suitable_to_join":"observe","bot_watch_state":"skim_window","participation_desire":22,"complexity_score":10,'
                '"understanding_confidence":76,"deep_analysis_needed":false,"summary":"群里在轻松闲聊"},'
                '"action_decision":{"action":"observe","reason":"看见了但状态不想展开","confidence":0.8,'
                '"scene_type":"普通闲聊","topic_owner":"shared_group_topic","understanding":"understood","deep_analysis":false,'
                '"inner_monologue":"看到了，先不接，等话题自己过去。","reply_strategy":"继续观察"},'
                '"subjective_impression":{"subjective_name":"会让我多看一眼的人","tags":["有点吵但不讨厌"],'
                '"relationship_story":"有时候会被这个人拉回群聊注意力。","impression_delta":"这次看见了但略过，留下了一点回避感。"},'
                '"relationship_points":[],"memory_targets":[],"preference_points":[]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        await runtime.archive.save_day(DayRecord(date="2026-05-24"))
        event = Event(
            sender_name="Alice",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="测试群",
            message_id="m2",
        )
        event.message_str = "今天又是摸鱼的一天。"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 5))

        self.assertIsNone(saved)
        self.assertEqual(await runtime.archive.get_recent_chat_summaries(10), [])
        relationships = await runtime.archive.get_recent_relationships(10)
        visibility = await runtime.archive.get_recent_message_visibility(10)
        day = await runtime.archive.get_day("2026-05-24")
        self.assertEqual(relationships[0].subjective_name, "会让我多看一眼的人")
        self.assertEqual(relationships[0].subjective_tags, ["有点吵但不讨厌"])
        self.assertIn("回避感", relationships[0].notes[-1].content)
        self.assertEqual(visibility[0].visibility, "seen_but_ignored")
        self.assertEqual(visibility[0].psychological_freshness, 58)
        self.assertTrue(any("看见了但状态不想展开" in item for item in day.state_log))
        self.assertIn("人物称谓与性别规则", provider.prompts[0])
        self.assertIn("证据不足时，使用昵称、对方、这个人、这位群友等中性称呼", provider.prompts[0])
        self.assertIn("人物边界", provider.prompts[0])
    async def test_chat_memory_prompt_uses_saved_persona_hint_for_pronoun_choice(self):
        provider = Provider(['{"worth_saving":false}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            date_str="2026-05-23",
            persona_hint="男生，喜欢看展",
            subjective_tags=["可靠"],
            relationship_story="我和阿林聊展览时比较放松，她总会提到新展。",
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格爽朗，经常约我看展。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "这周末想去看展。"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 5))

        prompt = provider.prompts[0]
        self.assertIn("人物边界", prompt)
        self.assertIn("已保存人设线索：男生，喜欢看展", prompt)
        self.assertIn("当前人设线索：我是女生。 阿林是我的男死党", prompt)
        self.assertIn("已保存关系短标签：可靠", prompt)
        self.assertIn("已保存关系叙事：我和阿林聊展览时比较放松，她总会提到新展。", prompt)
        self.assertIn("称谓依据：当前人设线索", prompt)
        self.assertIn("旧记忆作用：背景参考，不覆盖当前人设线索", prompt)
        self.assertIn("性别和关系称谓需要明确证据", prompt)
    async def test_chat_memory_prompt_uses_neutral_pronoun_when_persona_match_fails(self):
        provider = Provider(
            [
                '{"matched":false,"name":"","persona_hint":"","confidence":0.0,"reason":""}',
                '{"worth_saving":false}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._persona_hint_cache = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="小林",
            date_str="2026-06-21",
            relationship_story="她平时会记得我想去看展。",
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self, umo=""):
                return "我是女生。林远是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return ""

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="小林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "今天只是随便聊两句。"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 21, 17, 30))

        persona_prompt = provider.prompts[0]
        memory_prompt = provider.prompts[1]
        self.assertIn("已保存关系叙事：她平时会记得我想去看展。", persona_prompt)
        self.assertIn("如果只是昵称相似、语气像、平台标识、旧印象或猜测，matched=false", persona_prompt)
        self.assertIn("已保存关系叙事：她平时会记得我想去看展。", memory_prompt)
        self.assertIn("当前人设线索：无", memory_prompt)
        self.assertIn("称谓依据：证据不足", memory_prompt)
        self.assertIn("旧记忆作用：背景参考，不单独决定性别或亲密称谓", memory_prompt)
        self.assertIn("称呼策略：使用中性称呼", memory_prompt)
    async def test_relationship_calibration_rewrites_conflicting_saved_profile(self):
        provider = Provider(
            [
                '{"needs_revision":true,"reason":"已保存关系叙事把男死党写成女性称谓。",'
                '"subjective_name":"男死党阿林","subjective_tags":["可靠"],'
                '"relationship_story":"我和阿林熟到可以互相吐槽，他也会认真记住邀约。",'
                '"note":"按人设线索校准为男性死党。",'
                '"relationship_points":["阿林是我的男死党，喜欢看展。"]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._relationship_calibration_cache = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            date_str="2026-05-23",
            persona_hint="男生，死党",
            subjective_name="会让我多看一眼的人",
            subjective_tags=["可靠"],
            relationship_story="我和阿林熟到可以互相吐槽，她也会认真记住邀约。",
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()

        await runtime._calibrate_relationship_profile(
            "10001",
            "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。",
            "2026-05-24",
        )

        relationship = await runtime.archive.get_relationship("10001")
        self.assertEqual(relationship.subjective_name, "男死党阿林")
        self.assertEqual(relationship.relationship_story, "我和阿林熟到可以互相吐槽，他也会认真记住邀约。")
        self.assertEqual(relationship.notes[-1].content, "按人设线索校准为男性死党。")
        self.assertEqual(relationship.memory_points[-1].content, "阿林是我的男死党，喜欢看展。")
        self.assertIn("这不是文本清洗，不要机械替换字词", provider.prompts[0])
        self.assertIn("对方在人设中的线索：我是女生。阿林是我的男死党", provider.prompts[0])
    async def test_relationship_calibration_keeps_profile_when_llm_finds_no_conflict(self):
        provider = Provider(['{"needs_revision":false,"reason":"","relationship_story":""}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._relationship_calibration_cache = {}
        await runtime.archive.touch_relationship(
            "10001",
            name="阿林",
            date_str="2026-05-23",
            persona_hint="男生，死党",
            relationship_story="我和阿林熟到可以互相吐槽，他也会认真记住邀约。",
        )

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

        runtime.composer = Composer()

        await runtime._calibrate_relationship_profile(
            "10001",
            "我是女生。阿林是我的男死党，性格爽朗，经常约我看展。",
            "2026-05-24",
        )

        relationship = await runtime.archive.get_relationship("10001")
        self.assertEqual(relationship.relationship_story, "我和阿林熟到可以互相吐槽，他也会认真记住邀约。")
        self.assertEqual(relationship.notes, [])
        self.assertEqual(relationship.memory_points, [])
    async def test_chat_memory_prompt_extracts_persona_hint_when_profile_is_empty(self):
        provider = Provider(
            [
                '{"worth_saving":false,'
                '"visibility":{"level":"seen"},'
                '"subjective_impression":{"relationship_story":"我和阿林打球聊天时比较放松。"}}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self):
                return "我是女生。阿林是我的男死党，性格很爽朗，经常约我打球。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return "我是女生。 阿林是我的男死党，性格很爽朗，经常约我打球。"

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
        )
        event.message_str = "周末一起打球吗？"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 5))

        prompt = provider.prompts[0]
        self.assertIn("当前人设线索：我是女生。 阿林是我的男死党，性格很爽朗，经常约我打球。", prompt)
        self.assertIn("人物称谓与性别规则", prompt)
        self.assertIn("昵称、头像、平台、语气、表情或刻板印象不能单独作为依据", prompt)
        relationship = await runtime.archive.get_relationship("10001")
        self.assertEqual(relationship.persona_hint, "我是女生。 阿林是我的男死党，性格很爽朗，经常约我打球。")
    async def test_persona_hint_semantic_extract_repairs_alias_mismatch(self):
        provider = Provider(
            [
                '{"matched":true,"name":"林远","persona_hint":"林远是我的男死党，性格爽朗，经常约我看展。","confidence":0.86,"reason":"当前昵称是备注名，和人设里的林远对应同一位死党。"}',
                '{"worth_saving":false,'
                '"visibility":{"level":"seen"},'
                '"subjective_impression":{"subjective_name":"小林","tags":["可靠"],'
                '"relationship_story":"她总会记得我想去看展。","impression_delta":"这次又顺手来关心我。"}}',
                '{"needs_revision":true,"reason":"主观印象把男死党写成女性称谓。",'
                '"revised":{"subjective_impression":{"subjective_name":"小林","tags":["可靠"],'
                '"relationship_story":"他总会记得我想去看展。","impression_delta":"这次又顺手来关心我。"}}}',
                '{"needs_revision":true,"reason":"关系叙事把男死党写成女性称谓。",'
                '"subjective_name":"小林","subjective_tags":["可靠"],'
                '"relationship_story":"他总会记得我想去看展。",'
                '"note":"按对方人设线索校准为男性死党。",'
                '"relationship_points":["林远是我的男死党，性格爽朗，经常约我看展。"]}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._persona_hint_cache = {}
        runtime._relationship_calibration_cache = {}
        await runtime.archive.touch_relationship(
            "10000001",
            name="小林",
            date_str="2026-06-21",
            relationship_story="她平时会记得我想去看展。",
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("小林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self, umo=""):
                return "我是女生。林远是我的男死党，性格爽朗，经常约我看展。"

            @staticmethod
            def _extract_reference_persona(persona, name):
                return ""

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="小林",
            sender_id="10000001",
            unified_msg_origin="aiocqhttp:FriendMessage:10000001",
        )
        event.message_str = "周末还去看展吗？"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 6, 21, 15, 0))

        relationship = await runtime.archive.get_relationship("10000001")
        self.assertEqual(relationship.persona_hint, "林远是我的男死党，性格爽朗，经常约我看展。")
        self.assertEqual(relationship.relationship_story, "他总会记得我想去看展。")
        self.assertEqual(relationship.notes[-1].content, "按对方人设线索校准为男性死党。")
        self.assertIn("当前角色人设", provider.prompts[0])
        self.assertIn("这不是关键词匹配", provider.prompts[0])
        self.assertIn("当前人设线索：林远是我的男死党", provider.prompts[1])
        self.assertIn("当前人设线索：林远是我的男死党", provider.prompts[2])
        self.assertIn("对方在人设中的线索：林远是我的男死党", provider.prompts[3])
    async def test_persona_hint_semantic_extract_sees_late_persona_sections(self):
        provider = Provider(
            [
                '{"matched":true,"name":"林远","persona_hint":"林远是我的家人兼死党，平常称呼小林，性别男。","confidence":0.92,"reason":"小林是备注称呼，和人设中的林远/小林对应。"}',
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime._persona_hint_cache = {}
        runtime._relationship_calibration_cache = {}
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return("小林"))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _get_persona(self, umo=""):
                return (
                    "我是女生。"
                    + "日常性格描述。" * 500
                    + "情感与家人：我的家人兼死党是“林远”（平常称呼“小林”），性别男，特别好的朋友，纯友谊。"
                )

            @staticmethod
            def _extract_reference_persona(persona, name):
                return ""

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="小林",
            sender_id="10000001",
            unified_msg_origin="aiocqhttp:FriendMessage:10000001",
        )
        event.message_str = "在忙什么呢？"

        hint = await runtime._extract_speaker_persona_hint("小林", event=event)

        self.assertEqual(hint, "林远是我的家人兼死党，平常称呼小林，性别男。")
        self.assertIn("情感与家人", provider.prompts[0])
        self.assertIn("候选称呼可能是备注、群昵称、简称或日常称呼", provider.prompts[0])
    async def test_private_memory_awareness_does_not_save_group_environment(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        payload = {
            "visibility": {"level": "seen", "reason": "私聊消息"},
            "group_environment": {"atmosphere": "亲密", "topic": "私聊亲密表达"},
            "action_decision": {"action": "observe", "reason": "只是私聊"},
        }
        meta = {
            "session_id": "aiocqhttp:FriendMessage:10001",
            "message_id": "m1",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "",
            "group_name": "",
            "date": "2026-05-24",
            "is_group": "false",
        }

        await runtime._save_memory_awareness_records(payload, meta)

        self.assertEqual(await runtime.archive.get_recent_group_environments(10), [])
        self.assertEqual((await runtime.archive.get_recent_message_visibility(10))[0].reason, "私聊消息")
        self.assertEqual((await runtime.archive.get_recent_action_decisions(10))[0].reason, "只是私聊")
    async def test_memory_awareness_keeps_llm_text_without_cleanup(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        payload = {
            "visibility": {
                "level": "focused",
                "reason": "用户正在讨论机器人本身",
                "reactivation_hint": "后续如果@机器人再重新关注",
            },
            "group_environment": {
                "atmosphere": "玩梗",
                "topic": "机器人称呼梗",
                "summary": "群里在讨论机器人和用户之间的称呼",
                "is_discussing_bot": True,
            },
            "action_decision": {
                "action": "save_memory",
                "reason": "与机器人之间的称呼有延续价值",
                "inner_monologue": "他们在说机器人像神兽",
                "reply_strategy": "机器人先观察",
            },
            "subjective_impression": {
                "relationship_story": "与机器人之间有轻松玩梗关系",
                "impression_delta": "用户把机器人叫成神兽，留下轻松印象",
            },
            "life_terms": [
                {
                    "term": "神兽",
                    "meaning": "用来调侃机器人像需要投喂和照看的存在",
                    "evidence": "群里说与机器人之间的称呼",
                }
            ],
            "evidence_refs": [
                {
                    "target_type": "relationship",
                    "target_id": "10001",
                    "summary": "用户把机器人叫神兽",
                }
            ],
            "relationship_points": ["用户会用神兽调侃机器人"],
            "behavior_scenes": [
                {
                    "scene": "围观称呼梗",
                    "cues": ["群里调侃称呼"],
                    "preferred_action": "observe",
                    "avoid_action": "认真纠正",
                    "outcome_hint": "先看大家怎么接",
                    "confidence": 0.7,
                }
            ],
            "focus_slots": [
                {
                    "focus_key": "nickname_joke",
                    "label": "称呼梗",
                    "priority": 66,
                    "reason": "刚被群里提起",
                }
            ],
            "memory_corrections": [
                {
                    "target_type": "life_episode",
                    "target_id": "999",
                    "correction": "这个旧片段不应作为事实引用",
                    "evidence": "用户刚刚否认了旧说法",
                    "confidence": 0.8,
                }
            ],
            "expression_intent": {
                "emotion": "觉得好笑但先忍住",
                "emotion_category": "happy",
                "emoji_intent": "轻轻围观",
                "action_intent": "探头看一眼",
                "send_emoji": False,
                "reason": "此刻直接发文字会抢话",
            },
        }
        meta = {
            "session_id": "aiocqhttp:GroupMessage:20001",
            "message_id": "m1",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "20001",
            "group_name": "测试群",
            "platform": "aiocqhttp",
            "user_id": "10001",
            "date": "2026-05-24",
            "is_group": "true",
        }

        await runtime._save_memory_awareness_records(payload, meta)
        await runtime._save_subjective_impression(payload, meta)
        await runtime._save_experience_payload(payload, meta)

        relationship = (await runtime.archive.get_recent_relationships(10))[0]
        visibility = (await runtime.archive.get_recent_message_visibility(10))[0]
        environment = (await runtime.archive.get_recent_group_environments(10))[0]
        decision = (await runtime.archive.get_recent_action_decisions(10))[0]
        term = (await runtime.archive.get_life_terms(10))[0]
        evidence = (await runtime.archive.get_memory_evidence(target_type="relationship", limit=10))[0]
        scene = (await runtime.archive.get_behavior_scenes(10))[0]
        slot = (await runtime.archive.get_focus_slots(10, active_only=False))[0]
        correction = (await runtime.archive.get_memory_corrections(10))[0]
        intent = (await runtime.archive.get_expression_intents(10))[0]

        combined = "\n".join(
            [
                relationship.relationship_story,
                relationship.notes[-1].content,
                visibility.reason,
                visibility.reactivation_hint,
                environment.topic,
                environment.summary,
                decision.reason,
                decision.inner_monologue,
                decision.reply_strategy,
                term.meaning,
                term.evidence,
                evidence.summary,
            ]
        )
        self.assertIn("机器人", combined)
        self.assertIn("与机器人之间", combined)
        self.assertIn("讨论机器人", combined)
        self.assertIn("把机器人叫神兽", combined)
        self.assertEqual(scene.scene, "围观称呼梗")
        self.assertEqual(slot.label, "称呼梗")
        self.assertEqual(correction.correction, "这个旧片段不应作为事实引用")
        self.assertEqual(intent.emotion_category, "happy")
        self.assertEqual(intent.action_intent, "探头看一眼")
    async def test_life_adjustments_are_layered_into_short_and_long_term_records(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.archive = DataManager()
        runtime.schedule_memos_correction = lambda *args, **kwargs: False
        payload = {
            "life_adjustments": [
                {
                    "scope": "current",
                    "target_type": "schedule",
                    "content": "今天先别写高强度外出",
                    "reason": "用户只是在修正本轮日程",
                    "confidence": 0.7,
                },
                {
                    "scope": "short_term",
                    "target_type": "sleep",
                    "content": "这几天让她多休息",
                    "reason": "用户给了短期恢复目标",
                    "priority": 86,
                    "confidence": 0.9,
                },
                {
                    "scope": "long_term",
                    "target_type": "hair",
                    "category": "hair",
                    "content": "她平时更适合清爽自然的发型变化",
                    "reason": "用户明确纠偏角色长期造型倾向",
                    "confidence": 0.95,
                },
                {
                    "scope": "correction",
                    "target_type": "life_episode",
                    "target_id": "old-episode",
                    "content": "这个旧片段不应再当成事实引用",
                    "reason": "用户否认旧记录",
                    "confidence": 0.8,
                },
            ]
        }
        meta = {
            "session_id": "aiocqhttp:FriendMessage:10001",
            "message_id": "m-life-adjust",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "",
            "group_name": "",
            "platform": "aiocqhttp",
            "user_id": "10001",
            "date": "2026-05-24",
            "is_group": "false",
        }

        await runtime._save_experience_payload(payload, meta)

        slots = await runtime.archive.get_focus_slots(10, active_only=False)
        preferences = await runtime.archive.get_preferences(10)
        corrections = await runtime.archive.get_memory_corrections(10)
        evidence = await runtime.archive.get_memory_evidence(limit=20)

        self.assertEqual(slots[0].label, "这几天让她多休息")
        self.assertEqual(slots[0].priority, 86)
        self.assertEqual(slots[0].expires_at, "2026-05-31")
        self.assertEqual(preferences[0].category, "hair")
        self.assertEqual(preferences[0].content, "她平时更适合清爽自然的发型变化")
        self.assertEqual(corrections[0].target_type, "life_episode")
        self.assertEqual(corrections[0].correction, "这个旧片段不应再当成事实引用")
        kinds = {(item.target_type, item.evidence_type) for item in evidence}
        self.assertIn(("life_adjustment", "instruction"), kinds)
        self.assertIn(("focus", "correction"), kinds)
        self.assertIn(("preference", "correction"), kinds)
        self.assertIn(("life_episode", "correction"), kinds)
    async def test_memory_maintenance_consolidates_proactive_feedback_scenes(self):
        runtime, _ = self._make_proactive_runtime([], provider_id="proactive-model")
        runtime.config = LifeSettings.from_dict({})
        runtime._settle_stale_reply_effects = lambda: async_return(0)
        runtime.maintain_emoji_assets = lambda: async_return({})
        runtime.archive.cleanup_by_storage_policy = lambda policy: async_return(0)
        scope = "aiocqhttp:FriendMessage:10001"
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        await runtime.archive.add_behavior_feedback(
            BehaviorFeedbackRecord(
                date=today,
                target_type="proactive_session",
                target_id=scope,
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后会话继续有新回应",
                result="positive",
                score=1.0,
                source="proactive_reply",
            )
        )
        await runtime.archive.add_behavior_feedback(
            BehaviorFeedbackRecord(
                date=today,
                target_type="proactive_session",
                target_id=scope,
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后一段时间内没有新的可见回应",
                result="ignored",
                score=-1.0,
                source="proactive_reply",
            )
        )

        await runtime.run_memory_maintenance()

        scenes = await runtime.archive.get_behavior_scenes(10, scope=scope)
        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0].scene, "闲时回复读空气")
        self.assertEqual(scenes[0].source, "proactive_feedback")
        self.assertEqual(scenes[0].preferred_action, "reply")
        maintenance = await runtime.archive.get_memory_maintenance(10)
        self.assertIn("行为经验归纳 1 组", maintenance[0].summary)
    async def test_chat_memory_saves_experience_layer_records(self):
        provider = Provider(
            [
                '{"worth_saving":true,'
                '"brief":"群里把看展话题接了下去",'
                '"long_summary":"Alice 提到 Bob 准备看展，后面有人说可以蹲后续。",'
                '"people":["Alice","Bob"],'
                '"visibility":{"level":"focused","attention_level":72,"priority":"normal","is_directed_at_bot":false,"reason":"同一话题被重新接起"},'
                '"action_decision":{"action":"save_memory","reason":"话题有延续价值","confidence":0.88,"scene_type":"群友档案","understanding":"understood"},'
                '"life_episode":{"title":"看展话题重新被接起","summary":"群里再次聊到 Bob 看展，适合后续回看。","kind":"group","related_people":["Bob"],"impact":"看展话题近期更容易进入注意。","confidence":0.86},'
                '"evidence_refs":[{"target_type":"focus","target_id":"看展","evidence_type":"observation","summary":"多人继续接看展话题","confidence":0.8}],'
                '"behavior_feedback":[{"scene":"群聊观察","action":"save_memory","feedback":"保存这条能帮助后续接话","result":"positive","score":1.5,"reason":"话题被多人延续"}],'
                '"expression_profiles":[{"scope":"group:20001","profile_id":"10001","label":"Alice","tone":"轻松接梗","habits":["短句确认","偶尔顺手吐槽"],"avoid":["不要长篇解释"],"confidence":0.82,"evidence":"群里这次是轻量玩笑式接话"}],'
                '"behavior_patterns":[{"scope":"group:20001","scene":"同一话题被多人续上","pattern":"先观察自然落点，必要时短句接话。","suggested_action":"observe","confidence":0.8,"support_count":1,"score":1.2,"evidence":"看展话题被多人延续"}],'
                '"session_mid_summary":{"summary":"群里在接 Bob 看展的话题，可以继续蹲后续。","topic":"看展","mood":"轻松围观","participants":["Alice","Bob"],"message_count":3},'
                '"temporary_expression_state":{"scope":"group:20001","label":"轻轻围观","tone":"短句，少解释","reason":"话题正在自然延续，我不想抢话","intensity":66,"expires_at":"2026-05-25"},'
                '"focus_updates":[{"target_type":"topic","target_id":"看展","label":"看展话题","priority":76,"reason":"近期可能形成邀约","scope":"group:20001","enabled":true}],'
                '"life_terms":[{"term":"蹲后续","meaning":"先围观同一话题后续发展","scope":"group:20001","scene":"等同一话题继续","examples":["Bob 那个看展可以蹲后续"],"familiarity":70,"confidence":0.9,"evidence":"群里原话"}],'
                '"memory_boundary_hint":{"source_scope":"group:20001","target_scope":"private:10001","policy":"ask","reason":"群聊里的第三人信息不宜直接私聊引用"},'
                '"relationship_points":[],"memory_targets":[],"preference_points":[]}'
            ]
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="Alice",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展小群",
            message_id="m3",
        )
        event.message_str = "Bob 那个看展可以蹲后续。"

        saved = await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 8))

        self.assertIsNotNone(saved)
        self.assertEqual((await runtime.archive.get_life_episodes(10))[0].title, "看展话题重新被接起")
        self.assertEqual((await runtime.archive.get_memory_evidence(target_type="focus", limit=10))[0].summary, "多人继续接看展话题")
        self.assertEqual((await runtime.archive.get_behavior_feedback(10))[0].result, "positive")
        self.assertEqual((await runtime.archive.get_expression_profiles(10))[0].tone, "轻松接梗")
        self.assertEqual((await runtime.archive.get_behavior_patterns(10))[0].scene, "同一话题被多人续上")
        self.assertEqual((await runtime.archive.get_session_mid_summaries(10))[0].topic, "看展")
        self.assertEqual((await runtime.archive.get_temporary_expression_states(10, active_only=False))[0].label, "轻轻围观")
        self.assertEqual((await runtime.archive.get_focus_targets(10))[0].label, "看展话题")
        self.assertEqual((await runtime.archive.get_life_terms(10))[0].term, "蹲后续")
        self.assertEqual((await runtime.archive.get_life_terms(10))[0].familiarity, 70)
        self.assertEqual((await runtime.archive.get_memory_boundaries(10))[0].policy, "ask")
        self.assertIn("life_episode", provider.prompts[0])
        self.assertIn("memory_boundary_hint", provider.prompts[0])
        self.assertIn("expression_profiles", provider.prompts[0])
        self.assertIn("behavior_patterns", provider.prompts[0])
        self.assertIn("session_mid_summary", provider.prompts[0])
        self.assertIn("temporary_expression_state", provider.prompts[0])
    async def test_chat_memory_prompt_includes_structured_quote_context(self):
        provider = Provider(['{"worth_saving":false}'])
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {"memory_config": {"min_message_length": 1}}
        )
        runtime.archive = DataManager()
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()

        class Composer:
            async def _get_provider(self, provider_id=""):
                return provider

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime.composer = Composer()
        event = Event(
            sender_name="Alice",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展小群",
            message_id="m4",
        )
        event.quote = {"message": "Bob 说下午场还有票"}
        event.message_str = "那可以蹲一下。"

        await runtime.maybe_capture_chat_memory_from_event(event, now=datetime.datetime(2026, 5, 24, 12, 10))

        self.assertIn("引用/回复上下文", provider.prompts[0])
        self.assertIn("Bob 说下午场还有票", provider.prompts[0])
    async def test_injection_resolves_sender_once_for_capture_tasks(self):
        gate = asyncio.Event()
        provider = Provider(
            [
                '{"has_commitment":false}',
                '{"worth_saving":true,"brief":"阿林想周末看展",'
                '"long_summary":"阿林提到周末想一起去看展。",'
                '"people":[],"relationship_points":[]}',
            ]
        )

        class Resolver:
            def __init__(self):
                self.calls = 0

            async def resolve_event_sender(self, event):
                self.calls += 1
                await gate.wait()
                return "阿林"

        class Composer:
            def __init__(self, provider):
                self.provider = provider

            async def _get_provider(self, provider_id=""):
                return self.provider

            async def generate_daily(self, date=None, force=False, target_hour=None, extra=None):
                day = DayRecord(
                    date=date.strftime("%Y-%m-%d"),
                    outfit="浅蓝外套",
                    timeline=[TimelineItem(time="12:00", activity="在家整理资料", status="平静")],
                )
                await runtime.archive.save_day(day)
                return day

            async def _call_llm_text(self, provider, prompt, session_id, empty_retries=0, primary_provider_id=""):
                resp = await provider.text_chat(prompt, session_id, system_prompt=CORE_INTERNAL_SYSTEM_PROMPT)
                return resp.completion_text

            async def _cleanup_conversation(self, session_id):
                return None

            async def learn_preferences_from_payload(self, payload, *, date_str, source):
                return []

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(provider)
        runtime.config = LifeSettings.from_dict(
            {
                "memory_config": {"min_message_length": 1},
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
        runtime.contact_resolver = Resolver()
        runtime.composer = Composer(provider)
        runtime.resolve_injection_target = lambda now: async_return(("2026-05-24", False))
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "周末我们去展览馆看展吧。"
        req = type("Request", (), {"prompt": "你好", "system_prompt": "", "session_id": "chat_session"})()

        await runtime.inject_life_context(req, event)
        await asyncio.sleep(0)

        self.assertEqual(runtime.contact_resolver.calls, 1)
        self.assertEqual(len(provider.prompts), 0)
        self.assertIn("<daily_life>", req.system_prompt)
        gate.set()
        await asyncio.gather(*list(runtime._background_scheduler.tasks))

        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(len(await runtime.archive.get_recent_chat_summaries(5)), 1)
    async def test_chat_context_capture_deduplicates_same_message(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._background_scheduler = BackgroundTaskScheduler()
        calls = []

        async def capture(event, now):
            calls.append((event, now))

        runtime._capture_chat_context_background = capture
        event = Event(
            unified_msg_origin="aiocqhttp:FriendMessage:10001",
            message_id="same-message",
        )
        now = datetime.datetime(2026, 7, 5, 10, 30)

        self.assertTrue(runtime.schedule_chat_context_capture_from_event(event, now))
        self.assertFalse(runtime.schedule_chat_context_capture_from_event(event, now))
        self.assertEqual(len(runtime._background_scheduler.tasks), 1)

        await asyncio.gather(*list(runtime._background_scheduler.tasks))

        self.assertEqual(calls, [(event, now)])
        self.assertEqual(runtime._background_scheduler.keys, set())
