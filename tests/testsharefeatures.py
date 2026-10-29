"""生活进展版本与社交观察回执的归属、幂等和公开范围。"""

import datetime
import tempfile
import types
import unittest
from pathlib import Path

import support  # noqa: F401
from core.archive import LifeArchive
from core.models import DayRecord
from core.runtime.mirror.sharing import project_continuity
from core.runtime.sharememory import search_share_memories
from testsharelink import _IntegrationRuntime


class ShareFeatureTests(unittest.IsolatedAsyncioTestCase):
    def test_progress_version_tracks_meaningful_changes_not_timestamps(self):
        scope = "qq:FriendMessage:a"
        row = {
            "id": "activity:bread",
            "scope": scope,
            "kind": "shared_activity",
            "title": "做面包",
            "state": "active",
            "progress": "刚开始和面",
            "at": "2026-10-30T10:00:00",
        }

        def project():
            return project_continuity([row], {}, scope)["activities"][0]

        original = project()
        self.assertEqual(original["event_id"], row["id"])
        row.update(at="2026-10-30T11:00:00", revision=2, progress="刚开始和面。")
        self.assertEqual(original["progress_version"], project()["progress_version"])
        row["progress"] = "发酵完成"
        self.assertNotEqual(original["progress_version"], project()["progress_version"])
        self.assertEqual(
            project_continuity([row], {}, "qzone_broadcast")["activities"], []
        )

    async def test_observation_and_like_never_become_my_utterances_or_private_chats(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            archive = LifeArchive(Path(folder) / "life.db")
            date = datetime.date.today().isoformat()

            class Runtime(_IntegrationRuntime):
                async def resolve_injection_target(self, now):
                    return date, False

            runtime = Runtime()
            runtime.archive = archive
            try:
                await archive.save_day(DayRecord(date=date))
                for scene in ("qzone_observation", "qzone_like"):
                    receipt = {
                        "scene": scene,
                        "event_id": scene + ":photo",
                        "post_id": "friend:photo",
                        "content": "看到文字"
                        if scene == "qzone_observation"
                        else "已确认点赞",
                        "post_context": {
                            "author_name": "朋友",
                            "content": "摄影镜头新设计",
                            "occurred_at": 123,
                        },
                        "occurred_at": date + "T12:00:00",
                    }
                    self.assertTrue(await runtime.record_public_activity(receipt))
                    self.assertTrue(await runtime.record_public_activity(receipt))
                tasks = await archive.get_durable_tasks(kind="public_activity_receipt")
                self.assertEqual(len(tasks), 2)
                world = await archive.get_continuous_life()
                events = world["kernel"]["events"]
                self.assertEqual(len(events), 2)
                self.assertTrue(
                    any(row["kind"] == "public_observation" for row in events)
                )
                self.assertNotIn("chat_events", world)
                self.assertNotIn("我：", str(events))
                result = await search_share_memories(
                    types.SimpleNamespace(archive=archive),
                    "摄影镜头",
                    "qzone_broadcast",
                )
                self.assertEqual(
                    {row["kind"] for row in result},
                    {"public_observation", "public_like"},
                )
                self.assertNotIn("我：", str(result))
                self.assertEqual(
                    await archive.read_share_activity_receipts("qq:FriendMessage:a"), []
                )
            finally:
                archive.close()
