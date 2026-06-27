import datetime
import unittest

from support import LifeState
from core.labels import page_status_reason_label
from core.life.condition import classify_message_interrupt, message_can_interrupt, normalize_state
from core.clock import TIMEZONE, TIMEZONE_NAME, now as life_now, today as life_today


class LifeStateSubjectiveTest(unittest.TestCase):
    def test_normalize_state_defaults_subjective_attention_fields(self):
        state = normalize_state(
            {
                "boredom": 130,
                "fishing": -5,
                "attention_openness": "72",
                "watch_state": "ENGAGED",
                "interrupt_level": "medium",
            },
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertEqual(state["boredom"], 100)
        self.assertEqual(state["fishing"], 0)
        self.assertEqual(state["attention_openness"], 72)
        self.assertEqual(state["watch_state"], "engaged")
        self.assertEqual(state["interrupt_level"], "medium")

    def test_normalize_state_uses_ordinary_interrupt_default(self):
        state = normalize_state({}, now=datetime.datetime(2026, 5, 24, 12, 0))

        self.assertEqual(state["watch_state"], "peek")
        self.assertEqual(state["interrupt_level"], "ordinary")
        self.assertEqual(state["sleep"]["depth"], "awake")

    def test_normalize_state_round_trips_sleep_depth(self):
        state = normalize_state(
            {"sleep": {"quality": 42, "depth": "light_sleep", "summary": "浅浅睡着"}},
            now=datetime.datetime(2026, 5, 24, 23, 30),
        )

        self.assertEqual(state["sleep"]["depth"], "light_sleep")
        self.assertEqual(state["sleep"]["quality"], 42)

    def test_message_interrupt_rank_is_structural_not_semantic_reply_rule(self):
        quiet_state = {"interrupt_level": "high"}
        open_state = {"interrupt_level": "ordinary"}

        ordinary = classify_message_interrupt("路过闲聊")
        directed = classify_message_interrupt("在吗", directed=True)
        quoted = classify_message_interrupt("接上条", quoted=True)

        self.assertFalse(message_can_interrupt(quiet_state, ordinary))
        self.assertTrue(message_can_interrupt(quiet_state, directed))
        self.assertTrue(message_can_interrupt(quiet_state, quoted))
        self.assertTrue(message_can_interrupt(open_state, ordinary))

    def test_life_state_round_trips_subjective_attention_fields(self):
        state = LifeState.from_value(
            {
                "boredom": 64,
                "fishing": 41,
                "attention_openness": 35,
                "watch_state": "skim_window",
                "interrupt_level": "medium",
                "interrupt_reason": "正忙，只扫相关消息",
                "sleep": {"depth": "light_rest"},
            }
        )

        self.assertEqual(
            state.as_dict(),
            {
                "boredom": 64,
                "fishing": 41,
                "attention_openness": 35,
                "watch_state": "skim_window",
                "interrupt_level": "medium",
                "interrupt_reason": "正忙，只扫相关消息",
                "sleep": {"depth": "light_rest"},
            },
        )

    def test_page_status_reason_label_uses_chinese_text(self):
        self.assertEqual(page_status_reason_label("autonomous_life_update"), "自主生活状态与穿搭更新")
        self.assertEqual(page_status_reason_label("chat_state_refresh"), "聊天触发状态巡检")
        self.assertEqual(page_status_reason_label("private_revisit"), "私聊回访")
        self.assertEqual(page_status_reason_label("proactive_reply_decision"), "闲时回复裁定")


class TimeServiceTest(unittest.TestCase):
    def test_life_time_service_uses_configured_local_timezone(self):
        current = life_now()

        self.assertEqual(getattr(TIMEZONE, "key", TIMEZONE_NAME), TIMEZONE_NAME)
        self.assertIsNone(current.tzinfo)
        self.assertEqual(life_today(), current.date())
