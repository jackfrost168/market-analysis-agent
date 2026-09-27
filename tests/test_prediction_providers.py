import os
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agent.nodes import _evidence_for_prompt
from agent.nodes import AgentNodes
from agent.tools.coinrithm import _candidates, get_coinrithm_context
from agent.tools.evidence import normalize_evidence
from agent.tools.prediction_markets import get_polymarket_context, select_provider


NOW = datetime.now(timezone.utc)


def fixture_event():
    return {
        "id": "fixture-1", "slug": "bitcoin-above", "title": "Bitcoin above ___ tomorrow?",
        "status": "open", "endDate": (NOW + timedelta(days=1)).isoformat(),
        "freshness": {"asOf": NOW.isoformat(), "status": "fresh"},
        "source": {"id": "polymarket"}, "volume": 10000,
        "outcomes": [
            {"name": "70,000", "probability": 70, "externalMarketId": "one"},
            {"name": "80,000", "probability": 0.5, "externalMarketId": "two"},
            {"name": "60,000", "probability": 100, "externalMarketId": "settled"},
        ],
    }


class CoinRithmTests(unittest.TestCase):
    def test_percent_scale_including_sub_one_percent_and_zero_extremes(self):
        items, reason = _candidates(fixture_event(), "BTC", "Bitcoin", 72000.1234, None, NOW)
        self.assertIsNone(reason)
        self.assertEqual([item["probability"] for item in items], [0.7, 0.005])
        self.assertAlmostEqual(items[0]["current_distance"], 2000.1234)
        self.assertEqual(items[1]["matched_thresholds"], [80000])
        self.assertEqual(items[0]["provider"], "coinrithm")

    def test_closed_expired_wrong_venue_asset_nonprice_and_quality_are_rejected(self):
        changes = [
            {"status": "closed"}, {"endDate": (NOW - timedelta(days=1)).isoformat()},
            {"endDate": None}, {"resolvedAt": NOW.isoformat()},
            {"source": {"id": "kalshi"}}, {"title": "Tesla above $400?"},
            {"title": "Will China unban Bitcoin?"},
            {"freshness": {"asOf": (NOW - timedelta(days=2)).isoformat()}},
            {"freshness": {}}, {"freshness": {"asOf": (NOW + timedelta(days=1)).isoformat()}},
            {"quality": {"decisionEligible": False}},
            {"decisionSupport": {"flags": {"staleData": True}}},
        ]
        for change in changes:
            with self.subTest(change=change):
                rows, reason = _candidates({**fixture_event(), **change}, "BTC", "Bitcoin", 75000, None, NOW)
                self.assertEqual(rows, [])
                self.assertIsNotNone(reason)

    def test_binary_event_uses_yes_only_not_the_no_complement(self):
        event = {**fixture_event(), "title": "Will Bitcoin trade above $90k?", "outcomes": [
            {"name": "Yes", "probability": 30}, {"name": "No", "probability": 70},
        ]}
        rows, _ = _candidates(event, "BTC", "Bitcoin", 75000, None, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["probability_outcome"], "Yes")
        self.assertEqual(rows[0]["probability"], 0.3)

    def test_malformed_and_ambiguous_event_outcomes_are_not_used(self):
        for change in ({"freshness": "broken"}, {"outcomes": 4}, {"decisionSupport": {"flags": ["broken"]}}):
            with self.subTest(change=change):
                rows, reason = _candidates({**fixture_event(), **change}, "BTC", "Bitcoin", 75000, None, NOW)
                self.assertFalse(rows)
                self.assertEqual(reason, "invalid_event_shape")
        event = fixture_event()
        event["outcomes"].append({"name": "70,000", "probability": 40})
        rows, _ = _candidates(event, "BTC", "Bitcoin", 75000, None, NOW)
        self.assertEqual([row["matched_thresholds"] for row in rows], [[80000]])

    def test_provider_limitations_remain_visible_in_final_report(self):
        state = {
            "run_id": "fixture", "conversation_id": "fixture", "user_query": "Bitcoin odds",
            "task": {"task_type": "general_research", "asset_name": "Bitcoin", "symbol": "BTC", "asset_type": "crypto", "horizon": "1 day", "as_of": NOW.isoformat()},
            "raw_evidence": [{"kind": "polymarket", "payload": {"source": "Data by CoinRithm", "limitations": ["Aggregated quote, not Gamma direct."]}}],
        }
        report = AgentNodes(None, None).build_report(state)["report"]
        self.assertIn("Data by CoinRithm: Aggregated quote, not Gamma direct.", report["limitations"])

    @patch("agent.tools.coinrithm._fetch_events")
    def test_target_nearest_ranking_deduplicates_and_rounds_search_only(self, fetch):
        fetch.return_value = ([fixture_event()], "https://api.coinrithm.com/fixture")
        result = get_coinrithm_context("BTC", "Bitcoin", 71234.1234, 80000.49)
        self.assertTrue(result["success"])
        self.assertEqual(result["events_scanned"], 1)
        self.assertEqual(result["kept_count"], 2)
        self.assertEqual(result["items"][0]["matched_thresholds"], [80000])
        self.assertAlmostEqual(result["items"][0]["target_distance"], 0.49)
        self.assertIn("bitcoin 80000", result["search_terms"])
        self.assertFalse(result["gamma_called"])

    @patch("agent.tools.coinrithm._fetch_events")
    def test_current_nearest_without_target(self, fetch):
        fetch.return_value = ([fixture_event()], "https://api.coinrithm.com/fixture")
        result = get_coinrithm_context("BTC", "Bitcoin", 71000.1234)
        self.assertEqual(result["items"][0]["matched_thresholds"], [70000])

    @patch("agent.tools.coinrithm._fetch_events")
    def test_access_denial_stops_calls_and_is_not_empty(self, fetch):
        fetch.side_effect = urllib.error.HTTPError("fixture", 451, "test denial", {}, None)
        result = get_coinrithm_context("BTC", "Bitcoin", 70000)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["retrieval_status"], "blocked")
        self.assertEqual(result["items"], [])

    @patch("agent.tools.coinrithm._fetch_events")
    def test_timeout_is_not_zero_matches(self, fetch):
        fetch.side_effect = TimeoutError("fixture timeout")
        self.assertEqual(get_coinrithm_context("BTC", "Bitcoin")["retrieval_status"], "failed")

    @patch("agent.tools.coinrithm._fetch_events")
    def test_empty_success_is_not_network_failure(self, fetch):
        fetch.return_value = ([], "fixture")
        self.assertEqual(get_coinrithm_context("BTC", "Bitcoin")["retrieval_status"], "empty")

    @patch("agent.tools.coinrithm._fetch_events")
    def test_provider_timestamp_and_attribution_enter_thesis_prompt_bundle(self, fetch):
        event = fixture_event()
        event["freshness"]["asOf"] = (NOW - timedelta(hours=2)).isoformat()
        fetch.return_value = ([event], "fixture")
        payload = get_coinrithm_context("BTC", "Bitcoin", 75000)
        evidence, _ = normalize_evidence([{"kind": "polymarket", "payload": payload}], NOW.isoformat())
        self.assertEqual(evidence[0]["timestamp"], event["freshness"]["asOf"])
        bundle = _evidence_for_prompt(evidence)
        self.assertEqual(len(bundle), 2)
        self.assertIn("CoinRithm", str(bundle))
        self.assertIn("Polymarket", str(bundle))
        self.assertIn("0.005", str(bundle))


class ProviderSelectionTests(unittest.TestCase):
    @patch.dict(os.environ, {"PREDICTION_DATA_PROVIDER": "coinrithm"})
    def test_default_and_explicit_selection(self):
        self.assertEqual(select_provider(), "coinrithm")
        self.assertEqual(select_provider("gamma"), "gamma")
        with self.assertRaises(ValueError):
            select_provider("https://unknown.example")

    @patch("agent.tools.prediction_markets.get_coinrithm_context")
    @patch("agent.tools.prediction_markets.get_gamma_context")
    def test_gamma_denial_never_triggers_another_provider(self, gamma, coinrithm):
        gamma.return_value = {"success": False, "retrieval_status": "blocked", "items": []}
        result = get_polymarket_context("BTC", "Bitcoin", provider="gamma")
        self.assertEqual(result["provider"], "gamma")
        coinrithm.assert_not_called()

    @patch("agent.tools.prediction_markets.get_coinrithm_context")
    @patch("agent.tools.prediction_markets.get_gamma_context")
    def test_independently_selected_dataset_never_calls_gamma(self, gamma, coinrithm):
        coinrithm.return_value = {"success": True, "items": []}
        get_polymarket_context("BTC", "Bitcoin", provider="coinrithm")
        gamma.assert_not_called()
        coinrithm.assert_called_once()
