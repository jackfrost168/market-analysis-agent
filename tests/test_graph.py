import tempfile
import unittest
from pathlib import Path

from agent.graph import graph_spec, next_node_for_state
from agent.llm import StructuredResult
from agent.service import AgentService


class OfflineLlm:
    def generate_structured(self, prompt, response_model, model=None, temperature=0.15):
        return StructuredResult(
            success=False,
            model="offline-test-model",
            error="Intentional test fallback",
        )

    def list_models(self):
        return []

    def choose_model(self, requested=None):
        return "offline-test-model"


class FixtureResearchTools:
    def collect(self, asset, plan, query, target_price, user_notes, user_headlines):
        raw = [
            {
                "kind": "market_price",
                "payload": {
                    "success": True,
                    "price": 100000.1234,
                    "currency": "USD",
                    "return_1d_pct": 1.0,
                    "return_1w_pct": 3.0,
                    "timestamp": "2026-08-31T20:00:00+00:00",
                    "retrieved_at": "2026-09-01T00:00:00+00:00",
                    "source": "finance.yahoo.chart",
                },
            },
            {
                "kind": "price_history",
                "payload": {
                    "success": True,
                    "range": "6mo",
                    "retrieved_at": "2026-09-01T00:00:00+00:00",
                    "source": "finance.yahoo.chart",
                    "points": [
                        {"time": index, "price": 95000.0 + index * 100.0}
                        for index in range(30)
                    ],
                },
            },
            {
                "kind": "news",
                "payload": {
                    "success": True,
                    "items": [
                        {
                            "headline": "Bitcoin ETF inflows rise as demand strengthens",
                            "summary": "Fresh ETF inflows and stronger demand supported the latest market move.",
                            "source_name": "Test Publisher",
                            "published_at": "2026-08-31T12:00:00+00:00",
                        }
                    ],
                },
            },
            {
                "kind": "polymarket",
                "payload": {
                    "success": True,
                    "source": "gamma-api.polymarket.com",
                    "items": [
                        {
                            "market": "Will Bitcoin trade above $110k this month?",
                            "probability": 0.55,
                            "probability_outcome": "Yes",
                            "matched_thresholds": [110000.0],
                            "price_relevance": 90,
                            "retrieved_at": "2026-09-01T00:00:00+00:00",
                            "source": "gamma-api.polymarket.com",
                        }
                    ],
                },
            },
            {
                "kind": "vector_history",
                "payload": {"success": True, "items": []},
            },
        ]
        statuses = {
            "yahoo_finance": {"success": True, "status": "success", "record_count": 1},
            "price_history": {"success": True, "status": "success", "record_count": 30},
            "google_news": {"success": True, "status": "success", "record_count": 1},
            "polymarket": {"success": True, "status": "success", "record_count": 1},
            "vector_db": {"success": True, "status": "success", "record_count": 0},
        }
        tool_calls = [
            {
                "tool_call_id": "fixture-tool-1",
                "node": "collect_evidence",
                "function": "fixture_market_bundle",
                "arguments": {"symbol": asset["symbol"]},
                "status": "success",
                "success": True,
                "record_count": len(raw),
                "latency_ms": 1,
                "result_preview": {"success": True},
            }
        ]
        return {
            "raw_evidence": raw,
            "source_status": statuses,
            "payloads": {},
            "tool_calls": tool_calls,
        }

    def targeted_retrieval(self, *args, **kwargs):
        raise AssertionError("The fixture already satisfies the evidence gate")


class GraphIntegrationTests(unittest.TestCase):
    def test_missing_evidence_retries_once_then_finishes_with_limitations(self):
        class MissingEvidenceTools:
            def __init__(self):
                self.attempts = []

            def collect(self, *args, **kwargs):
                return {"raw_evidence": [], "source_status": {}, "payloads": {}, "tool_calls": []}

            def targeted_retrieval(self, asset, searches, existing_raw, target_price, attempt):
                self.attempts.append(attempt)
                return self.collect()

        with tempfile.TemporaryDirectory() as directory:
            tools = MissingEvidenceTools()
            service = AgentService(Path(directory), llm=OfflineLlm(), research_tools=tools)
            state = service.analyze({
                "asset": "AAPL", "query": "Show the price trend and volatility of Apple.",
                "max_retries": 1,
            })
            self.assertEqual(tools.attempts, [1])
            self.assertEqual(state["retrieval_attempts"], 1)
            self.assertFalse(state["evidence_gate"]["sufficient"])
            self.assertTrue(state["report"]["limitations"])
            self.assertEqual(state["node_trace"][-1]["node"], "build_report")

    def test_progress_shows_pending_node_not_completed_node(self):
        self.assertEqual(next_node_for_state({}), "understand_request")
        self.assertEqual(next_node_for_state({
            "node_trace": [{"node": "plan_research"}], "next_step": "build_report"
        }), "collect_evidence")
        self.assertEqual(next_node_for_state({
            "node_trace": [{"node": "targeted_retrieval"}], "next_step": "targeted_retrieval"
        }), "normalize_and_evidence_gate")

    def test_progress_tracks_both_conditional_edges(self):
        for node in ("normalize_and_evidence_gate", "verify_and_calibrate"):
            with self.subTest(node=node):
                self.assertEqual(next_node_for_state({
                    "node_trace": [{"node": node}], "next_step": "targeted_retrieval"
                }), "targeted_retrieval")
        self.assertEqual(next_node_for_state({"node_trace": [{"node": "build_report"}]}), "END")

    def test_graph_shape(self):
        spec = graph_spec()
        self.assertEqual(spec["node_count"], 9)
        self.assertEqual(spec["edge_count"], 12)

    def test_complete_offline_run_uses_real_graph_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(
                Path(directory), llm=OfflineLlm(), research_tools=FixtureResearchTools()
            )
            state = service.analyze(
                {
                    "asset": "BTC",
                    "query": "Why did Bitcoin move today?",
                    "horizon": "1 day",
                    "as_of": "2026-09-01T00:00:00+00:00",
                    "model": "offline-test-model",
                    "max_retries": 1,
                }
            )
            self.assertEqual(state["task"]["task_type"], "move_explanation")
            self.assertTrue(state["evidence_gate"]["sufficient"])
            self.assertEqual(state["retrieval_attempts"], 0)
            self.assertIn("report", state)
            self.assertEqual(state["report"]["workflow"]["framework"], "LangGraph")
            visited = [item["node"] for item in state["node_trace"]]
            self.assertEqual(visited[0], "understand_request")
            self.assertEqual(visited[-1], "build_report")
            self.assertNotIn("targeted_retrieval", visited)
            self.assertEqual(
                state["report"]["quantitative_signals"]["calculation_mode"],
                "deterministic_python",
            )
            self.assertEqual(len(state["decision_audit"]), len(state["node_trace"]))
            self.assertEqual(len(state["intermediate_results"]), len(state["node_trace"]))
            self.assertEqual(state["tool_calls"][0]["function"], "fixture_market_bundle")
            self.assertEqual(
                state["report"]["agent_audit"]["decision_audit"],
                state["decision_audit"],
            )
            self.assertEqual(
                state["report"]["agent_proof"]["actual_nodes_executed"],
                visited,
            )
            self.assertIn("observability", state["report"])
            scores = state["report"]["confidence_scores"]
            self.assertIsInstance(scores["composite_score"], float)
            self.assertGreaterEqual(scores["composite_score"], 0)
            self.assertLessEqual(scores["composite_score"], 100)
            self.assertEqual(state["report"]["observability"]["model_calls"]["total"], 3)
            self.assertTrue(state["report"]["decision_brief"]["watch_items"])
            reopened = AgentService(Path(directory), llm=OfflineLlm(), research_tools=FixtureResearchTools())
            saved = reopened.runs.get(state["run_id"])
            self.assertEqual(saved["report"], state["report"])
            self.assertEqual(saved["state"]["tool_calls"], state["tool_calls"])


if __name__ == "__main__":
    unittest.main()
