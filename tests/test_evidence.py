import unittest

from agent.tools.evidence import evaluate_gate, normalize_evidence


class EvidenceNormalizationTests(unittest.TestCase):
    def test_normalizes_deduplicates_and_gates(self):
        raw = [
            {
                "kind": "market_price",
                "payload": {
                    "success": True,
                    "price": 201.1234,
                    "currency": "USD",
                    "return_1d_pct": 1.2,
                    "return_1w_pct": 2.4,
                    "timestamp": "2026-08-31T20:00:00+00:00",
                    "source": "finance.yahoo.chart",
                },
            },
            {
                "kind": "news",
                "payload": {
                    "items": [
                        {
                            "headline": "Company revenue growth beats estimates",
                            "summary": "Company revenue growth beats estimates",
                            "source_name": "Publisher",
                            "published_at": "2026-08-31T10:00:00+00:00",
                        },
                        {
                            "headline": "Company revenue growth beats estimates",
                            "summary": "Company revenue growth beats estimates",
                            "source_name": "Publisher",
                            "published_at": "2026-08-31T10:00:00+00:00",
                        },
                    ]
                },
            },
        ]
        evidence, groups = normalize_evidence(raw, "2026-09-01T00:00:00+00:00")
        self.assertEqual(len(evidence), 2)
        self.assertEqual([item["id"] for item in evidence], ["EV-001", "EV-002"])
        self.assertEqual(evidence[1]["direction"], 1)
        self.assertEqual(len(groups), 1)
        gate = evaluate_gate(evidence, ["market_price", "news"], 0, 2)
        self.assertTrue(gate["sufficient"])
        self.assertEqual(gate["coverage_pct"], 100)

    def test_gate_requests_bounded_retry_then_degrades(self):
        gate = evaluate_gate([], ["market_price", "news"], 0, 1)
        self.assertEqual(gate["decision"], "retrieve_more")
        exhausted = evaluate_gate([], ["market_price", "news"], 1, 1)
        self.assertEqual(exhausted["decision"], "analyze")
        self.assertTrue(exhausted["degraded"])

    def test_asset_clause_prevents_cross_asset_sentiment_leakage(self):
        raw = [
            {
                "kind": "news",
                "payload": {
                    "items": [
                        {
                            "headline": "Bitcoin surges above $77,000, S&P 500 posts a weekly loss",
                            "summary": "Bitcoin surges above $77,000, S&P 500 posts a weekly loss",
                            "source_name": "Publisher",
                            "published_at": "2026-08-31T10:00:00+00:00",
                        }
                    ]
                },
            }
        ]
        evidence, _ = normalize_evidence(
            raw,
            "2026-09-01T00:00:00+00:00",
            asset_terms=["BTC", "Bitcoin"],
        )
        self.assertEqual(evidence[0]["direction"], 1)


if __name__ == "__main__":
    unittest.main()
