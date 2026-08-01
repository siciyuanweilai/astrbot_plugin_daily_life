import unittest

from core.evaluation import (
    ScenarioObservation,
    ScenarioRunner,
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


if __name__ == "__main__":
    unittest.main()
