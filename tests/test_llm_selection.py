import os
import unittest
from unittest.mock import patch

from agent.llm import OllamaClient


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


if __name__ == "__main__":
    unittest.main()
