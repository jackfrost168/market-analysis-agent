import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import Stubber, ANY
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agent.bedrock import DEFAULT_MODEL, credential_status, runtime_client
from agent.llm import OllamaClient
from agent.observability import build_run_metrics, aggregate_run_metrics
from agent.service import AgentService
from awsdeploy.api import create_app
from awsdeploy.bridge import AgentBridge


class Answer(BaseModel):
    ok: bool


ROUTES = json.dumps({DEFAULT_MODEL: {"provider": "bedrock", "region": "us-east-1", "label": "Qwen3 32B (AWS Bedrock Converse)"},
                     "qwen3:8b": {"base_url": "http://127.0.0.1:11434", "label": "Qwen3 8B (Mac)"}})


def response(content='{"ok":true}', usage=True, stop="end_turn"):
    body = {"output": {"message": {"role": "assistant", "content": [{"text": content}]}},
            "stopReason": stop, "metrics": {"latencyMs": 20}}
    if usage:
        body["usage"] = {"inputTokens": 3000, "outputTokens": 1000, "totalTokens": 4000}
    return body


@patch.dict(os.environ, {"OLLAMA_MODEL_ROUTES": ROUTES, "OLLAMA_MODEL": DEFAULT_MODEL,
                        "BEDROCK_PRICING_JSON": "", "AWS_ACCESS_KEY_ID": "fixture", "AWS_SECRET_ACCESS_KEY": "fixture",
                        "AWS_EC2_METADATA_DISABLED": "true"})
class BedrockTests(unittest.TestCase):
    def test_default_32b_sdk_converse_request_and_usage(self):
        client = runtime_client("us-east-1", 30)
        stub = Stubber(client)
        stub.add_response("converse", response(), {"modelId": DEFAULT_MODEL,
                         "system": [{"text": ANY}], "messages": [{"role": "user", "content": [{"text": "fixture\n/no_think"}]}],
                         "inferenceConfig": {"temperature": 0.15, "maxTokens": 4096}})
        with stub, patch("agent.bedrock.runtime_client", return_value=client):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertTrue(result.success)
        self.assertEqual(result.model, DEFAULT_MODEL)
        self.assertEqual(result.data, {"ok": True})
        self.assertEqual(result.total_tokens, 4000)
        self.assertEqual(result.usage["metered_api_cost_usd"], 0.00105)
        self.assertEqual(result.usage["pricing"]["region"], "us-east-1")
        stub.assert_no_pending_responses()

    def test_counterfactual_token_limit_timeout_and_one_attempt(self):
        sdk = Mock()
        sdk.converse.return_value = response()
        with patch("agent.bedrock.runtime_client", return_value=sdk) as factory:
            result = OllamaClient().generate_structured("fixture", Answer, max_output_tokens=512, timeout_seconds=15, think=False)
        self.assertTrue(result.success)
        self.assertEqual(sdk.converse.call_args.kwargs["inferenceConfig"]["maxTokens"], 512)
        factory.assert_called_once_with("us-east-1", 15)
        config = runtime_client("us-east-1", 15).meta.config
        self.assertEqual(config.retries["total_max_attempts"], 1)
        self.assertEqual(config.connect_timeout + config.read_timeout, 15)

    def test_low_cost_models_use_their_own_rates_and_keep_qwen_default(self):
        rates = {"amazon.nova-micro-v1:0": 0.000245,
                 "amazon.nova-lite-v1:0": 0.00042,
                 "google.gemma-3-4b-it": 0.0002,
                 "google.gemma-3-12b-it": 0.00056}
        routes = json.loads(ROUTES)
        for model in rates:
            routes[model] = {"provider": "bedrock", "region": "us-east-1"}
        with patch.dict(os.environ, {"OLLAMA_MODEL_ROUTES": json.dumps(routes)}):
            llm = OllamaClient()
            self.assertEqual(llm.choose_model("auto"), DEFAULT_MODEL)
            for model, cost in rates.items():
                with self.subTest(model=model):
                    sdk = Mock()
                    sdk.converse.return_value = response('```json\n{"ok":true}\n```')
                    with patch("agent.bedrock.runtime_client", return_value=sdk):
                        result = llm.generate_structured("fixture", Answer, model=model)
                    self.assertTrue(result.success)
                    self.assertEqual(result.model, model)
                    self.assertEqual(result.metered_api_cost_usd, cost)
                    request = sdk.converse.call_args.kwargs
                    self.assertNotIn("/no_think", request["messages"][0]["content"][0]["text"])
                    self.assertEqual(request["inferenceConfig"]["maxTokens"], 4096)

    def test_nova_output_cap_respects_model_limit(self):
        from agent.bedrock import generate_bedrock
        sdk = Mock()
        sdk.converse.return_value = response()
        with patch("agent.bedrock.runtime_client", return_value=sdk):
            result = generate_bedrock("fixture", Answer, "amazon.nova-micro-v1:0", {}, temperature=0, timeout_seconds=30, max_output_tokens=8192)
        self.assertTrue(result.success)
        self.assertEqual(sdk.converse.call_args.kwargs["inferenceConfig"]["maxTokens"], 5000)

    def test_invalid_json_still_records_billable_usage(self):
        sdk = Mock()
        sdk.converse.return_value = response('{"ok":"invalid"}')
        with patch("agent.bedrock.runtime_client", return_value=sdk):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertFalse(result.success)
        self.assertEqual(result.metered_api_cost_usd, 0.00105)

    def test_truncated_output_is_not_accepted(self):
        sdk = Mock()
        sdk.converse.return_value = response(stop="max_tokens")
        with patch("agent.bedrock.runtime_client", return_value=sdk):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertFalse(result.success)
        self.assertEqual(result.total_tokens, 4000)

    def test_timeout_has_unknown_cost_and_no_retry_or_provider_switch(self):
        sdk = Mock()
        sdk.converse.side_effect = ReadTimeoutError(endpoint_url="https://fixture")
        with patch("agent.bedrock.runtime_client", return_value=sdk):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertFalse(result.success)
        self.assertIsNone(result.metered_api_cost_usd)
        self.assertFalse(result.usage_reported)
        self.assertEqual(result.model, DEFAULT_MODEL)
        sdk.converse.assert_called_once()

    def test_access_denied_keeps_error_code_without_raw_sdk_message(self):
        sdk = Mock()
        sdk.converse.side_effect = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "private credentials fixture"}}, "Converse")
        with patch("agent.bedrock.runtime_client", return_value=sdk):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertIn("AccessDeniedException", result.error)
        self.assertNotIn("private credentials", result.error)

    def test_missing_usage_is_not_reported_as_free(self):
        sdk = Mock()
        sdk.converse.return_value = response(usage=False)
        with patch("agent.bedrock.runtime_client", return_value=sdk):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertTrue(result.success)
        self.assertFalse(result.usage_reported)
        self.assertIsNone(result.metered_api_cost_usd)

    def test_think_tags_and_reasoning_blocks_do_not_break_json(self):
        sdk = Mock()
        body = response('<think></think>\n```json\n{"ok":true}\n```')
        body["output"]["message"]["content"].insert(0, {"reasoningContent": {"reasoningText": {"text": "reasoning", "signature": "fixture"}}})
        sdk.converse.return_value = body
        with patch("agent.bedrock.runtime_client", return_value=sdk):
            result = OllamaClient().generate_structured("fixture", Answer)
        self.assertTrue(result.success)
        self.assertEqual(result.completion_tokens, 1000)

    def test_inventory_only_exposes_bedrock_and_mac_no_paid_probes(self):
        client = OllamaClient()
        with patch.object(client, "_inventory", return_value=[{"name": "qwen3:8b"}]) as inventory:
            models = client.list_models()
        self.assertEqual([m["name"] for m in models], [DEFAULT_MODEL, "qwen3:8b"])
        self.assertEqual(models[0]["status"], "credentials_configured")
        inventory.assert_called_once_with("http://127.0.0.1:11434/api/tags")
        with self.assertRaises(ValueError):
            client.choose_model("qwen3-8b")
        with self.assertRaises(ValueError):
            client.choose_model("qwen3-30b-a3b")

    def test_cost_totals_preserve_historical_api_and_include_unknown_calls(self):
        calls = [{"success": True, "usage": {"provider": "bedrock", "metered_api_cost_usd": 0.00105}},
                 {"success": False, "usage": {"provider": "qwen_api", "metered_api_cost_usd": 0.000755}},
                 {"success": False, "usage": {"provider": "bedrock", "metered_api_cost_usd": None}},
                 {"success": True, "usage": {"provider": "ollama", "metered_api_cost_usd": 0}}]
        metrics = build_run_metrics({"llm_calls": calls}, started_at="fixture", completed_at="fixture", total_duration_ms=1000)
        self.assertEqual(metrics["model_calls"]["metered_api_cost_usd"], 0.001805)
        self.assertEqual(metrics["model_calls"]["api_cost_unreported_calls"], 1)
        aggregate = aggregate_run_metrics([{"status": "failed", "state": {"run_metrics": metrics}}])
        self.assertEqual(aggregate["metered_api_cost_usd"], 0.001805)

    def test_missing_iam_credentials_rejected_before_queueing_mac_allowed(self):
        unavailable = {"available": False, "status": "missing_aws_credentials", "error": "AWS credentials are unavailable"}
        with tempfile.TemporaryDirectory() as directory, patch("agent.bedrock.credential_status", return_value=unavailable):
            service = AgentService(Path(directory), llm=OllamaClient())
            api = TestClient(create_app(AgentBridge(service)))
            with patch.object(service.llm, "_inventory", return_value=[{"name": "qwen3:8b"}]):
                inventory = api.get("/api/models").json()
            self.assertEqual(inventory["auto_selected"], DEFAULT_MODEL)
            self.assertFalse(inventory["default_available"])
            with patch.object(service, "analyze") as analyze:
                result = api.post("/api/runs", json={"asset": "AAPL", "model": "auto"})
            self.assertEqual(result.status_code, 400)
            self.assertIn("IAM role", result.json()["error"])
            analyze.assert_not_called()
            service.llm.validate_request("qwen3:8b")
            self.assertEqual(service.history(10), [])


if __name__ == "__main__":
    unittest.main()
