import json
import unittest

import support  # noqa: F401 - 安装轻量级 AstrBot 测试替身

from core.models import DayRecord
from core.runtime.refresh import RefreshMixin


class RefreshLegacyRepairTest(unittest.TestCase):
    def test_detects_legacy_expired_outfit_action(self):
        action_id = "2026-08-03:change_outfit:legacy"
        day = DayRecord(
            date="2026-08-03",
            meta={
                "planned_life_actions": json.dumps(
                    [{"action_id": action_id, "action_type": "change_outfit"}
                ]
                ),
                "life_action_expirations": json.dumps(
                    {action_id: {"status": "expired"}}
                ),
            },
        )

        self.assertTrue(RefreshMixin._has_legacy_outfit_expiration(day))

    def test_ignores_real_or_unrelated_expiration(self):
        day = DayRecord(
            date="2026-08-03",
            meta={
                "planned_life_actions": json.dumps(
                    [{"action_id": "photo-1", "action_type": "photo"}]
                ),
                "life_action_expirations": json.dumps(
                    {"photo-1": {"status": "expired"}}
                ),
            },
        )

        self.assertFalse(RefreshMixin._has_legacy_outfit_expiration(day))


if __name__ == "__main__":
    unittest.main()
