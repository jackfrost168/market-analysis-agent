import io
import json
import os
import tempfile
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

from pydantic import BaseModel

from agent.llm import OllamaClient
from agent.observability import build_run_metrics, aggregate_run_metrics
from agent.service import AgentService
from awsdeploy.api import create_app
from awsdeploy.bridge import AgentBridge
from fastapi.testclient import TestClient


class Answer(BaseModel):
    ok: bool


ROUTES = json.dumps({
    "qwen3-8b": {"provider": "qwen_api", "base_url": "https://dashscope-us.aliyuncs.com/compatible-mode/v1", "label": "Qwen API 8B"},
    "qwen3-30b-a3b": {"provider": "qwen_api", "base_url": "https://dashscope-us.aliyuncs.com/compatible-mode/v1", "label": "Qwen API 30B-A3B"},
    "qwen3:8b": {"base_url": "http://127.0.0.1:11434", "label": "Qwen3 8B (Mac)"},
})


def response(content='{"ok":true}', usage=True, finish_reason="stop"):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    if usage:
        body["usage"] = {"prompt_tokens": 3000, "completion_tokens": 1000, "total_tokens": 4000}
    return io.BytesIO(json.dumps(body).encode())


@patch.dict(os.environ, {"OLLAMA_MODEL_ROUTES": ROUTES, "OLLAMA_MODEL": "qwen3-8b", "DASHSCOPE_API_KEY": "fixture-key-never-log", "QWEN_API_BASE_URL": "", "QWEN_API_PRICING_JSON": ""})
class QwenApiTests(unittest.TestCase):
    def test_default_8b_structured_output_and_cost(self):
        client = OllamaClient()
        with patch("agent.qwen_api.urllib.request.urlopen", return_value=response()) as send:
            result = client.generate_structured("fixture", Answer)
        self.assertTrue(result.success)
        self.assertEqual(result.model, "qwen3-8b")
        self.assertEqual(result.data, {"ok": True})
        self.assertEqual(result.total_tokens, 4000)
        self.assertEqual(result.usage["metered_api_cost_usd"], 0.000503)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://dashscope-us.aliyuncs.com/compatible-mode/v1/chat/completions")
        body = json.loads(request.data)
        self.assertFalse(body["enable_thinking"])
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn("JSON Schema", body["messages"][0]["content"])
        self.assertEqual(body["max_tokens"], 4096)

    def test_30b_cost_and_counterfactual_call_limits(self):
        with patch("agent.qwen_api.urllib.request.urlopen", return_value=response()) as send:
            result = OllamaClient().generate_structured("fixture", Answer, model="qwen3-30b-a3b", max_output_tokens=512, timeout_seconds=15, think=False)
        self.assertTrue(result.success)
        self.assertEqual(result.usage["metered_api_cost_usd"], 0.000755)
        self.assertEqual(json.loads(send.call_args.args[0].data)["max_tokens"], 512)
        self.assertEqual(send.call_args.kwargs["timeout"], 15)

    def test_invalid_json_still_records_billable_usage(self):
        with patch("agent.qwen_api.urllib.request.urlopen", return_value=response('{"ok":"invalid"}')):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertFalse(result.success)
        self.assertEqual(result.usage["metered_api_cost_usd"], 0.000503)

    def test_truncated_output_is_not_accepted_and_usage_is_retained(self):
        with patch("agent.qwen_api.urllib.request.urlopen", return_value=response(finish_reason="length")):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertFalse(result.success)
        self.assertEqual(result.total_tokens, 4000)

    def test_timeout_has_unknown_cost_and_no_automatic_retry(self):
        with patch("agent.qwen_api.urllib.request.urlopen", side_effect=TimeoutError("fixture-key-never-log")) as send:
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertFalse(result.success)
        self.assertIsNone(result.usage["metered_api_cost_usd"])
        self.assertFalse(result.usage["usage_reported"])
        self.assertNotIn("fixture-key-never-log", result.error)
        send.assert_called_once()

    def test_http_error_response_is_not_logged(self):
        error = urllib.error.HTTPError("https://fixture", 401, "fixture-key-never-log", {}, io.BytesIO(b"private response"))
        with patch("agent.qwen_api.urllib.request.urlopen", side_effect=error):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertIn("401", result.error)
        self.assertNotIn("private", result.error)
        self.assertNotIn("fixture-key-never-log", result.error)

    def test_missing_usage_is_not_reported_as_free(self):
        with patch("agent.qwen_api.urllib.request.urlopen", return_value=response(usage=False)):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertTrue(result.success)
        self.assertFalse(result.usage["usage_reported"])
        self.assertIsNone(result.usage["metered_api_cost_usd"])

    def test_inventory_contains_api_models_and_mac_without_paid_probes(self):
        client = OllamaClient()
        with patch.object(client, "_inventory", return_value=[{"name": "qwen3:8b"}]) as inventory:
            models = client.list_models()
        self.assertEqual(len(models), 3)
        self.assertEqual(models[0]["status"], "configured")
        inventory.assert_called_once_with("http://127.0.0.1:11434/api/tags")
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "key is not configured"):
                client.validate_request("auto")
            client.validate_request("qwen3:8b")
            with patch.object(client, "_inventory", return_value=[]):
                self.assertFalse(client.list_models()[0]["available"])

    def test_custom_prices_and_url_are_applied(self):
        with patch.dict(os.environ, {"QWEN_API_PRICING_JSON": '{"qwen3-8b":{"input":0.18,"output":0.7}}', "QWEN_API_BASE_URL": "https://fixture/compatible-mode/v1"}):
            with patch("agent.qwen_api.urllib.request.urlopen", return_value=response()) as send:
                result = OllamaClient().generate_structured("fixture", Answer)
        self.assertEqual(result.metered_api_cost_usd, 0.00124)
        self.assertEqual(result.pricing["source"], "configured")
        self.assertEqual(send.call_args.args[0].full_url, "https://fixture/compatible-mode/v1/chat/completions")

    def test_cost_totals_include_failed_validation_and_unknown_calls(self):
        calls = [{"success": True, "usage": {"provider": "qwen_api", "metered_api_cost_usd": 0.000503}},
                 {"success": False, "usage": {"provider": "qwen_api", "metered_api_cost_usd": 0.000755}},
                 {"success": False, "usage": {"provider": "qwen_api", "metered_api_cost_usd": None}},
                 {"success": True, "usage": {"provider": "ollama", "metered_api_cost_usd": 0}}]
        metrics = build_run_metrics({"llm_calls": calls}, started_at="fixture", completed_at="fixture", total_duration_ms=1000)
        self.assertEqual(metrics["model_calls"]["metered_api_cost_usd"], 0.001258)
        self.assertEqual(metrics["model_calls"]["api_cost_unreported_calls"], 1)
        aggregate = aggregate_run_metrics([{"status": "failed", "state": {"run_metrics": metrics}}])
        self.assertEqual(aggregate["metered_api_cost_usd"], 0.001258)
        self.assertEqual(aggregate["api_cost_unreported_calls"], 1)

    def test_api_missing_key_is_rejected_before_queueing_and_inventory_keeps_mac(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            service = AgentService(Path(directory), llm=OllamaClient())
            api = TestClient(create_app(AgentBridge(service)))
            with patch.object(service.llm, "_inventory", return_value=[{"name": "qwen3:8b"}]):
                inventory = api.get("/api/models").json()
            self.assertEqual(inventory["auto_selected"], "qwen3-8b")
            self.assertFalse(inventory["default_available"])
            self.assertTrue(inventory["success"])
            with patch.object(service, "analyze") as analyze:
                result = api.post("/api/runs", json={"asset": "AAPL", "model": "auto"})
            self.assertEqual(result.status_code, 400)
            self.assertIn("key is not configured", result.json()["error"])
            analyze.assert_not_called()
            self.assertEqual(service.history(10), [])


if __name__ == "__main__":
    unittest.main()
