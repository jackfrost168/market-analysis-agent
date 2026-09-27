import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.tools.research import ResearchTools, _result_preview


class ToolDispatchTests(unittest.TestCase):
    def test_technical_route_does_not_call_unselected_tools(self):
        names = ("get_price_snapshot", "get_price_history", "get_news_context", "get_financial_report", "get_polymarket_context")
        mocks = {name: Mock(return_value={"success": True, "source": "test", "points": []}) for name in names}
        with tempfile.TemporaryDirectory() as directory, patch.multiple("agent.tools.research", **mocks):
            tools = ResearchTools(Path(directory))
            tools.memory = Mock()
            result = tools.collect(
                {"symbol": "AAPL", "asset_name": "Apple", "asset_type": "equity"},
                {"sources": ["yahoo_finance", "price_history"]}, "trend and drawdown", None, [], [],
            )
            self.assertEqual({call["function"] for call in result["tool_calls"]}, {"get_price_snapshot", "get_price_history"})
            for name in names[2:]:
                mocks[name].assert_not_called()
            tools.memory.retrieve.assert_not_called()
            tools.memory.persist_news.assert_not_called()

    def test_polymarket_receives_precise_price_and_target_after_quote(self):
        quote = Mock(return_value={"success": True, "price": 98765.4321})
        crowd = Mock(return_value={"success": False, "error": "Fixture blocked source", "items": []})
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
            "agent.tools.research", get_price_snapshot=quote, get_polymarket_context=crowd
        ):
            tools = ResearchTools(Path(directory))
            result = tools.collect(
                {"symbol": "BTC", "asset_name": "Bitcoin", "asset_type": "crypto"},
                {"sources": ["yahoo_finance", "polymarket"], "prediction_provider": "coinrithm"}, "odds", 100000, [], [],
            )
            crowd.assert_called_once_with("BTC", "Bitcoin", current_price=98765.4321, target_price=100000, provider="coinrithm")
            self.assertEqual(result["tool_calls"][-1]["arguments"]["provider"], "coinrithm")
            self.assertFalse(result["tool_calls"][-1]["success"])
            self.assertIn("Fixture blocked source", result["tool_calls"][-1]["error"])

    def test_memory_audit_exposes_actual_embedding_backend(self):
        preview = _result_preview({"success": True, "items": [], "embedding": {"backend": "local_hash", "model": "local-hash-v1"}})
        self.assertEqual(preview["embedding"]["backend"], "local_hash")

    def test_targeted_retrieval_keeps_the_run_provider(self):
        crowd = Mock(return_value={"success": False, "items": []})
        with tempfile.TemporaryDirectory() as directory, patch("agent.tools.research.get_polymarket_context", crowd):
            tools = ResearchTools(Path(directory))
            tools.targeted_retrieval(
                {"symbol": "BTC", "asset_name": "Bitcoin"},
                [{"source": "polymarket", "query": "Bitcoin price", "provider": "gamma"}],
                [], None, 1,
            )
            self.assertEqual(crowd.call_args.kwargs["provider"], "gamma")


if __name__ == "__main__":
    unittest.main()
