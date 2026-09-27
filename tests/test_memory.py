import tempfile
import unittest
from pathlib import Path

from agent.tools.memory import PersistentNewsMemory
from agent.tools.vector_store import EmbeddingService


class PersistentMemoryTests(unittest.TestCase):
    def test_chunks_survive_a_new_memory_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "vectors.sqlite3"
            asset = {"symbol": "AAPL", "asset_name": "Apple Inc."}
            news = [
                {
                    "headline": "Apple expands services",
                    "paragraphs": [
                        "Apple reported that services revenue expanded as paid subscriptions increased across several product categories."
                    ],
                    "source_url": "https://example.com/apple",
                    "published_at": "2026-08-01T00:00:00+00:00",
                }
            ]
            first = PersistentNewsMemory(
                database, EmbeddingService(force_hash=True)
            )
            stored = first.persist_news(news, asset)
            self.assertEqual(stored["inserted_count"], 1)

            restarted = PersistentNewsMemory(
                database, EmbeddingService(force_hash=True)
            )
            retrieved = restarted.retrieve("Apple services subscription revenue", limit=3)
            self.assertEqual(len(retrieved["items"]), 1)
            self.assertIn("services revenue", retrieved["items"][0]["chunk_text"])


if __name__ == "__main__":
    unittest.main()

