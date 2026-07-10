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
