import unittest

from agent.tools.targets import target_assessment, enforce_target_summary
from agent.tools.quantitative import analyze_quantitatively
from agent.nodes import AgentNodes


class TargetTests(unittest.TestCase):
    def test_three_user_examples(self):
        low = target_assessment(365, 300)
        near = target_assessment(365, 400)
        extreme = target_assessment(365, 4000)
        self.assertTrue(low["condition_met_at_quote"])
        self.assertEqual(near["status"], "requires_rise")
        self.assertAlmostEqual(near["signed_distance_pct"], 9.58904109589)
        self.assertTrue(extreme["extreme_target"])
        self.assertIsNone(extreme["probability"])

    def test_direction_and_equality(self):
        self.assertEqual(target_assessment(365, 300, condition="below")["status"], "requires_fall")
        self.assertTrue(target_assessment(365, 400, condition="below")["condition_met_at_quote"])
        self.assertTrue(target_assessment(365, 365)["condition_met_at_quote"])
        self.assertEqual(target_assessment(365, 300, query="Will it fall to 300?")["condition"], "below")

    def test_invalid_and_missing(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                target_assessment(365, value)
        self.assertEqual(target_assessment(None, 300)["status"], "quote_unavailable")

    def test_quantitative_and_summary_guard(self):
        analysis = analyze_quantitatively([{"kind": "market_price", "payload": {"price": 365}}], [], 300, query="Can it reach 400?")
        result = enforce_target_summary({"current_view": "Difficult", "upside": {}}, analysis["target_assessment"])
        self.assertIn("already", result["current_view"])
        self.assertIn("300", result["current_view"])
        self.assertIn("does not prove", result["future_expectation"])
        self.assertEqual(result["upside"], {})

    def test_verifier_cannot_restore_generic_difficulty(self):
        nodes = AgentNodes(None, None)
        for target in (300, 400, 4000):
            with self.subTest(target=target):
                analysis = analyze_quantitatively([{"kind": "market_price", "payload": {"price": 365}}], [], target)
                state = {"task": {}, "target_price": target, "quantitative_analysis": analysis, "normalized_evidence": [], "max_retries": 0}
                state["thesis_graph"] = nodes._fallback_thesis(state)
                state["thesis_graph"]["current_view"] = "Reaching the target is difficult."
                output = nodes.verify_and_calibrate(state)
                self.assertEqual(output["thesis_graph"]["current_view"], analysis["target_assessment"]["summary"])
                self.assertTrue(output["verification"]["checks"]["target_condition_consistent"])
                self.assertTrue(output["verification"]["checks"]["numbers_calculated_by_python"])
