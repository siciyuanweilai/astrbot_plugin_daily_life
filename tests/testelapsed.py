"""对话间隔来自收发事实，不把后台等待、其他会话或发送失败算进去。"""

import asyncio
import datetime
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.archive.schema import init_schema
from core.clock import TIMEZONE
from core.runtime.elapsed import RECEIVED_AT, SENT_AT, TURN_RECEIVED_AT
from support import DailyLifeRuntime, Event, LifeArchive, LifeSettings, ProviderRequest


def timestamp(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=TIMEZONE).timestamp()


class ConversationTimingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "life.db"
        self.archive = LifeArchive(self.path)
        self.runtime = self.new_runtime()
        self.scope = "test:FriendMessage:1"
        self.sent = timestamp("2026-10-08T23:30:00")
        self.arrived = timestamp("2026-10-09T08:00:00")

    def new_runtime(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime.config = LifeSettings.from_dict({"memory_config": {"enabled": False}})
        runtime.archive = self.archive
        return runtime

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.temp.cleanup()

    def incoming(self, when=None, *, scope=None, message_id="m1", group_id=""):
        event = Event(
            unified_msg_origin=scope or self.scope, sender_id="1", self_id="bot",
            message_id=message_id, group_id=group_id,
        )
        event.message_str = "我回来了"
        with patch("core.runtime.elapsed.time.time", return_value=when or self.arrived):
            self.runtime.note_conversation_incoming(event)
        return event

    async def test_overnight_gap_survives_reload_and_memory_disabled(self):
        await self.runtime.record_conversation_delivery(self.scope, self.sent)
        await self.archive.aclose()
        self.archive = LifeArchive(self.path)
        self.runtime = self.new_runtime()
        self.assertFalse(self.runtime.config.memory.enabled)
        context = await self.runtime.build_conversation_timing_context(self.incoming())
        self.assertIn("2026-10-08T23:30:00+08:00", context)
        self.assertIn("2026-10-09T08:00:00+08:00", context)
        self.assertIn("8小时30分钟（30600秒）", context)

    async def test_proactive_delivery_does_not_reset_the_users_absence(self):
        last_user = self.incoming(self.sent - 60)
        await self.runtime.record_conversation_arrival(last_user)
        await self.runtime.record_conversation_delivery(self.scope, self.sent)
        await self.runtime.record_conversation_delivery(self.scope, self.arrived - 120)
        context = await self.runtime.build_conversation_timing_context(self.incoming())
        self.assertIn("已过：2分钟（120秒）", context)
        self.assertIn("对方两次发来消息相隔：8小时31分钟（30660秒）", context)
        self.assertIn("这不代表对方在此期间回复过", context)

    async def test_arrivals_survive_reload_without_chat_memory_or_a_reply(self):
        last_user = self.incoming(self.sent - 60)
        await self.runtime.record_conversation_arrival(last_user)
        await self.archive.aclose()
        self.archive = LifeArchive(self.path)
        self.runtime = self.new_runtime()
        current = self.incoming()
        await self.runtime.record_conversation_arrival(current)
        context = await self.runtime.build_conversation_timing_context(current)
        self.assertFalse(self.runtime.config.memory.enabled)
        self.assertEqual(
            self.archive._conn.execute(
                "SELECT COUNT(*) FROM chat_memory_messages"
            ).fetchone()[0],
            0,
        )
        self.assertIn("对方两次发来消息相隔：8小时31分钟（30660秒）", context)
        self.assertIn("上次成功发送时间：无可核实记录", context)

    async def test_continuous_turn_excludes_all_current_bubbles_from_previous_arrival(
        self,
    ):
        await self.archive.save_conversation_arrival(self.scope, self.sent - 60)
        first = self.incoming()
        self.runtime.note_continuous_turn_incoming(first)
        await self.runtime.record_conversation_arrival(first)
        second = self.incoming(self.arrived + 3, message_id="second")
        self.runtime.note_continuous_turn_incoming(second)
        await self.runtime.record_conversation_arrival(second)
        await self.runtime.record_conversation_delivery(self.scope, self.arrived + 1)
        context = await self.runtime.build_conversation_timing_context(second)
        self.assertIn("对方两次发来消息相隔：8小时31分钟（30660秒）", context)
        self.assertIn("本轮连续消息开始到达：2026-10-09T08:00:00", context)
        self.assertNotIn("本会话上次成功发送：", context)

    async def test_arrival_scope_isolation_and_group_scope_are_explicit(self):
        await self.archive.save_conversation_arrival("other:FriendMessage:1", self.sent)
        context = await self.runtime.build_conversation_timing_context(self.incoming())
        self.assertNotIn("两次发来消息相隔", context)
        group = "test:GroupMessage:2"
        await self.archive.save_conversation_arrival(group, self.sent)
        context = await self.runtime.build_conversation_timing_context(
            self.incoming(scope=group, group_id="2")
        )
        self.assertIn("群内两次发来消息相隔：8小时30分钟", context)
        self.assertIn("不据此判断这个人迟回", context)

    async def test_arrivals_are_bounded_idempotent_and_cleared_with_conversation(self):
        for index in reversed(range(40)):
            event = self.incoming(self.sent + index, message_id=str(index))
            await self.runtime.record_conversation_arrival(event)
        await self.runtime.record_conversation_arrival(event)
        self.assertEqual(
            self.archive._conn.execute(
                "SELECT COUNT(*) FROM conversation_arrivals"
            ).fetchone()[0],
            32,
        )
        self.assertEqual(
            len(self.runtime.conversation_timing._arrivals[self.scope]), 32
        )
        self.assertEqual(
            await self.archive.get_conversation_arrival_before(
                self.scope, self.sent + 20
            ),
            self.sent + 19,
        )
        await self.archive.clear_storage_category("conversation")
        self.runtime.clear_conversation_timing()
        self.assertFalse(self.runtime.conversation_timing._arrivals)
        self.assertEqual(
            await self.archive.get_conversation_arrival_before(
                self.scope, self.arrived
            ),
            0,
        )

    async def test_self_and_synthetic_arrivals_are_not_persisted(self):
        for synthetic in (False, True):
            event = Event(sender_id="bot", self_id="bot") if not synthetic else Event()
            event.is_proactive_synthetic = synthetic
            self.runtime.note_conversation_incoming(event)
            await self.runtime.record_conversation_arrival(event)
            self.assertFalse(hasattr(event, RECEIVED_AT))
        self.assertEqual(
            self.archive._conn.execute(
                "SELECT COUNT(*) FROM conversation_arrivals"
            ).fetchone()[0],
            0,
        )

    async def test_failed_arrival_persistence_keeps_in_memory_gap(self):
        self.runtime.archive = types.SimpleNamespace(
            save_conversation_arrival=AsyncMock(side_effect=OSError())
        )
        last_user = self.incoming(self.sent)
        await self.runtime.record_conversation_arrival(last_user)
        context = await self.runtime.build_conversation_timing_context(self.incoming())
        self.assertIn("对方两次发来消息相隔：8小时30分钟", context)

    async def test_legacy_history_does_not_mistake_current_message_for_previous_arrival(
        self,
    ):
        self.archive._conn.executemany(
            "INSERT INTO chat_memory_messages(event_key, session_id, role, message_text, occurred_at) VALUES (?, ?, 'user', '聊天', ?)",
            [
                ("previous-user", self.scope, "2026-10-08T23:29:00"),
                ("current-user", self.scope, "2026-10-09T08:00:00"),
            ],
        )
        current = self.incoming(self.arrived + 0.75)
        await self.runtime.record_conversation_arrival(current)
        context = await self.runtime.build_conversation_timing_context(current)
        self.assertIn("对方两次发来消息相隔：8小时31分钟", context)
        self.assertEqual(
            await self.archive.get_conversation_arrival_before(
                self.scope, self.arrived + 0.75
            ),
            self.sent - 60,
        )

    async def test_v27_upgrade_preserves_receipts_and_reads_existing_user_history(self):
        from core.archive.migrations import (
            CURRENT_SCHEMA_FINGERPRINT,
            schema_fingerprint,
        )

        await self.runtime.record_conversation_delivery(self.scope, self.sent)
        for versioned in (True, False):
            with self.subTest(versioned=versioned):
                for table in ("relationship_evidence_claims", "relationship_changes", "relationship_states"):
                    self.archive._conn.execute(f"DROP TABLE {table}")
                self.archive._conn.execute("ALTER TABLE relationships DROP COLUMN address_basis")
                # 真正还原 v27，而不是保留 v31/v32 扩展后伪装成无版本旧库。
                for table in ("knowledge_readings", "knowledge_passages", "knowledge_sources", "memory_identity_redirects", "memory_associations", "memory_enrichments", "memory_chapters", "conversation_originals", "expression_learning_events"):
                    self.archive._conn.execute(f"DROP TABLE {table}")
                self.archive._conn.execute("ALTER TABLE expression_profiles DROP COLUMN learning_json")
                self.archive._conn.execute("DROP TABLE conversation_arrivals")
                self.archive._conn.execute("ALTER TABLE commitments DROP COLUMN follow_up_json")
                # 还原 v27 的已发布字段，不能把当前版本删列后的结构冒充旧库。
                table_sql = self.archive._conn.execute("SELECT sql FROM sqlite_master WHERE name='action_decisions'").fetchone()[0]
                index_sql = [row[0] for row in self.archive._conn.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name='action_decisions' AND type='index' AND sql IS NOT NULL"
                )]
                columns = [row[1] for row in self.archive._conn.execute("PRAGMA table_info(action_decisions)")]
                additions = "".join(f"{name} TEXT NOT NULL DEFAULT '',\n" for name in (
                    "decision_category", "decision_source", "decision_stage", "decision_outcome"
                ))
                self.archive._conn.execute("ALTER TABLE action_decisions RENAME TO current_decisions")
                self.archive._conn.execute(table_sql.replace("created_at TEXT", additions + "created_at TEXT"))
                column_list = ",".join(columns)
                self.archive._conn.execute(f"INSERT INTO action_decisions({column_list}) SELECT {column_list} FROM current_decisions")
                self.archive._conn.execute("DROP TABLE current_decisions")
                for statement in index_sql:
                    self.archive._conn.execute(statement)
                if versioned:
                    self.archive._conn.execute(
                        "UPDATE meta SET value = '27' WHERE key = 'schema_version'"
                    )
                else:
                    self.archive._conn.execute(
                        "DELETE FROM meta WHERE key = 'schema_version'"
                    )
                self.archive._conn.commit()
                init_schema(self.archive._conn)
                init_schema(self.archive._conn)
                self.assertEqual(
                    schema_fingerprint(self.archive._conn), CURRENT_SCHEMA_FINGERPRINT
                )
        self.assertEqual(
            await self.archive.get_conversation_receipt_before(
                self.scope, self.arrived
            ),
            self.sent,
        )
        self.archive._conn.execute(
            "INSERT INTO chat_memory_messages(event_key, session_id, role, message_text, occurred_at) VALUES ('old-user', ?, 'user', '稍后聊', '2026-10-08T23:29:00')",
            (self.scope,),
        )
        self.archive._conn.commit()
        await self.archive.save_conversation_arrival(self.scope, self.arrived)
        context = await self.runtime.build_conversation_timing_context(self.incoming())
        self.assertIn("对方两次发来消息相隔：8小时31分钟", context)
        self.assertIn("已过：8小时30分钟", context)

    async def test_media_preparation_and_model_wait_do_not_inflate_gap(self):
        await self.runtime.record_conversation_delivery(self.scope, self.arrived - 2)
        event = self.incoming()
        with patch("core.runtime.elapsed.time.time", return_value=self.arrived + 600):
            self.runtime.note_conversation_incoming(event)
            message = self.runtime.note_structured_incoming_message(event)
            context = await self.runtime.build_conversation_timing_context(event)
        self.assertEqual(message.timestamp, self.arrived)
        self.assertIn("已过：2秒（2秒）", context)
        self.assertIn("2026-10-09T08:00:00+08:00", self.runtime.format_structured_message_context(event))

    async def test_framework_arrival_excludes_wait_before_plugin_hook(self):
        await self.runtime.record_conversation_delivery(self.scope, self.arrived - 2)
        event = Event(unified_msg_origin=self.scope, sender_id="1", self_id="bot")
        event.created_at = self.arrived
        with patch("core.runtime.elapsed.time.time", return_value=self.arrived + 600):
            self.runtime.note_conversation_incoming(event)
        context = await self.runtime.build_conversation_timing_context(event)
        self.assertIn("已过：2秒（2秒）", context)

    async def test_continuous_bubbles_keep_first_arrival_anchor(self):
        self.runtime._init_continuous_turn_state()
        await self.runtime.record_conversation_delivery(self.scope, self.sent)
        first = self.incoming(message_id="first")
        self.assertTrue(self.runtime.note_continuous_turn_incoming(first))
        # 第一条到达后产生的回包不能把同轮后续气泡的间隔压成几秒。
        await self.runtime.record_conversation_delivery(self.scope, self.arrived + 1)
        second = self.incoming(self.arrived + 3, message_id="second")
        self.assertTrue(self.runtime.note_continuous_turn_incoming(second))
        self.assertEqual(getattr(second, TURN_RECEIVED_AT), self.arrived)
        context = await self.runtime.build_conversation_timing_context(second)
        self.assertIn("8小时30分钟（30600秒）", context)
        self.assertIn("本轮连续消息开始到达：2026-10-09T08:00:00", context)

    async def test_tool_loop_and_new_sends_do_not_rewrite_received_gap(self):
        await self.runtime.record_conversation_delivery(self.scope, self.sent)
        event = self.incoming()
        first = await self.runtime.build_conversation_timing_context(event)
        await self.runtime.record_conversation_delivery(self.scope, self.arrived + 300)
        self.assertEqual(await self.runtime.build_conversation_timing_context(event), first)
        restarted = self.new_runtime()
        self.assertEqual(
            await restarted.build_conversation_timing_context(self.incoming(message_id="other")), first
        )

    async def test_session_isolation_group_scope_and_unknown_history(self):
        await self.runtime.record_conversation_delivery("other:FriendMessage:1", self.sent)
        context = await self.runtime.build_conversation_timing_context(self.incoming())
        self.assertIn("间隔未知", context)
        group = "test:GroupMessage:2"
        await self.runtime.record_conversation_delivery(group, self.sent)
        context = await self.runtime.build_conversation_timing_context(
            self.incoming(scope=group, group_id="2")
        )
        self.assertIn("不能把群内收发间隔归因于当前发言者", context)

    async def test_synthetic_events_and_missing_arrival_do_not_invent_times(self):
        event = self.incoming()
        event.is_proactive_synthetic = True
        self.assertEqual(await self.runtime.build_conversation_timing_context(event), "")
        fresh = Event()
        self.assertEqual(await self.runtime.build_conversation_timing_context(fresh), "")
        fresh.is_proactive_synthetic = True
        self.runtime.note_conversation_incoming(fresh)
        self.assertFalse(hasattr(fresh, RECEIVED_AT))

    async def test_receipts_are_bounded_idempotent_and_order_independent(self):
        for index in reversed(range(40)):
            await self.runtime.record_conversation_delivery(self.scope, self.sent + index)
        await self.runtime.record_conversation_delivery(self.scope, self.sent + 39)
        rows = self.archive._conn.execute("SELECT sent_at FROM conversation_receipts").fetchall()
        self.assertEqual(len(rows), 32)
        self.assertEqual(len(self.runtime.conversation_timing._receipts[self.scope]), 32)
        self.assertEqual(await self.archive.get_conversation_receipt_before(self.scope, self.sent + 20), self.sent + 20)
        self.assertEqual(await self.archive.get_conversation_receipt_before(self.scope, self.sent - 1), 0)
        await self.archive.clear_storage_category("conversation")
        self.runtime.clear_conversation_timing()
        self.assertEqual(await self.archive.get_conversation_receipt_before(self.scope, self.arrived), 0)
        self.assertIn("间隔未知", await self.runtime.build_conversation_timing_context(self.incoming()))

    async def test_successful_send_recorded_before_nickname_lookup_and_duplicates_skipped(self):
        event = self.incoming()
        event.set_result(event.chain_result(["稍后聊"]))
        event._has_send_oper = True

        async def nickname(**kwargs):
            self.assertEqual(getattr(event, SENT_AT), self.arrived + 1)
            self.assertEqual(await self.archive.get_conversation_receipt_before(self.scope, self.arrived + 2), self.arrived + 1)
            return "角色"

        self.runtime.outbound_log.prepare_outbound_bot_name = nickname
        with patch("core.runtime.outbound.time.time", return_value=self.arrived + 1):
            self.assertTrue(await self.runtime.log_outbound_result_async(event))
        with patch("core.runtime.outbound.time.time", return_value=self.arrived + 99):
            self.assertFalse(await self.runtime.log_outbound_result_async(event))
        self.assertEqual(self.runtime.conversation_timing._receipts[self.scope], [self.arrived + 1])

    async def test_failed_or_empty_delivery_never_advances_clock(self):
        event = self.incoming()
        event.set_result(event.chain_result(["发送失败的正文"]))
        event._has_send_oper = False
        self.assertFalse(await self.runtime.log_outbound_result_async(event))
        self.assertIsNone(self.runtime.note_structured_sent_result(event))
        self.assertFalse(await self.runtime.log_outbound_message_async([], scope=self.scope))
        self.assertFalse(hasattr(event, SENT_AT))
        self.assertEqual(await self.archive.get_conversation_receipt_before(self.scope, self.arrived), 0)

    async def test_database_failure_does_not_turn_successful_delivery_into_failure(self):
        self.runtime.archive = types.SimpleNamespace(save_conversation_receipt=AsyncMock(side_effect=OSError()))
        await self.runtime.record_conversation_delivery(self.scope, self.sent)
        context = await self.runtime.build_conversation_timing_context(self.incoming())
        self.assertIn("8小时30分钟", context)

    async def test_slow_receipt_write_yields_to_other_chat_work(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_writer(*args):
            entered.set()
            await release.wait()

        self.runtime.archive = types.SimpleNamespace(save_conversation_receipt=delayed_writer)
        task = asyncio.create_task(self.runtime.record_conversation_delivery(self.scope, self.sent))
        await entered.wait()
        context = await self.runtime.build_conversation_timing_context(self.incoming())
        self.assertIn("8小时30分钟", context)
        release.set()
        await task

    async def test_both_injection_paths_keep_timing_temporary_and_prefix_stable(self):
        runtime = self.runtime
        runtime.resolve_injection_target = AsyncMock(return_value=("2026-10-09", False))
        runtime.ensure_injection_day_data = AsyncMock(return_value=None)
        runtime.maybe_update_injection_outfit = AsyncMock(return_value=None)
        for name in ("_build_injection_memos_context", "format_recent_sight_context", "build_heuristic_memory_context", "build_chat_style_injection_context", "_build_person_fact_injection_context"):
            setattr(runtime, name, AsyncMock(return_value=""))
        runtime.resolve_interaction_context = AsyncMock(return_value=types.SimpleNamespace(format_for_generation=lambda: ""))
        runtime.build_missing_life_context = lambda *a, **k: "暂无生活事实"
        runtime.friend_reference_injection_context = lambda event: ""
        runtime._voice_expression_channel_enabled = lambda event: False
        runtime._part_layer_chain_component._append_visual_input_anchor = lambda req: None
        runtime._append_video_input_anchor = lambda req, event: None
        runtime._part_layer_chain_component._schedule_chat_state_refresh = lambda *args: None
        runtime._part_layer_chain_component._build_available_life_context = AsyncMock(return_value="当前已经起床")
        await runtime.record_conversation_delivery(self.scope, self.sent)
        requests = []
        for data, arrival in ((None, self.arrived), (object(), self.arrived + 3600)):
            runtime.maybe_update_injection_outfit.return_value = data
            req = ProviderRequest(prompt="我回来了", system_prompt="角色原设定")
            req.contexts = [{"role": "assistant", "content": "晚点聊"}]
            await runtime.inject_life_context(req, self.incoming(arrival))
            self.assertIn("[ConversationTiming]", req.extra_user_content_parts[-1].text)
            self.assertTrue(req.extra_user_content_parts[-1].model_dump_for_context()["_no_save"])
            self.assertEqual(req.contexts, [{"role": "assistant", "content": "晚点聊"}])
            requests.append(req)
        self.assertEqual(requests[0].system_prompt, requests[1].system_prompt)
        self.assertNotIn("2026-10", requests[0].system_prompt)
        self.assertIn("9小时30分钟", requests[1].extra_user_content_parts[-1].text)

    async def test_v21_migration_keeps_existing_data_and_is_repeatable(self):
        self.archive._conn.execute("DROP TABLE conversation_receipts")
        self.archive._conn.execute("UPDATE meta SET value = '21' WHERE key = 'schema_version'")
        self.archive._conn.execute("INSERT INTO chat_memory_messages(event_key, session_id, role, message_text, occurred_at) VALUES ('before-upgrade', ?, 'assistant', '已发送', '2026-10-08T23:30:00')", (self.scope,))
        init_schema(self.archive._conn)
        init_schema(self.archive._conn)
        self.assertEqual(await self.archive.get_conversation_receipt_before(self.scope, self.arrived), self.sent)
        from core.archive import SCHEMA_VERSION
        self.assertEqual(self.archive._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0], str(SCHEMA_VERSION))


if __name__ == "__main__":
    unittest.main()
