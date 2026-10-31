import asyncio
import datetime
import json
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from componentfixtures import StyleCatalogComponent
from core.life.body import ContinuousBody
from core.life.domain import LifeDomainService
from core.life.dressing import (
    digest,
    reviewed_candidates,
    suitability_key,
    validate_aesthetics,
    wardrobe_conditions,
)
from core.life.wardrobe import synchronize_outfit_components_for_scene
from core.life.wardrobefit import (
    validate_scene_choices,
    validate_understanding,
    wardrobe_targets,
)
from core.media.picture.polling import ImageTaskFailed
from core.models import LifeActionIntent, TimelineItem, WeatherInfo
from core.runtime.background import BackgroundTaskScheduler
from core.runtime.clothier import _WARDROBE_REVIEW_VERSION
from support import (
    DailyLifeRuntime,
    DayRecord,
    LifeArchive,
    LifeSettings,
    LifeState,
    make_composer,
)


class Runtime(DailyLifeRuntime):
    def __init__(self, archive):
        self.background = BackgroundTaskScheduler()
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
        self.check_autonomous_life_update = AsyncMock()
        self.media = SimpleNamespace(
            image=SimpleNamespace(
                first_configured_character_reference_image=lambda: "/tmp/character.png",
                can_edit_image=lambda: True,
                resume_async_image=AsyncMock(),
                track_async_tasks=lambda accepted, **kwargs: nullcontext(),
            )
        )
        self._edit_life_image_with_policy_retry = AsyncMock()
        self._generate_life_image_with_policy_retry = AsyncMock()
        self._learn_style_catalog_image = AsyncMock()
        self.wardrobe_life._validate_wardrobe_acquisition = AsyncMock(return_value={"fits": True, "reason": "适合本次目标"})

    def _schedule_background_task(self, coro, **kwargs):
        return self.background.schedule(coro, **kwargs)

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
        await self.runtime.background.cancel_all()
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

    async def test_admission_establishes_ownership_without_wear(self):
        a = await self.item(used=True)
        b = await self.item(seed=2)
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(state["items"][str(a.id)]["ownership"], "owned")
        self.assertEqual(state["items"][str(b.id)]["ownership"], "owned")
        self.assertFalse(state["items"][str(b.id)]["currently_worn"])
        self.assertEqual(b.last_used_at, "")

    async def test_invalid_generated_material_has_one_persisted_correction(self):
        image = Path(self.tmp.name) / "invalid.png"
        image.write_bytes(b"image")
        self.runtime._learn_style_catalog_image.return_value = [await self.item()]
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(image))
        self.runtime.wardrobe_life._validate_wardrobe_acquisition.return_value = {"fits": False, "reason": "只有开衫，缺少目标长裙"}
        self.runtime.media.image.first_configured_character_reference_image = lambda: ""
        await self.archive.enqueue_wardrobe_job("correct-once", {
            "automatic": True, "count": 1, "requirement": "完整外出套装"}, at=str(self.now))
        await self.runtime.wardrobe_life._run_wardrobe_job("correct-once")
        first = await self.stored_job("correct-once")
        self.assertEqual(first["status"], "pending")
        self.assertEqual(len(first["progress"]["rejected_materials"]), 1)
        self.assertEqual(first["progress"]["rejected_materials"][0]["reason"], "只有开衫，缺少目标长裙")
        self.assertEqual(first["progress"]["current"], {})
        # 重启服务后仍使用同一持久化次数，不恢复成无限重试。
        self.runtime.__dict__.pop("wardrobe_life", None)
        self.runtime.now += datetime.timedelta(minutes=3)
        self.runtime.wardrobe_life._validate_wardrobe_acquisition = AsyncMock(return_value={"fits": False, "reason": "仍未符合目标"})
        await self.runtime.wardrobe_life._run_wardrobe_job("correct-once")
        second = await self.stored_job("correct-once")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(len(second["progress"]["rejected_materials"]), 2)
        await self.runtime.wardrobe_life._run_wardrobe_job("correct-once")
        self.assertEqual(self.runtime._generate_life_image_with_policy_retry.await_count, 2)

    async def test_unavailable_validation_retains_image_without_regeneration(self):
        image = Path(self.tmp.name) / "unverified.png"
        image.write_bytes(b"image")
        self.runtime._learn_style_catalog_image.return_value = [await self.item()]
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(image))
        self.runtime.wardrobe_life._validate_wardrobe_acquisition.return_value = None
        self.runtime.media.image.first_configured_character_reference_image = lambda: ""
        await self.archive.enqueue_wardrobe_job("verify-later", {
            "automatic": True, "count": 1, "requirement": "完整外出套装"}, at=str(self.now))
        await self.runtime.wardrobe_life._run_wardrobe_job("verify-later")
        job = await self.stored_job("verify-later")
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["progress"]["current"]["path"], str(image))
        self.assertNotIn("rejected_materials", job["progress"])
        self.runtime.now += datetime.timedelta(minutes=3)
        await self.runtime.wardrobe_life._run_wardrobe_job("verify-later")
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()

    async def stored_job(self, job_id):
        row = await self.archive._run_db(lambda: dict(self.archive._conn.execute(
            "SELECT * FROM wardrobe_jobs WHERE job_id=?", (job_id,)).fetchone()))
        return {**row, "progress": json.loads(row["progress_json"])}

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

    async def test_one_piece_catalog_alias_cannot_wash_the_dress_being_worn(self):
        outfit = await self.item()
        dress = await self.item(kind="top")
        day = await self.day([dress.id])
        await self.archive.record_wardrobe_wear(day, event_id="wear-dress")
        state = (await self.archive.get_wardrobe_snapshot())["items"]
        self.assertEqual(state[str(outfit.id)], state[str(dress.id)])
        self.assertTrue(state[str(outfit.id)]["currently_worn"])
        self.assertTrue(await self.archive.start_wardrobe_care(
            [outfit.id], decision="dirty", event_id="dirty-alias", at=str(self.now),
            observer="test", duration_minutes=30, reason="实际穿着后待洗",
        ))
        self.assertFalse(await self.archive.start_wardrobe_care(
            [outfit.id], decision="washing", event_id="wash-alias", at=str(self.now),
            observer="test", duration_minutes=30, reason="尝试使用套装编号送洗",
        ))

    async def test_switching_one_piece_catalog_alias_does_not_count_as_changing(self):
        outfit = await self.item()
        dress = await self.item(kind="top")
        day = await self.day([outfit.id])
        self.assertTrue(await self.archive.record_wardrobe_wear(day, event_id="wear"))
        day = await self.day([dress.id], self.now + datetime.timedelta(minutes=10))
        self.assertFalse(await self.archive.record_wardrobe_wear(day, event_id="alias"))

    async def test_stored_outfit_can_be_selected_and_worn_without_separate_retrieval(self):
        outfit = await self.item(used=True)
        self.assertTrue(await self.archive.start_wardrobe_care(
            [outfit.id], decision="stored", event_id="store", at=str(self.now),
            observer="test", duration_minutes=0, reason="干净衣物归位",
        ))
        composer = StyleCatalogComponent()
        composer.archive = self.archive
        appearance, reason = await composer._style_catalog_new_outfit_selection([outfit.id])
        self.assertTrue(appearance.get("outfit"))
        self.assertEqual(reason, "")
        issue = await self.archive._run_db(self.archive._wardrobe_action_issue_unlocked, [outfit.id])
        self.assertEqual(issue, "")
        day = await self.day([outfit.id])
        self.assertTrue(await self.archive.record_wardrobe_wear(day, event_id="wear-stored"))
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(outfit.id)]
        self.assertEqual(state["condition"], "worn")
        self.assertTrue(state["currently_worn"])

    async def test_later_single_piece_recognition_preserves_active_laundry(self):
        outfit = await self.item(used=True)
        for decision in ("dirty", "washing"):
            await self.archive.start_wardrobe_care(
                [outfit.id], decision=decision, event_id=decision, at=str(self.now),
                observer="test", duration_minutes=5, reason="已换下的衣物需要清洗",
            )
        dress = await self.item(kind="top")
        state = (await self.archive.get_wardrobe_snapshot())["items"]
        self.assertEqual(state[str(outfit.id)], state[str(dress.id)])
        self.assertEqual(state[str(dress.id)]["condition"], "washing")
        for minute in range(1, 6):
            await self.archive.advance_wardrobe_care(
                now=self.now + datetime.timedelta(minutes=minute), observer="test",
            )
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(dress.id)]
        self.assertEqual(state["condition"], "drying")
        self.assertFalse(state["available"])

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
        item = await self.item(attrs={"wardrobe_job": "pending"})
        await self.archive.save_wardrobe_review(
            {"aesthetics": []}, expected_revision=0, at=str(self.now)
        )
        composer = SimpleNamespace(archive=self.archive)

        class Composer(StyleCatalogComponent):
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

    async def test_background_cancel_preserves_accepted_job_and_releases_lease(self):
        accepted = []
        started = asyncio.Event()

        @contextmanager
        def track(listener, *, on_failed=None):
            accepted.append(listener)
            try:
                yield
            finally:
                accepted.pop()

        async def generate(*args, **kwargs):
            await accepted[-1](
                "accepted-job",
                SimpleNamespace(
                    api_url="https://example.invalid/v1", model="test", protocol="openai", api_key="test-key"
                ),
            )
            started.set()
            await asyncio.Event().wait()

        self.runtime.media.image.track_async_tasks = track
        self.runtime._edit_life_image_with_policy_retry.side_effect = generate
        event = SimpleNamespace(message_id="queued", unified_msg_origin="private:x")
        result = await self.runtime.queue_wardrobe_generation(
            event, requirement="秋季衣服", generation_mode="image_to_image", count=1
        )
        self.assertEqual(result.status, "submitted")
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(self.runtime.background.cancel_all(), 1)
        job = (await self.archive.get_wardrobe_snapshot())["jobs"][0]
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["progress"]["current"]["task_id"], "accepted-job")
        self.assertEqual(job["lease_owner"], "")
        self.assertEqual(accepted, [])

    async def test_service_state_is_isolated_and_reads_current_configuration(self):
        first = self.runtime.wardrobe_life
        other = Runtime(self.archive)
        second = other.wardrobe_life
        self.assertNotEqual(first._wardrobe_owner(), second._wardrobe_owner())
        first._review_retry_after = self.now
        first._recovered = True
        self.assertIsNone(second._review_retry_after)
        self.assertFalse(second._recovered)
        self.runtime.config = LifeSettings.from_dict(
            {"life_domain_config": {"home_address": "新的居住地"}}
        )
        conditions = first._wardrobe_conditions(DayRecord(date="2026-10-08"), self.now)
        self.assertEqual(conditions["residence"], "新的居住地")

    async def test_automatic_generation_uses_character_image_only(self):
        persona = "成年男性，喜欢简单实穿的日常服装。"
        self.runtime.get_persona_text.return_value = persona
        reference = Path(self.tmp.name) / "character.png"
        reference.write_bytes(b"reference image")
        self.runtime.media.image.first_configured_character_reference_image = (
            lambda: str(reference)
        )
        item = await self.item(attrs={"wardrobe_job": "auto"})
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
        await self.runtime.wardrobe_life._run_wardrobe_job("auto")
        self.runtime._generate_life_image_with_policy_retry.assert_not_called()
        args = self.runtime._edit_life_image_with_policy_retry.call_args.args
        self.assertEqual(args[2], str(reference))
        self.assertIn(persona, args[1])
        self.runtime.get_persona_text.assert_awaited_once()
        self.runtime.call_text_model.assert_not_awaited()
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(item.id)]
        self.assertEqual(state["ownership"], "owned")
        self.assertEqual(state["condition"], "clean")

    async def test_manual_text_generation_keeps_role_independent(self):
        item = await self.item(attrs={"wardrobe_job": "manual-text"})
        path = Path(self.tmp.name) / "output.png"
        path.write_bytes(b"image")
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(
            path=str(path)
        )
        self.runtime._learn_style_catalog_image.return_value = [item]
        await self.archive.enqueue_wardrobe_job(
            "manual-text",
            {"requirement": "新的通勤搭配灵感", "generation_mode": "text_to_image", "count": 1},
            at=str(self.now),
        )
        await self.runtime.wardrobe_life._run_wardrobe_job("manual-text")
        self.assertEqual((await self.archive.get_wardrobe_snapshot())["items"][str(item.id)]["ownership"], "owned")
        self.runtime.get_persona_text.assert_not_awaited()
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(
            state["recent_jobs"][0]["status"],
            "completed",
            state["recent_jobs"][0].get("error"),
        )

    async def test_failed_wardrobe_image_is_terminal_without_old_task_progress(self):
        callbacks = {}

        @contextmanager
        def track(listener, *, on_failed=None):
            callbacks.update(accepted=listener, failed=on_failed)
            yield

        async def generate(*_args, **_kwargs):
            route = SimpleNamespace(
                api_url="https://example.invalid/v1", model="image-test", protocol="openai", api_key="test-key"
            )
            await callbacks["accepted"]("failed-wardrobe-image", route)
            failure = ImageTaskFailed("生成任务已明确失败")
            await callbacks["failed"]("failed-wardrobe-image", route, failure)
            raise failure

        self.runtime.media.image.track_async_tasks = track
        self.runtime._edit_life_image_with_policy_retry.side_effect = generate
        await self.archive.enqueue_wardrobe_job(
            "failed-job",
            {"automatic": True, "count": 1, "requirement": "秋季衣物"},
            at=str(self.now),
        )
        await self.runtime.wardrobe_life._run_wardrobe_job("failed-job")
        job = (await self.archive.get_wardrobe_snapshot())["jobs"][0]
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("task_id", job["progress"]["current"])
        self.assertNotIn("route", job["progress"]["current"])
        self.runtime.media.image.resume_async_image.assert_not_awaited()

    async def test_missing_character_reference_falls_back_to_text_generation(self):
        item = await self.item()
        path = Path(self.tmp.name) / "output.png"
        path.write_bytes(b"image")
        self.runtime.media.image.first_configured_character_reference_image = lambda: (
            None
        )
        self.runtime._generate_life_image_with_policy_retry.return_value = (
            SimpleNamespace(path=str(path))
        )
        self.runtime._learn_style_catalog_image.return_value = [item]
        await self.archive.enqueue_wardrobe_job(
            "auto",
            {"automatic": True, "count": 1, "requirement": "秋季衣物"},
            at=str(self.now),
        )
        await self.runtime.wardrobe_life._run_wardrobe_job("auto")
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(
            state["recent_jobs"][0]["status"],
            "completed",
            state["recent_jobs"][0].get("error"),
        )
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        self.assertEqual(
            self.runtime._generate_life_image_with_policy_retry.call_args.kwargs[
                "include_character_reference"
            ],
            False,
        )
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()
        generated = self.runtime._generate_life_image_with_policy_retry.call_args.args[1]
        self.assertIn("秋季衣物", generated)

    async def test_missing_edit_channel_falls_back_to_text_generation(self):
        item = await self.item()
        path = Path(self.tmp.name) / "output.png"
        path.write_bytes(b"image")
        reference = Path(self.tmp.name) / "character.png"
        reference.write_bytes(b"reference image")
        self.runtime.media.image.first_configured_character_reference_image = (
            lambda: str(reference)
        )
        self.runtime.media.image.can_edit_image = lambda: False
        self.runtime._generate_life_image_with_policy_retry.return_value = (
            SimpleNamespace(path=str(path))
        )
        self.runtime._learn_style_catalog_image.return_value = [item]
        await self.archive.enqueue_wardrobe_job(
            "auto-no-edit",
            {"automatic": True, "count": 1, "requirement": "秋季衣物"},
            at=str(self.now),
        )

        await self.runtime.wardrobe_life._run_wardrobe_job("auto-no-edit")

        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(state["recent_jobs"][0]["status"], "completed")
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()

    async def test_automatic_single_item_can_be_verified_and_adopted(self):
        item = await self.item(kind="footwear", attrs={"wardrobe_job": "auto-footwear"})
        path = Path(self.tmp.name) / "output.png"
        path.write_bytes(b"image")
        reference = Path(self.tmp.name) / "character.png"
        reference.write_bytes(b"reference image")
        self.runtime.media.image.first_configured_character_reference_image = (
            lambda: str(reference)
        )
        self.runtime._edit_life_image_with_policy_retry.return_value = (
            SimpleNamespace(path=str(path))
        )
        self.runtime._learn_style_catalog_image.return_value = [item]
        await self.archive.enqueue_wardrobe_job(
            "auto-footwear",
            {
                "automatic": True,
                "count": 1,
                "requirement": "轻便透气的居家拖鞋",
                "reason": "炎热天气缺少可穿的室内拖鞋",
            },
            at=str(self.now),
        )

        await self.runtime.wardrobe_life._run_wardrobe_job("auto-footwear")

        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(
            state["recent_jobs"][0]["status"],
            "completed",
            state["recent_jobs"][0].get("error"),
        )
        self.assertEqual(state["items"][str(item.id)]["ownership"], "owned")
        self.assertFalse(
            self.runtime._learn_style_catalog_image.call_args.kwargs[
                "require_complete_clothing"
            ]
        )

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
        await self.runtime.wardrobe_life._run_wardrobe_job("resume")
        self.runtime.media.image.resume_async_image.assert_awaited_once_with(
            "original", {"model": "test"}
        )
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()
        self.runtime.get_persona_text.assert_not_awaited()

    async def test_wardrobe_recovery_uses_next_channel_and_learns_finished_image(self):
        import base64
        from unittest.mock import patch

        from core.media.picture import polling
        from core.media.picture.routes import task_reference
        from testimagetasks import ImageTaskTests, _accepted, _Response, _Session

        session = _Session([_accepted()], [_Response(404)])
        service = ImageTaskTests()._service(session, mode="edit")
        service._load_reference_image = AsyncMock(return_value=(b"original-look", "image/png"))
        route = (await service._request_routes("edit"))[0]
        with patch.object(polling.asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaises(polling.ImageTaskError):
                await service.edit_image("秋季衣物", "reference.png")
        self.runtime.media.image = service
        self.runtime._learn_style_catalog_image.return_value = [await self.item()]
        await self.archive.enqueue_wardrobe_job(
            "resume-fallback", {"automatic": False, "count": 1, "requirement": "秋季衣物"}, at=str(self.now)
        )
        await self.archive.claim_wardrobe_job("resume-fallback", "old", now=self.now)
        await self.archive.update_wardrobe_job(
            "resume-fallback", "old", status="pending", release=True,
            progress={"current": {"task_id": "imgtask_test", "route": task_reference(route)}}, at=str(self.now)
        )
        result = {"data": [{"b64_json": base64.b64encode(b"new-look").decode()}]}
        recovering = _Session([_Response(payload=result)], [
            _Response(payload={"status": "failed", "http_status": 502})
        ])
        service._get_session = AsyncMock(return_value=recovering)
        self.runtime.now += datetime.timedelta(minutes=3)
        with patch.object(polling.asyncio, "sleep", new_callable=AsyncMock):
            await self.runtime.wardrobe_life._run_wardrobe_job("resume-fallback")
        job = (await self.archive.get_wardrobe_snapshot())["recent_jobs"][0]
        self.assertEqual(job["status"], "completed")
        self.runtime._learn_style_catalog_image.assert_awaited_once()
        self.assertEqual([c[0] for c in recovering.calls], ["GET", "POST"])
        self.runtime._edit_life_image_with_policy_retry.assert_not_awaited()

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
        await self.runtime.wardrobe_life._run_wardrobe_job("uncertain")
        self.runtime._edit_life_image_with_policy_retry.assert_not_called()
        self.assertEqual(
            (await self.archive.get_wardrobe_snapshot())["jobs"][0]["status"],
            "uncertain",
        )

    async def test_wrong_generated_clothing_remains_candidate(self):
        item = await self.item(attrs={"wardrobe_job": "wrong"})
        path = Path(self.tmp.name) / "output.png"
        path.write_bytes(b"image")
        self.runtime._edit_life_image_with_policy_retry.return_value = SimpleNamespace(
            path=str(path)
        )
        self.runtime.media.image.first_configured_character_reference_image = lambda: ""
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(path))
        self.runtime._learn_style_catalog_image.return_value = [item]
        self.runtime.wardrobe_life._validate_wardrobe_acquisition.return_value = {"fits": False, "reason": "与需求不符"}
        await self.archive.enqueue_wardrobe_job(
            "wrong",
            {"automatic": True, "count": 1, "requirement": "暖外套", "reason": "降温"},
            at=str(self.now),
        )
        await self.runtime.wardrobe_life._run_wardrobe_job("wrong")
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(state["items"][str(item.id)]["ownership"], "candidate")
        self.assertEqual(state["jobs"][0]["status"], "pending")
        self.runtime.wardrobe_life._validate_wardrobe_acquisition.assert_awaited_once()
        self.assertEqual(state["jobs"][0]["progress"]["rejected_materials"][0]["reason"], "与需求不符")

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
        await self.runtime.wardrobe_life._run_wardrobe_job("reference-removed")
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
            *list(self.runtime.background.tasks)
        )
        self.runtime.call_text_model.assert_awaited_once()
        await self.runtime.check_wardrobe_life()
        self.runtime.call_text_model.assert_awaited_once()

    async def test_quiet_hours_preserve_wear_and_care_then_resume_review(self):
        self.runtime.now = self.now.replace(hour=1)
        item = await self.item()
        await self.archive.save_wardrobe_understanding({item.id: validate_understanding(
            {"status": "unknown", "confidence": 0}, item
        )})
        await self.day([item.id], at=self.runtime.now)
        self.archive.advance_wardrobe_care = AsyncMock(
            wraps=self.archive.advance_wardrobe_care
        )
        self.runtime.call_text_model.return_value = "{}"

        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))

        self.archive.advance_wardrobe_care.assert_awaited_once()
        self.runtime.get_persona_text.assert_not_awaited()
        self.runtime.call_text_model.assert_not_awaited()
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertTrue(snapshot["items"][str(item.id)]["currently_worn"])
        self.assertEqual(snapshot["profile_revision"], 0)
        self.assertIsNone(self.runtime.wardrobe_life._review_retry_after)
        self.assertTrue((await self.archive.get_day("2026-10-08")).meta["wardrobe_context"])

        self.runtime.now = self.now.replace(hour=6, minute=30)
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))

        self.runtime.call_text_model.assert_awaited_once()
        self.assertEqual(
            (await self.archive.get_wardrobe_snapshot())["profile_revision"], 1
        )

    async def test_existing_wardrobe_jobs_continue_in_quiet_hours(self):
        self.runtime.now = self.now.replace(hour=1)
        await self.day([])
        for name, automatic in (("manual", False), ("automatic", True)):
            await self.archive.enqueue_wardrobe_job(
                name,
                {"automatic": automatic, "requirement": "秋季衣服", "count": 1},
                at=str(self.runtime.now),
            )
        self.runtime.wardrobe_life._run_wardrobe_job = AsyncMock()

        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))

        self.assertEqual(
            {call.args[0] for call in self.runtime.wardrobe_life._run_wardrobe_job.await_args_list},
            {"manual", "automatic"},
        )
        self.runtime.call_text_model.assert_not_awaited()

    async def test_queued_review_checks_quiet_hours_when_it_starts(self):
        self.runtime.now = self.now.replace(hour=23, minute=59)
        await self.day([])
        gate = self.runtime.background.normal_gate
        for _ in range(4):
            await gate.acquire()
        await self.runtime.check_wardrobe_life()
        self.assertIn("wardrobe-review", self.runtime.background.keys)
        self.runtime.now = self.now.replace(hour=0, minute=1) + datetime.timedelta(days=1)
        for _ in range(4):
            gate.release()

        await asyncio.gather(*list(self.runtime.background.tasks))

        self.runtime.get_persona_text.assert_not_awaited()
        self.runtime.call_text_model.assert_not_awaited()
        self.assertIsNone(self.runtime.wardrobe_life._review_retry_after)

    async def test_review_preparation_cannot_start_a_model_request_in_quiet_hours(self):
        self.runtime.now = self.now.replace(hour=23, minute=59)
        await self.day([])

        async def provider(_provider_id):
            self.runtime.now += datetime.timedelta(minutes=2)
            return object()

        self.runtime.get_text_provider.side_effect = provider
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))

        self.runtime.call_text_model.assert_not_awaited()
        self.runtime.close_text_session.assert_awaited_once()
        self.assertEqual(
            (await self.archive.get_wardrobe_snapshot())["profile_revision"], 0
        )

    async def test_review_returning_in_quiet_hours_does_not_save_a_new_decision(self):
        self.runtime.now = self.now.replace(hour=23, minute=59)
        await self.day([])

        async def model(*_args, **_kwargs):
            self.runtime.now += datetime.timedelta(minutes=2)
            return "{}"

        self.runtime.call_text_model.side_effect = model
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))

        self.runtime.call_text_model.assert_awaited_once()
        self.runtime.close_text_session.assert_awaited_once()
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(snapshot["profile_revision"], 0)
        self.assertEqual(snapshot["jobs"], [])

    async def test_wardrobe_quiet_hours_follow_configuration_changes(self):
        await self.day([])
        self.runtime.config.state.quiet_hours = "11:00-13:00"
        self.runtime.call_text_model.return_value = "{}"
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
        self.runtime.call_text_model.assert_not_awaited()

        self.runtime.config.state.quiet_hours = ""
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
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
        self.runtime.wardrobe_life._run_wardrobe_job = AsyncMock()
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(
            *list(self.runtime.background.tasks)
        )
        self.runtime.wardrobe_life._run_wardrobe_job.assert_awaited_once_with("manual")

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
        await self.runtime.wardrobe_life._run_wardrobe_job("new")
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

    async def test_outdoor_partial_change_keeps_worn_dirty_clothes_and_changes_shoes(self):
        outfit = await self.item(used=True)
        shoes = await self.archive.upsert_style_catalog_item({
            "kind": "footwear", "title": "白色板鞋", "description": "白色板鞋",
            "source_image_hash": "shoes" * 13, "confidence": 0.95,
            "attributes": {"home_presence": "outdoor"},
        })
        await self.archive.adopt_wardrobe_items(
            [shoes.id], event_id="own-shoes", at=str(self.now), reason="已有鞋履",
        )
        day = await self.day([outfit.id])
        day.outfit = "浅色针织上衣与长裤；室内拖鞋"
        day.timeline = [TimelineItem(
            time="11:30", activity="在街边散步拍照", place_kind="generic",
        )]
        day.meta.update({
            "style": "甜美居家混搭", "outfit_scene_category": "public",
            "outfit_components": json.dumps({
                "main_clothing": {"state": "worn", "description": "浅色针织上衣与长裤"},
                "footwear": {"state": "worn", "description": "室内拖鞋", "home_presence": "home"},
            }, ensure_ascii=False),
        })
        await self.archive.save_day(day)
        await self.archive.record_wardrobe_wear(day, event_id="wear-main")
        await self.archive.start_wardrobe_care(
            [outfit.id], decision="dirty", event_id="dirty-current", at=str(self.now),
            observer="test", duration_minutes=0, reason="已经穿过，脱下后再洗",
        )
        response = {
            "outfit_decision": "partial_change", "current_outfit_basis": "stored",
            "scene_category": "public", "style_pool": "outfit_styles",
            "component_review": {"main_clothing": "keep", "footwear": "adjust"},
            "outfit": "浅色针织上衣与长裤；白色板鞋", "style": "清爽日常外出风",
            "outfit_components": {"footwear": {
                "state": "worn", "description": "白色板鞋", "home_presence": "outdoor",
            }},
            "catalog_reference_ids": [shoes.id], "reason": "衣服继续穿，换适合走路的鞋",
        }
        composer, provider, _, _ = make_composer([json.dumps(response, ensure_ascii=False)])
        composer.archive = self.archive
        updated = await composer.update_outfit(
            day.date, "noon", current_time=self.now + datetime.timedelta(minutes=1),
        )
        self.assertIsNotNone(updated)
        stored = await self.archive.get_day(day.date)
        self.assertEqual(stored.outfit, "浅色针织上衣与长裤；白色板鞋")
        self.assertEqual(stored.meta["style"], "清爽日常外出风")
        self.assertEqual(set(stored.meta["style_catalog_reference_ids"].split(",")),
                         {str(outfit.id), str(shoes.id)})
        states = (await self.archive.get_wardrobe_snapshot())["items"]
        self.assertEqual(states[str(outfit.id)]["condition"], "dirty")
        self.assertTrue(states[str(outfit.id)]["currently_worn"])
        self.assertTrue(states[str(shoes.id)]["currently_worn"])
        self.assertEqual(len(provider.prompts), 1)

    async def test_retained_catalog_id_does_not_allow_dirty_clothes_to_be_put_back_on(self):
        outfit = await self.item(used=True)
        day = await self.day([outfit.id])
        await self.archive.start_wardrobe_care(
            [outfit.id], decision="dirty", event_id="dirty", at=str(self.now),
            observer="test", duration_minutes=0, reason="已脱下等待清洗",
        )
        with self.assertRaises(ValueError):
            await self.archive.save_day(day, require_wearable=True)

    async def _planned_partial_change(self, *, continuous):
        outfit = await self.item(used=True)
        old_shoes = await self.item(kind="footwear", seed=2, used=True)
        new_shoes = await self.item(kind="footwear", seed=3, used=True)
        day = await self.day([outfit.id, old_shoes.id])
        day.outfit = "针织上衣与长裤；拖鞋"
        day.meta.update({
            "outfit_scene_category": "home", "style": "甜美居家混搭",
            "outfit_components": json.dumps({
                "main_clothing": {"state": "worn", "description": "针织上衣与长裤"},
                "footwear": {"state": "worn", "description": "拖鞋"},
            }),
        })
        if continuous:
            day.meta["continuous_body"] = json.dumps(ContinuousBody().as_dict())
        await self.archive.save_day(day, require_wearable=True)
        await self.archive.start_wardrobe_care(
            [outfit.id, old_shoes.id], decision="dirty", event_id="dirty-worn",
            at=str(self.now), observer="test", duration_minutes=0, reason="脱下后再洗",
        )
        action = LifeActionIntent.from_value({
            "action_id": "planned-shoes", "action_type": "change_outfit",
            "target": "针织上衣与长裤；板鞋",
            "payload": {
                "scene_category": "public", "style": "清爽日常外出风",
                "outfit_components": {"footwear": {"state": "worn", "description": "板鞋"}},
                "catalog_reference_ids": [new_shoes.id],
            },
        })
        domains = LifeDomainService(self.runtime.config.domains, self.archive)
        self.assertEqual(await domains.validate_action(action, day=day), (True, ""))
        self.assertEqual(action.payload["catalog_reference_ids"], [new_shoes.id])
        composer, _, _, _ = make_composer([])
        composer.archive = self.archive
        outcome = await composer.settle_and_persist_life_action(day, action, now=self.now)
        self.assertEqual(outcome.status, "committed")
        stored = await self.archive.get_day(day.date)
        self.assertEqual(stored.outfit, "针织上衣与长裤；板鞋")
        self.assertEqual(stored.meta["style"], "清爽日常外出风")
        self.assertEqual(set(stored.meta["style_catalog_reference_ids"].split(",")),
                         {str(outfit.id), str(new_shoes.id)})
        states = (await self.archive.get_wardrobe_snapshot())["items"]
        self.assertTrue(states[str(outfit.id)]["currently_worn"])
        self.assertEqual(states[str(outfit.id)]["condition"], "dirty")
        self.assertFalse(states[str(old_shoes.id)]["currently_worn"])
        self.assertTrue(states[str(new_shoes.id)]["currently_worn"])
        replay = await composer.settle_and_persist_life_action(day, action, now=self.now)
        self.assertTrue(replay.replayed)
        self.assertEqual((await self.archive.get_day(day.date)).outfit, stored.outfit)

    async def test_planned_partial_change_preserves_worn_main_and_replaces_shoe_reference(self):
        await self._planned_partial_change(continuous=False)

    async def test_continuous_receipt_partial_change_preserves_worn_main_and_replaces_shoe_reference(self):
        await self._planned_partial_change(continuous=True)

    async def test_home_only_staged_shoes_do_not_become_worn_when_going_out(self):
        components = synchronize_outfit_components_for_scene({
            "footwear": {"state": "staged", "description": "室内拖鞋", "home_presence": "home"},
            "carried_accessories": {"state": "staged", "description": "帆布包", "home_presence": "outdoor"},
        }, "public", previous_scene_category="home")
        self.assertEqual(components["footwear"]["state"], "staged")
        self.assertEqual(components["carried_accessories"]["state"], "carried")

    async def test_return_home_removes_shoe_reference_without_blocking_dirty_main(self):
        main = await self.item(used=True)
        shoes = await self.item(kind="footwear", seed=2, used=True)
        day = await self.day([main.id, shoes.id])
        day.outfit = "针织上衣与长裤；板鞋"
        day.meta.update({
            "style": "日常外出风", "outfit_scene_category": "public",
            "outfit_components": json.dumps({
                "main_clothing": {"state": "worn", "description": "针织上衣与长裤"},
                "footwear": {"state": "worn", "description": "板鞋"},
            }),
        })
        await self.archive.save_day(day, require_wearable=True)
        await self.archive.start_wardrobe_care(
            [main.id, shoes.id], decision="dirty", event_id="dirty-after-outing",
            at=str(self.now), observer="test", duration_minutes=0, reason="外出后待洗",
        )
        composer, _, _, _ = make_composer([])
        composer.archive = self.archive
        outcome = await composer.settle_and_persist_life_action(day, {
            "action_id": "take-off-shoes", "action_type": "change_outfit",
            "target": "针织上衣与长裤", "payload": {
                "scene_category": "home",
                "outfit_components": {"footwear": {"state": "removed", "description": "板鞋"}},
            },
        }, now=self.now)
        self.assertEqual(outcome.status, "committed")
        self.assertEqual(day.outfit, "针织上衣与长裤")
        self.assertEqual(day.meta["style_catalog_reference_ids"], str(main.id))
        self.assertNotIn("style", day.meta)
        states = (await self.archive.get_wardrobe_snapshot())["items"]
        self.assertTrue(states[str(main.id)]["currently_worn"])
        self.assertFalse(states[str(shoes.id)]["currently_worn"])

    async def test_incremental_review_reuses_fit_until_item_or_weather_changes(self):
        item = await self.item(used=True)
        day = await self.day([item.id])
        self.runtime.call_text_model.return_value = json.dumps({"suitability": [{
            "item_id": item.id, "verdict": "suitable", "score": 0.9,
            "confidence": 0.9, "reason": "适合当前天气与活动",
        }]})

        async def review():
            snapshot = await self.archive.get_wardrobe_snapshot()
            conditions = self.runtime.wardrobe_life._wardrobe_conditions(day, self.now)
            inventory_key = digest({k: {field: v.get(field) for field in ("ownership", "condition")}
                                    for k, v in snapshot["items"].items()})
            await self.runtime.wardrobe_life._review_wardrobe(day, conditions, snapshot, inventory_key, digest([]))
            return json.loads(self.runtime.call_text_model.call_args.args[1].split("【眼前内容】\n", 1)[1])

        first = await review()
        self.assertEqual([i["id"] for i in first["wardrobe"]], [item.id])
        second = await review()
        self.assertEqual(second["wardrobe"], [])
        self.assertEqual(second["assessed_wardrobe"][0]["verified_fit"]["verdict"], "suitable")
        day.weather_info.temp = 30
        await self.archive.save_day(day)
        changed = await review()
        self.assertEqual([i["id"] for i in changed["wardrobe"]], [item.id])
        self.assertEqual(changed["assessed_wardrobe"], [])

    async def test_weather_change_discards_background_review(self):
        day = await self.day([])
        snapshot = await self.archive.get_wardrobe_snapshot()
        conditions = self.runtime.wardrobe_life._wardrobe_conditions(day, self.now)
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
            self.runtime.wardrobe_life._review_wardrobe(
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

    async def test_body_condition_change_discards_background_review(self):
        from core.models.vitals import BodyCondition

        day = await self.day([])
        snapshot = await self.archive.get_wardrobe_snapshot()
        conditions = self.runtime.wardrobe_life._wardrobe_conditions(day, self.now)
        inventory_key = digest(
            {
                key: {
                    field: value.get(field)
                    for field in ("ownership", "condition")
                }
                for key, value in snapshot["items"].items()
            }
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow(*args, **kwargs):
            started.set()
            await release.wait()
            return "{}"

        self.runtime.call_text_model = slow
        task = asyncio.create_task(
            self.runtime.wardrobe_life._review_wardrobe(
                day, conditions, snapshot, inventory_key, digest([])
            )
        )
        await started.wait()

        def change_body(latest):
            latest.state = latest.state or LifeState()
            latest.state.physiological_rhythm.body_condition = BodyCondition(
                label="身体不适", intensity=75,
                burden_present=True, phase="active",
            )

        await self.archive.mutate_day(day.date, change_body)
        release.set()
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

    def test_body_burden_invalidates_suitability_without_clock_churn(self):
        from core.models.vitals import BodyCondition

        day = DayRecord(date="2026-10-08", state=LifeState())
        first = wardrobe_conditions(day, self.now, residence="现实居住地")
        day.state.physiological_rhythm.body_condition = BodyCondition(
            label="身体不适", intensity=75,
            burden_present=True, phase="active",
        )
        changed = wardrobe_conditions(day, self.now, residence="现实居住地")
        self.assertNotEqual(suitability_key(first), suitability_key(changed))
        day.state.physiological_rhythm.body_condition.started_at = "2026-10-08 12:01"
        timed = wardrobe_conditions(day, self.now, residence="现实居住地")
        self.assertEqual(suitability_key(changed), suitability_key(timed))

    def test_suitability_ignores_home_activity_wording_and_weather_metadata(self):
        conditions = {
            "date": "2026-10-08",
            "residence": "现实居住地",
            "weather_known": True,
            "weather": {
                "raw": "城市 多云 28°C AQI 30",
                "temp": 28,
                "condition": "多云",
                "is_warm": True,
                "outfit_hint": "建议轻薄穿着",
            },
            "current_activity": {
                "activity": "午睡醒来，坐在书桌前导出相片",
                "activity_kind": "other",
                "place_kind": "home",
                "duration_minutes": 30,
                "execution_state": "active",
            },
            "next_activity": {
                "activity": "起身去琴房做手腕热身",
                "activity_kind": "other",
                "place_kind": "home",
                "duration_minutes": 20,
                "execution_state": "planned",
            },
            "upcoming_outings": [
                {
                    "time": "2026-10-08T18:00:00",
                    "activity": "去书店逛逛",
                    "place_kind": "poi",
                }
            ],
            "current_outfit": "浅粉针织衫和牛仔短裤",
            "current_ids": "253,293",
            "body": {},
        }
        changed_copy = json.loads(json.dumps(conditions, ensure_ascii=False))
        changed_copy["weather"].update(
            raw="城市 多云 29°C AQI 42",
            temp=29,
            outfit_hint="天气预报补充说明有变化",
        )
        changed_copy["current_activity"].update(
            activity="午睡醒来，处理照片后准备慢练钢琴",
            duration_minutes=40,
            execution_state="completed",
        )

        self.assertEqual(suitability_key(conditions), suitability_key(changed_copy))
        changed_plan = json.loads(json.dumps(conditions, ensure_ascii=False))
        changed_plan["upcoming_outings"][0]["activity"] = "去公园散步"
        self.assertNotEqual(suitability_key(conditions), suitability_key(changed_plan))
        changed_kind = json.loads(json.dumps(conditions, ensure_ascii=False))
        changed_kind["current_activity"]["activity_kind"] = "exercise"
        self.assertNotEqual(suitability_key(conditions), suitability_key(changed_kind))
        changed_copy["next_activity"]["place_kind"] = "poi"
        self.assertNotEqual(suitability_key(conditions), suitability_key(changed_copy))

    async def test_scene_gap_uses_outing_and_sleep_activity_types(self):
        item = await self.item(
            used=True,
            attrs={"scene_categories": ["home", "sleep"]},
        )
        snapshot = await self.archive.get_wardrobe_snapshot()
        outing = [{
            "time": "2026-10-08T18:00:00",
            "activity": "去书店",
            "activity_kind": "outdoor",
            "place_kind": "poi",
        }]

        sleep = [{
            "time": "2026-10-08T23:00:00",
            "activity": "准备睡觉",
            "activity_kind": "sleep",
            "place_kind": "home",
        }]
        targets = wardrobe_targets({}, outing + sleep)
        self.assertEqual([t["scene_category"] for t in targets], ["public", "sleep"])
        choices = validate_scene_choices([
            {"target_id": targets[0]["id"], "verdict": "missing", "item_ids": [],
             "considered_ids": [item.id], "confidence": 0.9, "reason": "只有居家睡衣，不适合外出"},
            {"target_id": targets[1]["id"], "verdict": "fits", "item_ids": [item.id],
             "confidence": 0.9, "reason": "这套睡衣适合睡眠"},
        ], targets, [item], snapshot)
        self.assertEqual([c["verdict"] for c in choices], ["missing", "fits"])
        self.assertNotEqual(
            suitability_key({"current_activity": {"activity_kind": "sleep", "place_kind": "home"}}),
            suitability_key({"current_activity": {"activity_kind": "exercise", "place_kind": "home"}}),
        )

    async def test_scene_gap_accepts_model_approved_candidate_for_adoption(self):
        item = await self.item(
            attrs={"scene_categories": ["outdoor", "public"]},
        )
        snapshot = await self.archive.get_wardrobe_snapshot()
        outing = [{
            "time": "2026-10-08T18:00:00",
            "activity": "去书店",
            "activity_kind": "outdoor",
            "place_kind": "poi",
        }]
        targets = wardrobe_targets({}, outing)
        choices = validate_scene_choices([
            {"target_id": targets[0]["id"], "verdict": "fits", "item_ids": [item.id],
             "confidence": 0.9, "reason": "候选可正式纳入用于外出"},
        ], targets, [item], snapshot, {item.id})
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0]["item_ids"], [item.id])

    def test_wardrobe_review_runs_once_per_day_when_context_is_stable(self):
        due = self.runtime.wardrobe_life._wardrobe_review_due
        profile = {"reviewed_at": "2026-10-08 12:00"}

        self.assertFalse(due(profile, self.now + datetime.timedelta(minutes=30)))
        self.assertTrue(due(profile, self.now + datetime.timedelta(days=1)))
        self.assertTrue(due({}, self.now))

    async def save_stable_review(self):
        day = await self.day([])
        await self.archive.record_wardrobe_wear(
            day,
            event_id="wear:" + digest(
                [
                    day.date,
                    day.meta.get("outfit_fact_confirmed_at"),
                    day.meta.get("style_catalog_reference_ids"),
                ]
            ),
        )
        snapshot = await self.archive.get_wardrobe_snapshot()
        conditions = self.runtime.wardrobe_life._wardrobe_conditions(day, self.now)
        inventory_key = digest(
            {
                key: {field: value.get(field) for field in ("ownership", "condition")}
                for key, value in snapshot["items"].items()
            }
        )
        feedback_key = digest(
            [
                event["event_id"]
                for event in snapshot["events"]
                if event["kind"] in {"feedback", "wear"}
            ]
        )
        profile = {
            **snapshot["profile"],
            "review_version": _WARDROBE_REVIEW_VERSION,
            "catalog_key": digest({}),
            "context_key": suitability_key(conditions),
            "inventory_key": inventory_key,
            "feedback_key": feedback_key,
            "reviewed_at": self.now.isoformat(sep=" "),
        }
        self.assertTrue(
            await self.archive.save_wardrobe_review(
                profile,
                expected_revision=snapshot["profile_revision"],
                at=profile["reviewed_at"],
            )
        )
        return day

    async def test_stable_wardrobe_review_skips_half_hour_recheck(self):
        await self.save_stable_review()

        self.runtime.now += datetime.timedelta(minutes=30)
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))

        self.runtime.call_text_model.assert_not_awaited()

    async def test_feedback_waits_for_shared_interval_without_delaying_existing_jobs(self):
        self.runtime.config.state.refresh_minutes = 20
        await self.save_stable_review()
        await self.archive.record_wardrobe_feedback(
            event_id="feedback", item_ids=[], text="喜欢这种自然日常的搭配",
            at=str(self.now), scope="chat",
        )
        await self.archive.enqueue_wardrobe_job(
            "existing-job", {"automatic": False, "count": 1}, at=str(self.now),
        )
        service = self.runtime.wardrobe_life
        service._run_wardrobe_job = AsyncMock()
        service._review_wardrobe = AsyncMock()
        self.runtime.now += datetime.timedelta(minutes=19)
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
        service._run_wardrobe_job.assert_awaited_once()
        service._review_wardrobe.assert_not_awaited()
        self.runtime.now += datetime.timedelta(minutes=1)
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
        service._review_wardrobe.assert_awaited_once()

    async def test_new_outing_bypasses_routine_feedback_wait(self):
        await self.save_stable_review()
        self.runtime.config.state.refresh_minutes = 120
        self.runtime.now += datetime.timedelta(minutes=1)
        await self.archive.mutate_day(
            "2026-10-08", lambda day: day.timeline.append(TimelineItem(
                time="12:10", activity="去附近书店", activity_kind="outdoor", place_kind="poi",
            )),
        )
        self.runtime.wardrobe_life._review_wardrobe = AsyncMock()
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
        self.runtime.wardrobe_life._review_wardrobe.assert_awaited_once()

    async def test_failed_review_retries_at_shared_interval_but_new_scene_can_interrupt(self):
        await self.day([])
        self.runtime.config.state.refresh_minutes = 20
        self.runtime.call_text_model.return_value = "invalid"
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
        self.runtime.call_text_model.assert_awaited_once()
        self.runtime.now += datetime.timedelta(minutes=19)
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
        self.runtime.call_text_model.assert_awaited_once()
        self.runtime.now += datetime.timedelta(minutes=1)
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
        self.assertEqual(self.runtime.call_text_model.await_count, 2)
        await self.archive.mutate_day(
            "2026-10-08", lambda day: setattr(day, "outfit", "刚换好的外出服"),
        )
        self.runtime.now += datetime.timedelta(minutes=1)
        await self.runtime.check_wardrobe_life()
        await asyncio.gather(*list(self.runtime.background.tasks))
        self.assertEqual(self.runtime.call_text_model.await_count, 3)
