import asyncio
import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import LifeArchive, LifeSettings, DayRecord
from core.models import WeatherInfo
from core.life.dressing import (
    digest,
    suitability_key,
    reviewed_candidates,
    validate_aesthetics,
    wardrobe_conditions,
)
from core.life.inspiration import StyleCatalogMixin
from core.runtime.clothier import ClothierMixin
from core.runtime.channel.stylist import RuntimeStyleCatalogMixin
from core.runtime.background import BackgroundTaskMixin


class Runtime(ClothierMixin, RuntimeStyleCatalogMixin, BackgroundTaskMixin):
    def __init__(self, archive):
        self.archive = archive
        self.now = datetime.datetime(2026, 10, 8, 12)
        self.config = LifeSettings.from_dict(
            {
                "life_domain_config": {"home_address": "现实居住地"},
                "image_generation_config": {"creative_wardrobe": {"enabled": True}},
            }
        )
        self.mark_page_status_changed = AsyncMock()
        self.get_persona_text = AsyncMock(return_value="喜欢低饱和颜色与舒适的针织服装")
        self.get_text_provider = AsyncMock(return_value=object())
        self.call_text_model = AsyncMock()
        self.close_text_session = AsyncMock()
        self.media = SimpleNamespace(
            image=SimpleNamespace(
                first_configured_character_reference_image=lambda: "/tmp/character.png",
                can_edit_image=lambda: True,
                resume_async_image=AsyncMock(),
            )
        )
        self._edit_life_image_with_policy_retry = AsyncMock()
        self._generate_life_image_with_policy_retry = AsyncMock()
        self._learn_style_catalog_image = AsyncMock()
        self._validate_wardrobe_acquisition = AsyncMock(return_value=True)

    def _runtime_now(self):
        return self.now

    async def resolve_injection_target(self, now):
        return "2026-10-08", False

    def _event_session_id(self, event):
        return getattr(event, "unified_msg_origin", "private:test")

    def _event_message_id(self, event):
        return getattr(event, "message_id", "message-one")


class WardrobeLifeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.tmp.name) / "life.db")
        self.runtime = Runtime(self.archive)
        self.now = self.runtime.now

    async def asyncTearDown(self):
        await self.runtime._cancel_background_tasks()
        self.archive.close()
        self.tmp.cleanup()

    async def item(self, kind="outfit", seed=1, used=False, attrs=None):
        result = await self.archive.upsert_style_catalog_item(
            {
                "kind": kind,
                "source_image_hash": f"{seed:064x}",
                "description": "浅色针织上衣与长裤",
                "confidence": 0.95,
                "attributes": attrs or {},
            }
        )
        if used:
            await self.archive.mark_style_catalog_used([result.id])
        return result

    async def day(self, ids, at=None):
        day = DayRecord(
            date="2026-10-08", outfit="已确认穿搭", weather_info=WeatherInfo(temp=13)
        )
        day.meta.update(
            style_catalog_reference_ids=",".join(map(str, ids)),
            outfit_fact_confirmed_at=(at or self.now).isoformat(sep=" "),
        )
        await self.archive.save_day(day)
        return day

    async def test_bootstrap_only_actual_use_establishes_ownership(self):
        a = await self.item(used=True)
        b = await self.item(seed=2)
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(state["items"][str(a.id)]["ownership"], "owned")
        self.assertEqual(state["items"][str(b.id)]["ownership"], "candidate")

    async def test_outfit_and_separate_pieces_share_units(self):
        suit = await self.item()
        top = await self.item(kind="top")
        bottom = await self.item(kind="bottom")
        await self.archive.adopt_wardrobe_items(
            [suit.id], event_id="adopt", at=str(self.now), reason="正式纳入"
        )
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(
            set(state["items"][str(suit.id)]["unit_ids"]),
            set(
                state["items"][str(top.id)]["unit_ids"]
                + state["items"][str(bottom.id)]["unit_ids"]
            ),
        )
        self.assertEqual(state["items"][str(top.id)]["ownership"], "owned")

    async def test_reanalysis_cannot_erase_laundry_or_ownership(self):
        item = await self.item(used=True)
        await self.archive.start_wardrobe_care(
            [item.id],
            decision="dirty",
            event_id="dirty",
            at=str(self.now),
            observer="x",
            duration_minutes=30,
            reason="已完成运动",
        )
        await self.archive.upsert_style_catalog_item(
            {
                "kind": "outfit",
                "source_image_hash": f"{1:064x}",
                "description": "重新识别的衣服",
                "confidence": 0.95,
                "attributes": {"seasons": ["秋季"]},
            }
        )
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(item.id)]
        self.assertEqual(state["condition"], "dirty")
        self.assertEqual(state["ownership"], "owned")

    async def test_wear_receipt_is_idempotent_and_does_not_wash_current_clothes(self):
        item = await self.item()
        day = await self.day([item.id])
        self.assertTrue(await self.archive.record_wardrobe_wear(day, event_id="worn"))
        self.assertFalse(await self.archive.record_wardrobe_wear(day, event_id="worn"))
        await self.archive.start_wardrobe_care(
            [item.id],
            decision="dirty",
            event_id="d",
            at=str(self.now),
            observer="x",
            duration_minutes=30,
            reason="运动后脏了",
        )
        self.assertFalse(
            await self.archive.start_wardrobe_care(
                [item.id],
                decision="washing",
                event_id="w",
                at=str(self.now),
                observer="x",
                duration_minutes=30,
                reason="清洗",
            )
        )
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(item.id)]
        self.assertTrue(state["currently_worn"])

    async def test_old_wear_receipt_cannot_revert_current_clothes(self):
        first = await self.item()
        second = await self.item(seed=2)
        old = await self.day([first.id], self.now - datetime.timedelta(hours=1))
        current = await self.day([second.id])
        await self.archive.record_wardrobe_wear(current, event_id="latest")
        self.assertFalse(
            await self.archive.record_wardrobe_wear(old, event_id="late-old")
        )
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(state["items"][str(second.id)]["condition"], "worn")

    async def test_laundry_offline_time_does_not_become_execution(self):
        item = await self.item(used=True)
        await self.archive.start_wardrobe_care(
            [item.id],
            decision="dirty",
            event_id="d",
            at=str(self.now),
            observer="a",
            duration_minutes=5,
            reason="脏衣",
        )
        await self.archive.start_wardrobe_care(
            [item.id],
            decision="washing",
            event_id="w",
            at=str(self.now),
            observer="a",
            duration_minutes=5,
            reason="开始洗衣",
        )
        self.assertEqual(
            await self.archive.advance_wardrobe_care(
                now=self.now + datetime.timedelta(hours=2), observer="b"
            ),
            0,
        )
        for minute in range(1, 6):
            await self.archive.advance_wardrobe_care(
                now=self.now + datetime.timedelta(hours=2, minutes=minute), observer="b"
            )
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(item.id)]
        self.assertEqual(state["condition"], "drying")
        self.assertFalse(state["available"])

    async def test_candidate_and_washing_clothes_rejected_for_autonomous_selection(
        self,
    ):
        item = await self.item()
        await self.archive.save_wardrobe_review(
            {"aesthetics": []}, expected_revision=0, at=str(self.now)
        )
        composer = SimpleNamespace(archive=self.archive)

        class Composer(StyleCatalogMixin):
            pass

        composer = Composer()
        composer.archive = self.archive
        appearance, reason = await composer._style_catalog_new_outfit_selection(
            [item.id]
        )
        self.assertFalse(appearance)
        self.assertIn("尚未正式", reason)

    async def test_manual_generation_returns_before_slow_image_work(self):
        gate = asyncio.Event()

        async def slow(*args, **kwargs):
            await gate.wait()

        self.runtime._edit_life_image_with_policy_retry = slow
        event = SimpleNamespace(message_id="m", unified_msg_origin="private:x")
        result = await asyncio.wait_for(
            self.runtime.life_style_generate(
                event, requirement="秋季外套", generation_mode="image_to_image"
            ),
            timeout=0.5,
        )
        self.assertEqual(result.status, "submitted")
        self.assertIn("继续聊天", str(result))
        gate.set()

    async def test_automatic_generation_uses_character_image_only(self):
        item = await self.item()
        path = Path(self.tmp.name) / "output.png"
        path.write_bytes(b"image")
        self.runtime._edit_life_image_with_policy_retry.return_value = SimpleNamespace(
            path=str(path)
        )
        self.runtime._learn_style_catalog_image.return_value = [item]
        await self.archive.enqueue_wardrobe_job(
            "auto",
            {
                "requirement": "适合降温的完整搭配",
                "automatic": True,
                "generation_mode": "text_to_image",
                "count": 1,
                "reason": "缺少合适衣服",
            },
            at=str(self.now),
        )
        await self.runtime._run_wardrobe_job("auto")
        self.runtime._generate_life_image_with_policy_retry.assert_not_called()
        args = self.runtime._edit_life_image_with_policy_retry.call_args.args
        self.assertEqual(args[2], "/tmp/character.png")
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(item.id)]
        self.assertEqual(state["ownership"], "owned")
        self.assertEqual(state["condition"], "clean")

    async def test_missing_character_reference_keeps_original_job_pending(self):
        self.runtime.media.image.first_configured_character_reference_image = lambda: (
            None
        )
        await self.archive.enqueue_wardrobe_job(
            "auto",
            {"automatic": True, "count": 1, "requirement": "秋季衣物"},
            at=str(self.now),
        )
        await self.runtime._run_wardrobe_job("auto")
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(state["jobs"][0]["status"], "pending")
        self.runtime._generate_life_image_with_policy_retry.assert_not_called()
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()

    async def test_resume_accepted_image_does_not_submit_another_request(self):
        item = await self.item()
        path = Path(self.tmp.name) / "output.png"
        path.write_bytes(b"image")
        self.runtime._learn_style_catalog_image.return_value = [item]
        await self.archive.enqueue_wardrobe_job(
            "resume",
            {
                "automatic": True,
                "count": 1,
                "requirement": "降温服装",
                "reason": "缺衣",
            },
            at=str(self.now),
        )
        await self.archive.claim_wardrobe_job("resume", "old", now=self.now)
        await self.archive.update_wardrobe_job(
            "resume",
            "old",
            status="generating",
            progress={"current": {"task_id": "original", "route": {"model": "test"}}},
            at=str(self.now),
        )
        await self.archive.recover_wardrobe_jobs("new")
        self.runtime.media.image.resume_async_image.return_value = SimpleNamespace(
            path=str(path)
        )
        await self.runtime._run_wardrobe_job("resume")
        self.runtime.media.image.resume_async_image.assert_awaited_once_with(
            "original", {"model": "test"}
        )
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()

    async def test_request_without_recoverable_id_is_not_resubmitted(self):
        await self.archive.enqueue_wardrobe_job(
            "uncertain", {"automatic": True, "count": 1}, at=str(self.now)
        )
        await self.archive.claim_wardrobe_job("uncertain", "old", now=self.now)
        await self.archive.update_wardrobe_job(
            "uncertain",
            "old",
            status="generating",
            progress={"current": {"submitting": True}},
            at=str(self.now),
        )
        await self.archive.recover_wardrobe_jobs("new")
        await self.runtime._run_wardrobe_job("uncertain")
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()
        self.assertEqual(
            (await self.archive.get_wardrobe_snapshot())["jobs"][0]["status"],
            "uncertain",
        )

    async def test_wrong_generated_clothing_remains_candidate(self):
        item = await self.item()
        path = Path(self.tmp.name) / "output.png"
        path.write_bytes(b"image")
        self.runtime._edit_life_image_with_policy_retry.return_value = SimpleNamespace(
            path=str(path)
        )
        self.runtime._learn_style_catalog_image.return_value = [item]
        self.runtime._validate_wardrobe_acquisition.return_value = False
        await self.archive.enqueue_wardrobe_job(
            "wrong",
            {"automatic": True, "count": 1, "requirement": "暖外套", "reason": "降温"},
            at=str(self.now),
        )
        await self.runtime._run_wardrobe_job("wrong")
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(state["items"][str(item.id)]["ownership"], "candidate")
        self.assertEqual(state["jobs"][0]["status"], "failed")

    async def test_job_id_prevents_duplicate_generation(self):
        a = await self.archive.enqueue_wardrobe_job(
            "gap", {"requirement": "原缺口"}, at=str(self.now)
        )
        b = await self.archive.enqueue_wardrobe_job(
            "gap", {"requirement": "不同写法"}, at=str(self.now)
        )
        self.assertEqual(a["payload_json"], b["payload_json"])
        await self.archive.claim_wardrobe_job("gap", "one", now=self.now)
        self.assertIsNone(
            await self.archive.claim_wardrobe_job("gap", "two", now=self.now)
        )

    async def test_review_profile_rejects_stale_revision(self):
        self.assertTrue(
            await self.archive.save_wardrobe_review(
                {"aesthetics": ["最新"]}, expected_revision=0, at=str(self.now)
            )
        )
        self.assertFalse(
            await self.archive.save_wardrobe_review(
                {"aesthetics": ["旧结果"]}, expected_revision=0, at=str(self.now)
            )
        )

    def test_aesthetic_learning_requires_real_sources_and_repeated_own_evidence(self):
        sources = {
            "p": {"kind": "persona", "text": "人设原文"},
            "w1": {"kind": "wear", "date": "2026-10-07"},
            "w2": {"kind": "wear", "date": "2026-10-08"},
            "w3": {"kind": "wear", "date": "2026-10-08"},
            "u": {"kind": "feedback", "text": "反馈原文"},
        }
        points = [
            {"preference": "无证据", "evidence_ids": ["invented"]},
            {"preference": "一次选择", "evidence_ids": ["w1"]},
            {
                "preference": "自身偏好",
                "evidence_ids": ["w1", "w2", "w3"],
                "confidence": 1,
            },
            {
                "preference": "人设审美",
                "quote": "人设原文",
                "evidence_ids": ["p"],
                "confidence": 1,
            },
            {
                "preference": "对方建议",
                "quote": "反馈原文",
                "evidence_ids": ["u"],
                "confidence": 1,
            },
        ]
        result = validate_aesthetics(points, sources, [])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["origin"], "own_experience")
        self.assertLessEqual(result[0]["confidence"], 0.65)
        self.assertEqual(result[-1]["origin"], "user_feedback")

    def test_clothing_ranking_uses_weather_and_preserves_favorite_rewear(self):
        warm = SimpleNamespace(
            id=1,
            preference_score=0.2,
            attributes={"wardrobe": {"ownership": "owned", "available": True}},
        )
        summer = SimpleNamespace(
            id=2,
            preference_score=2,
            attributes={"wardrobe": {"ownership": "owned", "available": True}},
        )
        profile = {
            "context_key": "cold",
            "suitability": {
                "1": {"verdict": "suitable"},
                "2": {"verdict": "unsuitable"},
            },
        }
        self.assertEqual(reviewed_candidates([summer, warm], profile, "cold")[0].id, 1)
        self.assertEqual(
            reviewed_candidates([summer, warm], profile, "different-weather")[0].id, 2
        )

    async def test_same_wear_from_different_routes_is_one_experience(self):
        item = await self.item()
        day = await self.day([item.id])
        self.assertTrue(
            await self.archive.record_wardrobe_wear(day, event_id="action-one")
        )
        self.assertFalse(
            await self.archive.record_wardrobe_wear(day, event_id="periodic-one")
        )
        day.meta["outfit_fact_confirmed_at"] = str(
            self.now + datetime.timedelta(minutes=20)
        )
        await self.archive.save_day(day)
        self.assertFalse(
            await self.archive.record_wardrobe_wear(day, event_id="hair-only")
        )
        events = (await self.archive.get_wardrobe_snapshot())["events"]
        self.assertEqual(sum(e["kind"] == "wear" for e in events), 1)

    async def test_later_piece_recognition_preserves_existing_outfit_care(self):
        outfit = await self.item(used=True)
        await self.archive.start_wardrobe_care(
            [outfit.id],
            decision="dirty",
            event_id="dirty",
            at=str(self.now),
            observer="x",
            duration_minutes=30,
            reason="运动后衣物需洗",
        )
        top = await self.item(kind="top")
        bottom = await self.item(kind="bottom")
        state = (await self.archive.get_wardrobe_snapshot())["items"]
        self.assertEqual(state[str(outfit.id)]["condition"], "dirty")
        self.assertEqual(state[str(top.id)]["condition"], "dirty")
        self.assertEqual(state[str(bottom.id)]["ownership"], "owned")
        self.assertEqual(len(state[str(outfit.id)]["unit_ids"]), 2)

    async def test_accepted_generation_resumes_without_current_reference(self):
        item = await self.item()
        path = Path(self.tmp.name) / "ready.png"
        path.write_bytes(b"image")
        await self.archive.enqueue_wardrobe_job(
            "reference-removed",
            {
                "automatic": True,
                "count": 1,
                "requirement": "凉爽天气完整服装",
                "reason": "缺少服装",
            },
            at=str(self.now),
        )
        await self.archive.claim_wardrobe_job(
            "reference-removed", "previous", now=self.now
        )
        await self.archive.update_wardrobe_job(
            "reference-removed",
            "previous",
            status="generating",
            progress={
                "current": {"task_id": "accepted", "route": {"model": "configured"}}
            },
            at=str(self.now),
        )
        await self.archive.recover_wardrobe_jobs("new")
        self.runtime.media.image.first_configured_character_reference_image = lambda: ""
        self.runtime.media.image.resume_async_image.return_value = SimpleNamespace(
            path=str(path)
        )
        self.runtime._learn_style_catalog_image.return_value = [item]
        await self.runtime._run_wardrobe_job("reference-removed")
        self.runtime.media.image.resume_async_image.assert_awaited_once()
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()
        self.assertEqual(
            (await self.archive.get_wardrobe_snapshot())["items"][str(item.id)][
                "ownership"
            ],
            "owned",
        )

    async def test_review_failure_waits_before_another_model_call(self):
        await self.day([])
        self.runtime.call_text_model.return_value = "invalid"
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(
            *list(self.runtime._background_scheduler_for_runtime().tasks)
        )
        self.runtime.call_text_model.assert_awaited_once()
        await self.runtime.check_wardrobe_life()
        self.runtime.call_text_model.assert_awaited_once()

    async def test_queued_manual_job_recovers_when_autonomous_life_is_disabled(self):
        self.runtime.config.domains.enabled = False
        await self.archive.enqueue_wardrobe_job(
            "manual",
            {
                "automatic": False,
                "count": 1,
                "requirement": "完整套装",
                "generation_mode": "text_to_image",
            },
            at=str(self.now),
        )
        self.runtime._run_wardrobe_job = AsyncMock()
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(
            *list(self.runtime._background_scheduler_for_runtime().tasks)
        )
        self.runtime._run_wardrobe_job.assert_awaited_once_with("manual")

    def test_feedback_cannot_replace_own_aesthetic(self):
        previous = [
            {
                "preference": "喜欢米白色",
                "origin": "persona",
                "confidence": 0.8,
                "preference_id": "own",
            }
        ]
        result = validate_aesthetics(
            [
                {
                    "preference": "尝试亮红色",
                    "quote": "想看红色",
                    "evidence_ids": ["u"],
                    "replaces": ["own"],
                }
            ],
            {"u": {"kind": "feedback", "text": "我想看红色衣服"}},
            previous,
        )
        self.assertTrue(any(point["preference_id"] == "own" for point in result))
        self.assertEqual(result[-1]["origin"], "user_feedback")

    def test_invented_persona_quote_cannot_establish_aesthetic(self):
        result = validate_aesthetics(
            [
                {
                    "preference": "喜欢亮红色",
                    "quote": "喜欢亮红色",
                    "evidence_ids": ["p"],
                }
            ],
            {"p": {"kind": "persona", "text": "喜欢米白色"}},
            [],
        )
        self.assertEqual(result, [])

    def test_residence_change_does_not_reuse_previous_weather(self):
        day = DayRecord(date="2026-10-08", weather_info=WeatherInfo(temp=28))
        day.meta["residence_context_stale"] = "true"
        conditions = wardrobe_conditions(day, self.now, residence="新居住地")
        self.assertFalse(conditions["weather_known"])
        self.assertEqual(conditions["weather"], {})

    async def test_actual_submission_time_limits_auto_supplementation(self):
        await self.archive.enqueue_wardrobe_job(
            "older",
            {
                "automatic": True,
                "requested_at": str(self.now - datetime.timedelta(days=3)),
            },
            at=str(self.now),
        )
        await self.archive.claim_wardrobe_job("older", "x", now=self.now)
        await self.archive.update_wardrobe_job(
            "older",
            "x",
            status="completed",
            progress={"submitted_at": str(self.now)},
            at=str(self.now),
            release=True,
        )
        self.assertTrue(
            await self.archive.wardrobe_auto_requested_since(
                str(self.now - datetime.timedelta(hours=24))
            )
        )
        await self.archive.enqueue_wardrobe_job(
            "new",
            {"automatic": True, "count": 1, "requirement": "完整衣物"},
            at=str(self.now),
        )
        await self.runtime._run_wardrobe_job("new")
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()
        self.assertEqual(
            (await self.archive.get_wardrobe_snapshot())["jobs"][0]["status"], "pending"
        )

    async def test_wardrobe_commit_checks_laundry_and_records_wear_atomically(self):
        item = await self.item(used=True)
        day = await self.day([item.id])
        await self.archive.start_wardrobe_care(
            [item.id],
            decision="dirty",
            event_id="d",
            at=str(self.now),
            observer="x",
            duration_minutes=5,
            reason="需洗",
        )
        with self.assertRaises(ValueError):
            await self.archive.save_day(day, require_wearable=True)
        other = await self.item(seed=2, used=True)
        day.meta["style_catalog_reference_ids"] = str(other.id)
        day.meta["outfit_fact_confirmed_at"] = str(
            self.now + datetime.timedelta(minutes=1)
        )
        await self.archive.save_day(day, require_wearable=True)
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertTrue(snapshot["items"][str(other.id)]["currently_worn"])
        self.assertEqual(snapshot["items"][str(item.id)]["condition"], "dirty")
        self.assertEqual(sum(e["kind"] == "wear" for e in snapshot["events"]), 1)

    async def test_weather_change_discards_background_review(self):
        day = await self.day([])
        snapshot = await self.archive.get_wardrobe_snapshot()
        conditions = self.runtime._wardrobe_conditions(day, self.now)
        inventory_key = digest(
            {
                k: {field: v.get(field) for field in ("ownership", "condition")}
                for k, v in snapshot["items"].items()
            }
        )
        gate = asyncio.Event()
        started = asyncio.Event()

        async def slow(*args, **kwargs):
            started.set()
            await gate.wait()
            return "{}"

        self.runtime.call_text_model = slow
        task = asyncio.create_task(
            self.runtime._review_wardrobe(
                day, conditions, snapshot, inventory_key, digest([])
            )
        )
        await started.wait()
        await self.archive.mutate_day(
            day.date, lambda latest: setattr(latest.weather_info, "temp", 28)
        )
        gate.set()
        await task
        self.assertEqual(
            (await self.archive.get_wardrobe_snapshot())["profile_revision"], 0
        )

    def test_season_does_not_create_weather_facts(self):
        day = DayRecord(date="2026-10-08")
        conditions = wardrobe_conditions(day, self.now, residence="现实居住地")
        self.assertIn("秋季", conditions["season"])
        self.assertFalse(conditions["weather_known"])
        self.assertNotEqual(
            suitability_key(conditions),
            suitability_key({**conditions, "weather": {"temp": 10}}),
        )
