import unittest

from agent.nodes import _explicit_task_type, _select_research_sources


class DynamicPlanningTests(unittest.TestCase):
    def test_plain_price_forecast_enables_polymarket_without_magic_keywords(self):
        query = "Analyze the current situation, and make a prediction of TSLA price by the end of 2026."
        self.assertEqual(_explicit_task_type(query), "company_outlook")
        plan = _select_research_sources(
            {"task_type": "company_outlook", "asset_type": "stock"}, query, None, ["polymarket"]
        )
        self.assertIn("polymarket", plan["sources"])
        self.assertIn("price_history", plan["sources"])
        self.assertNotIn("llm_proposed_but_rejected", plan["source_rationale"]["polymarket"])

    def test_chinese_price_forecast_and_explicit_prediction_market_requests(self):
        for query in ("预测特斯拉到2026年底的股价", "预估比特币价格", "查看预测市场的赔率"):
            with self.subTest(query=query):
                plan = _select_research_sources({"task_type": "general_research", "asset_type": "stock"}, query, None)
                self.assertIn("polymarket", plan["sources"])

    def test_quote_plus_explicit_polymarket_or_target_is_not_price_only(self):
        for query, target in (("What is Apple's current price? Include Polymarket.", None), ("What is Apple's current price?", 350)):
            with self.subTest(query=query, target=target):
                plan = _select_research_sources({"task_type": "general_research", "asset_type": "stock"}, query, target)
                self.assertFalse(plan["price_only_request"])
                self.assertIn("polymarket", plan["sources"])

    def test_price_only_question_rejects_unrelated_llm_source_proposals(self):
        plan = _select_research_sources(
            {"task_type": "general_research", "asset_type": "stock"},
            "What is Apple's current price?",
            None,
            [
                "google_news",
                "polymarket",
                "vector_db",
                "financial_statements",
                "price_history",
            ],
        )

        self.assertEqual(plan["sources"], ["yahoo_finance"])
        self.assertTrue(plan["price_only_request"])
        self.assertTrue(
            plan["source_rationale"]["polymarket"]["llm_proposed_but_rejected"]
        )

    def test_stock_earnings_selects_fundamentals_but_not_polymarket(self):
        plan = _select_research_sources(
            {"task_type": "earnings_analysis", "asset_type": "stock"},
            "Analyze Apple's latest earnings, revenue, margins, and outlook.",
            None,
            [],
        )

        self.assertIn("yahoo_finance", plan["sources"])
        self.assertIn("google_news", plan["sources"])
        self.assertIn("financial_statements", plan["sources"])
        self.assertIn("vector_db", plan["sources"])
        self.assertNotIn("polymarket", plan["sources"])
        self.assertNotIn("price_history", plan["sources"])

    def test_move_explanation_uses_news_and_history_without_crowd_data(self):
        plan = _select_research_sources(
            {"task_type": "move_explanation", "asset_type": "crypto"},
            "Why did Bitcoin move today?",
            None,
            [],
        )

        self.assertEqual(
            plan["sources"],
            ["yahoo_finance", "google_news", "price_history", "vector_db"],
        )
        self.assertIn("polymarket", plan["skipped_sources"])

    def test_target_price_explicitly_enables_polymarket(self):
        plan = _select_research_sources(
            {"task_type": "company_outlook", "asset_type": "crypto"},
            "Could Bitcoin reach my target over the next quarter?",
            120000,
            [],
        )

        self.assertIn("polymarket", plan["sources"])
        self.assertEqual(
            plan["source_rationale"]["polymarket"]["origin"],
            ["probability_task_rule"],
        )

    def test_stock_technical_question_uses_history_without_news_or_sec(self):
        plan = _select_research_sources(
            {"task_type": "technical_analysis", "asset_type": "stock"},
            "Show Apple's six month price trend, volatility, and drawdown.",
            None,
            ["google_news", "financial_statements", "polymarket"],
        )

        self.assertEqual(plan["sources"], ["yahoo_finance", "price_history"])
        self.assertTrue(
            plan["source_rationale"]["google_news"]["llm_proposed_but_rejected"]
        )

    def test_event_impact_uses_news_memory_without_automatic_sec(self):
        plan = _select_research_sources(
            {"task_type": "event_impact", "asset_type": "stock"},
            "What is the impact of a new tariff on Apple?",
            None,
            [],
        )

        self.assertEqual(
            plan["sources"],
            ["yahoo_finance", "google_news", "vector_db"],
        )

    def test_explicit_task_phrases_have_stable_routes(self):
        self.assertEqual(
            _explicit_task_type("Why did Tesla fall today after earnings?"),
            "move_explanation",
        )
        self.assertEqual(
            _explicit_task_type("Analyze Apple's quarterly earnings and EPS"),
            "earnings_analysis",
        )
        self.assertEqual(
            _explicit_task_type("What is the impact of tariffs on Tesla?"),
            "event_impact",
        )
        self.assertEqual(
            _explicit_task_type("Show Apple's price trend, volatility, and drawdown."),
            "technical_analysis",
        )


if __name__ == "__main__":
    unittest.main()
