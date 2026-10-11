import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from agent.service import AgentService
from agent.tools.market_data import get_price_history, resolve_asset
from awsdeploy.api import create_app
from awsdeploy.bridge import AgentBridge


def yahoo_history(timestamps, closes, meta=None):
    return {
        "payload": {
            "chart": {
                "result": [{
                    "meta": meta or {},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [{"close": closes}],
                        "adjclose": [{"adjclose": [1.0] * len(closes)}],
                    },
                }],
            },
        },
        "latency_ms": 8,
    }, False


class PriceHistoryPreviewTests(unittest.TestCase):
    def setUp(self):
        # A preview must not need a graph, model, database, or run manager operation.
        self.service = AgentService.__new__(AgentService)
        self.service.llm = Mock()
        self.service.graph = Mock()
        self.service.runs = Mock()

    def test_tool_preserves_currency_and_gaps_and_sanitizes_points(self):
        base = 1720000000
        fixture = yahoo_history(
            [base + 3 * 86400, base, base + 86400, base + 2 * 86400,
             base + 4 * 86400, base + 5 * 86400, "bad", float("nan"),
             10**100, base + 6 * 86400, base + 3 * 86400],
            [103, 100, None, 102, 0, -3, 104, 105, 106, float("inf"), 103.5],
            {"currency": "JPY", "exchangeTimezoneName": "Asia/Tokyo"},
        )
        asset = {"symbol": "7203.T", "quote_symbol": "7203.T"}
        with patch("agent.tools.market_data._fetch_json", return_value=fixture):
            history = get_price_history(asset, "3mo")
        self.assertTrue(history["success"])
        self.assertEqual(history["points"], [
            {"time": base, "price": 100.0},
            {"time": base + 2 * 86400, "price": 102.0},
            {"time": base + 3 * 86400, "price": 103.5},
        ])
        self.assertEqual(history["currency"], "JPY")
        self.assertEqual(history["exchange_timezone"], "Asia/Tokyo")
        self.assertEqual(history["price_basis"], "daily_close")
        self.assertEqual(history["range"], "3mo")
        self.assertEqual(history["interval"], "1d")

    def test_missing_currency_is_not_assumed_to_be_usd(self):
        with patch("agent.tools.market_data._fetch_json", return_value=yahoo_history(
            [1720000000, 1720086400], [100, 101],
        )):
            history = get_price_history(resolve_asset("AAPL"))
        self.assertTrue(history["success"])
        self.assertIsNone(history["currency"])

    def test_insufficient_history_returns_an_explicit_error(self):
        with patch("agent.tools.market_data._fetch_json", return_value=yahoo_history(
            [1720000000, 1720086400], [None, 101], {"currency": "GBP"},
        )):
            history = get_price_history(resolve_asset("AAPL"), "1mo")
        self.assertFalse(history["success"])
        self.assertEqual(history["points"], [])
        self.assertIn("fewer than two", history["error"])
        self.assertEqual(history["currency"], "GBP")
        self.assertEqual(history["range"], "1mo")

    def test_invalid_range_or_empty_asset_never_calls_a_tool(self):
        with patch("agent.service.resolve_asset") as resolve, patch("agent.service.get_price_history") as fetch:
            for range_value in ("max", "5y", "", "6mo&interval=1m"):
                with self.subTest(range=range_value), self.assertRaises(ValueError):
                    self.service.price_history_preview("Apple", range_value)
            with self.assertRaises(ValueError):
                self.service.price_history_preview(" ")
            resolve.assert_not_called()
            fetch.assert_not_called()

    def test_company_name_and_crypto_alias_reuse_asset_resolution(self):
        with patch("agent.service.get_price_history", return_value={"success": True, "points": []}) as fetch:
            for name, symbol in (("Apple", "AAPL"), ("Bitcoin", "BTC-USD")):
                with self.subTest(asset=name):
                    result = self.service.price_history_preview(name, "1y")
                    self.assertEqual(result["asset"]["quote_symbol"], symbol)
                    fetch.assert_called_with(result["asset"], range_value="1y", interval="1d")
        self.assertEqual(self.service.llm.mock_calls, [])
        self.assertEqual(self.service.graph.mock_calls, [])
        self.assertEqual(self.service.runs.mock_calls, [])

    def test_fastapi_preview_contract_and_validation(self):
        client = TestClient(create_app(AgentBridge(self.service)))
        with patch("agent.tools.market_data._fetch_json", return_value=yahoo_history(
            [1720000000, 1720086400], [100, 101], {"currency": "USD"},
        )) as fetch:
            response = client.get("/api/price-history", params={"asset": "Apple"})
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertEqual(result["asset"]["symbol"], "AAPL")
            self.assertEqual(result["history"]["range"], "6mo")
            self.assertEqual(result["history"]["points"][-1]["price"], 101.0)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("retrieved_at", result)
            self.assertIn("range=6mo", fetch.call_args.args[0])
            fetch.reset_mock()
            invalid = client.get("/api/price-history?asset=AAPL&range=max")
            self.assertEqual(invalid.status_code, 400)
            self.assertIn("range", invalid.json()["error"])
            fetch.assert_not_called()
        self.assertEqual(self.service.llm.mock_calls, [])
        self.assertEqual(self.service.graph.mock_calls, [])
        self.assertEqual(self.service.runs.mock_calls, [])

    def test_fastapi_reports_upstream_failure_without_crashing(self):
        client = TestClient(create_app(AgentBridge(self.service)))
        with patch("agent.tools.market_data._fetch_json", side_effect=TimeoutError("Yahoo timed out")):
            response = client.get("/api/price-history?asset=AAPL&range=1mo")
        self.assertEqual(response.status_code, 200)
        history = response.json()["history"]
        self.assertFalse(history["success"])
        self.assertEqual(history["points"], [])
        self.assertEqual(history["error"], "Yahoo timed out")


if __name__ == "__main__":
    unittest.main()
