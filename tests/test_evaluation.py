import unittest

from evals.metrics import evaluate_case, summarize_evaluation


class EvaluationMetricTests(unittest.TestCase):
    def test_metrics_use_labels_and_pre_repair_thesis(self):
        case = {
            "id": "fixture",
            "expected": {
                "task_type": "technical_analysis",
                "tools": ["yahoo_finance", "price_history"],
                "critical_evidence_types": ["market_price", "price_history"],
            },
        }
        raw_thesis = {
            "current_view": "Price rose 12%.",
            "upside": {
                "supporting_evidence_ids": ["EV-001", "EV-MISSING"],
                "chain": [{"claim": "Trend", "evidence_ids": ["EV-001", "EV-MISSING"]}],
            },
            "downside": {"chain": [], "supporting_evidence_ids": []},
            "decision_brief": {"evidence_ids": ["EV-001"], "watch_items": []},
        }
        state = {
            "task": {"task_type": "technical_analysis"},
            "research_plan": {"sources": ["yahoo_finance", "price_history"]},
            "normalized_evidence": [
                {"id": "EV-001", "evidence_type": "market_price", "temporal_valid": True},
                {"id": "EV-002", "evidence_type": "price_history", "temporal_valid": True},
            ],
            "intermediate_results": [
                {"node": "generate_thesis_graph", "output": {"llm_raw_output": raw_thesis}}
            ],
            "verification": {
                "issues": [
                    {"type": "unverified_numeric_claim_repaired", "details": [{"path": "current_view"}]}
                ]
            },
            "node_trace": [{"node": "build_report"}],
            "report": {"run_id": "fixture"},
            "llm_calls": [
                {"success": True, "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
            ],
            "run_metrics": {
                "total_duration_ms": 1500,
                "model_calls": {
                    "total": 1,
                    "successful": 1,
                    "fallbacks": 0,
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "metered_api_cost_usd": 0.0,
                    "estimated_equivalent_cost_usd": None,
                },
            },
        }
        result = evaluate_case(case, state)
        self.assertTrue(result["task"]["correct"])
        self.assertTrue(result["tools"]["exact_match"])
        self.assertEqual(result["critical_evidence"]["coverage_pct"], 100.0)
        self.assertEqual(result["references"]["invalid_mentions"], 2)
        self.assertEqual(result["numeric_claims"]["error_rate_pct"], 100.0)
        summary = summarize_evaluation([result])
        self.assertEqual(summary["task_classification_accuracy_pct"], 100.0)
        self.assertEqual(summary["token_and_cost"]["total_tokens"], 120)
        self.assertEqual(summary["runtime"]["average_ms"], 1500)


if __name__ == "__main__":
    unittest.main()
