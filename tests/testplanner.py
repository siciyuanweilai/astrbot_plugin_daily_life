import asyncio
import datetime
import unittest

from support import ActionBot, AltProvider, ContactNameResolver, PersonaManager, PlatformManager, Provider, make_composer
from core.life import LifeBackgroundComposer
from core.presets import DEFAULT_CATALOG_POOLS, DEFAULT_STYLE_TO_HAIR_MAP
from core.models import ChatSummaryRecord, CommitmentRecord, DayRecord, EventRecord, LifeState, PlaceRecord, TimelineItem
from core.archive import builtin_entry_id


class LifePlannerTest(unittest.IsolatedAsyncioTestCase):
    async def test_extract_completion_text_recovers_structured_reasoning_content(self):
        resp = type(
            "Resp",
            (),
            {
                "content": "",
                "reasoning_content": '我先想一下。\n{"outfit_decision":"keep","reason":"已经准备睡了"}',
            },
        )()

        text = LifeBackgroundComposer._extract_completion_text(resp)

        self.assertTrue(text.startswith("{"))
        self.assertIn('"outfit_decision":"keep"', text)

    async def test_extract_completion_text_ignores_unstructured_reasoning_content(self):
        resp = type(
            "Resp",
            (),
            {
                "content": "",
                "reasoning_content": "我只是想了一下，但没有输出结构化结果。",
            },
        )()

        text = LifeBackgroundComposer._extract_completion_text(resp)

        self.assertEqual(text, "")

    async def test_extract_completion_text_prefers_visible_content(self):
        resp = type(
            "Resp",
            (),
            {
                "content": '{"visible": true}',
                "reasoning_content": '{"visible": false}',
            },
        )()

        text = LifeBackgroundComposer._extract_completion_text(resp)

        self.assertEqual(text, '{"visible": true}')

    async def test_builtin_catalog_states_filter_generation_pool(self):
        composer, _, _, archive = make_composer()
        disabled_theme = DEFAULT_CATALOG_POOLS["daily_themes"][0]
        disabled_style = next(iter(DEFAULT_STYLE_TO_HAIR_MAP))

        await archive.set_builtin_item_enabled(
            "catalog",
            builtin_entry_id(disabled_theme),
            False,
            scope="daily_themes",
        )
        await archive.set_builtin_item_enabled("hair", builtin_entry_id(disabled_style), False)

        catalog = await composer._get_catalog_settings()

        self.assertNotIn(disabled_theme, catalog.daily_themes)
        self.assertNotIn(disabled_style, catalog.style_to_hair_map)

    async def test_builtin_template_states_filter_week_templates(self):
        composer, _, _, archive = make_composer()

        await archive.set_builtin_item_enabled("template", "regular", False)

        active = await composer._get_week_templates()
        all_templates = await composer._get_week_templates(include_disabled=True)

        self.assertNotIn("regular", active)
        self.assertIn("regular", all_templates)
        self.assertFalse(all_templates["regular"]["enabled"])

    async def test_due_commitments_are_injected_and_scheduled(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"state":{"energy":60,"mood":"期待","busyness":30,"social":80,'
                '"sleep":{"quality":70,"summary":"睡得还行"},"summary":"适合履行约定"},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"09:42","activity":"在窗边确认今天的约定，把票务信息和路线重新看了一遍","status":"期待"},'
                '{"time":"15:10","activity":"和阿林去电影院看电影","status":"期待"},'
                '{"time":"21:36","activity":"回到家后把票根夹进手帐，洗漱前又回味了一会儿剧情","status":"满足"}],'
                '"timeline_audit":{"first_timeline_time":"09:42","last_timeline_time":"21:36","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴从上午准备延伸到夜间收尾，覆盖今天的约定节奏。"},'
                '"places":[{"name":"电影院","type":"cinema","hint":"看电影"}],'
                '"new_events":[]}'
            ]
        )
        saved = await archive.save_commitment(
            CommitmentRecord(
                content="周末一起看电影",
                kind="plan",
                trigger_date="2026-05-24",
                time_window="weekend",
                people=["阿林"],
            )
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0), force=True)

        self.assertIn("已答应过的承诺/约定", provider.prompts[0])
        self.assertIn("周末一起看电影", provider.prompts[0])
        self.assertEqual((await archive.get_commitment(saved.id)).status, "scheduled")
        self.assertIn(saved.id, archive.day_commitments["2026-05-24"])
        self.assertTrue(any(event.source == "commitment" for event in await archive.get_recent_events(10)))

    async def test_daily_prompt_uses_default_persona_prompt(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去咖啡店写手帐","status":"专注"}]}'
            ],
            persona_manager=PersonaManager(prompt="住在苏州，喜欢清冷书卷气和安静书店。"),
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIn("住在苏州", provider.prompts[0])
        self.assertIn("清冷书卷气", provider.prompts[0])

    async def test_daily_prompt_uses_complete_persona_prompt(self):
        late_hint = "后半段关键设定：小岑是我的男死党，性格爽朗，经常约我看展。"
        long_persona = "。".join([f"普通人设片段{i}" for i in range(160)]) + "。" + late_hint
        self.assertGreater(long_persona.index(late_hint), 1200)
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去咖啡店写手帐","status":"专注"}]}'
            ],
            persona_manager=PersonaManager(prompt=long_persona),
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIn("## 👤 角色设定", provider.prompts[0])
        self.assertIn(late_hint, provider.prompts[0])
        self.assertGreater(provider.prompts[0].index(late_hint), provider.prompts[0].index("## 👤 角色设定"))

    async def test_daily_prompt_links_reference_chat_to_persona_contact(self):
        now_ts = int(datetime.datetime.now().timestamp())
        bot = ActionBot(
            {
                "get_friend_msg_history": {
                    "messages": [
                        {
                            "message_id": 1,
                            "message_seq": 10,
                            "time": now_ts,
                            "sender": {"user_id": 123456, "nickname": "QQ昵称"},
                            "raw_message": "周末打球吗",
                        }
                    ]
                }
            }
        )
        composer, provider, _, archive = make_composer(
            [
                '{"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去咖啡店写手帐","status":"专注"}]}'
            ],
            persona_manager=PersonaManager(prompt="我是女生。阿林是我的男死党，性格很爽朗，经常约我打球。"),
        )
        composer.context.platform_manager = PlatformManager(bot)
        composer.config.reference_users = ["123456"]
        composer.config.history_hours = 24
        composer.config.history_max_count = 10
        composer.contact_resolver = ContactNameResolver(
            composer.context,
            {"relationship_aliases": ["123456:阿林"]},
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIn("【参考对象人设线索】称呼：阿林", provider.prompts[0])
        self.assertIn("阿林是我的男死党", provider.prompts[0])
        self.assertIn("人物称呼、性别和关系必须以人设线索为准", provider.prompts[0])
        self.assertNotIn("禁止把男性朋友/男死党写成姐妹、闺蜜或女性", provider.prompts[0])

    async def test_daily_prompt_marks_saved_pronouns_as_non_gender_evidence(self):
        composer, provider, _, archive = make_composer(
            [
                '{"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去咖啡店写手帐","status":"专注"}]}'
            ],
        )
        await archive.touch_relationship(
            "10001",
            name="小林",
            date_str="2026-05-23",
            relationship_story="她平时会记得我想去看展。",
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIn("## 关系档案", provider.prompts[0])
        self.assertIn("若某人没有明确人设线索", provider.prompts[0])
        self.assertIn("零散出现的他/她不能当作性别依据", provider.prompts[0])
        self.assertIn("关系叙事：她平时会记得我想去看展。", provider.prompts[0])

    async def test_daily_prompt_uses_configurable_story_rules(self):
        composer, provider, _, _ = make_composer(
            [
                '{"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去咖啡店写手帐","status":"专注"}]}'
            ],
        )
        composer.config.state_prompt = "状态规则：今天要有一点困倦感"
        composer.config.timeline_prompt = "时间轴规则：多写室内生活片段"
        composer.config.world_prompt = "地点事件规则：只记录真实出现地点"
        composer.config.chat_prompt = "聊天规则：严格按人设线索理解人物关系"

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIn("状态规则：今天要有一点困倦感", provider.prompts[0])
        self.assertIn("时间轴规则：多写室内生活片段", provider.prompts[0])
        self.assertIn("地点事件规则：只记录真实出现地点", provider.prompts[0])

    async def test_daily_prompt_does_not_use_provider_system_prompt_as_persona(self):
        composer, provider, _, _ = make_composer(
            [
                '{"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去咖啡店写手帐","status":"专注"}]}'
            ],
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertNotIn(provider.system_prompt, provider.prompts[0])
        self.assertIn("一个热爱生活的人", provider.prompts[0])

    async def test_provider_selection_and_completion_content_fallback(self):
        composer, default_provider, selected_provider, _ = make_composer(
            provider_id="selected",
            selected_responses=[
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"风格：莓果甜心风\\n粉色针织开衫搭配草莓发夹",'
                '"timeline":[{"time":"10:00","activity":"整理好草莓发夹和包里的小物","status":"开心"},'
                '{"time":"15:30","activity":"去奶茶店喝下午茶","status":"开心"},'
                '{"time":"21:20","activity":"回家后把开衫挂好，带着甜味慢慢放松下来","status":"满足"}],'
                '"timeline_audit":{"first_timeline_time":"10:00","last_timeline_time":"21:20","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖上午准备、下午茶和夜间收尾。"}}'
            ],
        )

        data = await composer.generate_daily(
            datetime.datetime(2026, 5, 24, 10, 0),
            extra="穿粉色针织开衫，戴草莓发夹，去奶茶店喝下午茶",
        )

        self.assertIsNotNone(data)
        self.assertEqual(len(default_provider.prompts), 0)
        self.assertEqual(len(selected_provider.prompts), 1)
        self.assertIn("草莓发夹", data.outfit)

    async def test_configured_provider_failure_temporarily_falls_back_to_default_provider(self):
        composer, default_provider, selected_provider, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"默认模型生成的浅蓝居家裙",'
                '"timeline":[{"time":"09:20","activity":"在窗边整理今天的小计划","status":"平稳"},'
                '{"time":"21:30","activity":"洗漱后把明天要用的杯子放好，慢慢关灯","status":"放松"}],'
                '"timeline_audit":{"first_timeline_time":"09:20","last_timeline_time":"21:30","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖白天开始和夜间睡前收束。"},'
                '"places":[],"new_events":[]}'
            ],
            provider_id="selected",
            selected_responses=[""],
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0), force=True)

        self.assertIsNotNone(data)
        self.assertIn("默认模型生成", data.outfit)
        self.assertEqual(len(selected_provider.prompts), 1)
        self.assertEqual(len(default_provider.prompts), 1)
        self.assertIs(await composer._get_provider(), default_provider)
        self.assertIs(await composer._get_provider("selected"), selected_provider)

    async def test_configured_provider_401_immediately_falls_back_to_config_default_provider(self):
        default_provider = Provider(["默认模型成功"], provider_id="default")
        selected_provider = AltProvider([RuntimeError("401 Unauthorized")], provider_id="selected")
        composer, _, _, _ = make_composer(
            provider_id="selected",
            providers={"default": default_provider, "selected": selected_provider},
            context_config={
                "provider_settings": {"default_provider_id": "default"},
                "provider": [
                    {"id": "default", "enable": True, "provider_type": "chat"},
                    {"id": "selected", "enable": True, "provider_type": "chat"},
                ],
            },
        )

        provider = await composer._get_provider("selected")
        text = await composer._call_llm_text(
            provider,
            "测试提示",
            "daily_life_test",
            empty_retries=0,
            primary_provider_id="selected",
        )
        next_provider = await composer._get_provider("selected")
        default_provider.responses.append("默认模型继续成功")
        followup_text = await composer._call_llm_text(
            provider,
            "二次测试提示",
            "daily_life_test_2",
            empty_retries=0,
            primary_provider_id="selected",
        )

        self.assertEqual(text, "默认模型成功")
        self.assertEqual(followup_text, "默认模型继续成功")
        self.assertIs(next_provider, selected_provider)
        self.assertEqual(len(selected_provider.prompts), 2)
        self.assertEqual(len(default_provider.prompts), 2)
        self.assertIn("隐藏推理口吻", selected_provider.prompts[0])
        self.assertIn("隐藏推理口吻", default_provider.prompts[0])
        self.assertTrue(selected_provider.prompts[0].startswith("隐藏推理口吻"))
        self.assertIn("隐藏推理也必须站在“我”的角色视角判断", selected_provider.prompts[0])
        self.assertEqual(selected_provider.prompts[0].count("隐藏推理也必须站在“我”的角色视角判断"), 1)
        self.assertIn("服务端隐藏推理的第一句必须以“我”开头", selected_provider.prompts[0])
        self.assertEqual(selected_provider.prompts[0].count("服务端隐藏推理的第一句必须以“我”开头"), 1)
        self.assertIn("不得把内心独白、旁白或“我……”句子写进最终可见输出", selected_provider.prompts[0])
        self.assertEqual(selected_provider.prompts[0].count("不得把内心独白、旁白或“我……”句子写进最终可见输出"), 1)
        self.assertIn("只写我此刻看到、想到、犹豫、决定和感受到的内容", selected_provider.prompts[0])
        self.assertIn("不要使用复数主语、模型自述、系统自述", selected_provider.prompts[0])
        self.assertIn("不要使用复数视角、规则审题、样本解析、任务说明等措辞", selected_provider.prompts[0])
        self.assertIn("隐藏推理第一句必须从“我”开始", selected_provider.system_prompts[0])
        self.assertIn("禁止把隐藏推理、内心独白、解释、旁白或“我……”前置句写进最终可见输出", selected_provider.system_prompts[0])
        self.assertIn("主语只用“我”", selected_provider.system_prompts[0])
        self.assertIn("不要使用复数视角、规则审题、样本解析、任务说明等措辞", selected_provider.system_prompts[0])
        self.assertNotIn("我们分析当前情况", selected_provider.prompts[0])
        self.assertNotIn("我们分析当前情况", selected_provider.system_prompts[0])

    async def test_update_outfit_missing_day_generates_target_date_without_deadlock(self):
        composer, _, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"target_period",'
                '"timeline_audit_coverage_mode":"target_period","closed_loop_required":false},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去咖啡店写手帐","status":"专注"}],'
                '"timeline_audit":{"first_timeline_time":"12:10","last_timeline_time":"12:10","coverage_mode":"target_period",'
                '"start_reason":"target_period","end_reason":"target_period","covers_full_day":false,"closed_loop":false,'
                '"summary":"时间轴只覆盖目标时段。"}}'
            ]
        )

        data = await asyncio.wait_for(
            composer.update_outfit("2026-05-24", "noon"),
            timeout=1,
        )

        self.assertIsNotNone(data)
        stored = await archive.get_day("2026-05-24")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.time_period, "noon")

    async def test_daily_generation_uses_autonomous_life_decision(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"late_night",'
                '"sleep":{"mode":"late_night","quality":38,"summary":"昨晚熬夜到很晚"},'
                '"outfit":{"decision":"keep","scene_category":"home","style_pool":"sleep_styles",'
                '"style":"宽松居家风","hair":"松散长发","reason":"还没准备出门"},'
                '"day_plan":{"schedule_type":"宅家充电的慵懒一日","schedule_intent":"rest","energy_bias":"rest","social_bias":"avoid"},'
                '"theme":"慢慢恢复日","mood":"薄荷绿·治愈"},'
                '"state":{"energy":36,"mood":"困倦但安稳","busyness":20,"social":15,'
                '"sleep":{"quality":38,"summary":"昨晚熬夜到很晚"},"summary":"今天适合低负担恢复"},'
                '"outfit":"宽松白色长T恤，松散长发垂在肩侧",'
                '"timeline":[{"time":"10:40","activity":"在被窝里慢慢清醒，听窗外的声音","status":"困倦"},'
                '{"time":"21:50","activity":"洗漱后仍然不急着睡，靠在床头把手机亮度调低","status":"低电量清醒"}],'
                '"timeline_audit":{"first_timeline_time":"10:40","last_timeline_time":"21:50","coverage_mode":"full_day",'
                '"start_reason":"life_decision","end_reason":"late_night","covers_full_day":true,"closed_loop":true,'
                '"summary":"低负担恢复日从晚起延伸到夜间清醒状态，形成完整全天节奏。"},'
                '"places":[],"new_events":[]}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 2, 30), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.meta["life_mode"], "late_night")
        self.assertEqual(data.meta["sleep_mode"], "late_night")
        self.assertEqual(data.meta["plan_outfit_decision"], "keep")
        self.assertEqual(data.meta["outfit_decision"], "keep")
        self.assertEqual(data.meta["outfit_scene_category"], "home")
        self.assertEqual(data.meta["outfit_style_pool"], "sleep_styles")
        self.assertEqual(data.meta["schedule_type"], "宅家充电的慵懒一日")
        self.assertEqual(data.meta["schedule_intent"], "rest")
        prompt = provider.prompts[0]
        self.assertIn("life_decision", prompt)
        self.assertIn("generation_contract", prompt)
        self.assertIn("timeline_audit", prompt)
        self.assertIn("素材灵感库", prompt)
        self.assertIn("schedule_type", prompt)
        self.assertIn("【通用自主原则】", prompt)
        self.assertIn("【通用状态行为原则】", prompt)
        self.assertIn("JSON 输出要求", prompt)
        self.assertIn("颜色名·情绪词", prompt)
        self.assertIn("日程类型标签", prompt)
        self.assertIn("不要写穿搭风格", prompt)
        self.assertIn("不要因为当前时间线索偏早就强制起床", prompt)
        self.assertIn("时间轴节点数量由 life_decision 和当天复杂度自主决定", prompt)
        self.assertIn("必须先判断穿搭是否适合外出场景和天气", prompt)
        self.assertIn("decision=keep 只表示当前穿搭本身已经适合接下来的活动", prompt)
        self.assertIn("life_decision.outfit.scene_category", prompt)
        self.assertIn("life_decision.outfit.style_pool", prompt)
        self.assertIn("home | sleep | outdoor | public | mixed", prompt)
        self.assertIn("sleep_styles | outfit_styles | mixed", prompt)
        self.assertNotIn("8 到 12", prompt)
        self.assertNotIn("绝对指令", prompt)
        self.assertNotIn("今日创意约束", prompt)
        episodes = await archive.get_life_episodes(10)
        self.assertTrue(episodes)
        self.assertIn("日程基调：夜间清醒", episodes[0].summary)
        self.assertIn("日程倾向：休息", episodes[0].summary)
        self.assertIn("日程穿搭：预计保持", episodes[0].summary)
        self.assertNotIn("时间轴：", episodes[0].summary)
        self.assertNotIn("late_night", episodes[0].summary)
        self.assertNotIn("outfit_decision", episodes[0].summary)

    async def test_daily_generation_prompt_keeps_static_rules_before_dynamic_context(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"09:00","activity":"整理今日计划","status":"平稳"},'
                '{"time":"21:00","activity":"洗漱后慢慢收尾","status":"放松"}],'
                '"timeline_audit":{"first_timeline_time":"09:00","last_timeline_time":"21:00","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"覆盖完整一天。"}}'
            ],
            persona_manager=PersonaManager(prompt="住在苏州，喜欢清冷书店。"),
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0), force=True)

        prompt = provider.prompts[0]
        self.assertTrue(prompt.startswith("隐藏推理口吻"))
        self.assertLess(prompt.index("生成当前角色的自主生活背景"), prompt.index("目标日期：2026-05-24"))
        self.assertLess(prompt.index("【通用自主原则】"), prompt.index("【今日生活资料】"))
        self.assertIn("住在苏州", prompt)

    async def test_daily_generation_rejects_visible_inner_monologue_before_json(self):
        valid_json = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
            '"life_decision":{"life_mode":"resting",'
            '"sleep":{"mode":"normal","quality":70,"depth":"awake","summary":"睡眠正常"},'
            '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"今天在家休息"},'
            '"day_plan":{"schedule_type":"宅家充电的慵懒一日","schedule_intent":"rest","energy_bias":"normal","social_bias":"light"},'
            '"theme":"雨天居家","mood":"薄荷绿·治愈"},'
            '"state":{"energy":62,"mood":"平静","mood_score":68,"busyness":20,"social":25,"stress":12,'
            '"focus":45,"sleepiness":25,"outgoing":18,"emotional_stability":82,"interaction_capacity":42,'
            '"boredom":20,"fishing":10,"attention_openness":35,"watch_state":"peek","interrupt_level":"medium",'
            '"interrupt_reason":"居家休息中，只让熟悉消息进入注意",'
            '"sleep":{"quality":70,"depth":"awake","summary":"昨晚睡眠正常"},"summary":"雨天低负担居家恢复"},'
            '"outfit":"宽松白色长T恤，低马尾，浅灰棉质长裤",'
            '"timeline":[{"time":"09:20","activity":"听着雨声慢慢醒来，先把窗帘拉开一点","status":"清醒"},'
            '{"time":"18:30","activity":"窝在沙发上合上书，考虑晚餐煮面","status":"放松"},'
            '{"time":"22:40","activity":"洗漱后关掉客厅小灯，准备睡觉","status":"安稳"}],'
            '"timeline_audit":{"first_timeline_time":"09:20","last_timeline_time":"22:40","coverage_mode":"full_day",'
            '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
            '"summary":"时间轴从上午醒来到夜间睡前收束，覆盖完整居家日。"},'
            '"places":[],"new_events":[]}'
        )
        composer, provider, _, _ = make_composer(
            [
                "我靠在窗边听雨，先把今天整理一下。\n" + valid_json,
                valid_json,
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 6, 23, 18, 29), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.outfit, "宽松白色长T恤，低马尾，浅灰棉质长裤")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("未能解析出 JSON 对象", provider.prompts[1])
        self.assertIn("第一个非空字符必须是 {", provider.prompts[1])
        self.assertIn("不要在 JSON 前后写“我……”内心独白", provider.prompts[1])

    async def test_daily_generation_repairs_outfit_style_contaminated_by_theme_or_mood(self):
        contaminated = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
            '"life_decision":{"life_mode":"awake",'
            '"sleep":{"mode":"normal","quality":72,"summary":"睡眠正常"},'
            '"outfit":{"decision":"keep","scene_category":"outdoor","style_pool":"outfit_styles",'
            '"style":"跟着直觉走的漫游日·奶茶色·惬意","hair":"高马尾","reason":"今天外出很轻松"},'
            '"day_plan":{"schedule_type":"走走停停的街头漫游","schedule_intent":"outing","energy_bias":"normal","social_bias":"light"},'
            '"theme":"跟着直觉走的漫游日","mood":"奶茶色·惬意"},'
            '"state":{"energy":70,"mood":"轻松","busyness":20,"social":40,'
            '"sleep":{"quality":72,"summary":"睡得不错"},"summary":"轻松出门"},'
            '"outfit":"浅杏色短袖衬衫，牛仔短裤，帆布鞋，高马尾",'
            '"timeline":[{"time":"09:00","activity":"收拾帆布包准备出门","status":"轻松"},'
            '{"time":"21:20","activity":"回家洗漱后准备休息","status":"放松"}],'
            '"timeline_audit":{"first_timeline_time":"09:00","last_timeline_time":"21:20","coverage_mode":"full_day",'
            '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
            '"summary":"时间轴覆盖从早晨出门准备到夜间回家休息的一整天。"},'
            '"places":[],"new_events":[]}'
        )
        repaired = contaminated.replace(
            '"style":"跟着直觉走的漫游日·奶茶色·惬意"',
            '"style":"元气休闲风"',
        )
        composer, provider, _, _ = make_composer([contaminated, repaired])

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 9, 0), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.meta["style"], "元气休闲风")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("life_decision.outfit.style 混入了今日主题", provider.prompts[1])
        self.assertIn("只写穿搭风格", provider.prompts[1])

    async def test_daily_generation_keeps_mood_color_separate_from_state_mood(self):
        composer, _, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"awake",'
                '"sleep":{"mode":"normal","quality":72,"summary":"昨晚睡得还可以"},'
                '"outfit":{"decision":"keep","style":"轻便居家风","hair":"低马尾","reason":"今天安排轻松"},'
                '"day_plan":{"schedule_type":"偷偷变优秀的学习时间","schedule_intent":"study","energy_bias":"normal","social_bias":"light"},'
                '"theme":"轻量学习日","mood":"元气满满，准备好好学习"},'
                '"state":{"energy":70,"mood":"元气满满，准备好好学习","busyness":40,"social":35,'
                '"sleep":{"quality":72,"summary":"昨晚睡得还可以"},"summary":"今天适合慢慢进入学习状态"},'
                '"outfit":"白色针织衫和浅灰长裙，长发扎成低马尾",'
                '"timeline":[{"time":"09:30","activity":"整理桌面后翻开笔记","status":"平稳"},'
                '{"time":"21:20","activity":"收好资料后准备休息","status":"放松"}],'
                '"timeline_audit":{"first_timeline_time":"09:30","last_timeline_time":"21:20","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖白天学习和夜间收束。"},'
                '"places":[],"new_events":[]}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 9, 0), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.state.mood, "元气满满，准备好好学习")
        self.assertNotIn("mood", data.meta)

    async def test_update_outfit_can_keep_current_outfit(self):
        composer, provider, _, archive = make_composer(
            [
                '{"outfit_decision":"keep","scene_category":"home","style_pool":"sleep_styles",'
                '"style":"延续居家风","hair":"低松马尾",'
                '"life_mode":"resting","reason":"今天没有出门需求"}'
            ]
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="宽松白色长T恤，低松马尾",
                timeline=[TimelineItem(time="10:00", activity="在家整理手帐", status="慢")],
                meta={"life_mode": "resting", "plan_outfit_decision": "outdoor", "outfit_decision": "outdoor"},
            )
        )

        data = await composer.update_outfit("2026-05-24", "forenoon")

        self.assertIsNotNone(data)
        self.assertEqual(data.outfit, "宽松白色长T恤，低松马尾")
        self.assertEqual(data.meta["plan_outfit_decision"], "outdoor")
        self.assertEqual(data.meta["outfit_decision"], "keep")
        self.assertEqual(data.meta["outfit_scene_category"], "home")
        self.assertEqual(data.meta["outfit_style_pool"], "sleep_styles")
        self.assertEqual(data.meta["style"], "延续居家风")
        self.assertIn("自主判断穿搭是否需要变化", provider.prompts[0])
        self.assertIn("素材灵感库", provider.prompts[0])
        self.assertIn("必须先判断【当前穿搭】是否适合该外出场景和天气", provider.prompts[0])
        self.assertIn("如果选择 keep，表示当前穿搭本身已经适合接下来的外出/活动", provider.prompts[0])
        self.assertIn('"scene_category": "home | sleep | outdoor | public | mixed"', provider.prompts[0])
        self.assertIn('"style_pool": "sleep_styles | outfit_styles | mixed"', provider.prompts[0])
        self.assertIn("必须和 style_pool 一致", provider.prompts[0])

    async def test_update_outfit_uses_dedicated_outfit_provider(self):
        outfit_provider = Provider(
            ['{"outfit_decision":"change","outfit":"浅蓝外出裙，低马尾","style":"清爽外出风","hair":"低马尾","reason":"下午要出门"}'],
            provider_id="outfit-model",
        )
        composer, default_provider, _, archive = make_composer(
            provider_id="generation-model",
            providers={"outfit-model": outfit_provider},
            config_overrides={"outfit_config": {"provider": "outfit-model"}},
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="宽松居家T恤，散发",
                timeline=[TimelineItem(time="15:00", activity="出门去书店", status="期待")],
                meta={"life_mode": "awake"},
            )
        )

        data = await composer.update_outfit("2026-05-24", "afternoon")

        self.assertIsNotNone(data)
        self.assertEqual(data.outfit, "浅蓝外出裙，低马尾")
        self.assertEqual(len(outfit_provider.prompts), 1)
        self.assertEqual(len(default_provider.prompts), 0)

    async def test_update_outfit_uses_current_default_provider_when_unset(self):
        composer, default_provider, generation_provider, archive = make_composer(
            provider_id="selected",
            responses=[
                '{"outfit_decision":"partial_change","outfit":"白色T恤外搭薄衬衫，低马尾","style":"轻便出门风","hair":"低马尾","reason":"临时出门"}'
            ],
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="白色T恤，散发",
                timeline=[TimelineItem(time="16:00", activity="去便利店买东西", status="轻松")],
            )
        )

        data = await composer.update_outfit("2026-05-24", "evening")

        self.assertIsNotNone(data)
        self.assertEqual(data.outfit, "白色T恤外搭薄衬衫，低马尾")
        self.assertEqual(len(default_provider.prompts), 1)
        self.assertEqual(len(generation_provider.prompts), 0)

    async def test_update_outfit_ignores_contaminated_outfit_style(self):
        composer, _, _, archive = make_composer(
            [
                '{"outfit_decision":"keep","outfit":"浅杏色短袖衬衫，牛仔短裤，高马尾",'
                '"style":"跟着直觉走的漫游日·奶茶色·惬意","hair":"高马尾","reason":"当前穿搭适合外出"}'
            ]
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅杏色短袖衬衫，牛仔短裤，高马尾",
                timeline=[TimelineItem(time="15:00", activity="沿着街边随意散步", status="轻松")],
                meta={
                    "theme": "跟着直觉走的漫游日",
                    "mood": "奶茶色·惬意",
                    "schedule_type": "走走停停的街头漫游",
                    "style": "元气休闲风",
                },
            )
        )

        data = await composer.update_outfit("2026-05-24", "afternoon")
        stored = await archive.get_day("2026-05-24")

        self.assertIsNone(data)
        self.assertEqual(stored.meta["style"], "元气休闲风")
        self.assertNotEqual(stored.meta["style"], "跟着直觉走的漫游日·奶茶色·惬意")

    async def test_update_outfit_localizes_internal_enum_tokens_in_reason(self):
        composer, provider, _, archive = make_composer(
            [
                '{"outfit_decision":"outdoor","outfit":"白色短外套和浅蓝长裙","style":"清爽外出风",'
                '"hair":"低马尾","reason":"当前居家穿搭不适合 outdoor，relaxing 的日程里临时出门需要更轻便"}'
            ]
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="奶茶色居家裙",
                timeline=[TimelineItem(time="15:00", activity="出门去书店闲逛", status="期待")],
                meta={"life_mode": "relaxing"},
            )
        )

        data = await composer.update_outfit("2026-05-24", "afternoon")

        self.assertIsNotNone(data)
        self.assertEqual(data.meta["outfit_decision"], "outdoor")
        self.assertIn("外出", data.meta["outfit_reason"])
        self.assertIn("放松", data.meta["outfit_reason"])
        self.assertNotIn("outdoor", data.meta["outfit_reason"])
        self.assertNotIn("relaxing", data.meta["outfit_reason"])
        self.assertIn("reason 必须使用自然中文", provider.prompts[0])

    async def test_outfit_prompt_keeps_static_rules_before_dynamic_context(self):
        composer, provider, _, archive = make_composer(
            ['{"outfit_decision":"keep","outfit":"奶茶色居家裙","style":"居家风","hair":"散发","reason":"今天无外出需求"}']
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="奶茶色居家裙",
                timeline=[TimelineItem(time="15:00", activity="在窗边发呆", status="安静")],
                meta={"life_mode": "resting"},
            )
        )

        await composer.update_outfit("2026-05-24", "afternoon")

        prompt = provider.prompts[0]
        self.assertTrue(prompt.startswith("隐藏推理口吻"))
        self.assertLess(prompt.index("当前已有生活日时间轴"), prompt.index("当前时间线索：下午"))
        self.assertLess(prompt.index("返回JSON格式"), prompt.index("【穿搭现场】"))
        self.assertIn("当前穿搭：奶茶色居家裙", prompt)

    async def test_update_outfit_anchors_to_current_time_and_state(self):
        composer, provider, _, archive = make_composer(
            [
                '{"outfit_decision":"outdoor","outfit":"米白防雨外套配浅色长裙，长发低低扎起",'
                '"style":"雨天外出风","hair":"低马尾","reason":"此刻正在雨天外出，睡前安排还没到"}'
            ]
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="奶白色纯棉睡裙，干发帽裹着湿发",
                timeline=[
                    TimelineItem(time="13:20", activity="坐在雨边长椅吃炸串", status="慵懒满足"),
                    TimelineItem(time="15:30", activity="去甜品店看看草莓慕斯", status="轻松"),
                    TimelineItem(time="21:00", activity="洗完澡换睡裙准备睡前放松", status="困倦"),
                ],
                meta={"life_mode": "mixed", "sleep_mode": "early_sleep"},
                state=LifeState(
                    mood="慵懒满足",
                    outgoing=55,
                    sleepiness=30,
                    summary="坐在雨边长椅吃炸串，想吃完再溜达过去碰头",
                    interrupt_reason="周围安静，消息进来会留意但不会急着回复",
                ),
            )
        )

        await composer.update_outfit(
            "2026-05-24",
            "noon",
            current_time=datetime.datetime(2026, 5, 24, 13, 40),
        )

        prompt = provider.prompts[0]
        self.assertIn("当前实际时间：2026-05-24 13:40", prompt)
        self.assertIn("当前时间范围：12:00-14:00", prompt)
        self.assertIn("当前日程位置：13:20 - 坐在雨边长椅吃炸串 [慵懒满足]", prompt)
        self.assertIn("下一项安排：15:30 - 去甜品店看看草莓慕斯 [轻松]", prompt)
        self.assertIn("实时状态摘要：坐在雨边长椅吃炸串，想吃完再溜达过去碰头", prompt)
        self.assertIn("未发生的未来安排只能作为预告，不能提前覆盖当前穿搭", prompt)
        self.assertLess(prompt.index("当前日程位置"), prompt.index("未发生日程预告"))
        self.assertIn("21:00 - 洗完澡换睡裙准备睡前放松 [困倦]（约 440 分钟后，尚未发生）", prompt)

    async def test_update_outfit_does_not_apply_future_homewear_before_arrival(self):
        composer, provider, _, archive = make_composer(
            [
                '{"outfit_decision":"keep","outfit":"米白针织开衫配浅色吊带裙，长发用发带松松束着",'
                '"style":"轻便外出风","hair":"发带松松束发","reason":"还在小超市外，回家换居家服的时间尚未到"}'
            ]
        )
        await archive.save_day(
            DayRecord(
                date="2026-06-23",
                outfit="米白针织开衫配浅色吊带裙，长发用发带松松束着",
                timeline=[
                    TimelineItem(time="17:04", activity="从书店出来，顺路去小超市买盒鸡蛋和一小把菠菜", status="轻松"),
                    TimelineItem(time="18:20", activity="回到家换下帆布鞋，换上宽松的米白色棉麻家居连衣裙", status="安稳"),
                ],
                meta={"life_mode": "mixed"},
                state=LifeState(
                    mood="轻松",
                    outgoing=55,
                    sleepiness=20,
                    summary="刚从小超市出来，手里拎着鸡蛋和菠菜，还在外面",
                    interrupt_reason="在街边走着，能看消息但还没回家",
                ),
            )
        )

        await composer.update_outfit(
            "2026-06-23",
            "evening",
            current_time=datetime.datetime(2026, 6, 23, 17, 45),
        )

        prompt = provider.prompts[0]
        self.assertIn("当前实际时间：2026-06-23 17:45", prompt)
        self.assertIn("当前日程位置：17:04 - 从书店出来，顺路去小超市买盒鸡蛋和一小把菠菜 [轻松]", prompt)
        self.assertIn("18:20 - 回到家换下帆布鞋，换上宽松的米白色棉麻家居连衣裙 [安稳]（约 35 分钟后，尚未发生）", prompt)
        self.assertIn("未发生日程预告（只能作为后续参考，不能提前生效）", prompt)
        self.assertIn("不能换成“赤脚/棉袜/睡裙/家居连衣裙/洗澡后/睡前”等室内状态", prompt)
        self.assertEqual(
            (await archive.get_day("2026-06-23")).outfit,
            "米白针织开衫配浅色吊带裙，长发用发带松松束着",
        )

    async def test_update_outfit_rejects_future_homewear_result(self):
        composer, provider, _, archive = make_composer(
            [
                '{"outfit_decision":"keep","outfit":"宽松的米白色棉麻家居连衣裙，长发自然披在肩上，赤脚穿棉袜",'
                '"style":"柔软治愈系","hair":"自然披发","reason":"提前套用回家后的居家服"}'
            ]
        )
        await archive.save_day(
            DayRecord(
                date="2026-06-23",
                outfit="米白针织开衫配浅色吊带裙，长发用发带松松束着",
                timeline=[
                    TimelineItem(time="17:04", activity="从书店出来，顺路去小超市买盒鸡蛋和一小把菠菜", status="轻松"),
                    TimelineItem(time="18:20", activity="回到家换下帆布鞋，换上宽松的米白色棉麻家居连衣裙", status="安稳"),
                ],
                meta={"life_mode": "mixed"},
            )
        )

        result = await composer.update_outfit(
            "2026-06-23",
            "evening",
            current_time=datetime.datetime(2026, 6, 23, 17, 45),
        )

        self.assertIsNone(result)
        stored = await archive.get_day("2026-06-23")
        self.assertEqual(stored.outfit, "米白针织开衫配浅色吊带裙，长发用发带松松束着")
        self.assertEqual(stored.outfit_history, {})
        self.assertEqual(len(provider.prompts), 1)

    async def test_update_outfit_keeps_previous_day_sleepwear_during_extended_night(self):
        composer, provider, _, archive = make_composer(
            [
                '{"outfit_decision":"keep","outfit":"奶油色云朵绒长袖睡衣套装，头发用深灰色抓夹松松盘起，赤脚踩在棉拖鞋里",'
                '"style":"软绵绵的云朵绒两件套","hair":"睡不醒丸子头","reason":"已经准备睡了，睡衣正合适"}'
            ]
        )
        await archive.save_day(
            DayRecord(
                date="2026-06-23",
                outfit="奶油色云朵绒长袖睡衣套装，头发用深灰色抓夹松松盘起，赤脚踩在棉拖鞋里",
                timeline=[
                    TimelineItem(time="18:20", activity="回到家换掉外出衣服，套上米白色棉麻家居连衣裙", status="放松"),
                    TimelineItem(time="20:50", activity="洗完澡换上奶油色云朵绒长袖睡衣套装", status="困倦"),
                ],
                meta={"life_mode": "resting"},
            )
        )

        result = await composer.update_outfit(
            "2026-06-23",
            "dawn",
            current_time=datetime.datetime(2026, 6, 24, 0, 17),
        )

        self.assertIsNotNone(result)
        stored = await archive.get_day("2026-06-23")
        self.assertIn("云朵绒长袖睡衣", stored.outfit)
        self.assertEqual(stored.outfit_history["dawn"], stored.outfit)
        prompt = provider.prompts[0]
        self.assertIn("生活日程日期：2026-06-23（如果和当前实际日期不同，表示凌晨延续该日程）", prompt)
        self.assertIn("20:50 - 洗完澡换上奶油色云朵绒长袖睡衣套装 [困倦]", prompt)
        self.assertIn("暂无未发生日程", prompt)

    async def test_daily_review_uses_dedicated_review_provider(self):
        review_provider = Provider(
            ['{"summary":"复盘模型沉淀的一天","memory_points":["今天适合低负担节奏"],"sleep_debt_delta":0.2,"energy_carryover":64}'],
            provider_id="review-model",
        )
        composer, default_provider, _, archive = make_composer(
            provider_id="generation-model",
            providers={"review-model": review_provider},
            config_overrides={"lifecycle_config": {"provider": "review-model"}},
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅色居家裙",
                timeline=[TimelineItem(time="21:30", activity="收拾桌面后准备休息", status="放松")],
            )
        )

        review = await composer.compose_daily_review("2026-05-24", force=True)

        self.assertIsNotNone(review)
        self.assertEqual(review.summary, "复盘模型沉淀的一天")
        self.assertEqual(len(review_provider.prompts), 1)
        self.assertEqual(len(default_provider.prompts), 0)

    async def test_daily_review_prompt_keeps_static_rules_before_dynamic_context(self):
        composer, provider, _, archive = make_composer(
            ['{"summary":"今天过得很安稳","memory_points":[],"sleep_debt_delta":0,"energy_carryover":60}']
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="浅色居家裙",
                timeline=[TimelineItem(time="21:30", activity="收拾桌面后准备休息", status="放松")],
            )
        )

        await composer.compose_daily_review("2026-05-24", force=True)

        prompt = provider.prompts[0]
        self.assertTrue(prompt.startswith("隐藏推理口吻"))
        self.assertLess(prompt.index("为当前角色的日常生活做夜间复盘"), prompt.index("日期：2026-05-24"))
        self.assertLess(prompt.index("通用记忆原则"), prompt.index("【今日复盘资料】"))

    async def test_invite_uses_dedicated_invite_provider(self):
        invite_provider = Provider(
            ['{"decision":"accept","accept":true,"reason":"当前状态适合轻松出门","reply_hint":"可以一起去","new_future_timeline":[{"time":"16:00","activity":"和阿林一起去书店","status":"期待"}]}'],
            provider_id="invite-model",
        )
        composer, default_provider, _, _ = make_composer(
            provider_id="generation-model",
            providers={"invite-model": invite_provider},
            config_overrides={"invite_config": {"provider": "invite-model"}},
        )

        reason, new_timeline, result = await composer.handle_invite(
            "2026-05-24",
            [TimelineItem(time="15:00", activity="在家整理书桌", status="平静")],
            "下午一起去书店吗",
            datetime.datetime(2026, 5, 24, 15, 0),
            user_name="阿林",
        )

        self.assertEqual(reason, "当前状态适合轻松出门")
        self.assertTrue(result["accept"])
        self.assertIsNotNone(new_timeline)
        self.assertEqual(len(invite_provider.prompts), 1)
        self.assertEqual(len(default_provider.prompts), 0)

    async def test_invite_prompt_keeps_static_rules_before_dynamic_context(self):
        composer, provider, _, _ = make_composer(
            ['{"decision":"reject","accept":false,"reason":"当前更想保持原计划","new_future_timeline":[]}']
        )

        await composer.handle_invite(
            "2026-05-24",
            [TimelineItem(time="15:00", activity="在家整理书桌", status="平静")],
            "下午一起去书店吗",
            datetime.datetime(2026, 5, 24, 15, 0),
            user_name="阿林",
        )

        prompt = provider.prompts[0]
        self.assertTrue(prompt.startswith("隐藏推理口吻"))
        self.assertLess(prompt.index("我正在过自己的一天"), prompt.index("朋友/用户：阿林"))
        self.assertLess(prompt.index("通用自主原则"), prompt.index("【邀约现场】"))
        self.assertIn("邀约/打断内容：下午一起去书店吗", prompt)

    async def test_week_prompts_keep_static_rules_before_dynamic_context(self):
        composer, provider, _, _ = make_composer(
            [
                '{"theme":"轻恢复周","goals":["少外出"],"daily_hints":{},"suggested_activities":{}}',
                '{"template_id":"light_recovery","name":"轻恢复周","emoji":"🌙","description":"减少外出，慢慢恢复",'
                '"weight":0.1,"cooldown_weeks":3,"goals":["少外出"],'
                '"daily_hints":{"monday":"慢慢来","tuesday":"继续恢复","wednesday":"补一点能量","thursday":"轻安排","friday":"早收尾","saturday":"低负担","sunday":"整理下周"},'
                '"suggested_activities":{"weekday":["早睡"],"weekend":["散步"]},"tags":["恢复"]}'
            ],
            persona_manager=PersonaManager(prompt="喜欢安静、低负担的生活节奏。"),
        )

        await composer.generate_week_plan(template_id="regular", goals="这周少外出")
        await composer.compose_week_template_from_text("轻恢复周：这周偏累，减少外出")

        week_prompt, template_prompt = provider.prompts[:2]
        self.assertTrue(week_prompt.startswith("隐藏推理口吻"))
        self.assertLess(week_prompt.index("生成当前角色的本周计划"), week_prompt.index("本周范围："))
        self.assertLess(week_prompt.index("返回JSON"), week_prompt.index("【本周计划资料】"))
        self.assertIn("目标：这周少外出", week_prompt)
        self.assertTrue(template_prompt.startswith("隐藏推理口吻"))
        self.assertLess(template_prompt.index("把用户描述整理成一个周计划模板"), template_prompt.index("用户描述："))
        self.assertLess(template_prompt.index("输出结构"), template_prompt.index("【周模板需求】"))

    async def test_week_prompt_uses_complete_persona_prompt(self):
        late_hint = "后半段关键设定：小岑是我的男死党，周末常约我看展。"
        long_persona = "。".join([f"普通人设片段{i}" for i in range(30)]) + "。" + late_hint
        self.assertGreater(long_persona.index(late_hint), 200)
        composer, provider, _, _ = make_composer(
            ['{"theme":"看展恢复周","goals":["低负担社交"],"daily_hints":{},"suggested_activities":{}}'],
            persona_manager=PersonaManager(prompt=long_persona),
        )

        await composer.generate_week_plan(template_id="regular", goals="这周想轻松一点")

        self.assertIn("人设：", provider.prompts[0])
        self.assertIn(late_hint, provider.prompts[0])

    async def test_material_workshop_uses_dedicated_material_provider(self):
        material_provider = Provider(
            ['{"text":"雨后窗边的安静手帐时间"}'],
            provider_id="material-model",
        )
        composer, default_provider, _, _ = make_composer(
            provider_id="generation-model",
            providers={"material-model": material_provider},
            config_overrides={"material_config": {"provider": "material-model"}},
        )

        item = await composer.compose_catalog_item_from_text("daily_themes", "雨后手帐")

        self.assertEqual(item.text, "雨后窗边的安静手帐时间")
        self.assertEqual(len(material_provider.prompts), 1)
        self.assertEqual(len(default_provider.prompts), 0)
        prompt = material_provider.prompts[0]
        self.assertTrue(prompt.startswith("隐藏推理口吻"))
        self.assertLess(prompt.index("把用户描述整理成一条日常生活素材"), prompt.index("【素材输入】"))
        self.assertLess(prompt.index("输出结构"), prompt.index("【素材输入】"))
        self.assertIn("用户描述：雨后手帐", prompt)

    async def test_material_workshop_can_use_web_inspiration_prompt(self):
        material_provider = Provider(
            ['{"text":"街头灵感里的轻快出游日"}'],
            provider_id="material-model",
        )
        composer, _, _, _ = make_composer(
            provider_id="generation-model",
            providers={"material-model": material_provider},
            config_overrides={
                "material_config": {"provider": "material-model"},
                "web_inspiration_config": {"enabled": True},
            },
        )

        async def fake_search(keyword, prompt_template, **kwargs):
            return "## 🌐 联网灵感参考\n- 摘要：街拍里常见明亮配色和轻便层次"

        composer.web_inspiration.search = fake_search
        item = await composer.compose_catalog_item_from_text("daily_themes", "少女出游", use_web=True)

        self.assertEqual(item.text, "街头灵感里的轻快出游日")
        prompt = material_provider.prompts[0]
        self.assertLess(prompt.index("JSON 输出要求"), prompt.index("【素材输入】"))
        self.assertIn("联网灵感参考", prompt)
        self.assertIn("明亮配色", prompt)

    async def test_hair_style_prompt_keeps_static_rules_before_dynamic_context(self):
        material_provider = Provider(
            ['{"name":"柔软居家风","hairstyles":["低马尾","半扎长发","松散披发","低丸子头"]}'],
            provider_id="material-model",
        )
        composer, default_provider, _, _ = make_composer(
            provider_id="generation-model",
            providers={"material-model": material_provider},
            config_overrides={"material_config": {"provider": "material-model"}},
        )

        style = await composer.compose_hair_style_from_text("柔软居家风：低马尾，半扎长发")

        self.assertEqual(style.name, "柔软居家风")
        self.assertEqual(style.hairstyles[:2], ["低马尾", "半扎长发"])
        self.assertEqual(len(default_provider.prompts), 0)
        prompt = material_provider.prompts[0]
        self.assertTrue(prompt.startswith("隐藏推理口吻"))
        self.assertLess(prompt.index("把用户描述整理成一个发型组"), prompt.index("【发型组输入】"))
        self.assertLess(prompt.index("输出结构"), prompt.index("【发型组输入】"))
        self.assertIn("用户描述：柔软居家风：低马尾，半扎长发", prompt)

    async def test_daily_generation_can_use_web_inspiration_as_soft_reference(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"going_out",'
                '"sleep":{"mode":"normal","quality":72,"summary":"睡得还行"},'
                '"outfit":{"decision":"outdoor","style":"清爽出游风","hair":"高马尾","reason":"今天要出门"},'
                '"day_plan":{"schedule_type":"拥抱阳光的元气出游","schedule_intent":"outing","energy_bias":"active","social_bias":"light"},'
                '"theme":"明亮出门日","mood":"汽水橙·活力"},'
                '"state":{"energy":70,"mood":"有点想出门","busyness":35,"social":40,'
                '"sleep":{"quality":72,"summary":"睡得还行"},"summary":"适合轻量外出"},'
                '"outfit":"白色T恤和浅蓝短裤，长发扎成高马尾",'
                '"timeline":[{"time":"09:20","activity":"在窗边确认今天想去的路线","status":"期待"},'
                '{"time":"21:10","activity":"回家后把帆布包挂回门口，慢慢洗漱","status":"满足"}],'
                '"timeline_audit":{"first_timeline_time":"09:20","last_timeline_time":"21:10","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖出门准备和夜间回家收束。"},'
                '"places":[],"new_events":[]}'
            ]
        )

        data = await composer.generate_daily(
            datetime.datetime(2026, 5, 24, 9, 0),
            force=True,
            extra="今天想出门",
            web_inspiration="## 🌐 联网灵感参考\n- 摘要：明亮短途出游和轻便穿搭",
        )

        self.assertIsNotNone(data)
        self.assertIn("联网灵感参考", provider.prompts[0])
        self.assertIn("不是必须执行的事项", provider.prompts[0])
        self.assertIn("明亮短途出游", provider.prompts[0])

    async def test_provider_fallback_is_single_call_and_does_not_affect_other_task_model(self):
        default_provider = Provider(
            ['{"outfit_decision":"keep","outfit":"宽松白色长T恤，低马尾","style":"居家风","hair":"低马尾","reason":"今天不急着出门"}'],
            provider_id="default",
        )
        outfit_provider = Provider([RuntimeError("401 Unauthorized")], provider_id="outfit-model")
        invite_provider = Provider(
            ['{"decision":"reject","accept":false,"reason":"当前更想保持原计划","new_future_timeline":[]}'],
            provider_id="invite-model",
        )
        composer, _, _, archive = make_composer(
            provider_id="generation-model",
            providers={
                "default": default_provider,
                "outfit-model": outfit_provider,
                "invite-model": invite_provider,
            },
            context_config={
                "provider_settings": {"default_provider_id": "default"},
                "provider": [
                    {"id": "default", "enable": True, "provider_type": "chat"},
                    {"id": "outfit-model", "enable": True, "provider_type": "chat"},
                    {"id": "invite-model", "enable": True, "provider_type": "chat"},
                ],
            },
            config_overrides={
                "outfit_config": {"provider": "outfit-model"},
                "invite_config": {"provider": "invite-model"},
            },
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                outfit="宽松白色长T恤，低马尾",
                timeline=[TimelineItem(time="14:00", activity="在家整理手帐", status="平静")],
            )
        )

        await composer.update_outfit("2026-05-24", "afternoon")
        await composer.handle_invite(
            "2026-05-24",
            [TimelineItem(time="15:00", activity="在家整理书桌", status="平静")],
            "下午一起去书店吗",
            datetime.datetime(2026, 5, 24, 15, 0),
            user_name="阿林",
        )

        self.assertEqual(len(outfit_provider.prompts), 1)
        self.assertEqual(len(default_provider.prompts), 1)
        self.assertEqual(len(invite_provider.prompts), 1)
        self.assertIs(await composer._get_provider("outfit-model"), outfit_provider)

    def test_daily_payload_validation_stays_structural(self):
        composer, *_ = make_composer()
        ok, reason = composer._validate_daily_payload(
            {
                "outfit": "奶油白居家裙",
                "timeline": [{"time": "09:00", "activity": "今天不出门，在窗边追番", "status": "安静"}],
            },
            "不要出门",
        )
        self.assertTrue(ok, reason)

        ok, reason = composer._validate_daily_payload(
            {
                "outfit": "",
                "timeline": [{"time": "09:00", "activity": "下午出门去便利店买布丁", "status": "轻松"}],
            },
            "不要出门",
        )
        self.assertFalse(ok)
        self.assertIn("outfit", reason)

    def test_daily_payload_validation_requires_structured_audit_for_late_timeline_start(self):
        composer, *_ = make_composer()
        ok, reason = composer._validate_daily_payload(
            {
                "life_decision": {
                    "sleep": {"mode": "normal", "quality": 70, "summary": "昨晚睡得还行"},
                },
                "state": {"sleep": {"summary": "睡眠正常"}, "summary": "普通居家日"},
                "outfit": "浅色居家裙",
                "timeline": [
                    {
                        "time": "14:37",
                        "activity": "拉开窗帘后发现雨还没停，摸到手机看了一眼时间",
                        "status": "缓慢进入状态",
                    }
                ],
            }
        )

        self.assertFalse(ok)
        self.assertIn("timeline_audit.coverage_mode/start_reason", reason)

        ok, reason = composer._validate_daily_payload(
            {
                "life_decision": {
                    "sleep": {"mode": "late_night", "quality": 35, "summary": "凌晨四点才睡，下午补觉醒来"},
                },
                "state": {"sleep": {"summary": "昨晚熬夜后补觉"}, "summary": "低负担恢复日"},
                "outfit": "宽松居家裙",
                "timeline": [
                    {
                        "time": "14:10",
                        "activity": "因为凌晨熬夜太久，补觉到下午才从被窝里慢慢醒来",
                        "status": "困倦",
                    }
                ],
                "timeline_audit": {
                    "first_timeline_time": "14:10",
                    "coverage_mode": "from_current_time",
                    "start_reason": "life_decision",
                    "covers_full_day": False,
                    "summary": "根据生活决策，时间轴从补觉后的当前状态开始记录。",
                },
            }
        )

        self.assertTrue(ok, reason)

    def test_daily_payload_validation_requires_generation_contract_for_expected_coverage(self):
        composer, *_ = make_composer()
        payload = {
            "outfit": "浅色居家裙",
            "timeline": [{"time": "09:00", "activity": "在窗边整理今天要做的事", "status": "清醒"}],
            "timeline_audit": {
                "first_timeline_time": "09:00",
                "last_timeline_time": "09:00",
                "coverage_mode": "full_day",
                "start_reason": "normal_day_start",
                "end_reason": "normal_day_end",
                "covers_full_day": True,
                "closed_loop": True,
                "summary": "时间轴声明为完整全天。",
            },
        }

        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")
        self.assertFalse(ok)
        self.assertIn("generation_contract", reason)

        payload["generation_contract"] = {
            "contract_version": "daily_life_v2",
            "expected_coverage": "target_period",
            "timeline_audit_coverage_mode": "target_period",
            "closed_loop_required": False,
        }
        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")
        self.assertFalse(ok)
        self.assertIn("expected_coverage", reason)

    def test_daily_payload_validation_rejects_timeline_audit_enum_aliases(self):
        composer, *_ = make_composer()
        payload = {
            "generation_contract": {
                "contract_version": "daily_life_v2",
                "expected_coverage": "full_day",
                "timeline_audit_coverage_mode": "full_day",
                "closed_loop_required": True,
            },
            "outfit": "浅色居家裙",
            "timeline": [
                {"time": "08:03", "activity": "自然醒后在床上缓了一会儿", "status": "刚醒"},
                {"time": "22:45", "activity": "关灯后慢慢睡着", "status": "入睡"},
            ],
            "timeline_audit": {
                "first_timeline_time": "08:03",
                "last_timeline_time": "22:45",
                "coverage_mode": "full_day",
                "start_reason": "day_start",
                "end_reason": "day_end",
                "covers_full_day": True,
                "closed_loop": True,
                "summary": "时间轴覆盖从早晨自然醒到夜间入睡的一整天。",
            },
        }

        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")

        self.assertFalse(ok)
        self.assertIn("timeline_audit.start_reason=day_start 无效", reason)

    def test_daily_payload_validation_requires_full_day_early_closure_audit(self):
        composer, *_ = make_composer()
        base_payload = {
            "generation_contract": {
                "contract_version": "daily_life_v2",
                "expected_coverage": "full_day",
                "timeline_audit_coverage_mode": "full_day",
                "closed_loop_required": True,
            },
            "outfit": "浅色居家裙",
            "timeline": [
                {"time": "05:47", "activity": "清晨醒来后把窗户推开一点", "status": "清醒"},
                {"time": "15:00", "activity": "下午整理完桌面后坐在窗边休息", "status": "平稳"},
            ],
            "timeline_audit": {
                "first_timeline_time": "05:47",
                "coverage_mode": "full_day",
                "start_reason": "normal_day_start",
                "end_reason": "normal_day_end",
                "covers_full_day": True,
                "closed_loop": True,
                "summary": "时间轴声明为完整全天。",
            },
        }

        ok, reason = composer._validate_daily_payload(base_payload, expected_coverage="full_day")
        self.assertFalse(ok)
        self.assertIn("last_timeline_time", reason)

        base_payload["timeline_audit"]["last_timeline_time"] = "15:00"
        ok, reason = composer._validate_daily_payload(base_payload, expected_coverage="full_day")
        self.assertFalse(ok)
        self.assertIn("较早收束", reason)

        base_payload["timeline_audit"]["end_reason"] = "low_activity"
        base_payload["timeline_audit"]["summary"] = "时间轴在下午提前收束，因为 life_decision 判断今天是低活动早睡恢复日。"
        ok, reason = composer._validate_daily_payload(base_payload, expected_coverage="full_day")
        self.assertTrue(ok, reason)

    async def test_daily_generation_repairs_late_timeline_start_without_structured_audit(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":70,"summary":"昨晚睡得还行"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"在家休息"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"雨天居家","mood":"安静"},'
                '"state":{"energy":65,"mood":"平静","busyness":20,"social":25,'
                '"sleep":{"quality":70,"summary":"昨晚睡得还行"},"summary":"普通居家日"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"14:37","activity":"拉开窗帘后发现雨还没停，摸到手机看了一眼时间","status":"缓慢进入状态"}],'
                '"places":[],"new_events":[]}',
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":70,"summary":"昨晚睡得还行"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"在家休息"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"雨天居家","mood":"安静"},'
                '"state":{"energy":65,"mood":"平静","busyness":20,"social":25,'
                '"sleep":{"quality":70,"summary":"昨晚睡得还行"},"summary":"普通居家日"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"08:46","activity":"听见雨声后慢慢醒来，先把窗帘拉开一点","status":"清醒中"},'
                '{"time":"14:37","activity":"窝在沙发上翻看昨天买的切片吐司","status":"放松"},'
                '{"time":"22:18","activity":"关掉客厅小灯，确认明早要用的杯子已经放回桌边","status":"安稳"}],'
                '"timeline_audit":{"first_timeline_time":"08:46","last_timeline_time":"22:18","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴从上午开始覆盖今天的主要生活节奏，并在夜间完成收束。"},'
                '"places":[],"new_events":[]}',
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 14, 30), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[0].time, "08:46")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("未通过校验", provider.prompts[1])
        self.assertIn("timeline_audit", provider.prompts[1])
        self.assertTrue(provider.prompts[1].startswith("隐藏推理口吻"))
        self.assertLess(provider.prompts[1].index("必须遵循"), provider.prompts[1].index("【日程修复资料】"))

    async def test_daily_generation_repairs_full_day_without_early_closure_audit(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":72,"summary":"昨晚睡眠正常"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"今天以室内恢复为主"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"清晨开始的居家日","mood":"平稳"},'
                '"state":{"energy":66,"mood":"平稳","busyness":25,"social":20,'
                '"sleep":{"quality":72,"summary":"昨晚睡眠正常"},"summary":"适合低强度安排"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"05:47","activity":"清晨醒来后把窗户推开一点","status":"清醒"},'
                '{"time":"15:00","activity":"下午整理完桌面后坐在窗边休息","status":"平稳"}],'
                '"timeline_audit":{"first_timeline_time":"05:47","last_timeline_time":"15:00","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴声明为完整全天。"},'
                '"places":[],"new_events":[]}',
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":72,"summary":"昨晚睡眠正常"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"今天以室内恢复为主"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"清晨开始的居家日","mood":"平稳"},'
                '"state":{"energy":66,"mood":"平稳","busyness":25,"social":20,'
                '"sleep":{"quality":72,"summary":"昨晚睡眠正常"},"summary":"适合低强度安排"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"05:47","activity":"清晨醒来后把窗户推开一点","status":"清醒"},'
                '{"time":"15:00","activity":"下午整理完桌面后坐在窗边休息","status":"平稳"},'
                '{"time":"21:45","activity":"洗漱后把明天要用的杯子放到桌边，慢慢关掉灯","status":"放松"}],'
                '"timeline_audit":{"first_timeline_time":"05:47","last_timeline_time":"21:45","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖清晨、下午和夜间睡前收束。"},'
                '"places":[],"new_events":[]}',
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 8, 0), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[-1].time, "21:45")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("较早收束", provider.prompts[1])
        self.assertIn("closed_loop", provider.prompts[1])

    async def test_daily_generation_rejects_audit_reason_alias_and_repairs(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":72,"summary":"睡眠正常"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"在家休息"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"雨天居家","mood":"奶油黄·慵懒"},'
                '"state":{"energy":66,"mood":"平稳","busyness":25,"social":20,'
                '"sleep":{"quality":72,"summary":"昨晚睡眠正常"},"summary":"适合低强度安排"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"05:47","activity":"傍晚醒来后看见窗外在下雨","status":"迷糊"},'
                '{"time":"23:35","activity":"关灯侧身睡下","status":"入睡"}],'
                '"timeline_audit":{"first_timeline_time":"17:47","last_timeline_time":"23:35","coverage_mode":"full_day",'
                '"start_reason":"previous_day_continuation","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴从傍晚补觉醒来延伸到夜间入睡。"},'
                '"places":[],"new_events":[]}',
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":72,"summary":"睡眠正常"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"在家休息"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"雨天居家","mood":"奶油黄·慵懒"},'
                '"state":{"energy":66,"mood":"平稳","busyness":25,"social":20,'
                '"sleep":{"quality":72,"summary":"昨晚睡眠正常"},"summary":"适合低强度安排"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"08:03","activity":"自然醒后在床上缓了一会儿","status":"刚醒"},'
                '{"time":"22:45","activity":"关灯后慢慢睡着","status":"入睡"}],'
                '"timeline_audit":{"first_timeline_time":"08:03","last_timeline_time":"22:45","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖从早晨自然醒到夜间入睡的一整天。"},'
                '"places":[],"new_events":[]}',
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 16, 0), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[0].time, "08:03")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("不要写 day_start", provider.prompts[1])

    async def test_daily_generation_repair_guides_late_full_day_start_reason(self):
        invalid_late = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
            '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":82,"summary":"下午自然醒"},'
            '"outfit":{"decision":"keep","style":"居家风","hair":"散发","reason":"宅家休息"},'
            '"day_plan":{"schedule_intent":"rest","energy_bias":"rest","social_bias":"light"},'
            '"theme":"宅家充电","mood":"薄荷绿·治愈"},'
            '"state":{"energy":72,"mood":"平静","busyness":15,"social":35,'
            '"sleep":{"quality":82,"summary":"下午自然醒"},"summary":"傍晚开始宅家"},'
            '"outfit":"奶白针织开衫和棉质长裤",'
            '"timeline":[{"time":"17:38","activity":"推开家门，把刚买的鸡蛋和菠菜放进厨房","status":"平静"},'
            '{"time":"22:50","activity":"钻进被窝听着雨声睡着","status":"安眠"}],'
            '"timeline_audit":{"first_timeline_time":"17:38","last_timeline_time":"22:50","coverage_mode":"full_day",'
            '"start_reason":"normal_day_start","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
            '"summary":"从傍晚进门开始，到睡前结束。"},'
            '"places":[{"name":"家","type":"anchor","hint":"主要活动场所"}],"new_events":[]}'
        )
        valid_late = invalid_late.replace(
            '"start_reason":"normal_day_start"',
            '"start_reason":"life_decision"',
        ).replace(
            '"summary":"从傍晚进门开始，到睡前结束。"',
            '"summary":"因为今天补觉到下午且当前时刻重生成，完整全天按生活决策从傍晚低活动展开，并在夜间入睡闭环。"',
        )
        composer, provider, _, _ = make_composer([invalid_late, invalid_late, valid_late])

        data = await composer.generate_daily(
            datetime.datetime(2026, 6, 23, 18, 7),
            force=True,
            web_inspiration="## 联网灵感参考\n- 摘要：雨后宅家晚餐与阅读",
        )

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[0].time, "17:38")
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("不能再写 start_reason=normal_day_start", provider.prompts[1])
        self.assertIn("补齐上午/中午", provider.prompts[1])
        self.assertIn("start_reason=life_decision/custom", provider.prompts[1])
        self.assertIn("不能写 start_reason=normal_day_start", provider.prompts[2])

    async def test_daily_generation_rejects_future_outfit_before_arrival(self):
        future_homewear = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
            '"life_decision":{"life_mode":"mixed","sleep":{"mode":"normal","quality":76,"summary":"睡眠正常"},'
            '"outfit":{"decision":"keep","style":"柔软治愈系","hair":"自然披发","reason":"误用未来居家服"},'
            '"day_plan":{"schedule_intent":"mixed","energy_bias":"normal","social_bias":"light"},'
            '"theme":"雨后慢逛","mood":"薄荷绿·治愈"},'
            '"state":{"energy":70,"mood":"轻松","busyness":20,"social":35,'
            '"sleep":{"quality":76,"summary":"昨晚睡眠正常"},"summary":"傍晚还在外面买菜"},'
            '"outfit":"宽松的米白色棉麻家居连衣裙，长发自然披在肩上，赤脚或者穿双棉袜",'
            '"timeline":[{"time":"17:04","activity":"从书店出来，顺路去小超市买盒鸡蛋和一小把菠菜","status":"轻松"},'
            '{"time":"18:20","activity":"回到家换下帆布鞋，换上宽松的米白色棉麻家居连衣裙","status":"安稳"},'
            '{"time":"22:40","activity":"关灯睡下","status":"入睡"}],'
            '"timeline_audit":{"first_timeline_time":"17:04","last_timeline_time":"22:40","coverage_mode":"full_day",'
            '"start_reason":"life_decision","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
            '"summary":"因为当前时刻重生成，完整全天按生活决策从傍晚低活动展开，并在夜间入睡闭环。"},'
            '"places":[],"new_events":[]}'
        )
        current_outfit = future_homewear.replace(
            '"outfit":"宽松的米白色棉麻家居连衣裙，长发自然披在肩上，赤脚或者穿双棉袜"',
            '"outfit":"米白针织开衫配浅色吊带裙，长发用发带松松束着，脚上穿帆布鞋"',
        )
        composer, provider, _, _ = make_composer([future_homewear, current_outfit])

        data = await composer.generate_daily(datetime.datetime(2026, 6, 23, 17, 45), force=True)

        self.assertIsNotNone(data)
        self.assertIn("帆布鞋", data.outfit)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("当前穿搭疑似提前使用了 18:20 尚未发生的换装内容", provider.prompts[1])
        self.assertIn("顶层 outfit 表示当前/目标时刻已经穿在身上的衣服", provider.prompts[1])

    def test_weather_life_indices_are_soft_prompt_references(self):
        composer, *_ = make_composer()
        weather_section, constraint_section = composer._build_weather_sections(
            {
                "raw": "北京 晴 24°C",
                "temp": 24,
                "temp_desc": "温暖",
                "condition": "晴",
                "outfit_hint": "穿衣建议：适合轻薄长袖",
                "activity_hint": "运动：适合户外散步",
                "is_hot": False,
                "is_cold": False,
                "is_rainy": False,
                "is_foggy": False,
            }
        )

        self.assertIn("穿衣参考", weather_section)
        self.assertIn("活动参考", weather_section)
        self.assertEqual("", constraint_section)

    def test_extreme_weather_becomes_safety_constraint(self):
        composer, *_ = make_composer()
        _, constraint_section = composer._build_weather_sections(
            {
                "raw": "北京 暴雨 18°C",
                "temp": 18,
                "temp_desc": "凉爽",
                "condition": "暴雨",
                "outfit_hint": "",
                "activity_hint": "",
                "is_hot": False,
                "is_cold": False,
                "is_rainy": True,
                "is_foggy": False,
            }
        )

        self.assertIn("天气安全约束", constraint_section)
        self.assertIn("必须遵守", constraint_section)
        self.assertIn("活动安全", constraint_section)

    async def test_manual_extra_is_prompt_guidance_without_rule_retry(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"白色T恤",'
                '"timeline":[{"time":"10:00","activity":"在家整理书桌","status":"平静"},'
                '{"time":"15:40","activity":"穿着粉色针织开衫去奶茶店喝下午茶","status":"轻松"},'
                '{"time":"21:10","activity":"回家后把发夹取下来放进小盒子里","status":"满足"}],'
                '"timeline_audit":{"first_timeline_time":"10:00","last_timeline_time":"21:10","coverage_mode":"full_day",'
                '"start_reason":"manual_instruction","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖用户指定的下午茶，并补齐夜间收尾。"}}',
            ]
        )

        data = await composer.generate_daily(
            datetime.datetime(2026, 5, 24, 10, 0),
            extra="穿粉色针织开衫，戴草莓发夹，去奶茶店喝下午茶",
        )

        self.assertIsNotNone(data)
        self.assertEqual(data.outfit, "白色T恤")
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("用户实时指令", provider.prompts[0])
        self.assertIn("草莓发夹", provider.prompts[0])

    async def test_world_context_guides_daily_prompt(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去常去咖啡店写手帐","status":"专注"},'
                '{"time":"21:08","activity":"回家后给新买的绿萝浇了少量水，整理今天的票据","status":"安定"}],'
                '"timeline_audit":{"first_timeline_time":"12:10","last_timeline_time":"21:08","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖午间外出和夜间回家收束。"},'
                '"places":[{"name":"常去咖啡店","type":"cafe","hint":"写手帐"}],'
                '"new_events":[{"summary":"在常去咖啡店完成手帐","place":"常去咖啡店","people":[],"importance":"normal"}]}'
            ]
        )
        await archive.touch_relationship("u1", "阿林", "上次聊到想去书店", "2026-05-23", source="chat")
        await archive.touch_places(
            "2026-05-23",
            [PlaceRecord(name="常去咖啡店", type="cafe", hint="适合写手帐")],
            source="daily",
        )
        await archive.add_events(
            "2026-05-23",
            [EventRecord(date="2026-05-23", summary="和阿林约了周末看展", people=["阿林"], place="展览馆")],
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        prompt = provider.prompts[0]
        self.assertIn("关系档案", prompt)
        self.assertIn("阿林", prompt)
        self.assertIn("已沉淀地点", prompt)
        self.assertIn("常去咖啡店", prompt)
        self.assertIn("近期事件记忆", prompt)
        self.assertIn("和阿林约了周末看展", prompt)
        self.assertIn("今日地点候选", prompt)

    async def test_chat_memory_guides_daily_prompt(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去展览馆看展","status":"期待"}],'
                '"places":[{"name":"展览馆","type":"gallery","hint":"看展"}],'
                '"new_events":[]}'
            ]
        )
        await archive.touch_relationship("u1", "阿林", "聊到展览馆", "2026-05-23", source="chat")
        await archive.add_relationship_point("u1", "阿林是男性死党，最近想一起看展。", "2026-05-23")
        await archive.save_chat_summary(
            ChatSummaryRecord(
                date="2026-05-23",
                brief="和阿林聊到周末看展",
                long_summary="阿林想周末去展览馆，顺路逛书店。",
                people=["阿林"],
            )
        )

        await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        prompt = provider.prompts[0]
        self.assertIn("会话摘要", prompt)
        self.assertIn("和阿林聊到周末看展", prompt)
        self.assertIn("记忆点：阿林是男性死党", prompt)

    async def test_daily_generation_consumes_pending_memo_after_scheduling(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"state":{"energy":60,"mood":"期待","busyness":20,"social":70,'
                '"sleep":{"quality":70,"summary":"睡得还行"},"summary":"适合履行约定"},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"13:20","activity":"和阿林去街角踩水，顺便吃路边摊","status":"期待"},'
                '{"time":"21:00","activity":"回家整理今天的小票","status":"满足"}],'
                '"timeline_audit":{"first_timeline_time":"13:20","last_timeline_time":"21:00","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴安排了备忘录里的约定并在夜间收束。"},'
                '"places":[{"name":"街角","type":"street","hint":"踩水"}],'
                '"new_events":[]}'
            ]
        )
        await archive.save_day(
            DayRecord(
                date="2026-05-24",
                memo="- 与【阿林】的约定：明天去街角踩水，顺便吃路边摊",
            )
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0), force=True)

        self.assertIsNotNone(data)
        self.assertIn("强制备忘录/用户指令", provider.prompts[0])
        self.assertIn("明天去街角踩水", provider.prompts[0])
        self.assertEqual(data.memo, "")
        stored = await archive.get_day("2026-05-24")
        self.assertEqual(stored.memo, "")

    async def test_daily_generation_persists_places_and_events(self):
        composer, _, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去常去咖啡店写手帐","status":"专注"},'
                '{"time":"21:08","activity":"回家后给新买的绿萝浇了少量水，整理今天的票据","status":"安定"}],'
                '"timeline_audit":{"first_timeline_time":"12:10","last_timeline_time":"21:08","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"normal_day_end","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖午间外出和夜间回家收束。"},'
                '"places":[{"name":"常去咖啡店","type":"cafe","hint":"写手帐"}],'
                '"new_events":['
                '{"summary":"在常去咖啡店完成手帐","place":"常去咖啡店","people":[],"importance":"normal"},'
                '{"summary":"买了一盆绿萝","place":"","people":[],"importance":"normal"}'
                ']}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIsNotNone(data)
        places = await archive.get_recent_places(10)
        events = await archive.get_recent_events(10)
        self.assertTrue(any(place.name == "常去咖啡店" for place in places))
        self.assertTrue(any(event.summary == "在常去咖啡店完成手帐" for event in events))
        self.assertTrue(any(event.summary == "买了一盆绿萝" for event in events))

    async def test_daily_generation_persists_state_and_log(self):
        composer, _, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"state":{"energy":35,"mood":"有点累但心情还稳","busyness":70,'
                '"social":25,"sleep":{"quality":42,"summary":"昨晚睡得浅"},'
                '"summary":"今天偏累，不太想出门"},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"在家整理手帐","status":"慢慢来"},'
                '{"time":"21:30","activity":"把手帐合上放回书架，洗漱后准备早点休息","status":"疲惫但稳定"}],'
                '"timeline_audit":{"first_timeline_time":"12:10","last_timeline_time":"21:30","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖白天整理和夜间休息收束。"}}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIsNotNone(data)
        self.assertEqual(data.state.energy, 35)
        self.assertEqual(data.state.sleep.quality, 42)
        self.assertEqual(data.state.source, "daily")
        self.assertTrue(data.state_log)
        self.assertIn("今天偏累", data.state_log[0])

    async def test_daily_generation_defaults_state_when_missing(self):
        composer, _, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"timeline_audit_coverage_mode":"full_day","closed_loop_required":true},'
                '"outfit":"白色T恤",'
                '"timeline":[{"time":"10:00","activity":"在窗边看书","status":"平静"},'
                '{"time":"21:05","activity":"把书签夹回书页，关掉床头灯前喝了几口水","status":"放松"}],'
                '"timeline_audit":{"first_timeline_time":"10:00","last_timeline_time":"21:05","coverage_mode":"full_day",'
                '"start_reason":"normal_day_start","end_reason":"sleep","covers_full_day":true,"closed_loop":true,'
                '"summary":"时间轴覆盖白天阅读和夜间睡前收束。"}}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIsNotNone(data)
        self.assertEqual(data.state.energy, 60)
        self.assertEqual(data.state.sleep.quality, 65)
        self.assertTrue(data.state_log)
