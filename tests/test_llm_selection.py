import os
import json
import unittest
from unittest.mock import patch

from agent.llm import OllamaClient, _usage_fields
from agent.schemas import SemanticPlan


@patch.dict(os.environ, {"OLLAMA_MODEL": ""})
class ModelSelectionTests(unittest.TestCase):
    def test_auto_prefers_installed_qwen_8b_over_llama(self):
        client = OllamaClient()
        with patch.object(client, "list_models", return_value=[
            {"name": "llama3.1:8b"}, {"name": "gemma3:4b"}, {"name": "qwen3:8b"}
        ]):
            self.assertEqual(client.choose_model("auto"), "qwen3:8b")
            self.assertEqual(client.choose_model(), "qwen3:8b")

    def test_manual_choice_is_not_overwritten_by_default(self):
        client = OllamaClient()
        with patch.object(client, "list_models") as models:
            self.assertEqual(client.choose_model("gemma3:4b"), "gemma3:4b")
            models.assert_not_called()

    def test_explicit_configuration_takes_precedence(self):
        with patch.dict(os.environ, {"OLLAMA_MODEL": "mistral-small:24b"}):
            self.assertEqual(OllamaClient().choose_model("auto"), "mistral-small:24b")
        self.assertEqual(OllamaClient(default_model="qwen3:8b").choose_model(), "qwen3:8b")

    def test_unavailable_model_inventory_retains_qwen_default(self):
        client = OllamaClient()
        with patch.object(client, "list_models", side_effect=TimeoutError("fixture")):
            self.assertEqual(client.choose_model("auto"), "qwen3:8b")

    def test_ollama_usage_and_configured_equivalent_cost(self):
        with patch.dict(os.environ, {
            "LLM_INPUT_USD_PER_MILLION_TOKENS": "1.0",
            "LLM_OUTPUT_USD_PER_MILLION_TOKENS": "2.0",
        }):
            usage = _usage_fields({"prompt_eval_count": 1000, "eval_count": 500, "total_duration": 2_000_000})
        self.assertEqual(usage["total_tokens"], 1500)
        self.assertEqual(usage["ollama_total_duration_ms"], 2.0)
        self.assertEqual(usage["estimated_equivalent_cost_usd"], 0.002)


@patch.dict(os.environ, {"OLLAMA_MODEL": "qwen3:1.7b", "OLLAMA_MODEL_ROUTES": json.dumps({
    "qwen3:1.7b": {"base_url": "http://127.0.0.1:11435", "label": "Qwen3 1.7B (AWS)", "think": False, "num_ctx": 4096},
    "qwen3:8b": {"base_url": "http://127.0.0.1:11434", "label": "Qwen3 8B (Mac)"},
})})
class ModelRoutingTests(unittest.TestCase):
    def test_default_and_explicit_models_stay_on_their_servers(self):
        client = OllamaClient()
        self.assertEqual(client.choose_model("auto"), "qwen3:1.7b")
        self.assertEqual(client.choose_model("qwen3:8b"), "qwen3:8b")
        with patch("agent.llm.urllib.request.urlopen", side_effect=TimeoutError("fixture")) as send:
            client.generate_structured("test", SemanticPlan)
            request = send.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:11435/api/generate")
            body = json.loads(request.data)
            self.assertEqual(body["model"], "qwen3:1.7b")
            self.assertFalse(body["think"])
            self.assertEqual(body["options"]["num_ctx"], 4096)
            client.generate_structured("test", SemanticPlan, model="qwen3:8b")
            request = send.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
            self.assertNotIn("think", json.loads(request.data))

    def test_one_endpoint_failure_does_not_hide_the_other_or_change_default(self):
        client = OllamaClient()
        with patch.object(client, "_inventory", side_effect=[TimeoutError("AWS offline"), [{"name": "qwen3:8b", "size": 100}]]):
            models = client.list_models()
        self.assertFalse(models[0]["available"])
        self.assertTrue(models[1]["available"])
        self.assertEqual(models[1]["label"], "Qwen3 8B (Mac)")
        self.assertEqual(client.choose_model("auto"), "qwen3:1.7b")

    def test_unknown_model_is_rejected_before_network_call(self):
        client = OllamaClient()
        with patch("agent.llm.urllib.request.urlopen") as send:
            with self.assertRaises(ValueError):
                client.generate_structured("test", SemanticPlan, model="unconfigured:8b")
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
