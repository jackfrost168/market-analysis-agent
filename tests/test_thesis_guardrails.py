import unittest

from agent.nodes import AgentNodes, _asserts_price_causality


class ThesisGuardrailTests(unittest.TestCase):
    def test_causal_disclaimers_are_not_positive_assertions(self):
        self.assertFalse(_asserts_price_causality("The move does not establish a fundamental cause."))
        self.assertFalse(_asserts_price_causality("Yahoo momentum is not a fundamental cause."))
        self.assertTrue(_asserts_price_causality("Momentum drives revenue growth."))
        self.assertTrue(_asserts_price_causality("It does not establish a cause, but momentum drives earnings."))

    def test_source_repair_preserves_unaffected_summary_and_removes_bad_reason(self):
        nodes = AgentNodes(None, None)
        state = {
            "task": {"task_type": "technical_analysis"},
            "normalized_evidence": [],
            "quantitative_analysis": {"market": {"period_return_pct": 20.0, "max_drawdown_pct": -12.0}},
        }
        thesis = nodes._fallback_thesis(state)
        thesis["current_view"] = "The retrieved history shows a gain of 20%."
        thesis["future_expectation"] = "Polymarket implies the rally will continue."
        thesis["why"] = ["Historical return is positive.", "Polymarket confirms the rally."]
        repaired = nodes._repair_thesis(thesis, state)
        self.assertEqual(repaired["current_view"], thesis["current_view"])
        self.assertNotIn("Polymarket", repaired["future_expectation"])
        self.assertEqual(repaired["why"], ["Historical return is positive."])
        self.assertIn("-12.000%", nodes._fallback_thesis(state)["current_view"])
