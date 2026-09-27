import unittest

from agent.tools.polymarket import extract_price_levels
from agent.tools.quantitative import analyze_quantitatively


class QuantitativeTests(unittest.TestCase):
    def test_calculations_are_deterministic(self):
        raw = [
            {
                "kind": "market_price",
                "payload": {
                    "success": True,
                    "price": 110.0,
                    "return_1d_pct": 1.5,
                    "return_1w_pct": 4.0,
                    "volume_vs_5d_avg_pct": 12.0,
                },
            },
            {
                "kind": "price_history",
                "payload": {
                    "success": True,
                    "points": [
                        {"time": index, "price": 100.0 + index * 0.5}
                        for index in range(30)
                    ],
                },
            },
            {
                "kind": "financials",
                "payload": {
                    "success": True,
                    "metrics": [
                        {"key": "revenue", "value": 1000.0, "growth_pct": 10.0},
                        {"key": "operating_income", "value": 250.0, "growth_pct": 12.0},
                        {"key": "net_income", "value": 200.0, "growth_pct": 9.0},
                    ],
                },
            },
            {
                "kind": "polymarket",
                "payload": {
                    "items": [
                        {"probability": 0.6},
                        {"probability": 0.4},
                    ]
                },
            },
        ]
        evidence = [
            {"direction": 1},
            {"direction": 1},
            {"direction": -1},
        ]
        result = analyze_quantitatively(raw, evidence, target_price=121.0)
        self.assertEqual(result["market"]["target_distance_pct"], 10.0)
        self.assertEqual(result["financials"]["operating_margin_pct"], 25.0)
        self.assertEqual(result["financials"]["net_margin_pct"], 20.0)
        self.assertEqual(result["polymarket"]["mean_selected_outcome_probability"], 0.5)
        self.assertEqual(result["calculation_mode"], "deterministic_python")

    def test_polymarket_level_parser_supports_suffixes(self):
        levels = extract_price_levels("Will Bitcoin hit $150k or trade above $1,000,000?")
        self.assertIn(150000.0, levels)
        self.assertIn(1000000.0, levels)

    def test_drawdown_tracks_running_peak_even_after_recovery(self):
        result = analyze_quantitatively([
            {"kind": "price_history", "payload": {"points": [
                {"price": value} for value in [100, 120, 90, 130]
            ]}}
        ], [], None)
        self.assertEqual(result["market"]["max_drawdown_pct"], -25.0)
        self.assertEqual(result["market"]["period_return_pct"], 30.0)
        self.assertIn("Maximum drawdown (close-to-close)", [row["label"] for row in result["signals"]])

    def test_rising_prices_have_zero_drawdown(self):
        result = analyze_quantitatively([
            {"kind": "price_history", "payload": {"points": [{"price": 100}, {"price": 110}]}}
        ], [], None)
        self.assertEqual(result["market"]["max_drawdown_pct"], 0.0)

    def test_insufficient_prices_are_not_reported_as_zero_risk(self):
        for prices in ([], [100], [None, -1, 0, "bad"]):
            with self.subTest(prices=prices):
                result = analyze_quantitatively([
                    {"kind": "price_history", "payload": {"points": [{"price": value} for value in prices]}}
                ], [], None)
                self.assertIsNone(result["market"]["max_drawdown_pct"])
                self.assertIsNone(result["market"]["period_return_pct"])


if __name__ == "__main__":
    unittest.main()
