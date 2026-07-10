import datetime
import re
import sqlite3
import tempfile
import unittest

from support import LifeArchive, LifeSettings
from core.clock import today as life_today
from core.models import (
    ActionDecisionRecord,
    BehaviorFeedbackRecord,
    BehaviorPatternRecord,
    BehaviorSceneRecord,
    ChatSummaryRecord,
    CommitmentRecord,
    DailyReviewRecord,
    DayRecord,
    EmotionArcRecord,
    EmojiAssetRecord,
    EventRecord,
    ExpressionIntentRecord,
    ExpressionProfileRecord,
    ExpressionReviewRecord,
    FocusSlotRecord,
    FocusTargetRecord,
    GroupEnvironmentRecord,
    LifeDecisionRecord,
    LifeEpisodeRecord,
    LifeState,
    LifeEventRecord,
    LifeTermRecord,
    MemoryBoundaryRecord,
    MemoryCorrectionRecord,
    MemoryEvidenceRecord,
    MemoryMaintenanceRecord,
    LongTermMemoryRecord,
    MessageVisibilityRecord,
    PhysiologicalRhythmLogRecord,
    PlaceRecord,
    PreferenceRecord,
    ReversePromptRecord,
    ReplyEffectRecord,
    SessionMidSummaryRecord,
    TemporaryExpressionStateRecord,
    SleepState,
    TimelineItem,
    WeekPlanRecord,
)
from core.archive.categories import STORAGE_CATEGORIES, validate_storage_categories
from core.archive.ddl import iter_schema_sql
from core.sight import SightClip, SightInsight, SightVault


class LifeArchiveSqliteTest(unittest.IsolatedAsyncioTestCase):
    async def test_video_insights_persist_in_daily_life_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            vault = SightVault(archive)
            clip = SightClip(
                scope="aiocqhttp:FriendMessage:10001",
                message_id="m-video-1",
                source="D:/tmp/rain-town.mp4",
                name="rain-town.mp4",
                text="看看这个视频",
            )

            await vault.upsert(
                SightInsight(
                    clip=clip,
                    summary="雨夜古镇小巷里有人撑伞走过",
                    details=["青石板有积水", "灯笼光偏暖"],
                    frame_notes=["第一帧是雨夜街边", "第二帧转到小巷深处"],
                    transcript="镜头里有人说雨停了，可以慢慢走回去。",
                    transcript_source="内置字幕",
                    note="雨夜小巷的慢节奏生活片段。",
                    note_source="内置摘要",
                    metadata={"title": "雨夜古镇", "duration": 18},
                    source_note="文件名：rain-town.mp4",
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            reopened_vault = SightVault(reopened)
            recent = await reopened_vault.recent("aiocqhttp:FriendMessage:10001")

            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0].summary, "雨夜古镇小巷里有人撑伞走过")
            self.assertEqual(recent[0].details, ["青石板有积水", "灯笼光偏暖"])
            self.assertEqual(recent[0].frame_notes, ["第一帧是雨夜街边", "第二帧转到小巷深处"])
            self.assertEqual(recent[0].transcript, "镜头里有人说雨停了，可以慢慢走回去。")
            self.assertEqual(recent[0].transcript_source, "内置字幕")
            self.assertEqual(recent[0].note, "雨夜小巷的慢节奏生活片段。")
            self.assertEqual(recent[0].note_source, "内置摘要")
            self.assertEqual(recent[0].metadata["title"], "雨夜古镇")

            deleted = await reopened_vault.remove_message("aiocqhttp:FriendMessage:10001", "m-video-1")
            self.assertEqual(deleted, 1)
            self.assertEqual(await reopened_vault.recent("aiocqhttp:FriendMessage:10001"), [])
            self.assertEqual(
                reopened._conn.execute("SELECT COUNT(*) AS count FROM video_insight_details").fetchone()["count"],
                0,
            )
            reopened.close()

    async def test_long_term_memories_persist_and_search_with_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            try:
                saved = await archive.upsert_long_term_memory(
                    LongTermMemoryRecord(
                        scope="10001",
                        category="short_term",
                        title="近期少宅家",
                        content="避免每天宅家一整天不出门，傍晚可以安排轻量散步。",
                        source_table="focus_slots",
                        source_id="7",
                        session_id="aiocqhttp:GroupMessage:10001",
                        message_id="m1",
                        date="2026-07-03",
                        confidence=0.9,
                        weight=2.0,
                    )
                )

                self.assertIsNotNone(saved)
                hits = await archive.search_long_term_memories(
                    "宅家 散步",
                    scopes=["10001"],
                    limit=5,
                )
                other_scope_hits = await archive.search_long_term_memories(
                    "宅家 散步",
                    scopes=["20002"],
                    limit=5,
                )

                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].content, "避免每天宅家一整天不出门，傍晚可以安排轻量散步。")
                self.assertEqual(hits[0].source_table, "focus_slots")
                self.assertEqual(hits[0].source_id, "7")
                self.assertEqual(other_scope_hits, [])
            finally:
                archive.close()

    async def test_long_term_memory_quality_tracks_lifecycle_entities_conflicts_and_decisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            try:
                stable = await archive.upsert_long_term_memory(
                    LongTermMemoryRecord(
                        scope="10001",
                        category="preference:life",
                        title="安静生活",
                        content="她长期喜欢安静生活，外出频率不高。",
                        source_table="preferences",
                        source_id="1",
                        date="2026-07-01",
                        weight=1.0,
                    )
                )
                short = await archive.upsert_long_term_memory(
                    LongTermMemoryRecord(
                        scope="10001",
                        category="short_term",
                        title="减少宅家重复",
                        content="最近别总写宅家，可以安排轻量散步。",
                        source_table="focus_slots",
                        source_id="2",
                        date="2026-07-03",
                    )
                )
                decision = await archive.save_life_decision(
                    LifeDecisionRecord(
                        date="2026-07-03",
                        kind="daily_plan",
                        subject="2026-07-03",
                        decision="傍晚轻量散步",
                        reason="最近别总写宅家，同时保持安静生活节奏。",
                        evidence="减少宅家重复；安静生活。",
                        outcome="傍晚走一小圈。",
                    )
                )

                clusters = await archive.get_memory_episode_clusters(limit=5)
                entities = await archive.get_memory_entities(limit=5)
                conflicts = await archive.get_memory_conflicts(limit=5)
                links = await archive.get_memory_decision_sources(decision.id, limit=5)

                self.assertIsNotNone(stable)
                self.assertIsNotNone(short)
                self.assertTrue(short.expires_at or (await archive.list_recent_long_term_memories(limit=5))[0].expires_at)
                self.assertTrue(any(item.title == "减少宅家重复" for item in clusters))
                self.assertTrue(any(item.name in {"安静生活", "减少宅家重复"} for item in entities))
                self.assertTrue(any(item.memory_id == short.id and item.related_memory_id == stable.id for item in conflicts))
                self.assertTrue(any(item["memory_id"] in {stable.id, short.id} for item in links))
            finally:
                archive.close()

    async def test_set_memo_deduplicates_same_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")

            await archive.set_memo("2026-05-24", "下午去书店")
            await archive.set_memo("2026-05-24", "下午去书店")
            await archive.set_memo("2026-05-27", "三天后看展")
            await archive.set_memo("2026-05-26", "后天取快递")

            day = await archive.get_day("2026-05-24")
            future_memos = await archive.get_future_memo_days("2026-05-24", limit=2)
            self.assertEqual(day.memo, "- 下午去书店")
            self.assertEqual([item.date for item in future_memos], ["2026-05-26", "2026-05-27"])
            self.assertEqual(future_memos[0].memo, "- 后天取快递")
            archive.close()

    async def test_expire_stale_reply_effects_marks_old_pending_as_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            stale = await archive.save_reply_effect(
                ReplyEffectRecord(
                    scope="group:10001",
                    reply_text="那我先蹲一下后续",
                    outcome="pending",
                )
            )
            fresh = await archive.save_reply_effect(
                ReplyEffectRecord(
                    scope="group:10001",
                    reply_text="刚发出去的闲时续话",
                    outcome="pending",
                )
            )
            archive._conn.execute(
                "UPDATE reply_effects SET updated_at = datetime('now', '-1 hour') WHERE id = ?",
                (stale.id,),
            )
            archive._conn.commit()

            expired = await archive.expire_stale_reply_effects(30 * 60)

            effects = await archive.get_reply_effects(limit=10)
            by_id = {item.id: item for item in effects}
            self.assertEqual(expired, 1)
            self.assertEqual(by_id[stale.id].outcome, "ignored")
            self.assertIn("没有新的可见回应", by_id[stale.id].evidence)
            self.assertEqual(by_id[fresh.id].outcome, "pending")
            archive.close()

    async def test_life_decisions_persist_and_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            await archive.save_life_decision(
                LifeDecisionRecord(
                    date="2026-06-11",
                    kind="daily_plan",
                    subject="2026-06-11",
                    decision="宅家恢复日",
                    reason="睡眠债偏高，减少外出",
                    evidence="昨日复盘与短期目标",
                    outcome="低强度日程",
                )
            )
            await archive.save_life_decision(
                LifeDecisionRecord(
                    date="2026-06-11",
                    kind="outfit",
                    subject="2026-06-11:afternoon",
                    decision="keep｜浅蓝外套",
                    reason="当前穿搭适合出门",
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            all_items = await reopened.get_life_decisions(limit=10)
            daily_items = await reopened.get_life_decisions(limit=10, kind="daily_plan")

            self.assertEqual([item.kind for item in all_items], ["outfit", "daily_plan"])
            self.assertEqual(daily_items[0].decision, "宅家恢复日")
            self.assertIn("睡眠债", daily_items[0].reason)
            reopened.close()

    async def test_life_decision_evidence_keeps_only_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            older = await archive.save_memory_evidence(
                MemoryEvidenceRecord(
                    target_type="life_decision",
                    target_id="1",
                    evidence_type="decision",
                    source_table="life_decisions",
                    source_id="1",
                    date="2026-06-11",
                    summary="older decision evidence",
                )
            )
            latest = await archive.save_memory_evidence(
                MemoryEvidenceRecord(
                    target_type="life_decision",
                    target_id="2",
                    evidence_type="decision",
                    source_table="life_decisions",
                    source_id="2",
                    date="2026-06-12",
                    summary="latest decision evidence",
                )
            )
            focus = await archive.save_memory_evidence(
                MemoryEvidenceRecord(
                    target_type="focus",
                    target_id="topic",
                    evidence_type="decision",
                    source_table="life_decisions",
                    source_id="2",
                    date="2026-06-12",
                    summary="focus evidence stays visible",
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            visible = await reopened.get_memory_evidence(limit=10)
            life_visible = await reopened.get_memory_evidence(target_type="life_decision", limit=10)
            life_rows = reopened._conn.execute(
                "SELECT target_id FROM memory_evidence WHERE target_type = 'life_decision'"
            ).fetchall()

            self.assertIsNotNone(older)
            self.assertIsNotNone(latest)
            self.assertIsNotNone(focus)
            self.assertEqual([item.summary for item in life_visible], ["latest decision evidence"])
            self.assertIn("focus evidence stays visible", [item.summary for item in visible])
            self.assertEqual([row["target_id"] for row in life_rows], ["2"])
            reopened.close()

    async def test_emotion_arcs_persist_filter_and_expire(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            saved = await archive.save_emotion_arc(
                EmotionArcRecord(
                    scope="group:20001",
                    date="2026-05-24",
                    label="困倦但放松",
                    valence=35,
                    arousal=28,
                    intensity=82,
                    stability=70,
                    trigger="睡前聊天",
                    evidence="刚躺下但仍在轻松接话",
                    influence="更适合短句和低强度安排",
                    expires_at="2099-01-01 00:00:00",
                    source="state",
                )
            )
            await archive.save_emotion_arc(
                EmotionArcRecord(
                    scope="group:20001",
                    label="过期情绪",
                    intensity=80,
                    expires_at="2000-01-01 00:00:00",
                    source="state",
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            active = await reopened.get_emotion_arcs(limit=10, scope="group:20001")
            all_items = await reopened.get_emotion_arcs(limit=10, scope="group:20001", active_only=False)

            self.assertIsNotNone(saved)
            self.assertEqual([item.label for item in active], ["困倦但放松"])
            self.assertEqual(active[0].valence, 35)
            self.assertEqual(active[0].influence, "更适合短句和低强度安排")
            self.assertTrue(any(item.label == "过期情绪" for item in all_items))
            reopened.close()

    async def test_emotion_arcs_keep_only_latest_active_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            older = await archive.save_emotion_arc(
                EmotionArcRecord(
                    scope="group:20001",
                    date="2026-05-24",
                    label="relaxed",
                    intensity=60,
                    trigger="old trigger",
                    evidence="old evidence",
                    influence="old influence",
                    expires_at="2099-01-01 00:00:00",
                )
            )
            latest = await archive.save_emotion_arc(
                EmotionArcRecord(
                    scope="group:20001",
                    date="2026-05-24",
                    label="relaxed",
                    intensity=72,
                    trigger="new trigger",
                    evidence="new evidence",
                    influence="new influence",
                    expires_at="2099-01-01 00:00:00",
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            active = await reopened.get_emotion_arcs(limit=10, scope="group:20001")
            all_items = await reopened.get_emotion_arcs(limit=10, scope="group:20001", active_only=False)

            self.assertIsNotNone(older)
            self.assertIsNotNone(latest)
            self.assertEqual([item.trigger for item in active if item.label == "relaxed"], ["new trigger"])
            status_by_trigger = {item.trigger: item.status for item in all_items}
            self.assertEqual(status_by_trigger["old trigger"], "superseded")
            self.assertEqual(status_by_trigger["new trigger"], "active")
            reopened.close()

    async def test_physiological_rhythm_logs_persist_and_build_trend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            today = datetime.datetime.now().date()
            today_text = today.strftime("%Y-%m-%d")
            yesterday_text = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            saved = await archive.save_physiological_rhythm_log(
                PhysiologicalRhythmLogRecord(
                    date=today_text,
                    source="state",
                    energy_curve="上午慢热，傍晚回升",
                    body_label="轻微疲惫",
                    body_intensity=38,
                    body_source="状态刷新",
                    recovery_actions=["补水", "早点收尾"],
                    social_battery=36,
                    attention_state="低刺激更舒服",
                    summary="身体负荷略高，适合慢一点",
                )
            )
            await archive.save_physiological_rhythm_log(
                PhysiologicalRhythmLogRecord(
                    date=yesterday_text,
                    source="daily_generation",
                    body_label="轻微疲惫",
                    body_intensity=42,
                    social_battery=39,
                    summary="延续轻恢复节奏",
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            logs = await reopened.get_physiological_rhythm_logs(limit=10)
            trend = await reopened.get_physiological_rhythm_trend(days=7, limit=10)
            health = await reopened.get_life_health_report(LifeSettings.from_dict({}).storage)

            self.assertIsNotNone(saved)
            self.assertEqual(logs[0].date, today_text)
            self.assertEqual(logs[0].recovery_actions, ["补水", "早点收尾"])
            self.assertEqual(logs[0].lifecycle_kind, "short_term")
            self.assertIn("平均身体负荷", trend["summary"])
            self.assertGreaterEqual(trend["high_body_days"], 1)
            self.assertTrue(any(item["key"] == "physiological_rhythm_logs" for item in health["checks"]))
            reopened.close()

    async def test_physiological_rhythm_keeps_only_latest_short_term_and_sustained_active(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            today = datetime.datetime.now().date()
            older_date = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            latest_date = today.strftime("%Y-%m-%d")
            older = await archive.save_physiological_rhythm_log(
                PhysiologicalRhythmLogRecord(
                    date=older_date,
                    source="state",
                    body_label="tired",
                    body_intensity=42,
                    social_battery=36,
                    summary="older short rhythm",
                )
            )
            latest = await archive.save_physiological_rhythm_log(
                PhysiologicalRhythmLogRecord(
                    date=latest_date,
                    source="state",
                    body_label="low energy",
                    body_intensity=38,
                    social_battery=34,
                    summary="latest short rhythm",
                )
            )
            older_sustained = await archive.save_physiological_rhythm_log(
                PhysiologicalRhythmLogRecord(
                    date=older_date,
                    source="state",
                    body_label="sleepy",
                    body_intensity=24,
                    social_battery=28,
                    summary="older sustained rhythm",
                    lifecycle_kind="sustained",
                )
            )
            latest_sustained = await archive.save_physiological_rhythm_log(
                PhysiologicalRhythmLogRecord(
                    date=latest_date,
                    source="state",
                    body_label="sleepy",
                    body_intensity=22,
                    social_battery=32,
                    summary="latest sustained rhythm",
                    lifecycle_kind="sustained",
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            active = await reopened.get_physiological_rhythm_logs(limit=10)
            all_items = await reopened.get_physiological_rhythm_logs(limit=10, active_only=False)
            trend = await reopened.get_physiological_rhythm_trend(days=7, limit=10)

            self.assertIsNotNone(older)
            self.assertIsNotNone(latest)
            self.assertIsNotNone(older_sustained)
            self.assertIsNotNone(latest_sustained)
            self.assertEqual([item.summary for item in active if item.lifecycle_kind == "short_term"], ["latest short rhythm"])
            self.assertEqual([item.summary for item in active if item.lifecycle_kind == "sustained"], ["latest sustained rhythm"])
            status_by_summary = {item.summary: item.status for item in all_items}
            self.assertEqual(status_by_summary["older short rhythm"], "superseded")
            self.assertEqual(status_by_summary["latest short rhythm"], "active")
            self.assertEqual(status_by_summary["older sustained rhythm"], "superseded")
            self.assertEqual(status_by_summary["latest sustained rhythm"], "active")
            self.assertEqual(
                [item["summary"] for item in trend["logs"] if item["lifecycle_kind"] == "short_term"],
                ["latest short rhythm"],
            )
            self.assertEqual(
                [item["summary"] for item in trend["logs"] if item["lifecycle_kind"] == "sustained"],
                ["latest sustained rhythm"],
            )
            reopened.close()

    async def test_physiological_rhythm_trend_cache_is_stable_until_new_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            today_text = datetime.datetime.now().date().strftime("%Y-%m-%d")
            await archive.save_physiological_rhythm_log(
                PhysiologicalRhythmLogRecord(
                    date=today_text,
                    source="state",
                    body_label="轻微疲惫",
                    body_intensity=38,
                    social_battery=36,
                    summary="适合低强度恢复",
                )
            )

            first = await archive.get_physiological_rhythm_trend(days=7, limit=10)
            second = await archive.get_physiological_rhythm_trend(days=7, limit=10)
            await archive.save_physiological_rhythm_log(
                PhysiologicalRhythmLogRecord(
                    date=today_text,
                    source="manual",
                    body_label="恢复中",
                    body_intensity=22,
                    social_battery=58,
                    summary="状态回稳",
                )
            )
            third = await archive.get_physiological_rhythm_trend(days=7, limit=10)

            self.assertEqual(first["trend_cache_key"], second["trend_cache_key"])
            self.assertEqual(first["summary"], second["summary"])
            self.assertNotEqual(first["trend_cache_key"], third["trend_cache_key"])
            self.assertIn("updated_at", third)
            archive.close()

    async def test_expression_patterns_and_mid_summary_persist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            await archive.upsert_expression_profile(
                ExpressionProfileRecord(
                    scope="group:10001",
                    profile_id="u1",
                    label="阿林",
                    tone="短句、轻吐槽",
                    habits=["先接梗再补一句", "少用正式解释"],
                    avoid=["不要突然长篇"],
                    evidence="群里连续几次都是轻松接话",
                    confidence=0.82,
                )
            )
            await archive.upsert_behavior_pattern(
                BehaviorPatternRecord(
                    scope="group:10001",
                    scene="熟人轻吐槽",
                    pattern="先顺着语气短回一句，比认真解释更自然。",
                    suggested_action="reply",
                    confidence=0.78,
                    support_count=1,
                    score=1.5,
                    evidence="闲时续话后有人继续回应",
                    last_seen="2026-05-24",
                )
            )
            await archive.upsert_session_mid_summary(
                SessionMidSummaryRecord(
                    session_id="aiocqhttp:GroupMessage:10001",
                    scope_label="看展群",
                    summary="群里在聊下午场门票，气氛轻松。",
                    topic="下午场门票",
                    mood="轻松等人补充",
                    participants=["阿林"],
                    message_count=3,
                    last_message_id="m3",
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            expressions = await reopened.get_expression_profiles(limit=10, scope="group:10001")
            patterns = await reopened.get_behavior_patterns(limit=10, scope="group:10001")
            mids = await reopened.get_session_mid_summaries(limit=10, session_id="aiocqhttp:GroupMessage:10001")

            self.assertEqual(expressions[0].label, "阿林")
            self.assertIn("先接梗", expressions[0].habits[0])
            self.assertEqual(patterns[0].scene, "熟人轻吐槽")
            self.assertEqual(patterns[0].suggested_action, "reply")
            self.assertEqual(mids[0].topic, "下午场门票")
            self.assertEqual(mids[0].participants, ["阿林"])
            reopened.close()

    async def test_recent_group_environments_keep_recent_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            await archive.save_group_environment(
                GroupEnvironmentRecord(
                    session_id="aiocqhttp:GroupMessage:100",
                    group_id="100",
                    group_name="测试群",
                    date="2026-06-16",
                    atmosphere="平稳",
                    topic="旧话题",
                )
            )
            await archive.save_group_environment(
                GroupEnvironmentRecord(
                    session_id="aiocqhttp:GroupMessage:200",
                    group_id="200",
                    group_name="茶话会",
                    date="2026-06-16",
                    atmosphere="活跃",
                    topic="闲聊",
                )
            )
            await archive.save_group_environment(
                GroupEnvironmentRecord(
                    session_id="aiocqhttp:GroupMessage:100",
                    group_id="100",
                    group_name="测试群",
                    date="2026-06-16",
                    atmosphere="冷清",
                    topic="最新话题",
                )
            )

            environments = await archive.get_recent_group_environments(8)

            self.assertEqual([item.group_id for item in environments], ["100", "200", "100"])
            self.assertEqual(environments[0].topic, "最新话题")
            self.assertEqual(environments[2].topic, "旧话题")
            archive.close()

    async def test_recent_visibility_and_decisions_keep_latest_per_scope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            await archive.save_message_visibility(
                MessageVisibilityRecord(
                    session_id="aiocqhttp:GroupMessage:100",
                    sender_profile_id="u1",
                    sender_name="Alice",
                    group_id="100",
                    group_name="测试群",
                    visibility="seen",
                    reason="旧留意",
                )
            )
            await archive.save_message_visibility(
                MessageVisibilityRecord(
                    session_id="aiocqhttp:GroupMessage:200",
                    sender_profile_id="u2",
                    sender_name="Bob",
                    group_id="200",
                    group_name="茶话会",
                    visibility="focused",
                    reason="另一个群",
                )
            )
            await archive.save_message_visibility(
                MessageVisibilityRecord(
                    session_id="aiocqhttp:GroupMessage:100",
                    sender_profile_id="u3",
                    sender_name="Carol",
                    group_id="100",
                    group_name="测试群",
                    visibility="missed",
                    reason="最新留意",
                )
            )
            await archive.save_action_decision(
                ActionDecisionRecord(
                    session_id="aiocqhttp:GroupMessage:100",
                    sender_profile_id="u1",
                    sender_name="Alice",
                    group_id="100",
                    group_name="测试群",
                    action="observe",
                    reason="旧裁定",
                )
            )
            await archive.save_action_decision(
                ActionDecisionRecord(
                    session_id="aiocqhttp:GroupMessage:200",
                    sender_profile_id="u2",
                    sender_name="Bob",
                    group_id="200",
                    group_name="茶话会",
                    action="reply",
                    reason="另一个群",
                )
            )
            await archive.save_action_decision(
                ActionDecisionRecord(
                    session_id="aiocqhttp:GroupMessage:100",
                    sender_profile_id="u3",
                    sender_name="Carol",
                    group_id="100",
                    group_name="测试群",
                    action="skip_memory",
                    reason="最新裁定",
                )
            )

            visibility = await archive.get_recent_message_visibility(8)
            decisions = await archive.get_recent_action_decisions(8)
            raw_decisions = await archive.get_action_decision_records(8)

            self.assertEqual([item.group_id for item in visibility], ["100", "200"])
            self.assertEqual(visibility[0].reason, "最新留意")
            self.assertEqual([item.group_id for item in decisions], ["100", "200"])
            self.assertEqual(decisions[0].reason, "最新裁定")
            self.assertEqual([item.reason for item in raw_decisions[:3]], ["最新裁定", "另一个群", "旧裁定"])
            archive.close()

    async def test_archive_persists_core_records_in_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)

            await archive.save_day(
                DayRecord(
                    date="2026-05-24",
                    outfit="浅蓝外套",
                    timeline=[TimelineItem(time="10:00", activity="写手帐", status="专注")],
                    state=LifeState(
                        energy=45,
                        mood="平稳",
                        mood_score=58,
                        busyness=36,
                        social=42,
                        stress=33,
                        focus=71,
                        sleepiness=28,
                        outgoing=44,
                        emotional_stability=66,
                        interaction_capacity=52,
                        boredom=64,
                        fishing=18,
                        attention_openness=70,
                        watch_state="active_watch",
                        interrupt_level="medium",
                        interrupt_reason="写手帐时会留意相关聊天",
                        sleep=SleepState(quality=61, depth="light_rest", summary="浅休息恢复中"),
                        physiological_rhythm=LifeState.from_value(
                            {
                                "physiological_rhythm": {
                                    "energy_curve": "上午专注，下午轻微回落",
                                    "body_condition": {"label": "轻微疲惫", "intensity": 32, "source": "状态刷新"},
                                    "recovery_actions": ["喝温水", "早点收尾"],
                                    "social_battery": 48,
                                    "attention_state": "低刺激更舒服",
                                    "summary": "适合低强度推进",
                                }
                            }
                        ).physiological_rhythm,
                    ),
                )
            )
            await archive.set_memo("2026-05-24", "下午去书店")
            await archive.save_week_plan(WeekPlanRecord(week_id="2026-W21", theme="慢生活周", goals=["恢复体力"]))
            await archive.add_events(
                "2026-05-24",
                [
                    EventRecord(
                        date="2026-05-24",
                        summary="在书店买了新本子",
                        place="常去书店",
                        people=["阿林"],
                        importance="normal",
                        source="daily",
                    )
                ],
            )
            await archive.touch_places(
                "2026-05-24",
                [PlaceRecord(name="常去书店", type="bookstore", hint="安静看书")],
            )
            await archive.touch_relationship(
                "u1",
                name="阿林",
                note="聊到周末看展",
                date_str="2026-05-24",
                source="chat",
                platform="aiocqhttp",
                user_id="10001",
                alias="阿林",
                persona_hint="男生，死党",
                subjective_name="会让我多看一眼的人",
                subjective_tags=["偶尔嘴硬", "可靠"],
                relationship_story="熟到可以互相吐槽，但仍会认真记住对方的邀约。",
            )
            await archive.add_relationship_point(
                "u1",
                "阿林是男性死党，喜欢看展和书店。",
                date_str="2026-05-24",
                source="chat_memory",
            )
            await archive.save_chat_summary(
                ChatSummaryRecord(
                    session_id="aiocqhttp:FriendMessage:10001",
                    date="2026-05-24",
                    brief="和阿林聊到周末看展",
                    long_summary="阿林提到周末想去展览馆，顺便逛书店。",
                    people=["阿林"],
                    source="chat",
                )
            )
            await archive.save_group_environment(
                GroupEnvironmentRecord(
                    session_id="aiocqhttp:GroupMessage:20001",
                    group_id="20001",
                    group_name="看展小群",
                    date="2026-05-24",
                    atmosphere="平稳",
                    topic="Bob 准备看展",
                    topic_owner="target_user_topic",
                    bot_watch_state="peek",
                    participation_desire=35,
                    complexity_score=42,
                    understanding_confidence=88,
                    deep_analysis_needed=False,
                    summary="群里在聊 Bob 的近况",
                )
            )
            await archive.save_message_visibility(
                MessageVisibilityRecord(
                    session_id="aiocqhttp:GroupMessage:20001",
                    sender_profile_id="10001",
                    sender_name="Alice",
                    group_id="20001",
                    group_name="看展小群",
                    date="2026-05-24",
                    visibility="focused",
                    attention_level=80,
                    freshness="fresh",
                    psychological_freshness=73,
                    reactivated_from_id=2,
                    reactivation_hint="后续有人引用 Bob 时重新关注",
                    reason="包含可沉淀的群友近况",
                )
            )
            await archive.save_action_decision(
                ActionDecisionRecord(
                    session_id="aiocqhttp:GroupMessage:20001",
                    sender_profile_id="10001",
                    sender_name="Alice",
                    group_id="20001",
                    group_name="看展小群",
                    date="2026-05-24",
                    action="save_memory",
                    reason="这是 Bob 的稳定近况",
                    confidence=0.92,
                    scene_type="群友档案",
                    inner_monologue="这像是 Bob 的近况，不该记到 Alice 身上。",
                    reply_strategy="继续观察，必要时轻轻接话。",
                )
            )
            episode = await archive.save_life_episode(
                LifeEpisodeRecord(
                    date="2026-05-24",
                    title="看展话题被记住",
                    summary="Alice 提到 Bob 准备看展，角色选择先观察再沉淀。",
                    kind="group",
                    source="chat_memory",
                    related_people=["Alice", "Bob"],
                    related_places=["看展小群"],
                    impact="后续看见看展话题会更容易重新关注。",
                    confidence=0.9,
                )
            )
            await archive.save_memory_evidence(
                MemoryEvidenceRecord(
                    target_type="life_episode",
                    target_id=str(episode.id),
                    evidence_type="observation",
                    source_table="chat_summaries",
                    source_id="1",
                    session_id="aiocqhttp:GroupMessage:20001",
                    message_id="m1",
                    date="2026-05-24",
                    summary="来自群聊摘要和动作裁定",
                    confidence=0.86,
                )
            )
            await archive.add_behavior_feedback(
                BehaviorFeedbackRecord(
                    date="2026-05-24",
                    scene="群友档案",
                    action="save_memory",
                    feedback="保存到 Bob 而不是 Alice 的档案更准确。",
                    result="positive",
                    score=2.0,
                    reason="避免错误归属",
                )
            )
            await archive.save_emotion_arc(
                EmotionArcRecord(
                    scope="group:20001",
                    date="2026-05-24",
                    label="好奇但克制",
                    valence=24,
                    arousal=42,
                    intensity=66,
                    stability=70,
                    trigger="看展话题",
                    evidence="想蹲后续但没有抢话",
                    influence="更适合观察和短句接话",
                    expires_at="2099-01-01 00:00:00",
                    source="state",
                )
            )
            await archive.save_reply_effect(
                ReplyEffectRecord(
                    scope="group:20001",
                    target_message_id="m1",
                    reply_text="那我先蹲一下后续",
                    outcome="pending",
                    reason="先轻轻接住话题",
                )
            )
            await archive.save_memory_correction(
                MemoryCorrectionRecord(
                    target_type="relationship",
                    target_id="10001",
                    correction="阿林是男性死党。",
                    evidence="人设线索明确说明",
                    confidence=0.92,
                    applied=True,
                )
            )
            await archive.save_expression_review(
                ExpressionReviewRecord(
                    scope="group:20001",
                    reply_text="那我先蹲一下后续",
                    passed=True,
                    reason="轻接不突兀",
                )
            )
            await archive.upsert_behavior_scene(
                BehaviorSceneRecord(
                    scope="group:20001",
                    scene="群里等后续",
                    cues=["有人提到还没讲完的事"],
                    preferred_action="observe",
                    avoid_action="抢话",
                    outcome_hint="先等别人自然接",
                    confidence=0.77,
                    last_seen="2026-05-24",
                )
            )
            await archive.upsert_focus_target(
                FocusTargetRecord(
                    target_type="topic",
                    target_id="看展",
                    label="看展话题",
                    priority=78,
                    reason="最近多人提到，可能形成邀约。",
                    scope="group:20001",
                )
            )
            await archive.upsert_life_term(
                LifeTermRecord(
                    term="蹲后续",
                    meaning="继续围观同一话题是否有人接话。",
                    scope="group:20001",
                    scene="群里等同一话题的新消息",
                    examples=["先蹲后续，看看 Bob 怎么说"],
                    familiarity=72,
                    last_seen="2026-05-24",
                    evidence="群里有人等 Bob 回复",
                )
            )
            await archive.upsert_temporary_expression_state(
                TemporaryExpressionStateRecord(
                    scope="group:20001",
                    label="只想轻轻围观",
                    tone="短句、少解释",
                    reason="群里还在接话，我暂时不想抢话",
                    intensity=64,
                    expires_at="2026-05-25",
                )
            )
            await archive.upsert_focus_slot(
                FocusSlotRecord(
                    scope="group:20001",
                    focus_key="bob_followup",
                    label="Bob 的后续",
                    priority=76,
                    reason="刚才话题还没落稳",
                    expires_at="2026-05-25",
                )
            )
            await archive.save_expression_intent(
                ExpressionIntentRecord(
                    scope="group:20001",
                    message_id="m1",
                    emotion="好奇但克制",
                    emotion_category="happy",
                    emoji_intent="轻轻围观",
                    action_intent="先探头看一眼",
                    send_emoji=False,
                    reason="文字更自然",
                )
            )
            await archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash="emoji-hash-1",
                    file_path="C:/tmp/emoji.png",
                    label="探头",
                    description="适合轻轻围观",
                    emotions=["好奇", "围观"],
                    status="ready",
                    source_url="https://example.com/emoji.png",
                    source_kind="trusted",
                    asset_type="sticker",
                    confidence=0.92,
                    sendable=True,
                )
            )
            await archive.save_memory_maintenance(
                MemoryMaintenanceRecord(
                    date="2026-05-24",
                    summary="维护完成",
                    corrected_count=1,
                    reason="测试长期维护记录",
                )
            )
            await archive.set_memory_boundary(
                MemoryBoundaryRecord(
                    source_scope="group:20001",
                    target_scope="private:10001",
                    policy="ask",
                    reason="群聊里的第三人信息不要直接带到私聊。",
                )
            )
            same_scope_boundary = await archive.set_memory_boundary(
                {
                    "source_scope": "group:20001",
                    "target_scope": "group:20001",
                    "policy": "allow",
                    "reason": "群聊内部讨论",
                }
            )
            self.assertIsNone(same_scope_boundary)
            archive.close()

            reopened = LifeArchive(db_path)
            day = await reopened.get_day("2026-05-24")
            plans = await reopened.get_all_week_plans()
            events = await reopened.get_recent_events(10)
            places = await reopened.get_recent_places(10)
            relationships = await reopened.get_recent_relationships(10)
            summaries = await reopened.get_recent_chat_summaries(10)
            environments = await reopened.get_recent_group_environments(10)
            visibility = await reopened.get_recent_message_visibility(10)
            decisions = await reopened.get_recent_action_decisions(10)
            episodes = await reopened.get_life_episodes(10)
            evidence = await reopened.get_memory_evidence(target_type="life_episode", limit=10)
            feedback = await reopened.get_behavior_feedback(10)
            emotion_arcs = await reopened.get_emotion_arcs(10, scope="group:20001")
            reply_effects = await reopened.get_reply_effects(10)
            corrections = await reopened.get_memory_corrections(10)
            expression_reviews = await reopened.get_expression_reviews(10)
            behavior_scenes = await reopened.get_behavior_scenes(10, scope="group:20001")
            focus = await reopened.get_focus_targets(10)
            focus_slots = await reopened.get_focus_slots(10, scope="group:20001", active_only=False)
            expression_intents = await reopened.get_expression_intents(10, scope="group:20001")
            emoji_assets = await reopened.get_emoji_assets(10, status="ready")
            maintenance = await reopened.get_memory_maintenance(10)
            terms = await reopened.get_life_terms(10)
            temp_states = await reopened.get_temporary_expression_states(10, scope="group:20001", active_only=False)
            boundaries = await reopened.get_memory_boundaries(10)
            health = await reopened.get_life_health_report(LifeSettings.from_dict({}).storage)

            self.assertIn("下午去书店", day.memo)
            self.assertEqual(day.state.energy, 45)
            self.assertEqual(day.state.mood_score, 58)
            self.assertEqual(day.state.stress, 33)
            self.assertEqual(day.state.focus, 71)
            self.assertEqual(day.state.sleepiness, 28)
            self.assertEqual(day.state.outgoing, 44)
            self.assertEqual(day.state.emotional_stability, 66)
            self.assertEqual(day.state.interaction_capacity, 52)
            self.assertEqual(day.state.boredom, 64)
            self.assertEqual(day.state.fishing, 18)
            self.assertEqual(day.state.attention_openness, 70)
            self.assertEqual(day.state.watch_state, "active_watch")
            self.assertEqual(day.state.interrupt_level, "medium")
            self.assertEqual(day.state.interrupt_reason, "写手帐时会留意相关聊天")
            self.assertEqual(day.state.sleep.depth, "light_rest")
            self.assertEqual(day.state.sleep.quality, 61)
            self.assertEqual(day.state.physiological_rhythm.energy_curve, "上午专注，下午轻微回落")
            self.assertEqual(day.state.physiological_rhythm.body_condition.label, "轻微疲惫")
            self.assertEqual(day.state.physiological_rhythm.body_condition.intensity, 32)
            self.assertEqual(day.state.physiological_rhythm.recovery_actions, ["喝温水", "早点收尾"])
            self.assertEqual(day.state.physiological_rhythm.social_battery, 48)
            self.assertEqual(day.state.physiological_rhythm.attention_state, "低刺激更舒服")
            self.assertEqual(plans["2026-W21"].theme, "慢生活周")
            self.assertEqual(events[0].summary, "在书店买了新本子")
            self.assertEqual(events[0].people, ["阿林"])
            self.assertEqual(places[0].name, "常去书店")
            self.assertEqual(places[0].visits, 1)
            self.assertEqual(relationships[0].name, "阿林")
            self.assertEqual(relationships[0].platform, "aiocqhttp")
            self.assertEqual(relationships[0].user_id, "10001")
            self.assertEqual(relationships[0].persona_hint, "男生，死党")
            self.assertEqual(relationships[0].subjective_name, "会让我多看一眼的人")
            self.assertEqual(relationships[0].subjective_tags, ["偶尔嘴硬", "可靠"])
            self.assertIn("互相吐槽", relationships[0].relationship_story)
            self.assertEqual(relationships[0].notes[0].content, "聊到周末看展")
            self.assertEqual(relationships[0].memory_points[0].content, "阿林是男性死党，喜欢看展和书店。")
            self.assertEqual(summaries[0].brief, "和阿林聊到周末看展")
            self.assertEqual(summaries[0].people, ["阿林"])
            self.assertEqual(environments[0].group_name, "看展小群")
            self.assertEqual(environments[0].topic_owner, "target_user_topic")
            self.assertEqual(environments[0].participation_desire, 35)
            self.assertEqual(environments[0].complexity_score, 42)
            self.assertEqual(environments[0].understanding_confidence, 88)
            self.assertEqual(visibility[0].visibility, "focused")
            self.assertEqual(visibility[0].freshness, "fresh")
            self.assertEqual(visibility[0].psychological_freshness, 73)
            self.assertEqual(visibility[0].reactivated_from_id, 2)
            self.assertIn("重新关注", visibility[0].reactivation_hint)
            self.assertEqual(decisions[0].action, "save_memory")
            self.assertIn("Bob 的近况", decisions[0].inner_monologue)
            self.assertIn("继续观察", decisions[0].reply_strategy)
            self.assertEqual(episodes[0].title, "看展话题被记住")
            self.assertEqual(episodes[0].related_people, ["Alice", "Bob"])
            self.assertEqual(episodes[0].related_places, ["看展小群"])
            self.assertEqual(evidence[0].summary, "来自群聊摘要和动作裁定")
            self.assertEqual(feedback[0].result, "positive")
            self.assertEqual(emotion_arcs[0].label, "好奇但克制")
            self.assertEqual(emotion_arcs[0].influence, "更适合观察和短句接话")
            self.assertEqual(reply_effects[0].outcome, "pending")
            self.assertEqual(corrections[0].correction, "阿林是男性死党。")
            self.assertTrue(corrections[0].applied)
            self.assertTrue(expression_reviews[0].passed)
            self.assertEqual(behavior_scenes[0].scene, "群里等后续")
            self.assertEqual(focus[0].label, "看展话题")
            self.assertEqual(focus_slots[0].label, "Bob 的后续")
            self.assertEqual(expression_intents[0].emotion, "好奇但克制")
            self.assertEqual(expression_intents[0].emotion_category, "happy")
            self.assertEqual(emoji_assets[0].label, "探头")
            self.assertEqual(emoji_assets[0].source_url, "https://example.com/emoji.png")
            self.assertEqual(emoji_assets[0].source_kind, "trusted")
            self.assertEqual(emoji_assets[0].asset_type, "sticker")
            self.assertAlmostEqual(emoji_assets[0].confidence, 0.92)
            self.assertTrue(emoji_assets[0].sendable)
            self.assertEqual(maintenance[0].corrected_count, 1)
            self.assertEqual(terms[0].term, "蹲后续")
            self.assertEqual(terms[0].scene, "群里等同一话题的新消息")
            self.assertEqual(terms[0].examples, ["先蹲后续，看看 Bob 怎么说"])
            self.assertEqual(terms[0].familiarity, 72)
            self.assertEqual(temp_states[0].label, "只想轻轻围观")
            self.assertEqual(temp_states[0].intensity, 64)
            self.assertEqual(boundaries[0].policy, "ask")
            self.assertGreaterEqual(health["score"], 80)
            reopened.close()

    async def test_revise_relationship_profile_persists_semantic_calibration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            await archive.touch_relationship(
                "u1",
                name="阿林",
                note="聊到周末看展",
                date_str="2026-05-24",
                source="chat",
                persona_hint="男生，死党",
                subjective_name="会让我多看一眼的人",
                subjective_tags=["可靠"],
                relationship_story="我和阿林熟到可以互相吐槽，她也会认真记住邀约。",
            )
            await archive.revise_relationship_profile(
                "u1",
                date_str="2026-05-25",
                subjective_name="男死党阿林",
                subjective_tags=["可靠", "爽朗"],
                relationship_story="我和阿林熟到可以互相吐槽，他也会认真记住邀约。",
                note="按人设线索校准为男性死党。",
                relationship_points=["阿林是我的男死党，喜欢看展。"],
            )
            archive.close()

            reopened = LifeArchive(db_path)
            relationship = await reopened.get_relationship("u1")

            self.assertEqual(relationship.subjective_name, "男死党阿林")
            self.assertEqual(relationship.subjective_tags, ["可靠", "爽朗"])
            self.assertEqual(relationship.relationship_story, "我和阿林熟到可以互相吐槽，他也会认真记住邀约。")
            self.assertEqual(relationship.notes[-1].content, "按人设线索校准为男性死党。")
            self.assertEqual(relationship.memory_points[-1].content, "阿林是我的男死党，喜欢看展。")
            reopened.close()

    async def test_touch_relationship_replaces_generic_name_with_real_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            await archive.touch_relationship("10000001", name="用户", note="提出邀约：出门游玩", date_str="2026-06-20")
            await archive.touch_relationship("10000001", name="小林", note="继续聊出门安排", date_str="2026-06-20")

            relationship = await archive.get_relationship("10000001")

            self.assertEqual(relationship.name, "小林")
            self.assertEqual(relationship.interactions, 2)
            archive.close()

    async def test_memory_boundaries_skip_same_scope_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            await archive.set_memory_boundary(
                MemoryBoundaryRecord(
                    source_scope="group:20001",
                    target_scope="private:10001",
                    policy="ask",
                    reason="群聊内容跨到私聊前要谨慎。",
                )
            )
            blocked = await archive.set_memory_boundary(
                {
                    "source_scope": "group:20001",
                    "target_scope": "group:20001",
                    "policy": "allow",
                    "reason": "群聊内部讨论",
                }
            )
            self.assertIsNone(blocked)
            archive._conn.execute(
                """
                INSERT INTO memory_boundaries(
                    source_scope, target_scope, policy, reason, enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                ("group:1509160", "group:1509160", "allow", "群聊内部讨论"),
            )
            archive._conn.commit()

            boundaries = await archive.get_memory_boundaries(10, enabled_only=False)

            self.assertEqual(len(boundaries), 1)
            self.assertEqual(boundaries[0].target_scope, "private:10001")
            archive.close()

    async def test_archive_cleanup_and_reset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            old_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            await archive.save_day(
                DayRecord(date=old_date, timeline=[TimelineItem(time="09:00", activity="旧日程")])
            )
            await archive.save_day(
                DayRecord(date=today, timeline=[TimelineItem(time="09:00", activity="今日程")])
            )

            await archive.cleanup_storage_category("日常记录", keep_days=14)

            self.assertIsNone(await archive.get_day(old_date))
            self.assertTrue(await archive.get_day(today))

            await archive.reset_all()

            self.assertIsNone(await archive.get_day(today))
            self.assertEqual(await archive.get_all_week_plans(), {})
            self.assertEqual(await archive.get_recent_events(10), [])
            self.assertEqual(await archive.get_recent_places(10), [])
            self.assertEqual(await archive.get_recent_relationships(10), [])
            self.assertEqual(await archive.get_recent_chat_summaries(10), [])
            self.assertEqual(await archive.get_recent_group_environments(10), [])
            self.assertEqual(await archive.get_recent_message_visibility(10), [])
            self.assertEqual(await archive.get_recent_action_decisions(10), [])
            archive.close()

    async def test_focus_targets_default_query_excludes_expired_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            today = life_today()
            await archive.upsert_focus_target(
                FocusTargetRecord(
                    target_type="topic",
                    target_id="过期话题",
                    label="过期话题",
                    priority=90,
                    expires_at=(today - datetime.timedelta(days=1)).isoformat(),
                )
            )
            await archive.upsert_focus_target(
                FocusTargetRecord(
                    target_type="topic",
                    target_id="当前话题",
                    label="当前话题",
                    priority=80,
                    expires_at=(today + datetime.timedelta(days=1)).isoformat(),
                )
            )

            active = await archive.get_focus_targets(10)
            all_items = await archive.get_focus_targets(10, include_expired=True)

            self.assertEqual([item.label for item in active], ["当前话题"])
            self.assertEqual({item.label for item in all_items}, {"当前话题", "过期话题"})
            archive.close()

    async def test_storage_categories_report_counts_and_clear_category(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            await archive.save_day(
                DayRecord(
                    date="2026-05-24",
                    timeline=[TimelineItem(time="09:00", activity="整理书桌")],
                    state=LifeState(energy=66),
                )
            )
            await archive.save_daily_review(
                DailyReviewRecord(date="2026-05-24", summary="今天节奏稳定。")
            )
            await archive.touch_relationship(
                "u1",
                name="阿林",
                note="聊到周末看展",
                date_str="2026-05-24",
            )
            await archive.touch_places(
                "2026-05-24",
                [PlaceRecord(name="常去书店", type="bookstore", hint="安静看书")],
            )
            await archive.save_group_environment(
                GroupEnvironmentRecord(
                    session_id="aiocqhttp:GroupMessage:20001",
                    group_id="20001",
                    group_name="看展小群",
                    date="2026-05-24",
                    atmosphere="平稳",
                    topic="周末看展",
                )
            )
            await archive.add_behavior_feedback(
                BehaviorFeedbackRecord(
                    date="2026-05-24",
                    scene="闲时续话",
                    action="reply",
                    feedback="回复后对方继续聊了",
                    result="positive",
                    score=1.0,
                )
            )

            overview = await archive.get_storage_overview(LifeSettings.from_dict({}).storage)
            categories = {item["key"]: item for item in overview["categories"]}
            memory_groups = {
                item["key"]: item
                for item in categories["world"]["groups"]
            }

            self.assertGreater(categories["daily"]["total_rows"], 0)
            self.assertGreater(categories["relationships"]["total_rows"], 0)
            self.assertGreater(categories["world"]["total_rows"], 0)
            self.assertGreater(categories["conversation"]["total_rows"], 0)
            self.assertGreater(categories["experience"]["total_rows"], 0)
            self.assertGreater(categories["review"]["total_rows"], 0)
            self.assertEqual(categories["daily"]["retention_days"], 30)
            self.assertGreater(memory_groups["places"]["total_rows"], 0)

            result = await archive.clear_storage_category("日常记录")

            self.assertEqual(result["category"], "daily")
            self.assertGreater(result["deleted_rows"], 0)
            self.assertIsNone(await archive.get_day("2026-05-24"))
            self.assertIsNotNone(await archive.get_daily_review("2026-05-24"))
            archive.close()

    def test_storage_categories_cover_all_business_tables(self):
        schema_source = "\n".join(iter_schema_sql())
        schema_tables = {
            match.group(1)
            for match in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)", schema_source)
        }
        validate_storage_categories(schema_tables, ignored_tables={"meta"})

    def test_storage_category_groups_stay_inside_category_tables(self):
        for category in STORAGE_CATEGORIES.values():
            grouped_tables = [
                table
                for group in category.groups
                for table in group.tables
            ]

            self.assertEqual(len(grouped_tables), len(set(grouped_tables)), category.key)
            self.assertEqual(set(grouped_tables) - set(category.tables), set(), category.key)

    def test_storage_categories_have_clean_table_boundaries(self):
        schema_source = "\n".join(iter_schema_sql())
        schema_tables = {
            match.group(1)
            for match in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)", schema_source)
        }
        table_owners = {}
        for category in STORAGE_CATEGORIES.values():
            table_set = set(category.tables)
            clear_set = set(category.clear_order)
            self.assertEqual(table_set - clear_set, set(), category.key)
            self.assertEqual(clear_set - schema_tables, set(), category.key)
            for table in category.tables:
                self.assertIn(table, schema_tables, table)
                self.assertNotIn(table, table_owners, table)
                table_owners[table] = category.key

        self.assertEqual(schema_tables - set(table_owners), {"meta"})
        self.assertEqual(set(table_owners) - schema_tables, set())

    async def test_new_schema_does_not_create_removed_guardian_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            tables = {
                row["name"]
                for row in archive._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }

            self.assertFalse({table for table in tables if table.startswith("guardian_")})
            archive.close()

    async def test_reverse_prompts_persist_and_cleanup_in_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            saved = await archive.save_reverse_prompt(
                ReversePromptRecord(
                    scope="aiocqhttp:FriendMessage:10001",
                    prompt="窗边热茶，暖色台灯，浅景深生活照",
                    image_path=f"{tmpdir}/reverse/reverse_reference_demo.png",
                    title="窗边热茶",
                    keywords=["热茶", "台灯"],
                    ratio="4:3",
                    usage="文生图",
                    profile="生活照",
                    source_prompt="保留窗边暖光",
                )
            )

            self.assertIsNotNone(saved)
            latest = await archive.get_latest_reverse_prompt("aiocqhttp:FriendMessage:10001")
            self.assertIsNotNone(latest)
            self.assertEqual(latest.prompt, "窗边热茶，暖色台灯，浅景深生活照")
            self.assertEqual(latest.keywords, ["热茶", "台灯"])
            self.assertEqual(latest.profile, "生活照")

            old_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d 00:00:00")
            archive._conn.execute("UPDATE reverse_prompts SET created_at = ?", (old_date,))
            archive._conn.commit()
            deleted = await archive.cleanup_reverse_prompts(7)

            self.assertEqual(deleted, 1)
            self.assertIsNone(await archive.get_latest_reverse_prompt("aiocqhttp:FriendMessage:10001"))
            archive.close()

    async def test_storage_policy_cleanup_uses_configured_retention(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            old_date = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
            recent_date = datetime.datetime.now().strftime("%Y-%m-%d")
            await archive.save_day(DayRecord(date=old_date, timeline=[TimelineItem(time="09:00", activity="旧日程")]))
            await archive.save_day(DayRecord(date=recent_date, timeline=[TimelineItem(time="09:00", activity="今日程")]))
            await archive.save_daily_review(DailyReviewRecord(date=old_date, summary="旧复盘"))
            await archive.save_chat_summary(
                ChatSummaryRecord(
                    session_id="aiocqhttp:FriendMessage:10001",
                    date=old_date,
                    brief="旧聊天摘要",
                    long_summary="很久以前的聊天摘要",
                )
            )
            policy = LifeSettings.from_dict(
                {
                    "storage_config": {
                        "daily_keep_days": 30,
                        "review_keep_days": 30,
                        "conversation_keep_days": 30,
                        "planning_keep_days": 0,
                    }
                }
            ).storage

            result = await archive.cleanup_by_storage_policy(policy)

            self.assertGreaterEqual(result["deleted_rows"], 3)
            self.assertIsNone(await archive.get_day(old_date))
            self.assertIsNotNone(await archive.get_day(recent_date))
            self.assertIsNone(await archive.get_daily_review(old_date))
            self.assertEqual(await archive.get_recent_chat_summaries(10), [])
            archive.close()

    async def test_archive_schema_has_no_json_payload_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            forbidden = {"payload", "people", "notes"}
            for table in ("days", "week_plans", "events", "relationships"):
                columns = {
                    row["name"]
                    for row in archive._conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                self.assertFalse(columns & forbidden, table)
            archive.close()

    async def test_behavior_feedback_deduplicates_identical_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(f"{tmpdir}/daily_life.db")
            record = BehaviorFeedbackRecord(
                date="2026-06-22",
                target_type="proactive_session",
                target_id="aiocqhttp:FriendMessage:10001",
                scene="闲时回复读空气",
                action="闲时续话",
                feedback="闲时续话后会话继续有新回应",
                result="positive",
                score=1.0,
                reason="后续消息：还在",
                source="proactive_reply",
            )

            first = await archive.add_behavior_feedback(record)
            second = await archive.add_behavior_feedback(
                BehaviorFeedbackRecord.from_value({**record.as_dict(), "id": 0})
            )
            feedback = await archive.get_behavior_feedback(10)

            self.assertIsNotNone(first)
            self.assertEqual(second.id, first.id)
            self.assertEqual(len(feedback), 1)
            archive.close()

    async def test_day_meta_persists_autonomous_life_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            await archive.save_day(
                DayRecord(
                    date="2026-05-24",
                    outfit="宽松白色长T恤",
                    timeline=[TimelineItem(time="10:00", activity="补觉醒来", status="困倦")],
                    meta={
                        "life_mode": "late_night",
                        "sleep_mode": "late_night",
                        "plan_outfit_decision": "outdoor",
                        "outfit_decision": "keep",
                        "schedule_type": "宅家充电的慵懒一日",
                        "schedule_intent": "rest",
                        "theme": "慢恢复日",
                    },
                )
            )
            archive.close()

            reopened = LifeArchive(db_path)
            day = await reopened.get_day("2026-05-24")

            self.assertEqual(day.meta["life_mode"], "late_night")
            self.assertEqual(day.meta["sleep_mode"], "late_night")
            self.assertEqual(day.meta["plan_outfit_decision"], "outdoor")
            self.assertEqual(day.meta["outfit_decision"], "keep")
            self.assertEqual(day.meta["schedule_type"], "宅家充电的慵懒一日")
            self.assertEqual(day.meta["schedule_intent"], "rest")
            self.assertEqual(day.meta["theme"], "慢恢复日")
            reopened.close()

    async def test_lifecycle_records_persist_in_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)
            await archive.save_day(
                DayRecord(
                    date="2026-05-24",
                    timeline=[TimelineItem(time="09:00", activity="整理手帐", status="平静")],
                )
            )
            await archive.save_daily_review(
                DailyReviewRecord(
                    date="2026-05-24",
                    summary="今天适合低强度恢复。",
                    memory_points=["雨天更愿意待在室内。"],
                    preference_points=[
                        PreferenceRecord(
                            category="activity",
                            content="雨天偏好室内低强度活动",
                            weight=1.2,
                            evidence="今日复盘",
                        )
                    ],
                    sleep_debt_delta=0.4,
                    energy_carryover=52,
                    life_events=[
                        LifeEventRecord(
                            date="2026-05-24",
                            title="买了新的手帐贴纸",
                            detail="适合接下来几天整理手帐。",
                            effect="可能增加居家整理类日程",
                        )
                    ],
                )
            )
            await archive.replace_day_timeline(
                "2026-05-24",
                [TimelineItem(time="10:30", activity="重排时间轴", status="专注")],
            )
            archive.close()

            reopened = LifeArchive(db_path)
            review = await reopened.get_daily_review("2026-05-24")
            preferences = await reopened.get_preferences(10)
            life_events = await reopened.get_life_events(limit=10)
            day = await reopened.get_day("2026-05-24")

            self.assertEqual(review.summary, "今天适合低强度恢复。")
            self.assertEqual(review.memory_points, ["雨天更愿意待在室内。"])
            self.assertEqual(preferences[0].content, "雨天偏好室内低强度活动")
            self.assertEqual(life_events[0].title, "买了新的手帐贴纸")
            self.assertEqual(day.timeline[0].time, "10:30")
            reopened.close()

    async def test_commitments_persist_and_link_to_day(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/daily_life.db"
            archive = LifeArchive(db_path)

            saved = await archive.save_commitment(
                CommitmentRecord(
                    content="周末一起看电影",
                    kind="plan",
                    trigger_date="2026-05-30",
                    time_window="weekend",
                    people=["阿林"],
                    confidence=0.9,
                    source="chat",
                )
            )
            await archive.link_commitments_to_day("2026-05-30", [saved.id])
            archive.close()

            reopened = LifeArchive(db_path)
            due = await reopened.get_due_commitments("2026-05-30", include_scheduled=True)
            active = await reopened.get_commitments(status="active")
            scheduled = await reopened.get_commitments(status="scheduled")

            self.assertEqual(active, [])
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].content, "周末一起看电影")
            self.assertEqual(due[0].people, ["阿林"])
            self.assertEqual(scheduled[0].id, saved.id)
            reopened.close()
