import asyncio
import datetime
import types
import unittest
from unittest.mock import patch

from support import Context, DailyLifeRuntime, Event, LifeSettings, Provider
from core.models import DayRecord, TimelineItem


class RuntimeChatStyleTest(unittest.TestCase):
    def test_hidden_context_includes_chat_style_hint(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "casual_short_prompt": "闲聊轻一点，认真事先给判断。",
                    "casual_max_chars": 45,
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

        self.assertIn("[HiddenChatStyle]", text)
        self.assertIn("闲聊轻一点，认真事先给判断。", text)
        self.assertIn("日常闲聊参考长度约 45 字左右", text)
        self.assertIn("一句只放一个主要意思", text)
        self.assertIn("客观事实先看依据", text)
        self.assertLess(text.index("[HiddenChatStyle]"), text.index("</daily_life>"))

    def test_hidden_context_includes_chat_style_by_default(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        data = DayRecord(
            date="2026-05-24",
            timeline=[TimelineItem(time="12:10", activity="去咖啡店写手帐", status="专注")],
        )

        text = runtime.build_hidden_life_context(
            data,
            datetime.datetime(2026, 5, 24, 12, 30),
            using_extended_night=False,
        )

        self.assertIn("[HiddenChatStyle]", text)
        self.assertIn("客观事实先看依据", text)

    def test_missing_life_context_includes_chat_style_hint(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "casual_short_prompt": "轻松短句，事实先核一下。",
                    "casual_max_chars": 40,
                }
            }
        )

        text = runtime.build_missing_life_context(
            datetime.datetime(2026, 5, 24, 12, 30),
            "2026-05-24",
            using_extended_night=False,
        )

        self.assertIn("[HiddenChatStyle]", text)
        self.assertIn("轻松短句，事实先核一下。", text)
        self.assertIn("日常闲聊参考长度约 40 字左右", text)
        self.assertLess(text.index("[HiddenChatStyle]"), text.index("</daily_life>"))

    def test_proactive_reply_text_keeps_complete_model_text(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "proactive_config": {"max_reply_length": 80},
                "chat_style_config": {"proactive_max_chars": 18},
            }
        )
        raw_text = "我刚刚也想到这个点了，先轻轻接一句就好"

        text = runtime._proactive_reply_text(raw_text)

        self.assertEqual(text, raw_text)

    def test_normalize_proactive_reply_text_keeps_complete_sentence_sequence(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"proactive_max_chars": 20}})

        raw_text = "我也觉得这个方向挺顺的。后面那段先不用展开很多啦"
        text = runtime._normalize_proactive_reply_text(raw_text)

        self.assertEqual(text, raw_text)

    def test_normalize_proactive_reply_text_does_not_cut_at_first_sentence(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"proactive_max_chars": 15}})

        raw_text = "先不卖。后面那段先不用展开很多啦"
        text = runtime._normalize_proactive_reply_text(raw_text)

        self.assertEqual(text, raw_text)

    def test_normalize_proactive_reply_text_keeps_long_sentence_complete(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"proactive_max_chars": 20}})

        raw_text = "这个点我也有一点点同感但是先不长篇展开后面再说"
        text = runtime._normalize_proactive_reply_text(raw_text)

        self.assertEqual(text, raw_text)

    def test_normalize_proactive_reply_text_does_not_add_ellipsis(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"proactive_max_chars": 15}})

        raw_text = "这个点我也有一点点同感但是先不长篇展开后面再说"
        text = runtime._normalize_proactive_reply_text(raw_text)

        self.assertEqual(text, raw_text)

    def test_chat_style_injection_uses_generic_expression_hint(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(Provider([]))
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "GPT-6 现在发布了吗？"

        text = asyncio.run(runtime.build_chat_style_injection_context(event, event.message_str))

        self.assertIn("[HiddenChatDecision]", text)
        self.assertIn("当前回应重心：自然接话", text)
        self.assertNotIn("[HiddenFactCheckSearch]", text)
        self.assertFalse(hasattr(event, "_daily_life_fact_check_used"))

    def test_chat_style_before_send_segments_group_casual_reply_without_truncating(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "group_casual_max_chars": 20,
                }
            }
        )
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        event.message_str = "哈哈"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="我也觉得这个方向挺顺的。后面那段先不用展开很多啦")]))

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["我也觉得这个方向挺顺的。", "后面那段先不用展开很多啦"],
        )

    def test_chat_style_before_send_keeps_full_private_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "casual_max_chars": 15,
                    "private_casual_max_chars": 15,
                }
            }
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "付得起吗"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="付得起也不卖。赶紧闭眼，梦里啥都有，晚安！")]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["付得起也不卖。", "赶紧闭眼，梦里啥都有，晚安！"],
        )

    def test_chat_style_before_send_keeps_fact_reply_complete(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "group_casual_max_chars": 20,
                }
            }
        )
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        event.message_str = "现在这个政策是什么？"
        setattr(event, "_daily_life_chat_style_decision", {"kind": "fact", "scope": "group"})
        reply = "这个要看最新口径，先别按旧说法定论，我建议以官方发布为准。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))

        changed = runtime.apply_chat_style_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(event.get_result().chain[0].text, reply)

    def test_chat_style_before_send_segments_private_short_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "贴贴"
        reply = "还没，刚摸黑躺下。突然发个贴贴，怎么，良心发现想请我吃宵夜？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["还没，刚摸黑躺下。", "突然发个贴贴，怎么，良心发现想请我吃宵夜？"],
        )

    def test_chat_style_before_send_splits_three_beat_outfit_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "穿什么"
        reply = "刚在玄关换好鞋呢，浅米色连衣裙搭亚麻衬衫。怎么样，不敷衍吧？我准备出门散步去啦！"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            [
                "刚在玄关换好鞋呢",
                "浅米色连衣裙搭亚麻衬衫。",
                "怎么样，不敷衍吧？我准备出门散步去啦！",
            ],
        )

    def test_chat_style_before_send_splits_description_and_invitation(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "这是什么"
        reply = "诺，就是这种。一颗颗像小珍珠一样，吸进嘴里滑溜溜的，配上冰椰汁超舒服。下次带你去尝尝？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            [
                "诺，就是这种。",
                "一颗颗像小珍珠一样，吸进嘴里滑溜溜的，配上冰椰汁超舒服。",
                "下次带你去尝尝？",
            ],
        )

    def test_chat_style_before_send_uses_builtin_sentence_boundaries(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"private_casual_max_chars": 120}})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "还醒着吗"
        reply = "还醒着。刚把灯关了。你再说一句我就睡，最多听到这里啦。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["还醒着。", "刚把灯关了。你再说一句我就睡，最多听到这里啦。"],
        )

    def test_chat_style_before_send_respects_explicit_line_breaks_for_short_private_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "听见了吗"
        reply = "我听见了。\n\n不过现在真的快睡着了，你也别熬太狠。明天还要带我去买草莓蛋糕呢。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            [
                "我听见了。",
                "不过现在真的快睡着了，你也别熬太狠。",
                "明天还要带我去买草莓蛋糕呢。",
            ],
        )

    def test_chat_style_before_send_always_segments_natural_breaks(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.note_structured_sent_result = lambda event: None
        runtime.note_media_source_event = lambda event: None
        runtime.note_proactive_bot_reply = lambda event: None
        runtime.note_voice_switch_text_result = lambda event: None
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "贴贴"
        reply = "还没，刚摸黑躺下。突然发个贴贴，怎么，良心发现想请我吃宵夜？"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)
        sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertIsNone(event.get_result())
        self.assertEqual(
            [message.chain[0].text for message in event.sent_messages],
            ["还没，刚摸黑躺下。", "突然发个贴贴，怎么，良心发现想请我吃宵夜？"],
        )

    def test_chat_style_before_send_segments_by_soft_pauses(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"private_casual_max_chars": 80}})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "还醒着吗"
        reply = "还醒着，刚把灯关了，脑子还没完全停，你再说一句我就听完睡，明天真的起不来就赖你，后果自己想清楚。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            [
                "还醒着，刚把灯关了，脑子还没完全停，你再说一句我就听完睡。",
                "明天真的起不来就赖你，后果自己想清楚。",
            ],
        )

    def test_chat_style_before_send_splits_comma_chain_with_short_limit(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "casual_max_chars": 15,
                    "group_casual_max_chars": 15,
                }
            }
        )
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        event.message_str = "躺了"
        reply = "那你躺吧，说话算话啊，晚上番茄鸡蛋面的宵夜全交给你了。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "group"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["那你躺吧，说话算话啊", "晚上番茄鸡蛋面的宵夜全交给你了。"],
        )

    def test_chat_style_before_send_splits_short_lead_soft_pause(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "今天呢"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text="你呢，今天不出去浪")]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["你呢", "今天不出去浪"],
        )

    def test_chat_style_before_send_limits_natural_segments(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {"chat_style_config": {"casual_max_chars": 120, "private_casual_max_chars": 120}}
        )
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "然后呢"
        reply = (
            "刚洗完脸，头发还没干。被你一问又精神了。本来都准备把手机扣下了，"
            "结果你又来一句。再聊两句可以，但我真的要睡了，不然明天起不来。"
        )
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        segments = [item.text for item in event.get_result().chain]
        self.assertEqual(len(segments), 3)
        self.assertEqual("".join(segments), reply)

    def test_chat_style_before_send_allows_four_segments_for_long_casual_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "然后呢"
        reply = (
            "下午本来只想去超市买牛奶和洗衣液顺便透口气。"
            "结果路过面包架又顺手拿了酸奶吐司和一盒草莓准备明天当早餐。"
            "回家路上突然下小雨幸好包里还塞着那把小伞一路慢慢走也没淋湿。"
            "到家把东西分好放进冰箱以后整个人终于像完成任务一样松下来了。"
        )
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        segments = [item.text for item in event.get_result().chain]
        self.assertEqual(len(segments), 4)
        self.assertEqual("".join(segments), reply)
        self.assertTrue(segments[2].startswith("回家路上"))
        self.assertTrue(segments[3].startswith("到家把东西"))

    def test_chat_style_before_send_splits_soft_rich_long_tail(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        event.message_str = "这讲的是什么？"
        reply = (
            "就一个母女聊西游记选老公的视频。妈妈本来想选唐僧，说实在不行还能炖了长生不老。"
            "结果女儿嫌唐僧没智慧，非要选孙悟空，还给算了一笔账，"
            "说金箍棒值六十亿、花果山能开发房地产，直接把妈妈说服了。"
        )
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "group"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        segments = [item.text for item in event.get_result().chain]
        self.assertEqual(len(segments), 4)
        self.assertEqual(segments[0], "就一个母女聊西游记选老公的视频。")
        self.assertEqual(segments[1], "妈妈本来想选唐僧，说实在不行还能炖了长生不老。")
        self.assertEqual(segments[2], "结果女儿嫌唐僧没智慧，非要选孙悟空，还给算了一笔账。")
        self.assertTrue(segments[3].startswith("说金箍棒值六十亿"))

    def test_chat_style_before_send_allows_five_segments_for_long_break_rich_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "继续说"
        reply = (
            "早上醒来先把窗帘拉开让房间透了会儿气。"
            "桌上的杯子还没洗我就顺手拿去冲干净了顺便换了热水。"
            "午饭前把冰箱里剩下的菜都整理了一遍看着清爽很多。"
            "下午坐在窗边回了几条消息又把明天要用的东西装进包里。"
            "晚上洗完澡以后整个人轻了很多终于可以踏实躺下了。"
        )
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        segments = [item.text for item in event.get_result().chain]
        self.assertEqual(len(segments), 5)
        self.assertEqual("".join(segments), reply)
        self.assertTrue(segments[-1].startswith("晚上洗完澡"))

    def test_chat_style_before_send_prefers_two_segments_for_medium_three_beat_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "科普下"
        reply = "哈哈这叫平头龟蚁，它一出生就是去当防盗门的。那个大扁头刚好卡在洞口，谁来都敲不开。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["哈哈这叫平头龟蚁", "它一出生就是去当防盗门的。那个大扁头刚好卡在洞口，谁来都敲不开。"],
        )

    def test_chat_style_before_send_splits_short_reaction_three_complete_sentences(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "抢一口"
        reply = "哎你真抢啊！慢点吃别溅我身上。吃完赶紧去把锅和碗都洗了。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["哎你真抢啊！", "慢点吃别溅我身上。", "吃完赶紧去把锅和碗都洗了。"],
        )

    def test_chat_style_before_send_merges_tiny_reaction_then_splits_bridge_clause(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "跳楼了"
        reply = "啊？跳楼？这也太极端了吧……不管怎么吵架，也没必要拿自己的命去赌气啊，太冲动了。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            [
                "啊？跳楼？",
                "这也太极端了吧……",
                "不管怎么吵架",
                "也没必要拿自己的命去赌气啊，太冲动了。",
            ],
        )

    def test_chat_style_before_send_splits_reaction_and_soft_condition_tail(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "回来吗"
        reply = (
            "你想得美。"
            "我只是在考虑，"
            "你要是再不回来，"
            "属于你的那瓶酸奶就要被我喝掉了。"
        )
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            [
                "你想得美。",
                "我只是在考虑，你要是再不回来",
                "属于你的那瓶酸奶就要被我喝掉了。",
            ],
        )

    def test_chat_style_before_send_splits_full_lead_and_short_soft_tail(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "睡了吗"
        reply = "刚困得眯了一会儿。没骗你啊，快点的，再不回来我真反锁了。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            [
                "刚困得眯了一会儿。",
                "没骗你啊，快点的",
                "再不回来我真反锁了。",
            ],
        )

    def test_chat_style_before_send_does_not_split_inside_quotes(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "你回我了吗"
        reply = "你这不行啊，最新的说说底下我都回复过了，你还没回我最后那句“垃圾袋扎好了，速去”呢。赶紧的，把垃圾拎下去丢了。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        segments = [item.text for item in event.get_result().chain]
        self.assertIn("“垃圾袋扎好了，速去”", "".join(segments))
        self.assertTrue(any("“垃圾袋扎好了，速去”" in segment for segment in segments))
        self.assertFalse(any("“垃圾袋扎好了" in segment and "速去”" not in segment for segment in segments))

    def test_chat_style_natural_segments_keep_english_space(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"private_casual_max_chars": 90}})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "这个呢"
        reply = "这个 prompt 先别改太满，style 可以轻一点。后面再看效果。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        segments = [item.text for item in event.get_result().chain]
        self.assertIn("prompt 先别改太满", segments[0])
        self.assertEqual("".join(segments), reply)

    def test_chat_style_natural_segments_keep_protected_spans_intact(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "这个呢"
        url = "https://example.com/a,b?x=1"
        reply = f"这个 `a,b` 先别拆，链接 {url}。后面再看。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        segments = [item.text for item in event.get_result().chain]
        self.assertTrue(any("`a,b`" in segment for segment in segments))
        self.assertTrue(any(url in segment for segment in segments))
        self.assertFalse(any("`a" in segment and "b`" not in segment for segment in segments))
        self.assertFalse(any("https://example.com/a" in segment and url not in segment for segment in segments))

    def test_chat_style_before_send_records_segment_plan(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "听见了吗"
        reply = "我听见了。不过现在真的快睡着了，你也别熬太狠。明天还要带我去买草莓蛋糕呢。"
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        pending = getattr(event, runtime._CHAT_STYLE_PENDING_SEGMENTS_ATTR)
        self.assertEqual([segment.text for segment in pending], [item.text for item in event.get_result().chain])
        self.assertEqual(pending[0].reason, "完整句停顿")
        self.assertEqual(pending[-1].reason, "收尾")
        self.assertTrue(all(segment.compact_length > 0 for segment in pending))

    def test_chat_style_before_send_segments_do_not_share_component_state(self):
        class SharedStateText:
            def __init__(self, text):
                self.state = {"text": text}

            @property
            def text(self):
                return self.state["text"]

            @text.setter
            def text(self, value):
                self.state["text"] = value

        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "再陪我聊聊呗"
        reply = "我都闭眼了……眼皮重得像挂了秤砣。你赶紧数羊去，再吵我真装听不见了，晚安。"
        event.set_result(types.SimpleNamespace(chain=[SharedStateText(reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        segments = [item.text for item in event.get_result().chain]
        self.assertEqual(len(segments), 2)
        self.assertNotEqual(segments[0], segments[1])
        self.assertEqual("".join(segments), reply)

    def test_chat_style_before_send_keeps_unknown_paragraph_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "想吃什么"
        reply = (
            "回我一个问号脸，是你先问我想吃什么的诶。我都躺下了你才问，诚心馋我是吧？\n\n"
            "生滚牛肉粥还有干炒牛河，先在账本上给你记下了，明天睡醒我要吃双份的。撑不住了，不许再发消息吵我，真睡了，晚安！"
        )
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "unknown", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertGreaterEqual(len(event.get_result().chain), 2)
        segments = [item.text for item in event.get_result().chain]
        self.assertEqual(segments[0], "回我一个问号脸")
        self.assertEqual(segments[1], "是你先问我想吃什么的诶。")
        self.assertIn("明天睡醒我要吃双份", "".join(segments))

    def test_chat_style_before_send_keeps_multi_text_casual_reply(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"private_casual_max_chars": 40}})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "想吃什么"
        event.set_result(
            types.SimpleNamespace(
                chain=[
                    types.SimpleNamespace(text="好。"),
                    types.SimpleNamespace(text="嗯。"),
                    types.SimpleNamespace(text="行。"),
                ]
            )
        )
        setattr(event, "_daily_life_chat_style_decision", {"kind": "unknown", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(len(event.get_result().chain), 3)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            ["好。", "嗯。", "行。"],
        )

    def test_chat_style_before_send_keeps_long_reply_for_astrbot_t2i(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            Provider([]),
            config={
                "t2i": True,
                "t2i_word_threshold": 50,
            },
        )
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"private_casual_max_chars": 40}})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "详细说说"
        reply = (
            "这段我会完整说，不拆成一小条一小条刷屏。"
            "因为 AstrBot 已经开启文本转图像，而且这段内容超过阈值后应该交回默认发送链处理。"
            "这样后续的 Markdown、长文本排版和图片渲染都会保持一致。"
        )
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(len(event.get_result().chain), 1)
        self.assertEqual(event.get_result().chain[0].text, reply)

    def test_chat_style_before_send_uses_plugin_segments_when_astrbot_segment_enabled(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.context = Context(
            Provider([]),
            config={
                "t2i": False,
                "platform_settings": {
                    "segmented_reply": {
                        "enable": True,
                        "words_count_threshold": 50,
                    }
                },
            },
        )
        runtime.config = LifeSettings.from_dict({"chat_style_config": {"private_casual_max_chars": 20}})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "详细说说"
        reply = (
            "这段内容虽然超过 AstrBot 自己的分段回复字数上限。"
            "但聊天表达应该继续使用插件自己的自然短句分段，避免两个分段系统抢控制权。"
        )
        event.set_result(types.SimpleNamespace(chain=[types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertGreater(len(event.get_result().chain), 1)
        self.assertEqual("".join(item.text for item in event.get_result().chain), reply)

    def test_chat_style_before_send_refines_multi_text_comma_chain(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "casual_max_chars": 15,
                    "group_casual_max_chars": 15,
                }
            }
        )
        event = Event(
            unified_msg_origin="aiocqhttp:GroupMessage:10001",
            group_id="10001",
        )
        event.message_str = "躺了"
        event.set_result(
            types.SimpleNamespace(
                chain=[
                    types.SimpleNamespace(text="那你躺吧，说话算话啊，晚上番茄鸡蛋面的宵夜全交给你了。"),
                    types.SimpleNamespace(text="秒懂还能回我消息？"),
                    types.SimpleNamespace(text="你这属于梦游打字了。"),
                ]
            )
        )
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "group"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertTrue(changed)
        self.assertEqual(
            [item.text for item in event.get_result().chain],
            [
                "那你躺吧，说话算话啊",
                "晚上番茄鸡蛋面的宵夜全交给你了。",
                "秒懂还能回我消息？你这属于梦游打字了。",
            ],
        )

    def test_chat_style_before_send_does_not_segment_media_chain(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "贴贴"
        text_item = types.SimpleNamespace(text="还没，刚摸黑躺下。突然发个贴贴，怎么，良心发现想请我吃宵夜？")
        image_item = {"type": "image", "file": "a.png"}
        event.set_result(types.SimpleNamespace(chain=[text_item, image_item]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)

        self.assertFalse(changed)
        self.assertEqual(event.get_result().chain, [text_item, image_item])

    def test_chat_style_segmented_reply_sends_separate_messages(self):
        class Runtime(DailyLifeRuntime):
            pass

        runtime = Runtime.__new__(Runtime)
        notes = []
        runtime.note_structured_sent_result = lambda event: notes.append("structured")
        runtime.note_media_source_event = lambda event: notes.append("media")
        runtime.note_proactive_bot_reply = lambda event: notes.append("proactive")
        runtime.note_voice_switch_text_result = lambda event: notes.append("voice")
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "科普下"
        reply = "哈哈这叫平头龟蚁，它一出生就是去当防盗门的。那个大扁头刚好卡在洞口，谁来都敲不开。"
        event.set_result(event.chain_result([types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        changed = runtime.apply_chat_style_before_send(event)
        sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(changed)
        self.assertTrue(sent)
        self.assertIsNone(event.get_result())
        self.assertEqual(len(event.sent_messages), 2)
        self.assertEqual(event.sent_messages[0].chain[0].text, "哈哈这叫平头龟蚁")
        self.assertEqual(
            event.sent_messages[1].chain[0].text,
            "它一出生就是去当防盗门的。那个大扁头刚好卡在洞口，谁来都敲不开。",
        )
        self.assertEqual(notes, ["structured", "media", "proactive", "voice"])

    def test_chat_style_segmented_reply_uses_configured_delay(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        runtime.note_structured_sent_result = lambda event: None
        runtime.note_media_source_event = lambda event: None
        runtime.note_proactive_bot_reply = lambda event: None
        runtime.note_voice_switch_text_result = lambda event: None
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "科普下"
        reply = "哈哈这叫平头龟蚁，它一出生就是去当防盗门的。那个大扁头刚好卡在洞口，谁来都敲不开。"
        event.set_result(event.chain_result([types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        self.assertTrue(runtime.apply_chat_style_before_send(event))
        with patch("core.runtime.style.random.uniform", return_value=0.65), patch(
            "core.runtime.style.asyncio.sleep",
            fake_sleep,
        ):
            sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertTrue(sent)
        self.assertEqual(sleeps, [0.65])
        self.assertEqual(len(event.sent_messages), 2)

    def test_chat_style_segment_delay_follows_segment_length(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        ranges = []

        def fake_uniform(low, high):
            ranges.append((low, high))
            return low

        with patch("core.runtime.style.random.uniform", fake_uniform):
            lead_delay = runtime._chat_style_natural_segment_delay_seconds("你呢", "今天不出去浪")
            short_delay = runtime._chat_style_natural_segment_delay_seconds("嗯。", "好。")
            middle_delay = runtime._chat_style_natural_segment_delay_seconds(
                "还没，刚摸黑躺下，脑子还没醒。",
                "突然发个贴贴，怎么，",
            )
            long_delay = runtime._chat_style_natural_segment_delay_seconds(
                "那个大扁头刚好卡在洞口，谁来都敲不开。",
                "后面这句比较长一点，像是在慢慢补完一个完整意思，所以停顿也该稍微长一点。",
            )

        self.assertEqual(ranges, [(0.65, 1.05), (0.45, 0.9), (0.8, 1.8), (1.29, 2.29)])
        self.assertEqual((lead_delay, short_delay, middle_delay, long_delay), (0.65, 0.45, 0.8, 1.29))

    def test_chat_style_typing_weight_treats_english_lighter_than_cjk(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)

        chinese_weight = runtime._chat_style_typing_weight("明天还要带我去买草莓蛋糕")
        english_weight = runtime._chat_style_typing_weight("please check this tomorrow")

        self.assertGreaterEqual(chinese_weight, 12)
        self.assertLess(english_weight, len("pleasecheckthistomorrow"))
        self.assertGreater(english_weight, 0)

    def test_chat_style_initial_typing_delay_uses_text_weight(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        ranges = []

        def fake_uniform(low, high):
            ranges.append((low, high))
            return high

        with patch("core.runtime.style.random.uniform", fake_uniform):
            delay = runtime._chat_style_initial_typing_delay_seconds("我听见了，不过现在真的快睡着了")

        self.assertEqual(delay, ranges[0][1])
        self.assertGreater(ranges[0][0], 0.35)
        self.assertLessEqual(ranges[0][1], 3.5)

    def test_chat_style_segmented_reply_does_not_clear_on_changed_result(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({})
        event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "贴贴"
        reply = "还没，刚摸黑躺下。突然发个贴贴，怎么，良心发现想请我吃宵夜？"
        event.set_result(event.chain_result([types.SimpleNamespace(text=reply)]))
        setattr(event, "_daily_life_chat_style_decision", {"kind": "casual", "scope": "private"})

        self.assertTrue(runtime.apply_chat_style_before_send(event))
        event.set_result(event.chain_result([{"type": "record", "file": "voice.wav"}]))

        sent = asyncio.run(runtime.send_chat_style_segments_if_needed(event))

        self.assertFalse(sent)
        self.assertIsNotNone(event.get_result())
        self.assertEqual(event.sent_messages, [])

    def test_chat_style_limits_follow_compact_caps(self):
        group_runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        private_runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        config = LifeSettings.from_dict(
            {
                "chat_style_config": {
                    "casual_max_chars": 50,
                    "group_casual_max_chars": 30,
                    "private_casual_max_chars": 15,
                }
            }
        )
        group_runtime.config = config
        private_runtime.config = config

        group_event = Event(unified_msg_origin="aiocqhttp:GroupMessage:10001", group_id="10001")
        private_event = Event(unified_msg_origin="aiocqhttp:FriendMessage:10001")

        self.assertEqual(group_runtime._chat_style_limit_for_event(group_event), 30)
        self.assertEqual(private_runtime._chat_style_limit_for_event(private_event), 15)

