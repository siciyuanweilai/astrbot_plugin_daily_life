import unittest

from core.runtime.context import ContextSnapshotRepository


class ContextSnapshotRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_repository_delegates_to_archive_contract(self):
        class Archive:
            def __init__(self):
                self.calls = []

            async def get_context_snapshot(self, **kwargs):
                self.calls.append(kwargs)
                return {"relationships": []}

        archive = Archive()
        repository = ContextSnapshotRepository(archive)

        result = await repository.read(
            max_summaries=6,
            experience_scope="test-scope",
            session_id="test-session",
        )

        self.assertEqual(result, {"relationships": []})
        self.assertEqual(
            archive.calls,
            [
                {
                    "max_summaries": 6,
                    "experience_scope": "test-scope",
                    "session_id": "test-session",
                }
            ],
        )

    async def test_repository_rejects_archive_without_snapshot_contract(self):
        with self.assertRaisesRegex(TypeError, "缺少上下文快照读取能力"):
            ContextSnapshotRepository(object())


if __name__ == "__main__":
    unittest.main()
