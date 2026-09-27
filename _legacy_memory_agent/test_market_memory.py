import tempfile
import unittest
from pathlib import Path

from analysis import (
    extract_article_paragraphs,
    news_items_to_chunks,
    normalize_answer,
    retrieve_then_persist_memory,
    split_paragraphs,
    validate_answer_against_evidence,
)
from vector_store import EmbeddingService, SQLiteVectorStore


class ParagraphChunkingTests(unittest.TestCase):
    def test_each_html_paragraph_becomes_one_chunk(self):
        paragraphs = split_paragraphs(
            "<p>First catalyst paragraph.</p><p>Second risk paragraph.</p>"
        )
        self.assertEqual(
            paragraphs,
            ["First catalyst paragraph.", "Second risk paragraph."],
        )

    def test_headline_is_used_when_rss_has_no_article_body(self):
        chunks = news_items_to_chunks(
            [
                {
                    "headline": "Bitcoin ETF inflows accelerate",
                    "published_at": "2026-08-21T00:00:00+00:00",
                    "source": "https://example.com/news",
                }
            ],
            "BTC",
            "Bitcoin",
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["chunk_text"], "Bitcoin ETF inflows accelerate")

    def test_article_parser_and_chunker_preserve_paragraph_boundaries(self):
        page_html = """
        <html><body><article>
          <p>Institutional demand increased after the latest fund-flow report, according to the published market data.</p>
          <p>Macro uncertainty remains a separate risk because rate expectations can quickly reverse risk appetite.</p>
        </article></body></html>
        """
        paragraphs = extract_article_paragraphs(page_html)
        chunks = news_items_to_chunks(
            [{"headline": "Market update", "paragraphs": paragraphs}],
            "BTC",
            "Bitcoin",
        )

        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(len(chunks), 2)
        self.assertIn("Institutional demand", chunks[0]["chunk_text"])


class PersistentVectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "vectors.sqlite3"

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_documents_persist_and_rank_after_store_is_reopened(self):
        store = SQLiteVectorStore(self.database_path)
        embedder = EmbeddingService(force_hash=True)
        chunks = [
            {
                "asset_symbol": "BTC",
                "asset_name": "Bitcoin",
                "headline": "Bitcoin ETF inflows accelerate",
                "chunk_text": "Bitcoin ETF inflows accelerate as institutional demand grows.",
            },
            {
                "asset_symbol": "AAPL",
                "asset_name": "Apple",
                "headline": "Apple launches a device",
                "chunk_text": "Apple launches a new consumer device.",
            },
        ]
        stored = store.upsert_documents(chunks)
        vectors = embedder.embed_texts(
            [document["chunk_text"] for document in stored["documents"]]
        )
        store.upsert_embeddings(
            stored["documents"], vectors, embedder.embedding_space
        )

        reopened = SQLiteVectorStore(self.database_path)
        query = embedder.embed_texts(
            ["Bitcoin institutional ETF demand and inflows"]
        )[0]
        matches = reopened.search(query, embedder.embedding_space, limit=2)

        self.assertEqual(reopened.stats()["document_count"], 2)
        self.assertEqual(matches[0]["asset_symbol"], "BTC")
        self.assertGreater(matches[0]["similarity"], matches[1]["similarity"])

    def test_duplicate_paragraph_updates_seen_count_without_new_document(self):
        store = SQLiteVectorStore(self.database_path)
        chunk = {
            "asset_symbol": "BTC",
            "asset_name": "Bitcoin",
            "headline": "Repeated headline",
            "chunk_text": "The same paragraph is retrieved again.",
        }
        first = store.upsert_documents([chunk])
        second = store.upsert_documents([chunk])

        self.assertEqual(first["inserted_count"], 1)
        self.assertEqual(second["inserted_count"], 0)
        self.assertEqual(second["duplicate_count"], 1)
        self.assertEqual(store.stats()["document_count"], 1)

    def test_retrieval_happens_before_current_chunks_are_inserted(self):
        store = SQLiteVectorStore(self.database_path)
        embedder = EmbeddingService(force_hash=True)
        old_chunk = {
            "asset_symbol": "BTC",
            "asset_name": "Bitcoin",
            "headline": "Earlier Bitcoin catalyst",
            "chunk_text": "Earlier Bitcoin ETF demand supported the market.",
        }
        store.upsert_documents([old_chunk])
        current_chunk = {
            "asset_symbol": "BTC",
            "asset_name": "Bitcoin",
            "headline": "Current Bitcoin catalyst",
            "chunk_text": "Current Bitcoin ETF inflows are stronger today.",
        }

        result = retrieve_then_persist_memory(
            store,
            embedder,
            "Bitcoin ETF demand and current inflows",
            [current_chunk],
        )

        self.assertEqual(result["documents_before"], 1)
        self.assertEqual(result["retrieved_count"], 1)
        self.assertEqual(
            result["historical_matches"][0]["headline"],
            "Earlier Bitcoin catalyst",
        )
        self.assertEqual(result["documents_after"], 2)


class StructuredAnswerTests(unittest.TestCase):
    def test_answer_is_clamped_and_missing_sections_use_fallback(self):
        fallback = {
            "current_view": "fallback view",
            "why": ["fallback why"],
            "risks": ["fallback risk"],
            "future_expectation": "fallback future",
            "confidence": 0.5,
            "evidence_used": ["price-1"],
            "reasoning_trace": ["fallback trace"],
            "stop_reason": "fallback stop",
        }
        answer = normalize_answer(
            {"current_view": "new view", "confidence": 1.8}, fallback
        )

        self.assertEqual(answer["current_view"], "new view")
        self.assertEqual(answer["confidence"], 1.0)
        self.assertEqual(answer["risks"], ["fallback risk"])
        self.assertEqual(answer["future_expectation"], "fallback future")

    def test_claim_guard_removes_references_to_empty_sources(self):
        fallback = {
            "current_view": "fallback view",
            "why": ["Price evidence is available."],
            "risks": ["General uncertainty remains."],
            "future_expectation": "fallback future",
            "confidence": 0.5,
            "evidence_used": ["price-1"],
            "reasoning_trace": [],
            "stop_reason": "complete",
        }
        answer = dict(fallback)
        answer["why"] = ["Polymarket crowd probability is strongly bullish."]
        answer["risks"] = ["Historical context may be stale."]
        evidence = {
            "current_polymarket": [],
            "historical_context": [],
            "current_news": [{"evidence_id": "news-1"}],
        }

        validated, report = validate_answer_against_evidence(
            answer, evidence, fallback
        )

        self.assertEqual(report["removed_claim_count"], 2)
        self.assertNotIn("Polymarket crowd probability is strongly bullish.", validated["why"])
        self.assertTrue(
            any("Polymarket market was found" in risk for risk in validated["risks"])
        )
        self.assertTrue(
            any("No prior historical chunk" in risk for risk in validated["risks"])
        )


if __name__ == "__main__":
    unittest.main()
