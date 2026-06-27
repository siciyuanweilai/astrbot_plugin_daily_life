import asyncio
import unittest

from support import (
    async_return,
    BehaviorFeedbackRecord,
    Event,
    ExpressionProfileRecord,
    LifeEpisodeRecord,
    LifeSettings,
    DailyLifeRuntime,
)
from core.memos import HostedMemOSService


class FakeMemOSClient:
    def __init__(self, data=None):
        self.available = True
        self.data = data or {}
        self.search_payloads = []
        self.add_payloads = []
        self.feedback_payloads = []

    async def search_memory(self, payload):
        self.search_payloads.append(payload)
        return type("Result", (), {"success": True, "data": self.data, "error": ""})()

    async def add_message(self, payload):
        self.add_payloads.append(payload)
        return type("Result", (), {"success": True, "data": {}, "error": ""})()

    async def add_feedback(self, payload):
        self.feedback_payloads.append(payload)
        return type("Result", (), {"success": True, "data": {}, "error": ""})()


class SlowMemOSClient(FakeMemOSClient):
    async def search_memory(self, payload):
        self.search_payloads.append(payload)
        await asyncio.sleep(0.3)
        return type("Result", (), {"success": True, "data": self.data, "error": ""})()


class MemOSTest(unittest.IsolatedAsyncioTestCase):
    def test_format_hidden_context_limits_and_labels_items(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                    "max_context_items": 2,
                    "max_context_chars": 120,
                }
            }
        ).memos
        service = HostedMemOSService(settings)
        items = service._parse_search_result(
            {
                "memory_detail_list": [{"memory_value": "阿林喜欢周末看展", "update_time": 1780000000000}],
                "preference_detail_list": [{"preference": "阿林偏好自然轻松的聊天"}],
            }
        )

        text = service.format_hidden_context(items)

        self.assertIn("事实: 阿林喜欢周末看展", text)
        self.assertIn("偏好: 阿林偏好自然轻松的聊天", text)
        self.assertEqual(len(text.splitlines()), 2)

    def test_format_hidden_context_has_chat_injection_hard_cap(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                    "max_context_items": 20,
                    "max_context_chars": 3000,
                }
            }
        ).memos
        service = HostedMemOSService(settings)
        items = [
            *[type("Item", (), {"kind": "fact", "content": f"第 {index} 条很长的外部事实记忆，包含很多不应该全部塞进聊天主提示的细节", "score": 0.8})() for index in range(6)],
            *[type("Item", (), {"kind": "preference", "content": f"第 {index} 条偏好记忆", "score": 0.7})() for index in range(6)],
        ]

        text = service.format_hidden_context(items)

        self.assertLessEqual(len(text.splitlines()), 3)
        self.assertLessEqual(len(text), 360)

    async def test_runtime_builds_memos_hidden_context_with_raw_session_user_id(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                }
            }
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = settings
        runtime.memos = HostedMemOSService(settings.memos)
        runtime.memos.client = FakeMemOSClient(
            {"memory_detail_list": [{"memory_value": "对方喜欢雨天散步"}]}
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "下雨了，要不要出去走走"

        text = await runtime.build_memos_hidden_context(event)

        payload = runtime.memos.client.search_payloads[0]
        self.assertIn("事实: 对方喜欢雨天散步", text)
        self.assertEqual(payload["user_id"], "aiocqhttp:FriendMessage:10001")
        self.assertEqual(payload["query"], "下雨了，要不要出去走走")
        self.assertNotIn("source", payload)
        self.assertNotIn("include_tool_memory", payload)

    async def test_runtime_memos_hidden_context_times_out_quickly(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                    "injection_timeout_seconds": 0.2,
                }
            }
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = settings
        runtime.memos = HostedMemOSService(settings.memos)
        runtime.memos.client = SlowMemOSClient({"memory_detail_list": [{"memory_value": "不该阻塞本轮回复"}]})
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "今晚聊什么"

        text = await runtime.build_memos_hidden_context(event)

        self.assertEqual(text, "")
        self.assertEqual(len(runtime.memos.client.search_payloads), 1)

    async def test_runtime_memos_hidden_context_reuses_short_cache(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                }
            }
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = settings
        runtime.memos = HostedMemOSService(settings.memos)
        runtime.memos.client = FakeMemOSClient({"memory_detail_list": [{"memory_value": "阿林喜欢夜聊"}]})
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {"resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name()))},
        )()
        event = Event(sender_name="阿林", sender_id="10001", unified_msg_origin="aiocqhttp:FriendMessage:10001")
        event.message_str = "今晚聊什么"

        first = await runtime.build_memos_hidden_context(event)
        second = await runtime.build_memos_hidden_context(event)

        self.assertEqual(first, second)
        self.assertIn("阿林喜欢夜聊", first)
        self.assertEqual(len(runtime.memos.client.search_payloads), 1)

    async def test_group_memos_context_is_scoped_by_group_and_sender(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                }
            }
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = settings
        runtime.memos = HostedMemOSService(settings.memos)
        runtime.memos.client = FakeMemOSClient(
            {"memory_detail_list": [{"memory_value": "阿林在这个群里喜欢聊展览"}]}
        )
        runtime.contact_resolver = type(
            "Resolver",
            (),
            {
                "resolve_event_sender": staticmethod(lambda event: async_return(event.get_sender_name())),
                "resolve_group_name": staticmethod(lambda *args, **kwargs: async_return("看展小群")),
            },
        )()
        event = Event(
            sender_name="阿林",
            sender_id="10001",
            unified_msg_origin="aiocqhttp:GroupMessage:20001",
            group_id="20001",
            group_name="看展小群",
        )
        event.message_str = "这个展还挺想去的"

        text = await runtime.build_memos_hidden_context(event)

        payload = runtime.memos.client.search_payloads[0]
        self.assertIn("事实: 阿林在这个群里喜欢聊展览", text)
        self.assertEqual(payload["user_id"], "group:20001:user:10001")
        self.assertEqual(payload["query"], "阿林: 这个展还挺想去的")
        self.assertTrue(payload["conversation_id"].startswith("dl_conv_"))

    async def test_runtime_schedules_memos_sync_in_background(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                    "sync_selected_memory": True,
                }
            }
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = settings
        runtime.memos = HostedMemOSService(settings.memos)
        scheduled = []

        def schedule(coro, label="", key=""):
            scheduled.append((coro, label, key))
            coro.close()
            return True

        runtime._schedule_background_task = schedule
        summary = type("Summary", (), {"id": 8, "brief": "阿林周末想一起看展"})()
        payload = {"brief": "阿林周末想一起看展"}
        meta = {"session_id": "aiocqhttp:FriendMessage:10001"}

        result = runtime.schedule_memos_selected_memory(payload, meta, user_message="周末去看展吗", summary=summary)

        self.assertTrue(result)
        self.assertEqual(scheduled[0][1], "MemOS 记忆同步")
        self.assertTrue(scheduled[0][2].startswith("memos_memory_"))

    async def test_runtime_schedules_memos_memo_in_background(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                }
            }
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = settings
        runtime.memos = HostedMemOSService(settings.memos)
        scheduled = []

        def schedule(coro, label="", key=""):
            scheduled.append((coro, label, key))
            coro.close()
            return True

        runtime._schedule_background_task = schedule
        meta = {"session_id": "aiocqhttp:FriendMessage:10001"}

        result = runtime.schedule_memos_memo(meta, "与【阿林】的约定：明天去吃火锅")

        self.assertTrue(result)
        self.assertEqual(scheduled[0][1], "MemOS 备忘录同步")
        self.assertTrue(scheduled[0][2].startswith("memos_memo_"))

    async def test_sync_selected_memory_uploads_only_selected_summary(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                    "sync_selected_memory": True,
                }
            }
        )
        service = HostedMemOSService(settings.memos)
        service.client = FakeMemOSClient()
        meta = {
            "session_id": "aiocqhttp:FriendMessage:10001",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
            "group_id": "",
        }

        saved = await service.sync_selected_memory(
            meta=meta,
            user_message="周末去看展吗",
            summary="阿林周末想一起看展",
            points=["阿林最近对看展很感兴趣"],
            preferences=["偏好周末轻松出门"],
        )

        self.assertTrue(saved)
        payload = service.client.add_payloads[0]
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertIn("长期记忆精选摘要", payload["messages"][1]["content"])
        self.assertIn("阿林周末想一起看展", payload["messages"][1]["content"])
        self.assertFalse(payload["async_mode"])
        self.assertNotIn("asyncMode", payload)
        self.assertNotIn("tags", payload)
        self.assertNotIn("info", payload)
        self.assertNotIn("source", payload)
        self.assertNotIn("allow_public", payload)

    async def test_sync_selected_items_uploads_compact_long_term_entries(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                    "sync_selected_memory": True,
                }
            }
        )
        service = HostedMemOSService(settings.memos)
        service.client = FakeMemOSClient()
        meta = {
            "session_id": "aiocqhttp:FriendMessage:10001",
            "sender_profile_id": "10001",
        }

        saved = await service.sync_selected_items(
            meta=meta,
            reason="同步已确认的长期条目。",
            user_message="以后别总发语音",
            items=["行为反馈：用户希望必要时改用文字", "表达方式：回复要短一点"],
        )

        self.assertTrue(saved)
        payload = service.client.add_payloads[0]
        self.assertEqual(payload["messages"][0]["content"], "以后别总发语音")
        self.assertIn("同步已确认的长期条目。", payload["messages"][1]["content"])
        self.assertIn("行为反馈：用户希望必要时改用文字", payload["messages"][1]["content"])
        self.assertFalse(payload["async_mode"])
        self.assertNotIn("source", payload)

    async def test_runtime_schedules_selected_items_in_background(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                    "sync_selected_memory": True,
                }
            }
        )
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = settings
        runtime.memos = HostedMemOSService(settings.memos)
        scheduled = []

        def schedule(coro, label="", key=""):
            scheduled.append((coro, label, key))
            coro.close()
            return True

        runtime._schedule_background_task = schedule
        meta = {"session_id": "aiocqhttp:FriendMessage:10001"}

        result = runtime.schedule_memos_selected_items(
            meta,
            ["约定追踪：明天去看展", "稳定偏好：偏好周末出门"],
            reason="同步已确认的长期条目。",
            marker="demo",
        )

        self.assertTrue(result)
        self.assertEqual(scheduled[0][1], "MemOS 精选条目同步")
        self.assertTrue(scheduled[0][2].startswith("memos_items_"))

    def test_runtime_builds_selected_items_from_saved_memory_payload(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        summary = type("Summary", (), {"brief": "阿林提出周末看展", "long_summary": ""})()
        payload = {
            "relationship_points": ["阿林会主动约我看展"],
            "preference_points": [{"category": "social", "content": "偏好周末轻松出门", "evidence": "聊天提到"}],
            "memory_targets": [
                {
                    "name": "阿林",
                    "relationship_story": "一起聊展览比较自然",
                    "points": ["喜欢轻松安排"],
                }
            ],
        }
        saved_records = {
            "speaker_relationship": [
                {
                    "name": "阿林",
                    "subjective_name": "看展搭子",
                    "subjective_tags": ["轻松"],
                    "relationship_story": "周末出门话题很自然",
                    "note": "主动靠近",
                }
            ],
            "behavior_feedback": [
                BehaviorFeedbackRecord(scene="语音回复", action="发语音", feedback="希望必要时用文字", result="negative")
            ],
            "expression_profiles": [
                ExpressionProfileRecord(label="阿林", tone="短句自然", habits=["少铺垫"], avoid=["太像系统说明"])
            ],
            "life_episodes": [
                LifeEpisodeRecord(title="约看展", summary="阿林提到周末一起看展", impact="后续可以回访")
            ],
        }

        items = runtime._memos_items_from_payload(payload, saved_summary=summary, saved_records=saved_records)

        joined = "\n".join(items)
        self.assertIn("聊天摘要", joined)
        self.assertIn("关系画像", joined)
        self.assertIn("稳定偏好", joined)
        self.assertIn("行为反馈", joined)
        self.assertIn("表达方式", joined)
        self.assertIn("长期生活事件", joined)

    async def test_sync_memo_uploads_confirmed_memo(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                }
            }
        )
        service = HostedMemOSService(settings.memos)
        service.client = FakeMemOSClient()
        meta = {
            "session_id": "aiocqhttp:FriendMessage:10001",
            "sender_profile_id": "10001",
        }

        saved = await service.sync_memo(meta=meta, memo="与【阿林】的约定：明天去吃火锅")

        self.assertTrue(saved)
        payload = service.client.add_payloads[0]
        self.assertEqual(payload["user_id"], "aiocqhttp:FriendMessage:10001")
        self.assertFalse(payload["async_mode"])
        self.assertIn("明日备忘录：与【阿林】的约定：明天去吃火锅", payload["messages"][1]["content"])

    async def test_sync_correction_uses_feedback_endpoint(self):
        settings = LifeSettings.from_dict(
            {
                "memos_config": {
                    "enabled": True,
                    "api_key": "key",
                    "sync_corrections": True,
                }
            }
        )
        service = HostedMemOSService(settings.memos)
        service.client = FakeMemOSClient()
        meta = {
            "session_id": "aiocqhttp:FriendMessage:10001",
            "sender_profile_id": "10001",
            "sender_name": "阿林",
        }

        saved = await service.sync_feedback(meta=meta, feedback="阿林是男性死党")

        self.assertTrue(saved)
        payload = service.client.feedback_payloads[0]
        self.assertEqual(payload["feedback_content"], "阿林是男性死党")
        self.assertNotIn("allow_public", payload)
