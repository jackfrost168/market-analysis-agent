import unittest
from unittest.mock import patch

from agent.tools.polymarket import (
    GammaAPIError,
    _fetch_active_events,
    _fetch_search_events,
    _market_candidate,
    _hydrate_event,
    get_polymarket_context,
    get_polymarket_status,
)


class PolymarketApiTests(unittest.TestCase):
    @patch("agent.tools.polymarket._fetch_json")
    def test_search_summary_is_enriched_into_a_price_market(self, fetch_json):
        event = {"id": "test-event", "slug": "tesla-price", "title": "Tesla price", "active": True, "closed": False}
        market = {
            "slug": "tesla-above-400", "question": "Will Tesla trade above $400?",
            "active": True, "closed": False, "endDate": "2099-12-31T00:00:00Z",
            "outcomes": '["Yes", "No"]', "outcomePrices": '["0.42", "0.58"]',
        }

        def response(url):
            if "/events/slug/" in url:
                payload = {**event, "markets": [market]}
            elif "/public-search?" in url:
                payload = {"events": [event]}
            else:
                payload = []
            return {"payload": payload, "latency_ms": 1}

        fetch_json.side_effect = response
        result = get_polymarket_context("TSLA", "Tesla", current_price=390.1234, target_price=400)
        self.assertTrue(result["success"])
        self.assertEqual(result["kept_count"], 1)
        self.assertEqual(result["items"][0]["probability"], 0.42)
        self.assertEqual(result["items"][0]["target_distance"], 0)
        self.assertEqual(result["retrieval_status"], "success")

    @patch("agent.tools.polymarket._fetch_json")
    def test_single_event_detail_object_is_hydrated(self, fetch_json):
        fetch_json.return_value = {"payload": {"id": "event-1", "slug": "tesla-price", "markets": [{"question": "Tesla above $400?"}]}, "latency_ms": 1}
        event = _hydrate_event({"slug": "tesla-price", "title": "Tesla price"})
        self.assertEqual(event["markets"][0]["question"], "Tesla above $400?")
        self.assertEqual(event["title"], "Tesla price")
        self.assertIn("/events/slug/tesla-price", fetch_json.call_args.args[0])

    @patch("agent.tools.polymarket._fetch_json")
    def test_blocked_calls_stop_discovery_without_fake_markets(self, fetch_json):
        fetch_json.side_effect = GammaAPIError(451, "https://gamma-api.polymarket.com", "error code: 1026")
        result = get_polymarket_context("TSLA", "Tesla", current_price=350.1234)
        self.assertEqual(result["retrieval_status"], "blocked")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["events_scanned"], 0)
        self.assertEqual(result["provider_error_codes"], ["1026"])
        self.assertFalse(result["fallback_used"])
        self.assertEqual(fetch_json.call_count, 3)

    @patch("agent.tools.polymarket._fetch_json")
    def test_network_failure_is_not_reported_as_no_matching_markets(self, fetch_json):
        fetch_json.side_effect = TimeoutError("Fixture timeout")
        result = get_polymarket_context("TSLA", "Tesla")
        self.assertEqual(result["error_code"], "gamma_retrieval_failed")
        self.assertEqual(result["retrieval_status"], "failed")
        self.assertFalse(result["access_blocked"])

    @patch("agent.tools.polymarket._fetch_json")
    def test_successful_empty_response_is_distinct_from_network_failure(self, fetch_json):
        fetch_json.return_value = {"payload": {"events": []}, "latency_ms": 1}
        result = get_polymarket_context("TSLA", "Tesla")
        self.assertEqual(result["retrieval_status"], "empty")
        self.assertEqual(result["error_code"], "no_matching_ongoing_price_markets")
        self.assertEqual(result["diagnostics"], [])

    @patch("agent.tools.polymarket._fetch_json")
    def test_search_uses_documented_public_search_parameters(self, fetch_json):
        fetch_json.return_value = {"payload": {"events": []}, "latency_ms": 1}

        _fetch_search_events("Bitcoin 120000")

        url = fetch_json.call_args.args[0]
        self.assertIn("/public-search?", url)
        self.assertIn("events_status=active", url)
        self.assertIn("keep_closed_markets=0", url)
        self.assertNotIn("title_search", url)

    @patch("agent.tools.polymarket._fetch_json")
    def test_active_event_fallback_uses_tag_and_supported_filters(self, fetch_json):
        fetch_json.return_value = {"payload": [], "latency_ms": 1}

        _fetch_active_events("BTC", "Bitcoin")

        urls = [call.args[0] for call in fetch_json.call_args_list]
        self.assertTrue(any("tag_slug=bitcoin" in url for url in urls))
        self.assertTrue(all("active=true" in url and "closed=false" in url for url in urls))
        self.assertTrue(all("title_search" not in url for url in urls))

    @patch("agent.tools.polymarket._fetch_json")
    def test_status_distinguishes_network_451_from_missing_configuration(self, fetch_json):
        fetch_json.side_effect = GammaAPIError(
            451,
            "https://gamma-api.polymarket.com",
            '{"title":"Error 1026: Cloudflare Error","error_code":1026}',
            {"Server": "cloudflare", "CF-RAY": "test-ICN"},
        )

        status = get_polymarket_status()

        self.assertTrue(status["configured"])
        self.assertFalse(status["authentication_required"])
        self.assertEqual(status["status"], "access_blocked")
        self.assertTrue(status["access_blocked"])
        self.assertEqual([probe["http_status"] for probe in status["probes"]], [451, 451])
        self.assertEqual(status["blocking_layer"], "upstream_edge")
        self.assertEqual(status["probes"][0]["provider_error_code"], "1026")
        self.assertEqual(status["geoblock_probe"]["http_status"], 451)

    def test_ongoing_asset_price_market_passes_filter_without_event_prefix(self):
        event = {
            "title": "Bitcoin price in September",
            "slug": "bitcoin-price-in-september",
            "active": True,
            "closed": False,
            "endDate": "2099-09-30T23:59:59Z",
        }
        market = {
            "question": "Will Bitcoin trade above $100,000 by September 30?",
            "slug": "bitcoin-above-100000",
            "active": True,
            "closed": False,
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.42", "0.58"]',
            "liquidity": "25000",
            "volume": "100000",
        }

        candidate = _market_candidate(event, market, ["bitcoin", "btc"], 92000, 100000)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["market"], market["question"])
        self.assertEqual(candidate["probability"], 0.42)
        self.assertEqual(candidate["target_distance"], 0)

    def test_closed_or_non_price_market_is_rejected(self):
        event = {"title": "Bitcoin", "active": True, "closed": False}
        closed = {
            "question": "Will Bitcoin trade above $100,000?",
            "active": True,
            "closed": True,
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]',
        }
        non_price = {
            "question": "Will a Bitcoin developer speak at the conference?",
            "active": True,
            "closed": False,
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]',
        }

        self.assertIsNone(_market_candidate(event, closed, ["bitcoin"], 92000, None))
        self.assertIsNone(_market_candidate(event, non_price, ["bitcoin"], 92000, None))


if __name__ == "__main__":
    unittest.main()
