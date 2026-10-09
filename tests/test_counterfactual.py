import copy
import json
import os
import tempfile
from unittest.mock import Mock
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.counterfactual import Limits, run_counterfactual
from agent.graph import graph_spec
from agent.llm import OllamaClient, StructuredResult
from agent.service import AgentService


def example_state():
    claim = "Revenue and margin improvement support the positive thesis."
    return {
        "model": "fixture-model", "run_id": "cf-example", "conversation_id": "cf-example",
        "user_query": "Analyze the financial improvement.", "task": {"symbol": "ABC"},
        "normalized_evidence": [
            {"id": "E1", "title": "Revenue Growth", "evidence_type": "financial_statement",
             "source": "SEC", "reliability": 0.96, "content": "Revenue grew 20%. REVENUE_FACT_ONLY"},
            {"id": "E2", "title": "Gross Margin", "evidence_type": "financial_statement",
             "source": "SEC", "reliability": 0.96, "content": "Gross margin expanded from 30% to 35%. MARGIN_FACT_ONLY"},
            {"id": "E3", "title": "AI Demand News", "evidence_type": "news",
             "source": "Publisher", "reliability": 0.72, "content": "Analysts report stronger AI demand. NEWS_FACT_ONLY"},
        ],
        "quantitative_analysis": {"derived_revenue": "REVENUE_FACT_ONLY"},
        "thesis_graph": {"current_view": claim, "upside": {
            "scenario": claim, "supporting_evidence_ids": ["E1", "E2", "E3"], "chain": []
        }},
        "verification": {"passed": True}, "node_trace": [{"node": "build_report"}],
        "llm_calls": [], "report": {"workflow": {}, "llm": {"calls": []}},
    }


class ExampleLlm:
    """Deterministic TEST double; qualitative outcomes are not production rules."""

    def __init__(self):
        self.requests = []

    def generate_structured(self, prompt, response_model, **kwargs):
        data = json.loads(prompt.split("/no_think\n", 1)[1])
        self.requests.append((data, kwargs))
        if "claims" in data:
            output = {"claims": [{"claim_id": "upside", "selected_evidence_ids": ["E1", "E2", "E3"],
                                 "original_support": "strong", "reason": "Revenue and margin directly support the claim."}]}
        else:
            ids = {item["id"] for item in data["remaining_evidence"]}
            loses_core_fact = not {"E1", "E2"}.issubset(ids)
            output = {"counterfactual_support": "weak" if loses_core_fact else "strong",
                      "substantially_changes_thesis": loses_core_fact,
                      "reason": "A core financial assertion loses support." if loses_core_fact else "The financial evidence still supports the thesis."}
        return StructuredResult(success=True, model="fixture-model", data=response_model.model_validate(output).model_dump(),
                                prompt_tokens=100, completion_tokens=30, total_tokens=130)


class CounterfactualTests(unittest.TestCase):
    def test_saved_report_test_reuses_thesis_and_evidence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            llm = ExampleLlm()
            service = AgentService(Path(directory), llm=llm)
            state = example_state()
            state["report"]["generated_at"] = "2026-09-01T00:00:00Z"
            state["run_metrics"] = {"started_at": "original-start", "completed_at": "original-end", "total_duration_ms": 1000}
            service.runs.save(state)
            service.graph = Mock()
            service.research_tools = Mock()
            original = service.saved_run("cf-example")
            updated = service.counterfactual_saved_report("cf-example")
            self.assertEqual(updated["thesis_graph"], state["thesis_graph"])
            self.assertEqual(updated["normalized_evidence"], state["normalized_evidence"])
            self.assertEqual(updated["node_trace"], state["node_trace"])
            self.assertEqual(updated["run_metrics"]["total_duration_ms"], 1000)
            self.assertEqual(updated["run_metrics"]["completed_at"], "original-end")
            self.assertTrue(updated["report"]["counterfactual_evidence_test"]["added_to_saved_report"])
            self.assertEqual(len(llm.requests), 4)
            self.assertEqual(updated["run_metrics"]["model_calls"]["total"], 4)
            self.assertEqual(service.saved_run("cf-example")["created_at"], original["created_at"])
            self.assertEqual(service.counterfactual_saved_report("cf-example")["report"], updated["report"])
            self.assertEqual(len(llm.requests), 4)
            service.graph.stream.assert_not_called()
            service.research_tools.collect.assert_not_called()
            self.assertIsNone(service.counterfactual_saved_report("missing"))

    def test_example_ablations_and_no_state_mutation_or_evidence_leak(self):
        state = example_state()
        original = copy.deepcopy(state)
        llm = ExampleLlm()
        result = run_counterfactual(llm, state)
        self.assertEqual(state, original)
        self.assertEqual(result["counterfactual_tests"]["status"], "completed")
        tests = result["counterfactual_tests"]["tests"]
        self.assertEqual([item["importance"] for item in tests], ["Critical", "Critical", "Supporting"])
        self.assertEqual([item["impact"] for item in tests], ["high", "high", "low"])
        self.assertEqual(len(llm.requests), 4)  # one selection/baseline + three ablations
        scope = result["counterfactual_tests"]["scope"]
        self.assertEqual(scope["candidate_claims"], 1)
        self.assertEqual(scope["selected_claims"], 1)
        self.assertEqual(scope["completed_removal_tests"], 3)
        self.assertEqual(result["evidence_dependency"][0]["supporting_evidence_ids"], ["E1", "E2", "E3"])
        self.assertEqual(result["evidence_dependency"][0]["evidence"][0]["remaining_evidence_ids"], ["E2", "E3"])
        for removed, (payload, kwargs) in zip(["E1", "E2", "E3"], llm.requests[1:]):
            self.assertNotIn(removed, {item["id"] for item in payload["remaining_evidence"]})
            self.assertEqual(payload["claim"], state["thesis_graph"]["upside"]["scenario"])
            removed_marker = {"E1": "REVENUE_FACT_ONLY", "E2": "MARGIN_FACT_ONLY", "E3": "NEWS_FACT_ONLY"}[removed]
            self.assertNotIn(removed_marker, json.dumps(payload))
            self.assertLessEqual(kwargs["timeout_seconds"], 15)
            self.assertEqual(kwargs["max_output_tokens"], 512)
            self.assertFalse(kwargs["think"])
        for test in tests:
            for key in ["claim_id", "removed_evidence_id", "impact", "support_before", "support_after", "latency_ms"]:
                self.assertIn(key, test)
        self.assertNotIn("REVENUE_FACT_ONLY", json.dumps(result))  # no document copies in added state/log records

    def test_failed_ablation_is_not_labelled_supporting_and_stops_calls(self):
        class FailingLlm(ExampleLlm):
            def generate_structured(self, prompt, response_model, **kwargs):
                if "remaining_evidence" in prompt:
                    return StructuredResult(success=False, model="fixture", error="timed out")
                return super().generate_structured(prompt, response_model, **kwargs)
        result = run_counterfactual(FailingLlm(), example_state())
        self.assertEqual(result["counterfactual_tests"]["status"], "partial")
        tests = result["counterfactual_tests"]["tests"]
        self.assertEqual(len(tests), 1)
        self.assertIsNone(tests[0]["importance"])
        self.assertIsNone(tests[0]["impact"])
        self.assertEqual(len(result["llm_calls"]), 2)

    def test_supported_claim_that_weakens_is_important(self):
        class ModerateLlm(ExampleLlm):
            def generate_structured(self, prompt, response_model, **kwargs):
                result = super().generate_structured(prompt, response_model, **kwargs)
                if "remaining_evidence" in prompt:
                    result.data.update(counterfactual_support="moderate", substantially_changes_thesis=False)
                return result
        result = run_counterfactual(ModerateLlm(), example_state())
        self.assertTrue(all(item["importance"] == "Important" and item["impact"] == "medium"
                            for item in result["counterfactual_tests"]["tests"]))

    def test_two_claim_limit_has_at_most_seven_calls(self):
        state = example_state()
        state["thesis_graph"]["decision_brief"] = {
            "key_insight": "The financial improvement is more material than the news.",
            "evidence_ids": ["E1", "E2", "E3"],
        }
        class TwoClaimsLlm(ExampleLlm):
            def generate_structured(self, prompt, response_model, **kwargs):
                result = super().generate_structured(prompt, response_model, **kwargs)
                if "claims" in result.data:
                    second = copy.deepcopy(result.data["claims"][0])
                    second["claim_id"] = "key_insight"
                    result.data["claims"].append(second)
                return result
        with patch.dict(os.environ, {"COUNTERFACTUAL_MAX_CLAIMS": "2"}):
            result = run_counterfactual(TwoClaimsLlm(), state)
        self.assertEqual(len(result["counterfactual_tests"]["tests"]), 6)
        self.assertEqual(len(result["llm_calls"]), 7)

    def test_invalid_selection_cannot_test_unlinked_evidence(self):
        class BadSelection(ExampleLlm):
            def generate_structured(self, *args, **kwargs):
                return StructuredResult(success=True, model="fixture", data={"claims": [{
                    "claim_id": "upside", "selected_evidence_ids": ["E999"],
                    "original_support": "strong", "reason": "invalid selection"}]})
        result = run_counterfactual(BadSelection(), example_state())
        self.assertEqual(result["counterfactual_tests"]["status"], "partial")
        self.assertEqual(result["counterfactual_tests"]["tests"], [])

    def test_no_claim_or_evidence_skips_without_model_calls(self):
        llm = ExampleLlm()
        result = run_counterfactual(llm, {})
        self.assertEqual(result["counterfactual_tests"]["status"], "skipped")
        self.assertEqual(llm.requests, [])

    def test_unsupported_baseline_does_not_claim_importance(self):
        class UnsupportedBaseline(ExampleLlm):
            def generate_structured(self, *args, **kwargs):
                result = super().generate_structured(*args, **kwargs)
                result.data["claims"][0]["original_support"] = "unsupported"
                return result
        llm = UnsupportedBaseline()
        result = run_counterfactual(llm, example_state())
        self.assertEqual(result["counterfactual_tests"]["tests"], [])
        self.assertEqual(len(llm.requests), 1)

    def test_limits_are_clamped_and_two_evidence_setting_is_respected(self):
        with patch.dict(os.environ, {"COUNTERFACTUAL_MAX_CLAIMS": "100", "COUNTERFACTUAL_MAX_EVIDENCE": "100"}):
            self.assertEqual(Limits.from_env().max_claims, 2)
            self.assertEqual(Limits.from_env().max_evidence, 3)
        with patch.dict(os.environ, {"COUNTERFACTUAL_MAX_EVIDENCE": "2"}):
            result = run_counterfactual(ExampleLlm(), example_state())
            self.assertEqual(len(result["counterfactual_tests"]["tests"]), 2)

    def test_duplicate_news_group_is_not_tested_twice(self):
        state = example_state()
        state["evidence_groups"] = [{"group_id": "G1", "evidence_ids": ["E2", "E3"]}]
        result = run_counterfactual(ExampleLlm(), state)
        self.assertEqual([x["removed_evidence_id"] for x in result["counterfactual_tests"]["tests"]], ["E1", "E2"])

    def test_total_budget_exhaustion_stops_before_another_call(self):
        clock = [0.0]
        class BudgetLlm(ExampleLlm):
            def generate_structured(self, *args, **kwargs):
                result = super().generate_structured(*args, **kwargs)
                clock[0] = 61
                return result
        llm = BudgetLlm()
        with patch("agent.counterfactual.time.perf_counter", side_effect=lambda: clock[0]):
            result = run_counterfactual(llm, example_state())
        self.assertEqual(result["counterfactual_tests"]["status"], "partial")
        self.assertEqual(len(llm.requests), 1)

    def test_optional_service_hook_keeps_original_report_and_graph(self):
        class FixedGraph:
            def stream(self, *args, **kwargs):
                yield example_state()
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(Path(directory), llm=ExampleLlm())
            service.graph = FixedGraph()
            with patch.dict(os.environ, {"ENABLE_COUNTERFACTUAL_EVIDENCE_TEST": "false"}), patch("agent.service.run_counterfactual") as extension:
                baseline = service.analyze({}, run_id="cf-example")
                extension.assert_not_called()
                self.assertNotIn("counterfactual_tests", baseline)
                self.assertNotIn("counterfactual_evidence_test", baseline["report"])
                self.assertEqual(baseline["llm_calls"], [])
            with patch.dict(os.environ, {"ENABLE_COUNTERFACTUAL_EVIDENCE_TEST": "true"}):
                enhanced = service.analyze({}, run_id="cf-example")
            for field in ["thesis_graph", "verification", "normalized_evidence", "node_trace"]:
                self.assertEqual(enhanced[field], baseline[field])
            self.assertEqual(enhanced["report"]["counterfactual_evidence_test"]["status"], "completed")
            self.assertEqual(enhanced["run_metrics"]["model_calls"]["total"], 4)
            saved = service.saved_run("cf-example")
            self.assertEqual(saved["state"]["counterfactual_tests"], enhanced["counterfactual_tests"])
            self.assertEqual(graph_spec()["node_count"], 9)

    def test_llm_limits_are_optional_and_existing_requests_are_unchanged(self):
        from agent.schemas import SemanticPlan
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"response":"{}"}'
        client = OllamaClient(default_model="fixture")
        with patch("agent.llm.urllib.request.urlopen", return_value=response) as request:
            client.generate_structured("fixture", SemanticPlan)
            body = json.loads(request.call_args.args[0].data)
            self.assertEqual(body["options"], {"temperature": 0.15})
            self.assertNotIn("think", body)
            self.assertEqual(request.call_args.kwargs["timeout"], 180)
            client.generate_structured("fixture", SemanticPlan, timeout_seconds=8, max_output_tokens=256, think=False)
            body = json.loads(request.call_args.args[0].data)
            self.assertEqual(body["options"]["num_predict"], 256)
            self.assertFalse(body["think"])
            self.assertEqual(request.call_args.kwargs["timeout"], 8)

    def test_per_request_choice_overrides_environment_without_changing_default_runs(self):
        class FixedGraph:
            def stream(self, *args, **kwargs):
                yield example_state()
        with tempfile.TemporaryDirectory() as directory:
            service = AgentService(Path(directory), llm=ExampleLlm())
            service.graph = FixedGraph()
            with patch.dict(os.environ, {"ENABLE_COUNTERFACTUAL_EVIDENCE_TEST": "true"}):
                state = service.analyze({"enable_counterfactual_evidence_test": False})
                self.assertNotIn("counterfactual_tests", state)
                self.assertFalse(state["report"]["counterfactual_choice"]["enabled"])
                self.assertEqual(state["llm_calls"], [])
            with patch.dict(os.environ, {"ENABLE_COUNTERFACTUAL_EVIDENCE_TEST": "false"}):
                state = service.analyze({"enable_counterfactual_evidence_test": True})
                self.assertEqual(state["counterfactual_tests"]["status"], "completed")
                self.assertEqual(len(state["llm_calls"]), 4)
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                service.validate_payload({"enable_counterfactual_evidence_test": "false"}, "invalid")


if __name__ == "__main__":
    unittest.main()
