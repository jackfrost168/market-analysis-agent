import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from agent.service import AgentService
from agent.tools.market_data import resolve_asset
from agent.tools.sector_performance import compare_histories, get_sector_performance
from awsdeploy.api import create_app
from awsdeploy.bridge import AgentBridge


def history(prices, currency="USD", hours=14):
    return {
        "success": True, "currency": currency, "exchange_timezone": "America/New_York",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/test",
        "points": [{"time": datetime(2024, 7, day, hours, 30, tzinfo=timezone.utc).timestamp(), "price": price}
                   for day, price in prices],
    }


class SectorPerformanceTests(unittest.TestCase):
    def test_returns_use_identical_dates_and_percentage_point_difference(self):
        stock = history([(1, 50), (2, 100), (3, 999), (4, 110)])
        benchmark = history([(2, 200), (4, 212), (5, 500)], hours=20)
        result = compare_histories(stock, benchmark, now=datetime(2024, 7, 8, tzinfo=timezone.utc))
        self.assertEqual(result["stock_return_pct"], 10)
        self.assertEqual(result["benchmark_return_pct"], 6)
        self.assertEqual(result["excess_return_pp"], 4)
        self.assertEqual((result["start_date"], result["end_date"], result["shared_sessions"]), ("2024-07-02", "2024-07-04", 2))

    def test_todays_incomplete_bars_are_excluded_in_exchange_timezone(self):
        stock = history([(2, 100), (3, 110), (4, 500)])
        benchmark = history([(2, 200), (3, 212), (4, 5)])
        result = compare_histories(stock, benchmark, now=datetime(2024, 7, 4, 18, tzinfo=timezone.utc))
        self.assertEqual(result["end_date"], "2024-07-03")
        self.assertEqual(result["excess_return_pp"], 4)

    def test_no_comparison_for_missing_timezone_currency_or_shared_dates(self):
        stock = history([(2, 100), (3, 110)])
        for benchmark in (history([(2, 100)], "USD"), history([(2, 100), (3, 110)], "JPY"),
                          {**history([(2, 100), (3, 110)]), "exchange_timezone": None},
                          {"success": False}):
            with self.subTest(benchmark=benchmark), self.assertRaises(ValueError):
                compare_histories(stock, benchmark)

    def test_exact_classification_selects_benchmark_without_llm(self):
        payload = {"payload": {"quotes": [
            {"symbol": "AAPU", "quoteType": "ETF", "sector": "Financial Services", "exchange": "PCX"},
            {"symbol": "AAPL", "quoteType": "EQUITY", "sector": "Technology", "industry": "Consumer Electronics", "exchange": "NMS"},
        ]}}
        with patch("agent.tools.sector_performance._fetch_json", return_value=(payload, False)), \
             patch("agent.tools.sector_performance.get_price_history", side_effect=[history([(2, 100), (3, 110)]), history([(2, 200), (3, 212)])]) as prices:
            result = get_sector_performance(resolve_asset("Apple"), "3mo")
        self.assertTrue(result["success"])
        self.assertEqual(result["benchmark"]["symbol"], "XLK")
        self.assertEqual(result["excess_return_pp"], 4)
        self.assertEqual(prices.call_args.args[0]["quote_symbol"], "XLK")
        self.assertIn("not an equal-weight", result["note"])

    def test_unknown_non_us_and_crypto_never_get_guessed_benchmarks(self):
        with patch("agent.tools.sector_performance._fetch_json") as fetch, \
             patch("agent.tools.sector_performance.get_price_history") as prices:
            self.assertEqual(get_sector_performance(resolve_asset("BTC"))["status"], "not_applicable")
            fetch.assert_not_called()
            for profile in (
                {"symbol": "AAPL", "quoteType": "EQUITY", "sector": "Technology", "exchange": "TOR"},
                {"symbol": "AAPL", "quoteType": "EQUITY", "exchange": "NMS"},
                {"symbol": "AAPU", "quoteType": "EQUITY", "sector": "Technology", "exchange": "NMS"},
            ):
                fetch.return_value = ({"payload": {"quotes": [profile]}}, False)
                self.assertFalse(get_sector_performance(resolve_asset("AAPL"))["success"])
            prices.assert_not_called()

    def test_upstream_failure_is_visible(self):
        with patch("agent.tools.sector_performance._fetch_json", side_effect=TimeoutError("Yahoo timed out")):
            result = get_sector_performance(resolve_asset("AAPL"))
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("timed out", result["reason"])

    def test_endpoint_validates_before_fetching_and_never_runs_analysis(self):
        service = AgentService.__new__(AgentService)
        service.graph, service.llm, service.runs = Mock(), Mock(), Mock()
        client = TestClient(create_app(AgentBridge(service)))
        with patch("agent.service.get_sector_performance", return_value={"success": True, "excess_return_pp": 4}) as fetch:
            response = client.get("/api/sector-performance?asset=Apple&range=3mo")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["asset"]["symbol"], "AAPL")
            self.assertEqual(response.json()["comparison"]["excess_return_pp"], 4)
            self.assertEqual(response.headers["cache-control"], "no-store")
            fetch.reset_mock()
            for url in ("/api/sector-performance?asset=AAPL&range=max", "/api/sector-performance?asset="):
                self.assertEqual(client.get(url).status_code, 400)
            fetch.assert_not_called()
        self.assertEqual(service.graph.mock_calls + service.llm.mock_calls + service.runs.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
