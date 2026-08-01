import unittest
import tempfile
from pathlib import Path

from support import LifeArchive
from core.evaluation import (
    ScenarioObservation,
    ScenarioRunner,
    ProductionScenarioEvaluator,
    default_virtual_life_scenarios,
)


class ScenarioRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_scenarios_can_reach_full_score(self):
        cases = default_virtual_life_scenarios()

        async def evaluator(case):
            state = dict(case.expected_state)
            return ScenarioObservation(
                decision=case.expected_decision,
                reason_code="expected_behavior",
                state=state,
                stages=case.required_stages,
            )

        report = await ScenarioRunner().run(cases, evaluator)
        self.assertEqual(report.total, 8)
        self.assertEqual(report.passed, 8)
        self.assertEqual(report.score, 100.0)

    async def test_report_detects_overreach_and_unsupported_claims(self):
        case = default_virtual_life_scenarios()[3]

        report = await ScenarioRunner().run(
            [case],
            lambda _: {
                "decision": "reply",
                "reason_code": "model_overreach",
                "claims": ["已经发过照片"],
                "stages": ["candidate", "evaluated"],
            },
        )

        self.assertEqual(report.passed, 0)
        self.assertEqual(report.proactive_overreach_rate, 1.0)
        self.assertEqual(report.unsupported_claim_rate, 1.0)
        self.assertEqual(report.results[0].missing_stages, ["cooldown"])

    async def test_production_evaluator_replays_real_domain_boundaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = LifeArchive(Path(tmpdir) / "daily_life.db")
            try:
                evaluator = ProductionScenarioEvaluator(archive)
                report = await ScenarioRunner().run(
                    default_virtual_life_scenarios(), evaluator.evaluate
                )
                self.assertEqual(report.total, 8)
                self.assertEqual(report.passed, 8)
                self.assertEqual(report.unsupported_claim_rate, 0.0)
                self.assertEqual(report.lifecycle_completeness, 1.0)
            finally:
                archive.close()


if __name__ == "__main__":
    unittest.main()
