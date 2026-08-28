import datetime
import unittest

from core.clock import TIMEZONE, TIMEZONE_NAME
from core.clock import now as life_now
from core.clock import today as life_today
from core.labels import page_status_reason_label, preference_category_label
from core.life.condition import (
    classify_message_interrupt,
    daily_variability_signal,
    message_can_interrupt,
    normalize_physiological_rhythm,
    normalize_state,
)
from support import LifeState


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

    def test_normalize_physiological_rhythm_clamps_and_expires_short_state(self):
        rhythm = normalize_physiological_rhythm(
            {
                "energy_curve": "上午低，傍晚回升",
                "body_condition": {
                    "label": "嗓子有点干",
                    "intensity": 160,
                    "source": "实时状态",
                    "expires_at": "2026-05-23",
                },
                "recovery_actions": ["喝温水", "早睡", "喝温水"],
                "social_battery": -8,
                "attention_state": "低刺激更舒服",
                "optional_cycle": {
                    "enabled": True,
                    "label": "周期波动",
                    "intensity": 42,
                    "source": "用户说明",
                },
            },
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertEqual(rhythm["energy_curve"], "上午低，傍晚回升")
        self.assertEqual(rhythm["body_condition"]["label"], "无明显不适")
        self.assertEqual(rhythm["body_condition"]["intensity"], 0)
        self.assertEqual(rhythm["recovery_actions"], ["喝温水", "早睡"])
        self.assertEqual(rhythm["social_battery"], 0)
        self.assertEqual(rhythm["attention_state"], "低刺激更舒服")
        self.assertTrue(rhythm["optional_cycle"]["enabled"])
        self.assertEqual(rhythm["optional_cycle"]["label"], "周期波动")

    def test_body_condition_uses_explicit_burden_flag_not_label_text(self):
        healthy = normalize_physiological_rhythm(
            {
                "body_condition": {
                    "label": "嗓子发干并有些乏力",
                    "intensity": 85,
                    "burden_present": False,
                    "source": "自然醒后的感受",
                }
            }
        )
        unwell = normalize_physiological_rhythm(
            {
                "body_condition": {
                    "label": "清醒舒爽",
                    "intensity": 36,
                    "burden_present": True,
                    "source": "起床后的身体感受",
                }
            }
        )

        self.assertEqual(healthy["body_condition"]["intensity"], 0)
        self.assertFalse(healthy["body_condition"]["burden_present"])
        self.assertEqual(unwell["body_condition"]["intensity"], 36)
        self.assertTrue(unwell["body_condition"]["burden_present"])

    def test_short_body_discomfort_survives_ordinary_refresh_until_expiry(self):
        previous = {
            "body_condition": {
                "label": "鼻子有点堵，头也发沉",
                "intensity": 32,
                "burden_present": True,
                "source": "晨起身体感受",
                "expires_at": "2026-05-24 13:00",
            }
        }
        raw = {"body_condition": {"label": "普通状态刷新"}}

        active = normalize_physiological_rhythm(
            raw,
            previous=previous,
            now=datetime.datetime(2026, 5, 24, 12, 30),
        )
        recovered = normalize_physiological_rhythm(
            {
                "body_condition": {
                    "label": "清醒舒适",
                    "intensity": 80,
                    "burden_present": False,
                    "source": "恢复后的身体感受",
                }
            },
            previous=previous,
            now=datetime.datetime(2026, 5, 24, 13, 1),
        )

        self.assertEqual(active["body_condition"]["label"], "鼻子有点堵，头也发沉")
        self.assertEqual(active["body_condition"]["intensity"], 32)
        self.assertEqual(recovered["body_condition"]["label"], "清醒舒适")
        self.assertEqual(recovered["body_condition"]["intensity"], 0)
        self.assertFalse(recovered["body_condition"]["burden_present"])

    def test_omitted_burden_flag_never_inherits_semantics_from_text_or_score(self):
        rhythm = normalize_physiological_rhythm(
            {
                "body_condition": {
                    "label": "任意新状态",
                    "intensity": 80,
                }
            },
            previous={
                "body_condition": {
                    "label": "任意旧状态",
                    "intensity": 32,
                    "burden_present": True,
                }
            },
        )

        self.assertEqual(rhythm["body_condition"]["intensity"], 0)
        self.assertFalse(rhythm["body_condition"]["burden_present"])

    def test_daily_variability_signal_uses_state_not_calendar_lottery(self):
        ordinary = daily_variability_signal(
            "2026-09-01", state={"mood_score": 70, "stress": 20}
        )
        same_state_next_day = daily_variability_signal(
            "2026-09-02", state={"mood_score": 70, "stress": 20}
        )
        burdened = daily_variability_signal(
            "2026-09-01",
            state={
                "stress": 72,
                "physiological_rhythm": {
                    "body_condition": {
                        "burden_present": True,
                        "intensity": 35,
                    }
                },
            },
        )

        self.assertEqual(ordinary, same_state_next_day)
        self.assertEqual(ordinary, {"body": "ordinary", "mood": "ordinary"})
        self.assertEqual(burdened, {"body": "watch", "mood": "watch"})

    def test_realtime_state_refresh_clamps_unexplained_score_jumps(self):
        state = normalize_state(
            {"energy": 10, "mood_score": 98, "stress": 95},
            previous={"energy": 60, "mood_score": 60, "stress": 35},
            max_score_delta=12,
        )

        self.assertEqual(state["energy"], 48)
        self.assertEqual(state["mood_score"], 72)
        self.assertEqual(state["stress"], 47)

    def test_optional_cycle_defaults_closed_without_current_structural_value(self):
        rhythm = normalize_physiological_rhythm(
            {"energy_curve": "今天平稳"},
            previous={
                "optional_cycle": {
                    "enabled": True,
                    "label": "上一轮周期",
                    "intensity": 60,
                    "source": "上一轮状态",
                }
            },
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertFalse(rhythm["optional_cycle"]["enabled"])
        self.assertEqual(rhythm["optional_cycle"]["label"], "")

    def test_optional_cycle_requires_current_structural_detail(self):
        rhythm = normalize_physiological_rhythm(
            {"optional_cycle": {"enabled": True}},
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertFalse(rhythm["optional_cycle"]["enabled"])
        self.assertEqual(rhythm["optional_cycle"]["intensity"], 0)

    def test_optional_cycle_ignores_field_descriptor_text(self):
        rhythm = normalize_physiological_rhythm(
            {
                "optional_cycle": {
                    "enabled": "布尔值，是否存在可选周期",
                    "label": "字段说明",
                    "intensity": 60,
                    "source": "字段说明",
                }
            },
            now=datetime.datetime(2026, 5, 24, 12, 0),
        )

        self.assertFalse(rhythm["optional_cycle"]["enabled"])
        self.assertEqual(rhythm["optional_cycle"]["label"], "")

    def test_normalize_state_ignores_legacy_flat_sleep_fields(self):
        state = normalize_state(
            {
                "sleep_quality": 12,
                "sleep_depth": "deep_sleep",
                "sleep_summary": "旧扁平睡眠字段",
            },
            now=datetime.datetime(2026, 5, 24, 23, 30),
        )

        self.assertEqual(state["sleep"]["quality"], 65)
        self.assertEqual(state["sleep"]["depth"], "awake")
        self.assertNotEqual(state["sleep"]["summary"], "旧扁平睡眠字段")

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
                "physiological_rhythm": {
                    "energy_curve": "午后回落",
                    "body_condition": {
                        "label": "轻微疲惫",
                        "intensity": 35,
                        "burden_present": True,
                        "source": "状态刷新",
                    },
                    "recovery_actions": ["早点收尾"],
                    "social_battery": 44,
                    "attention_state": "慢半拍",
                    "optional_cycle": {"enabled": False},
                    "summary": "适合低强度安排",
                },
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
                "physiological_rhythm": {
                    "energy_curve": "午后回落",
                    "body_condition": {
                        "label": "轻微疲惫",
                        "intensity": 35,
                        "burden_present": True,
                        "source": "状态刷新",
                    },
                    "recovery_actions": ["早点收尾"],
                    "social_battery": 44,
                    "attention_state": "慢半拍",
                    "summary": "适合低强度安排",
                },
            },
        )

    def test_page_status_reason_label_uses_chinese_text(self):
        self.assertEqual(
            page_status_reason_label("autonomous_life_update"), "自主生活状态与穿搭更新"
        )
        self.assertEqual(
            page_status_reason_label("chat_state_refresh"), "聊天触发状态巡检"
        )
        self.assertEqual(page_status_reason_label("private_revisit"), "私聊回访")
        self.assertEqual(
            page_status_reason_label("proactive_reply_decision"), "闲时回复裁定"
        )
        self.assertEqual(page_status_reason_label("memo"), "备忘录更新")
        self.assertEqual(page_status_reason_label("outfit_update"), "穿搭更新")
        generation_reasons = {
            "daily_generation_searching": "正在搜索日程参考",
            "daily_generation_waiting": "等待日程生成",
            "daily_generation_generating": "正在生成日程",
            "daily_generation_completed": "日程生成完成",
            "daily_generation_failed": "日程生成失败",
        }
        for reason, label in generation_reasons.items():
            with self.subTest(reason=reason):
                self.assertEqual(page_status_reason_label(reason), label)

    def test_preference_category_label_uses_chinese_text(self):
        self.assertEqual(preference_category_label("hair"), "发型")


class TimeServiceTest(unittest.TestCase):
    def test_life_time_service_uses_configured_local_timezone(self):
        current = life_now()

        self.assertEqual(getattr(TIMEZONE, "key", TIMEZONE_NAME), TIMEZONE_NAME)
        self.assertIsNone(current.tzinfo)
        self.assertEqual(life_today(), current.date())
