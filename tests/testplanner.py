import asyncio
import datetime
import unittest

from support import ActionBot, AltProvider, ContactNameResolver, PersonaManager, PlatformManager, Provider, make_composer
from core.life import LifeBackgroundComposer
from core.models import (
    ChatSummaryRecord,
    CommitmentRecord,
    DayRecord,
    EventRecord,
    FocusSlotRecord,
    LifeState,
    MemoryCorrectionRecord,
    PlaceRecord,
    TimelineItem,
)


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

    def test_daily_decision_outcome_keeps_result_concise(self):
        self.assertEqual(
            LifeBackgroundComposer._daily_decision_outcome(
                {"decision_summary": {"novelty": "傍晚换新路线短走一圈，顺路去便利店买冰绿茶。"}}
            ),
            "傍晚换新路线短走一圈，顺路去便利店买冰绿茶。",
        )
        self.assertEqual(LifeBackgroundComposer._daily_decision_outcome({"decision_summary": {}}), "")

    async def test_due_commitments_are_injected_and_scheduled(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"state":{"energy":60,"mood":"期待","busyness":30,"social":80,'
                '"sleep":{"quality":70,"summary":"睡得还行"},"summary":"适合履行约定"},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"09:42","activity":"在窗边确认今天的约定，把票务信息和路线重新看了一遍","status":"期待"},'
                '{"time":"15:10","activity":"和阿林去电影院看电影","status":"期待"},'
                '{"time":"21:36","activity":"回到家后把票根夹进手帐，洗漱前又回味了一会儿剧情","status":"满足"}],''"places":[{"name":"电影院","type":"cinema","hint":"看电影"}],'
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
                '"closed_loop_required":true},'
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
                '"closed_loop_required":true},'
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
        self.assertIn("人物称呼、性别、亲疏和关系以明确资料为准", provider.prompts[0])
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
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"outfit":"默认模型生成的浅蓝外出裙",'
                '"timeline":[{"time":"10:00","activity":"整理好草莓发夹和包里的小物","status":"开心"},'
                '{"time":"15:30","activity":"去奶茶店喝下午茶","status":"开心"},'
                '{"time":"21:20","activity":"回家后把开衫挂好，带着甜味慢慢放松下来","status":"满足"}],'
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
        self.assertIn("current_role=当前角色", selected_provider.prompts[0])
        self.assertIn("speaker=消息发送者", selected_provider.prompts[0])
        self.assertIn("perspective=当前角色第一人称", selected_provider.prompts[0])
        self.assertIn("message_owner=speaker", selected_provider.prompts[0])
        self.assertIn("内容范围=我此刻看到、想到、犹豫、决定和感受到的内容", selected_provider.prompts[0])
        self.assertNotIn("隐藏推理也必须站在“我”的角色视角判断", selected_provider.prompts[0])
        self.assertNotIn("服务端隐藏推理的第一句必须以“我”开头", selected_provider.prompts[0])
        self.assertNotIn("不得把内心独白", selected_provider.prompts[0])
        self.assertNotIn("不要使用复数主语", selected_provider.prompts[0])
        self.assertIn("current_role=当前角色", selected_provider.system_prompts[0])
        self.assertIn("speaker=消息发送者", selected_provider.system_prompts[0])
        self.assertIn("perspective=当前角色第一人称", selected_provider.system_prompts[0])
        self.assertNotIn("隐藏推理第一句必须从“我”开始", selected_provider.system_prompts[0])
        self.assertNotIn("禁止把隐藏推理", selected_provider.system_prompts[0])
        self.assertNotIn("主语只用“我”", selected_provider.system_prompts[0])
        self.assertNotIn("我们分析当前情况", selected_provider.prompts[0])
        self.assertNotIn("我们分析当前情况", selected_provider.system_prompts[0])

    async def test_update_outfit_missing_day_generates_target_date_without_deadlock(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"target_period",'
                '"closed_loop_required":false},'
                '"life_decision":{"life_mode":"late_night",'
                '"sleep":{"mode":"late_night","quality":45,"summary":"凌晨还没睡踏实"},'
                '"outfit":{"decision":"keep","scene_category":"home","style_pool":"sleep_styles",'
                '"style":"居家睡前","hair":"自然散着","reason":"当前仍在家里低强度活动"},'
                '"day_plan":{"schedule_type":"宅家充电的慵懒一日","schedule_intent":"rest",'
                '"energy_bias":"rest","social_bias":"avoid"},'
                '"theme":"慢慢放空","mood":"雾蓝·松弛"},'
                '"outfit":"宽松浅蓝棉质睡裙，头发自然散着",'
                '"timeline":[{"time":"02:30","activity":"窝在床边收拾手机消息，准备慢慢睡下","status":"松弛"}],'
                '"places":[],"new_events":[]}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 2, 30), force=True, target_hour=2)

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
        self.assertNotIn("timeline_audit", prompt)
        self.assertIn("近期生活惯性", prompt)
        self.assertIn("schedule_type", prompt)
        self.assertIn("【通用自主原则】", prompt)
        self.assertIn("【通用状态行为原则】", prompt)
        self.assertIn("JSON 输出要求", prompt)
        self.assertIn("颜色名·情绪词", prompt)
        self.assertIn("日程类型标签", prompt)
        self.assertIn("不要写穿搭风格", prompt)
        self.assertIn("不要因为当前时间线索偏早就强制起床", prompt)
        self.assertIn("节点数量由 life_decision 与当天复杂度决定", prompt)
        self.assertIn("必须先判断穿搭是否适合外出场景和天气", prompt)
        self.assertIn("decision=keep 只表示当前穿搭本身已经适合接下来的活动", prompt)
        self.assertIn("life_decision.outfit.scene_category", prompt)
        self.assertIn("life_decision.outfit.style_pool", prompt)
        self.assertIn("home | sleep | outdoor | public | mixed", prompt)
        self.assertIn("sleep_styles | outfit_styles | mixed", prompt)
        self.assertIn("保持视觉一致性", prompt)
        self.assertIn("色彩细节放在顶层 outfit", prompt)
        self.assertIn("穿搭、发型和整体风格偏好只是软参考", prompt)
        self.assertIn("用户当前明确要求 > 短期生活纠偏 > 已学习长期偏好 > 配置审美", prompt)
        self.assertNotIn("默认角色审美（来自配置，可清空；只作为软参考）", prompt)
        self.assertNotIn("穿搭审美偏甜美、奶系、少女感", prompt)
        self.assertIn("不要机械复用同一套衣服、发型、配饰或色彩", prompt)
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

    async def test_full_day_generation_before_schedule_time_uses_stable_day_anchor(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting",'
                '"sleep":{"mode":"late_night","quality":42,"summary":"凌晨才睡，今天慢慢恢复"},'
                '"outfit":{"decision":"keep","scene_category":"home","style_pool":"home_styles",'
                '"style":"居家风","hair":"低马尾","reason":"今天先在家恢复"},'
                '"day_plan":{"schedule_type":"低强度恢复日","schedule_intent":"rest","energy_bias":"rest","social_bias":"light"},'
                '"theme":"慢慢恢复","mood":"薄荷绿·安稳"},'
                '"state":{"energy":42,"mood":"安稳","busyness":15,"social":20,'
                '"sleep":{"quality":42,"summary":"凌晨才睡"},"summary":"适合低强度恢复"},'
                '"outfit":"浅色居家T恤和棉质短裤，低马尾",'
                '"timeline":[{"time":"09:10","activity":"慢慢醒来后先喝水，看一眼窗外天气","status":"刚醒"},'
                '{"time":"15:20","activity":"在桌边整理相册，把昨天存下来的照片分到文件夹","status":"专注"},'
                '{"time":"22:10","activity":"洗漱后关掉主灯，只留床头小灯准备睡觉","status":"放松"}],'
                '"places":[],"new_events":[]}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 7, 5, 2, 32), force=True)

        self.assertIsNotNone(data)
        self.assertIn("当前/目标实际时间：2026-07-05 07:00", provider.prompts[0])
        self.assertNotIn("当前/目标实际时间：2026-07-05 02:32", provider.prompts[0])
        self.assertEqual(data.timeline[0].time, "09:10")

    async def test_full_day_generation_repairs_current_dawn_fragment_by_rewriting_timeline(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"late_night",'
                '"sleep":{"mode":"late_night","quality":40,"summary":"凌晨两点半还没睡意"},'
                '"outfit":{"decision":"keep","scene_category":"sleep","style_pool":"sleep_styles",'
                '"style":"宽松居家","hair":"自然散着","reason":"今晚已经穿好过夜衣服"},'
                '"day_plan":{"schedule_type":"收假前夜·懒散过渡","schedule_intent":"rest","energy_bias":"rest","social_bias":"avoid"},'
                '"theme":"深夜的安静回神","mood":"墨蓝·松弛"},'
                '"state":{"energy":30,"mood":"安静松散","busyness":10,"social":15,'
                '"sleep":{"quality":40,"summary":"还没睡"},"summary":"凌晨趴床上等困意"},'
                '"outfit":"白色宽松棉质圆领T恤，灰色运动短裤，头发随意散着",'
                '"timeline":[{"time":"02:32","activity":"趴着看天花板，空调嗡嗡响","status":"松散"},'
                '{"time":"03:05","activity":"翻身侧躺，闭上眼睛试睡","status":"安静"},'
                '{"time":"04:10","activity":"半梦半醒翻了几次身后睡着","status":"迷糊"}],''"places":[],"new_events":[]}',
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting",'
                '"sleep":{"mode":"late_night","quality":40,"summary":"凌晨才睡，上午自然补觉"},'
                '"outfit":{"decision":"keep","scene_category":"home","style_pool":"home_styles",'
                '"style":"宽松居家","hair":"低马尾","reason":"今天以宅家恢复为主"},'
                '"day_plan":{"schedule_type":"晚起恢复日","schedule_intent":"rest","energy_bias":"rest","social_bias":"avoid"},'
                '"theme":"慢慢回血的周日","mood":"薄荷绿·松弛"},'
                '"state":{"energy":45,"mood":"松弛","busyness":10,"social":15,'
                '"sleep":{"quality":40,"summary":"凌晨才睡"},"summary":"晚起后低强度恢复"},'
                '"outfit":"宽松白色T恤和灰色运动短裤，头发扎成低马尾",'
                '"timeline":[{"time":"10:40","activity":"自然醒后先赖在床上缓一会儿，伸手摸到水杯喝水","status":"刚醒"},'
                '{"time":"15:30","activity":"在客厅整理相册，把最近存下来的生活照分好类","status":"安静"},'
                '{"time":"21:40","activity":"洗漱后换到床边，把空调调到舒适温度准备睡前放空","status":"放松"}],'
                '"places":[],"new_events":[]}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 7, 5, 2, 32), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[0].time, "10:40")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("重写 timeline", provider.prompts[1])
        self.assertIn("系统会根据 timeline 自动检查覆盖范围", provider.prompts[1])

    async def test_daily_generation_uses_short_term_context_and_records_decision(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting",'
                '"sleep":{"mode":"normal","quality":68,"depth":"awake","summary":"睡眠尚可"},'
                '"outfit":{"decision":"keep","scene_category":"home","style_pool":"outfit_styles",'
                '"style":"清爽居家风","hair":"低马尾","reason":"短期目标需要低负担恢复"},'
                '"day_plan":{"schedule_type":"低负担恢复日","schedule_intent":"rest","energy_bias":"rest","social_bias":"avoid"},'
                '"theme":"恢复节奏","mood":"薄荷绿·安稳"},'
                '"state":{"energy":52,"mood":"平稳","mood_score":64,"busyness":25,"social":20,"stress":18,'
                '"focus":45,"sleepiness":36,"outgoing":20,"emotional_stability":72,"interaction_capacity":38,'
                '"boredom":20,"fishing":10,"attention_openness":32,"watch_state":"peek","interrupt_level":"medium",'
                '"interrupt_reason":"恢复日只留意高相关消息",'
                '"sleep":{"quality":68,"depth":"awake","summary":"睡眠尚可"},"summary":"今天适合低负担恢复"},'
                '"outfit":"浅绿色宽松针织衫配白色棉裙，头发低低扎起",'
                '"timeline":[{"time":"09:30","activity":"醒来后先喝一杯温水，把窗边的小桌整理出来","status":"慢慢恢复"},'
                '{"time":"22:10","activity":"收好小桌上的本子，提前关灯准备睡觉","status":"安稳"}],''"decision_summary":{"decision":"低负担恢复日","reason":"短期目标要求早睡恢复，且修正提醒不要再安排长时间外出",'
                '"continuity":"延续近期低体力惯性","novelty":"改为室内整理和早睡",'
                '"memory_used":["早睡恢复","不要安排长时间外出"],"avoid_repeat":["连续外出"]},'
                '"places":[],"new_events":[]}'
            ]
        )
        await archive.upsert_focus_slot(
            FocusSlotRecord(
                scope="",
                focus_key="early_sleep",
                label="早睡恢复",
                priority=90,
                reason="这两天睡眠债偏高",
            )
        )
        await archive.save_memory_correction(
            MemoryCorrectionRecord(
                target_type="life_episode",
                target_id="last_outing",
                correction="不要把昨天的长时间外出当成今天也必须继续。",
                evidence="用户刚纠正过日程重复",
            )
        )
        await archive.save_day(
            DayRecord(
                date="2026-06-11",
                outfit="深蓝外出裙和小皮鞋",
                timeline=[
                    TimelineItem(time="10:00", activity="出门去展览馆看展", status="活跃"),
                    TimelineItem(time="22:30", activity="回家后累到不想说话", status="疲惫"),
                ],
                meta={
                    "theme": "长时间外出",
                    "schedule_type": "展馆出游日",
                    "mood": "天蓝·兴奋",
                    "outfit_style_pool": "outfit_styles",
                },
            )
        )

        data = await composer.generate_daily(datetime.datetime(2026, 6, 12, 9, 0), force=True)

        self.assertIsNotNone(data)
        prompt = provider.prompts[0]
        self.assertIn("短期目标与注意槽", prompt)
        self.assertIn("早睡恢复", prompt)
        self.assertIn("待应用修正", prompt)
        self.assertIn("不要把昨天的长时间外出", prompt)
        self.assertIn("重复抑制参考", prompt)
        decisions = await archive.get_life_decisions(limit=5)
        self.assertEqual(decisions[0].kind, "daily_plan")
        self.assertIn("低负担恢复日", decisions[0].decision)
        self.assertIn("短期目标", decisions[0].reason)
        self.assertIn("早睡恢复", decisions[0].evidence)
        self.assertIn("不要安排长时间外出", decisions[0].evidence)
        self.assertIn("连续外出", decisions[0].evidence)
        self.assertNotIn("['", decisions[0].evidence)
        self.assertEqual(decisions[0].outcome, "改为室内整理和早睡")
        self.assertNotIn("主题：", decisions[0].outcome)
        self.assertNotIn("穿搭：", decisions[0].outcome)
        self.assertLessEqual(len(decisions[0].outcome), 180)
        decision_evidence = await archive.get_memory_evidence(target_type="life_decision", limit=5)
        self.assertEqual(decision_evidence[0].evidence_type, "decision")
        self.assertIn("原因：", decision_evidence[0].summary)
        self.assertIn("依据：", decision_evidence[0].summary)
        self.assertIn("结果：", decision_evidence[0].summary)
        self.assertIn("短期目标", decision_evidence[0].summary)
        self.assertIn("连续外出", decision_evidence[0].summary)
        self.assertNotIn("；连续外出", decision_evidence[0].summary)
        focus_slots = await archive.get_focus_slots(limit=5, active_only=False)
        absorbed = [item for item in focus_slots if item.focus_key == "early_sleep"][0]
        self.assertLess(absorbed.priority, 90)
        self.assertEqual(absorbed.expires_at, "2026-06-12")
        self.assertIn("已参与 2026-06-12 的daily_plan决策", absorbed.reason)
        focus_evidence = await archive.get_memory_evidence(target_type="focus", limit=5)
        self.assertEqual(focus_evidence[0].evidence_type, "decision")
        self.assertIn("早睡恢复", focus_evidence[0].summary)

    async def test_daily_generation_uses_only_active_life_corrections(self):
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting",'
                '"sleep":{"mode":"normal","quality":70,"depth":"awake","summary":"恢复中"},'
                '"outfit":{"decision":"keep","scene_category":"home","style_pool":"outfit_styles",'
                '"style":"柔软居家风","hair":"自然披发","reason":"短期目标提醒多休息"},'
                '"day_plan":{"schedule_type":"安静恢复日","schedule_intent":"rest","energy_bias":"rest","social_bias":"avoid"},'
                '"theme":"多休息","mood":"薄荷绿·安稳"},'
                '"state":{"energy":58,"mood":"安稳","mood_score":68,"busyness":20,"social":18,"stress":15,'
                '"focus":42,"sleepiness":35,"outgoing":16,"emotional_stability":75,"interaction_capacity":30,'
                '"boredom":15,"fishing":8,"attention_openness":24,"watch_state":"peek","interrupt_level":"medium",'
                '"interrupt_reason":"恢复日少接普通闲聊",'
                '"sleep":{"quality":70,"depth":"awake","summary":"恢复中"},"summary":"今天以休息为主"},'
                '"outfit":"柔软米白开衫和浅灰棉裙，头发自然披在肩侧",'
                '"timeline":[{"time":"09:20","activity":"醒来后先坐在床边慢慢缓一会儿","status":"安静"},'
                '{"time":"22:00","activity":"提前放下手机，关灯休息","status":"放松"}],''"decision_summary":{"decision":"安静恢复日","reason":"短期目标提醒多休息，且未应用纠偏要求别总出门",'
                '"continuity":"延续恢复惯性","novelty":"减少外出，改为早睡",'
                '"memory_used":["这几天让她多休息","最近别总写出门"],"avoid_repeat":["频繁外出"]},'
                '"places":[],"new_events":[]}'
            ]
        )
        await archive.upsert_focus_slot(
            FocusSlotRecord(
                scope="",
                focus_key="more_rest",
                label="这几天让她多休息",
                priority=90,
                reason="短期恢复目标",
                expires_at="2099-01-01",
            )
        )
        await archive.upsert_focus_slot(
            FocusSlotRecord(
                scope="",
                focus_key="old_outing_limit",
                label="已经过期的少外出",
                priority=95,
                reason="旧目标",
                expires_at="2026-01-01",
            )
        )
        await archive.save_memory_correction(
            MemoryCorrectionRecord(
                target_type="life_episode",
                target_id="pending",
                correction="最近别总写出门，优先恢复。",
                evidence="用户纠偏",
            )
        )
        applied = await archive.save_memory_correction(
            MemoryCorrectionRecord(
                target_type="life_episode",
                target_id="applied",
                correction="已吸收的旧纠偏不应继续注入。",
                evidence="已经处理",
            )
        )
        await archive.mark_memory_correction_applied(applied.id, True)

        await composer.generate_daily(datetime.datetime(2026, 6, 12, 9, 0), force=True)

        prompt = provider.prompts[0]
        self.assertIn("这几天让她多休息", prompt)
        self.assertIn("最近别总写出门", prompt)
        self.assertNotIn("已经过期的少外出", prompt)
        self.assertNotIn("已吸收的旧纠偏", prompt)

    async def test_daily_generation_repairs_when_too_close_to_recent_day(self):
        duplicate_payload = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"closed_loop_required":true},'
            '"life_decision":{"life_mode":"resting",'
            '"sleep":{"mode":"normal","quality":68,"depth":"awake","summary":"睡眠尚可"},'
            '"outfit":{"decision":"keep","scene_category":"home","style_pool":"outfit_styles",'
            '"style":"清爽居家风","hair":"低马尾","reason":"延续恢复节奏"},'
            '"day_plan":{"schedule_type":"低负担恢复日","schedule_intent":"rest","energy_bias":"rest","social_bias":"avoid"},'
            '"theme":"恢复节奏","mood":"薄荷绿·安稳"},'
            '"state":{"energy":52,"mood":"平稳","mood_score":64,"busyness":25,"social":20,"stress":18,'
            '"focus":45,"sleepiness":36,"outgoing":20,"emotional_stability":72,"interaction_capacity":38,'
            '"boredom":20,"fishing":10,"attention_openness":32,"watch_state":"peek","interrupt_level":"medium",'
            '"interrupt_reason":"恢复日只留意高相关消息",'
            '"sleep":{"quality":68,"depth":"awake","summary":"睡眠尚可"},"summary":"今天适合低负担恢复"},'
            '"outfit":"浅绿色宽松针织衫配白色棉裙，头发低低扎起",'
            '"timeline":[{"time":"09:30","activity":"醒来后先喝一杯温水，把窗边的小桌整理出来","status":"慢慢恢复"},'
            '{"time":"22:10","activity":"收好小桌上的本子，提前关灯准备睡觉","status":"安稳"}],''"decision_summary":{"decision":"低负担恢复日","reason":"延续恢复节奏",'
            '"continuity":"延续近期低体力惯性","novelty":"","memory_used":["近期恢复"],"avoid_repeat":[]},'
            '"places":[],"new_events":[]}'
        )
        repaired_payload = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"closed_loop_required":true},'
            '"life_decision":{"life_mode":"resting",'
            '"sleep":{"mode":"normal","quality":70,"depth":"awake","summary":"睡眠恢复中"},'
            '"outfit":{"decision":"partial_change","scene_category":"home","style_pool":"outfit_styles",'
            '"style":"柔软居家层次","hair":"松散编发","reason":"保持休息但换成更轻的居家层次"},'
            '"day_plan":{"schedule_type":"窗边阅读恢复日","schedule_intent":"rest","energy_bias":"rest","social_bias":"light"},'
            '"theme":"安静阅读日","mood":"雾蓝·平和"},'
            '"state":{"energy":58,"mood":"平和","mood_score":70,"busyness":22,"social":28,"stress":15,'
            '"focus":56,"sleepiness":28,"outgoing":24,"emotional_stability":76,"interaction_capacity":44,'
            '"boredom":18,"fishing":8,"attention_openness":38,"watch_state":"peek","interrupt_level":"medium",'
            '"interrupt_reason":"轻恢复日保留少量交流余地",'
            '"sleep":{"quality":70,"depth":"awake","summary":"睡眠恢复中"},"summary":"今天用阅读替代重复整理"},'
            '"outfit":"雾蓝薄开衫搭米白棉质长裙，头发松松编到一侧",'
            '"timeline":[{"time":"09:10","activity":"打开窗透气，把昨晚夹好的书签翻到新一页","status":"清醒"},'
            '{"time":"15:20","activity":"泡一杯热茶，坐在窗边读完一章小说并记下喜欢的句子","status":"专注"},'
            '{"time":"21:40","activity":"把书签夹回书里，收起茶杯后准备提前休息","status":"平和"}],''"decision_summary":{"decision":"窗边阅读恢复日","reason":"保留恢复惯性，但避开昨天整理小桌的重复动作",'
            '"continuity":"延续低负担恢复","novelty":"把整理改为窗边阅读和摘句",'
            '"memory_used":["近期恢复"],"avoid_repeat":["重复整理小桌"]},'
            '"places":[],"new_events":[]}'
        )
        composer, provider, _, archive = make_composer([duplicate_payload, repaired_payload])
        await archive.save_day(
            DayRecord(
                date="2026-06-11",
                outfit="浅绿色宽松针织衫配白色棉裙，头发低低扎起",
                timeline=[
                    TimelineItem(time="09:30", activity="醒来后先喝一杯温水，把窗边的小桌整理出来", status="慢慢恢复"),
                    TimelineItem(time="22:10", activity="收好小桌上的本子，提前关灯准备睡觉", status="安稳"),
                ],
                meta={
                    "theme": "恢复节奏",
                    "schedule_type": "低负担恢复日",
                    "schedule_intent": "rest",
                    "outfit_style_pool": "outfit_styles",
                },
            )
        )

        day = await composer.generate_daily(datetime.datetime(2026, 6, 12, 9, 0), force=True)

        self.assertIsNotNone(day)
        self.assertEqual(day.meta["schedule_type"], "窗边阅读恢复日")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("过于相似", provider.prompts[1])
        saved = await archive.get_day("2026-06-12")
        self.assertEqual(saved.outfit, "雾蓝薄开衫搭米白棉质长裙，头发松松编到一侧")

    async def test_daily_generation_prompt_keeps_static_rules_before_dynamic_context(self):
        valid_json = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"closed_loop_required":true},'
            '"outfit":"宽松白色长T恤，低马尾，浅灰棉质长裤",'
            '"timeline":[{"time":"09:00","activity":"整理今日计划","status":"平稳"},'
            '{"time":"21:00","activity":"洗漱后慢慢收尾","status":"放松"}],'
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
        self.assertLess(provider.prompts[0].index("## 👤 角色设定"), provider.prompts[0].index("目标日期：2026-06-23"))
        self.assertIn("未能解析出 JSON 对象", provider.prompts[1])
        self.assertIn("只输出完整 JSON 对象", provider.prompts[1])
        self.assertIn("原始输出", provider.prompts[1])

    async def test_daily_generation_repairs_outfit_style_contaminated_by_theme_or_mood(self):
        contaminated = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"closed_loop_required":true},'
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
            '{"time":"21:20","activity":"回家洗漱后准备休息","status":"放松"}],''"places":[],"new_events":[]}'
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
        self.assertIn("只输出完整 JSON 对象", provider.prompts[1])
        self.assertIn("原始输出", provider.prompts[1])

    async def test_daily_generation_keeps_mood_color_separate_from_state_mood(self):
        composer, _, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"awake",'
                '"sleep":{"mode":"normal","quality":72,"summary":"昨晚睡得还可以"},'
                '"outfit":{"decision":"keep","style":"轻便居家风","hair":"低马尾","reason":"今天安排轻松"},'
                '"day_plan":{"schedule_type":"偷偷变优秀的学习时间","schedule_intent":"study","energy_bias":"normal","social_bias":"light"},'
                '"theme":"轻量学习日","mood":"元气满满，准备好好学习"},'
                '"state":{"energy":70,"mood":"元气满满，准备好好学习","busyness":40,"social":35,'
                '"sleep":{"quality":72,"summary":"昨晚睡得还可以"},"summary":"今天适合慢慢进入学习状态"},'
                '"outfit":"白色针织衫和浅灰长裙，长发扎成低马尾",'
                '"timeline":[{"time":"09:30","activity":"整理桌面后翻开笔记","status":"平稳"},'
                '{"time":"21:20","activity":"收好资料后准备休息","status":"放松"}],''"places":[],"new_events":[]}'
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
                meta={
                    "life_mode": "resting",
                    "mood": "薄荷绿·安稳",
                    "plan_outfit_decision": "outdoor",
                    "outfit_decision": "outdoor",
                },
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
        decisions = await archive.get_life_decisions(limit=5, kind="outfit")
        self.assertEqual(decisions[0].outcome, "风格：延续居家风；发型：低松马尾；场景：居家；风格池：居家/睡眠风格")
        self.assertNotIn("sleep_styles", decisions[0].outcome)
        self.assertIn("自主判断穿搭是否需要变化", provider.prompts[0])
        self.assertIn("只围绕当前实际时间、当前日程位置、实时生活状态和下一项安排判断", provider.prompts[0])
        self.assertIn("scene_category 只写当前真实场景", provider.prompts[0])
        self.assertIn("style_pool 由系统根据它自动派生", provider.prompts[0])
        self.assertIn("不要因为时段标签变化而强制换装", provider.prompts[0])
        self.assertIn("长期审美偏好", provider.prompts[0])
        self.assertNotIn("默认角色审美（来自配置，可清空；只作为软参考）", provider.prompts[0])
        self.assertNotIn("穿搭审美偏甜美、奶系、少女感", provider.prompts[0])
        self.assertIn('"scene_category": "home | sleep | outdoor | public | mixed"', provider.prompts[0])
        self.assertNotIn('"style_pool": "sleep_styles | outfit_styles | mixed"', provider.prompts[0])
        self.assertNotIn("必须和 style_pool 一致", provider.prompts[0])

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
        self.assertLess(prompt.index("当前已有生活时间轴"), prompt.index("当前时间范围：14:00-16:00"))
        self.assertLess(prompt.index("返回JSON格式"), prompt.index("【穿搭现场】"))
        self.assertLess(prompt.index("生活日程日期：2026-05-24"), prompt.index("当前实际时间："))
        self.assertLess(prompt.index("当前穿搭：奶茶色居家裙"), prompt.index("当前实际时间："))
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
        self.assertIn("未发生的未来安排只能作为预告，不能提前覆盖当前穿搭", prompt)
        self.assertIn("scene_category 只写当前真实场景", prompt)
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
        self.assertIn("生活日程日期：2026-06-23", prompt)
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
                '{"theme":"轻恢复周","goals":["少外出"],"daily_hints":{},"suggested_activities":{}}'
            ],
            persona_manager=PersonaManager(prompt="喜欢安静、低负担的生活节奏。"),
        )

        await composer.generate_week_plan(goals="这周少外出")

        week_prompt = provider.prompts[0]
        self.assertTrue(week_prompt.startswith("隐藏推理口吻"))
        self.assertIn("生成当前角色的本周自主生活周计划", week_prompt)
        self.assertIn("周计划是给每日生活生成使用的软参考", week_prompt)
        self.assertIn("不要套用固定周模板", week_prompt)
        self.assertLess(week_prompt.index("生成当前角色的本周自主生活周计划"), week_prompt.index("【本周计划资料】"))
        self.assertLess(week_prompt.index("返回 JSON"), week_prompt.index("【本周计划资料】"))
        self.assertIn('"YYYY-MM-DD": "当天提示"', week_prompt)
        self.assertLess(week_prompt.index("人设："), week_prompt.index("本周范围："))
        self.assertIn("用户目标", week_prompt)
        self.assertIn("这周少外出", week_prompt)
        self.assertEqual(len(provider.prompts), 1)

    async def test_week_prompt_uses_complete_persona_prompt(self):
        late_hint = "后半段关键设定：小岑是我的男死党，周末常约我看展。"
        long_persona = "。".join([f"普通人设片段{i}" for i in range(30)]) + "。" + late_hint
        self.assertGreater(long_persona.index(late_hint), 200)
        composer, provider, _, _ = make_composer(
            ['{"theme":"看展恢复周","goals":["低负担社交"],"daily_hints":{},"suggested_activities":{}}'],
            persona_manager=PersonaManager(prompt=long_persona),
        )

        await composer.generate_week_plan(goals="这周想轻松一点")

        self.assertIn("人设：", provider.prompts[0])
        self.assertIn(late_hint, provider.prompts[0])

    async def test_daily_generation_auto_maintains_week_plan_when_missing(self):
        composer, provider, _, archive = make_composer(
            [
                '{"theme":"低负担恢复周","goals":["把休息放在前面"],'
                '"daily_hints":{"2026-05-24":"保留轻松节奏"},'
                '"suggested_activities":{"weekday":["短散步"],"weekend":["整理房间"]}}',
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"mixed",'
                '"sleep":{"mode":"normal","quality":72,"depth":"awake","summary":"睡眠正常"},'
                '"outfit":{"decision":"keep","scene_category":"home","style_pool":"sleep_styles",'
                '"style":"浅色居家休闲","hair":"自然披发","reason":"今天以低负担居家为主"},'
                '"day_plan":{"schedule_type":"低负担恢复日","schedule_intent":"mixed",'
                '"energy_bias":"normal","social_bias":"light"},'
                '"theme":"轻恢复","mood":"薄荷绿·放松"},'
                '"state":{"energy":70,"mood":"放松","busyness":25,"social":35,'
                '"sleep":{"quality":72,"summary":"睡眠正常"},"summary":"适合轻量安排"},'
                '"outfit":"浅色棉质居家裙，长发自然披着",'
                '"timeline":[{"time":"09:10","activity":"在窗边慢慢醒神，整理今天想做的小事","status":"放松"},'
                '{"time":"21:20","activity":"洗漱后把手机放远一点，准备早点休息","status":"安稳"}],''"places":[],"new_events":[]}'
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

    def test_daily_payload_validation_without_generation_contract_stays_structural_for_late_start(self):
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

        self.assertTrue(ok, reason)

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
            }
        )

        self.assertTrue(ok, reason)

    def test_daily_payload_validation_requires_generation_contract_for_expected_coverage(self):
        composer, *_ = make_composer()
        payload = {
            "outfit": "浅色居家裙",
            "timeline": [{"time": "09:00", "activity": "在窗边整理今天要做的事", "status": "清醒"}],
        }

        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")
        self.assertFalse(ok)
        self.assertIn("generation_contract", reason)

        payload["generation_contract"] = {
            "contract_version": "daily_life_v2",
            "expected_coverage": "target_period",
            "closed_loop_required": False,
        }
        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")
        self.assertFalse(ok)
        self.assertIn("expected_coverage", reason)

    def test_daily_payload_validation_derives_timeline_audit_from_timeline(self):
        composer, *_ = make_composer()
        payload = {
            "generation_contract": {
                "contract_version": "daily_life_v2",
                "expected_coverage": "full_day",
                "closed_loop_required": True,
            },
            "outfit": "浅色居家裙",
            "timeline": [
                {"time": "08:03", "activity": "自然醒后在床上缓了一会儿", "status": "刚醒"},
                {"time": "22:45", "activity": "关灯后慢慢睡着", "status": "入睡"},
            ],
        }

        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")

        self.assertTrue(ok, reason)
        self.assertEqual(payload["timeline_audit"]["first_timeline_time"], "08:03")
        self.assertEqual(payload["timeline_audit"]["last_timeline_time"], "22:45")
        self.assertEqual(payload["timeline_audit"]["start_reason"], "normal_day_start")
        self.assertEqual(payload["timeline_audit"]["end_reason"], "sleep")

    def test_daily_payload_validation_requires_full_day_evening_closure(self):
        composer, *_ = make_composer()
        base_payload = {
            "generation_contract": {
                "contract_version": "daily_life_v2",
                "expected_coverage": "full_day",
                "closed_loop_required": True,
            },
            "outfit": "浅色居家裙",
            "timeline": [
                {"time": "05:47", "activity": "清晨醒来后把窗户推开一点", "status": "清醒"},
                {"time": "15:00", "activity": "下午整理完桌面后坐在窗边休息", "status": "平稳"},
            ],
        }

        ok, reason = composer._validate_daily_payload(base_payload, expected_coverage="full_day")
        self.assertFalse(ok)
        self.assertIn("最后一条时间过早", reason)

        base_payload["timeline"] = [
            {"time": "17:38", "activity": "傍晚醒来后走到厨房倒水", "status": "刚醒"},
            {"time": "22:50", "activity": "关灯睡下", "status": "入睡"},
        ]
        ok, reason = composer._validate_daily_payload(base_payload, expected_coverage="full_day")
        self.assertFalse(ok)
        self.assertIn("第一条时间过晚", reason)

    def test_daily_payload_validation_allows_night_life_cross_midnight_closure(self):
        composer, *_ = make_composer()
        payload = {
            "generation_contract": {
                "contract_version": "daily_life_v2",
                "expected_coverage": "full_day",
                "closed_loop_required": True,
            },
            "life_decision": {
                "life_mode": "late_night",
                "sleep": {"mode": "late_night", "quality": 58, "summary": "夜生活节奏，白天晚些醒"},
            },
            "outfit": "黑色短袖和宽松长裤",
            "timeline": [
                {"time": "14:30", "activity": "午后慢慢醒来，拉开窗帘喝水", "status": "刚醒"},
                {"time": "20:10", "activity": "晚上出门去常去的小店坐了一会儿", "status": "清醒"},
                {"time": "02:30", "activity": "回到家洗漱后关灯睡下", "status": "困了"},
            ],
        }

        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")

        self.assertTrue(ok, reason)
        self.assertEqual(payload["timeline_audit"]["first_timeline_time"], "14:30")
        self.assertEqual(payload["timeline_audit"]["last_timeline_time"], "02:30")
        self.assertTrue(payload["timeline_audit"]["covers_full_day"])
        self.assertEqual(payload["timeline_audit"]["start_reason"], "life_decision")
        self.assertEqual(payload["timeline_audit"]["end_reason"], "sleep")

    def test_daily_payload_validation_keeps_normal_day_start_guard(self):
        composer, *_ = make_composer()
        payload = {
            "generation_contract": {
                "contract_version": "daily_life_v2",
                "expected_coverage": "full_day",
                "closed_loop_required": True,
            },
            "life_decision": {
                "life_mode": "resting",
                "sleep": {"mode": "normal", "quality": 70, "summary": "普通作息"},
            },
            "outfit": "浅色居家裙",
            "timeline": [
                {"time": "14:30", "activity": "下午才开始整理桌面", "status": "平静"},
                {"time": "20:10", "activity": "晚上简单吃饭后看书", "status": "放松"},
                {"time": "02:30", "activity": "又看了一会儿手机后睡下", "status": "困了"},
            ],
        }

        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")

        self.assertFalse(ok)
        self.assertIn("第一条时间过晚", reason)

    def test_daily_payload_validation_allows_late_sleep_recovery_day(self):
        composer, *_ = make_composer()
        payload = {
            "generation_contract": {
                "contract_version": "daily_life_v2",
                "expected_coverage": "full_day",
                "closed_loop_required": True,
            },
            "life_decision": {
                "life_mode": "resting",
                "sleep": {"mode": "late_night", "quality": 42, "summary": "凌晨才睡，白天补觉"},
            },
            "outfit": "宽松居家T恤和棉质长裤",
            "timeline": [
                {"time": "14:30", "activity": "补觉醒来后在床边慢慢喝水", "status": "刚醒"},
                {"time": "18:10", "activity": "傍晚在家附近短走一圈透气", "status": "放松"},
                {"time": "21:40", "activity": "回家洗漱后把灯调暗，准备早点睡", "status": "安稳"},
            ],
        }

        ok, reason = composer._validate_daily_payload(payload, expected_coverage="full_day")

        self.assertTrue(ok, reason)
        self.assertTrue(payload["timeline_audit"]["covers_full_day"])

    async def test_daily_generation_repairs_late_timeline_start_without_structured_audit(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
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
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":70,"summary":"昨晚睡得还行"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"在家休息"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"雨天居家","mood":"安静"},'
                '"state":{"energy":65,"mood":"平静","busyness":20,"social":25,'
                '"sleep":{"quality":70,"summary":"昨晚睡得还行"},"summary":"普通居家日"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"08:46","activity":"听见雨声后慢慢醒来，先把窗帘拉开一点","status":"清醒中"},'
                '{"time":"14:37","activity":"窝在沙发上翻看昨天买的切片吐司","status":"放松"},'
                '{"time":"22:18","activity":"关掉客厅小灯，确认明早要用的杯子已经放回桌边","status":"安稳"}],''"places":[],"new_events":[]}',
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 14, 30), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[0].time, "08:46")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("未通过校验", provider.prompts[1])
        self.assertIn("只输出完整 JSON 对象", provider.prompts[1])
        self.assertIn("原始输出", provider.prompts[1])

    async def test_daily_generation_repairs_full_day_without_early_closure_audit(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":72,"summary":"昨晚睡眠正常"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"今天以室内恢复为主"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"清晨开始的居家日","mood":"平稳"},'
                '"state":{"energy":66,"mood":"平稳","busyness":25,"social":20,'
                '"sleep":{"quality":72,"summary":"昨晚睡眠正常"},"summary":"适合低强度安排"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"05:47","activity":"清晨醒来后把窗户推开一点","status":"清醒"},'
                '{"time":"15:00","activity":"下午整理完桌面后坐在窗边休息","status":"平稳"}],''"places":[],"new_events":[]}',
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":72,"summary":"昨晚睡眠正常"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"今天以室内恢复为主"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"清晨开始的居家日","mood":"平稳"},'
                '"state":{"energy":66,"mood":"平稳","busyness":25,"social":20,'
                '"sleep":{"quality":72,"summary":"昨晚睡眠正常"},"summary":"适合低强度安排"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"05:47","activity":"清晨醒来后把窗户推开一点","status":"清醒"},'
                '{"time":"15:00","activity":"下午整理完桌面后坐在窗边休息","status":"平稳"},'
                '{"time":"21:45","activity":"洗漱后把明天要用的杯子放到桌边，慢慢关掉灯","status":"放松"}],''"places":[],"new_events":[]}',
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 8, 0), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[-1].time, "21:45")
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("最后一条时间过早", provider.prompts[1])
        self.assertIn("closed_loop", provider.prompts[1])

    async def test_daily_generation_uses_timeline_content_without_model_audit(self):
        composer, provider, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":72,"summary":"睡眠正常"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"在家休息"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"雨天居家","mood":"奶油黄·慵懒"},'
                '"state":{"energy":66,"mood":"平稳","busyness":25,"social":20,'
                '"sleep":{"quality":72,"summary":"昨晚睡眠正常"},"summary":"适合低强度安排"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"05:47","activity":"傍晚醒来后看见窗外在下雨","status":"迷糊"},'
                '{"time":"23:35","activity":"关灯侧身睡下","status":"入睡"}],''"places":[],"new_events":[]}',
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":72,"summary":"睡眠正常"},'
                '"outfit":{"decision":"keep","style":"居家风","hair":"低马尾","reason":"在家休息"},'
                '"day_plan":{"schedule_intent":"home","energy_bias":"normal","social_bias":"light"},'
                '"theme":"雨天居家","mood":"奶油黄·慵懒"},'
                '"state":{"energy":66,"mood":"平稳","busyness":25,"social":20,'
                '"sleep":{"quality":72,"summary":"昨晚睡眠正常"},"summary":"适合低强度安排"},'
                '"outfit":"浅色居家裙，低马尾",'
                '"timeline":[{"time":"08:03","activity":"自然醒后在床上缓了一会儿","status":"刚醒"},'
                '{"time":"22:45","activity":"关灯后慢慢睡着","status":"入睡"}],''"places":[],"new_events":[]}',
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 16, 0), force=True)

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[0].time, "05:47")
        self.assertEqual(len(provider.prompts), 1)

    async def test_daily_generation_repairs_late_full_day_start_to_full_coverage(self):
        invalid_late = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"closed_loop_required":true},'
            '"life_decision":{"life_mode":"resting","sleep":{"mode":"normal","quality":82,"summary":"下午自然醒"},'
            '"outfit":{"decision":"keep","style":"居家风","hair":"散发","reason":"宅家休息"},'
            '"day_plan":{"schedule_intent":"rest","energy_bias":"rest","social_bias":"light"},'
            '"theme":"宅家充电","mood":"薄荷绿·治愈"},'
            '"state":{"energy":72,"mood":"平静","busyness":15,"social":35,'
            '"sleep":{"quality":82,"summary":"下午自然醒"},"summary":"傍晚开始宅家"},'
            '"outfit":"奶白针织开衫和棉质长裤",'
            '"timeline":[{"time":"17:38","activity":"推开家门，把刚买的鸡蛋和菠菜放进厨房","status":"平静"},'
            '{"time":"22:50","activity":"钻进被窝听着雨声睡着","status":"安眠"}],''"places":[{"name":"家","type":"anchor","hint":"主要活动场所"}],"new_events":[]}'
        )
        invalid_life_decision_start = invalid_late.replace(
            '"start_reason":"normal_day_start"',
            '"start_reason":"life_decision"',
        )
        valid_day = (
            invalid_late
            .replace(
                '"timeline":[{"time":"17:38","activity":"推开家门，把刚买的鸡蛋和菠菜放进厨房","status":"平静"},'
                '{"time":"22:50","activity":"钻进被窝听着雨声睡着","status":"安眠"}]',
                '"timeline":[{"time":"10:20","activity":"自然醒后在床上缓了一会儿，摸到手机看天气","status":"刚醒"},'
                '{"time":"17:38","activity":"推开家门，把刚买的鸡蛋和菠菜放进厨房","status":"平静"},'
                '{"time":"22:50","activity":"钻进被窝听着雨声睡着","status":"安眠"}]',
            )
            .replace('"first_timeline_time":"17:38"', '"first_timeline_time":"10:20"')
            .replace(
                '"summary":"从傍晚进门开始，到睡前结束。"',
                '"summary":"时间轴从上午醒来到夜间睡前收束，覆盖今天完整生活节奏。"',
            )
        )
        composer, provider, _, _ = make_composer([invalid_late, invalid_life_decision_start, valid_day])

        data = await composer.generate_daily(
            datetime.datetime(2026, 6, 23, 18, 7),
            force=True,
            web_inspiration="## 联网灵感参考\n- 摘要：雨后宅家晚餐与阅读",
        )

        self.assertIsNotNone(data)
        self.assertEqual(data.timeline[0].time, "10:20")
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("只输出完整 JSON 对象", provider.prompts[1])
        self.assertIn("原始输出", provider.prompts[1])
        self.assertIn("只输出完整 JSON 对象", provider.prompts[2])
        self.assertIn("原始输出", provider.prompts[2])

    async def test_daily_generation_rejects_future_outfit_before_arrival(self):
        future_homewear = (
            '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
            '"closed_loop_required":true},'
            '"life_decision":{"life_mode":"mixed","sleep":{"mode":"normal","quality":76,"summary":"睡眠正常"},'
            '"outfit":{"decision":"keep","style":"柔软治愈系","hair":"自然披发","reason":"误用未来居家服"},'
            '"day_plan":{"schedule_intent":"mixed","energy_bias":"normal","social_bias":"light"},'
            '"theme":"雨后慢逛","mood":"薄荷绿·治愈"},'
            '"state":{"energy":70,"mood":"轻松","busyness":20,"social":35,'
            '"sleep":{"quality":76,"summary":"昨晚睡眠正常"},"summary":"傍晚还在外面买菜"},'
            '"outfit":"宽松的米白色棉麻家居连衣裙，长发自然披在肩上，赤脚或者穿双棉袜",'
            '"timeline":[{"time":"10:30","activity":"上午慢慢醒来，确认下午想去书店和超市转一圈","status":"清醒"},'
            '{"time":"17:04","activity":"从书店出来，顺路去小超市买盒鸡蛋和一小把菠菜","status":"轻松"},'
            '{"time":"18:20","activity":"回到家换下帆布鞋，换上宽松的米白色棉麻家居连衣裙","status":"安稳"},'
            '{"time":"22:40","activity":"关灯睡下","status":"入睡"}],''"places":[],"new_events":[]}'
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
        self.assertIn("只输出完整 JSON 对象", provider.prompts[1])
        self.assertIn("原始输出", provider.prompts[1])

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
        composer, provider, _, archive = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"outfit":"白色T恤",'
                '"timeline":[{"time":"10:00","activity":"在家整理书桌","status":"平静"},'
                '{"time":"15:40","activity":"穿着粉色针织开衫去奶茶店喝下午茶","status":"轻松"},'
                '{"time":"21:10","activity":"回家后把发夹取下来放进小盒子里","status":"满足"}],''"places":[{"name":"常去咖啡店","type":"cafe","hint":"写手帐"},'
                '{"name":"未去书店","type":"bookstore","hint":"候选地点"}],'
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
                '"closed_loop_required":true},'
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
                '"closed_loop_required":true},'
                '"state":{"energy":60,"mood":"期待","busyness":20,"social":70,'
                '"sleep":{"quality":70,"summary":"睡得还行"},"summary":"适合履行约定"},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"13:20","activity":"和阿林去街角踩水，顺便吃路边摊","status":"期待"},'
                '{"time":"21:00","activity":"回家整理今天的小票","status":"满足"}],''"places":[{"name":"街角","type":"street","hint":"踩水"}],'
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
                '"closed_loop_required":true},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"去常去咖啡店写手帐","status":"专注"},'
                '{"time":"21:08","activity":"回家后给新买的绿萝浇了少量水，整理今天的票据","status":"安定"}],''"places":[{"name":"常去咖啡店","type":"cafe","hint":"写手帐"}],'
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
        self.assertFalse(any(place.name == "未去书店" for place in places))
        self.assertTrue(any(event.summary == "在常去咖啡店完成手帐" for event in events))
        self.assertTrue(any(event.summary == "买了一盆绿萝" for event in events))

    async def test_daily_generation_persists_state_and_log(self):
        composer, _, _, _ = make_composer(
            [
                '{"generation_contract":{"contract_version":"daily_life_v2","expected_coverage":"full_day",'
                '"closed_loop_required":true},'
                '"state":{"energy":35,"mood":"有点累但心情还稳","busyness":70,'
                '"social":25,"sleep":{"quality":42,"summary":"昨晚睡得浅"},'
                '"summary":"今天偏累，不太想出门"},'
                '"outfit":"浅蓝外出裙",'
                '"timeline":[{"time":"12:10","activity":"在家整理手帐","status":"慢慢来"},'
                '{"time":"21:30","activity":"把手帐合上放回书架，洗漱后准备早点休息","status":"疲惫但稳定"}],'
                '"places":[],"new_events":[]}'
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
                '"closed_loop_required":true},'
                '"outfit":"白色T恤",'
                '"timeline":[{"time":"10:00","activity":"在窗边看书","status":"平静"},'
                '{"time":"21:05","activity":"把书签夹回书页，关掉床头灯前喝了几口水","status":"放松"}],'
                '"places":[],"new_events":[]}'
            ]
        )

        data = await composer.generate_daily(datetime.datetime(2026, 5, 24, 10, 0))

        self.assertIsNotNone(data)
        self.assertEqual(data.state.energy, 60)
        self.assertEqual(data.state.sleep.quality, 65)
        self.assertTrue(data.state_log)
