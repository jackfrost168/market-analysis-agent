import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .vector_store import EmbeddingService, SQLiteVectorStore


class _ParagraphParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs: List[str] = []
        self._paragraph_depth = 0
        self._ignored_depth = 0
        self._buffer: List[str] = []

    def handle_starttag(self, tag, attrs):
        lowered = tag.lower()
        if lowered in {"script", "style", "nav", "footer", "header", "aside"}:
            self._ignored_depth += 1
        if lowered == "p" and not self._ignored_depth:
            self._paragraph_depth += 1
            if self._paragraph_depth == 1:
                self._buffer = []

    def handle_endtag(self, tag):
        lowered = tag.lower()
        if lowered == "p" and self._paragraph_depth:
            self._paragraph_depth -= 1
            if self._paragraph_depth == 0:
                paragraph = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
                if len(paragraph) >= 80 and paragraph not in self.paragraphs:
                    self.paragraphs.append(paragraph)
                self._buffer = []
        if lowered in {"script", "style", "nav", "footer", "header", "aside"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)

    def handle_data(self, data):
        if self._paragraph_depth and not self._ignored_depth:
            text = data.strip()
            if text:
                self._buffer.append(text)


def extract_article_paragraphs(page_html: str, limit: int = 12) -> List[str]:
    parser = _ParagraphParser()
    try:
        parser.feed(page_html)
    except Exception:
        return []
    blocked_phrases = (
        "enable javascript",
        "accept all cookies",
        "sign up for our newsletter",
        "all rights reserved",
    )
    return [
        paragraph
        for paragraph in parser.paragraphs
        if not any(phrase in paragraph.lower() for phrase in blocked_phrases)
    ][:limit]


def _fetch_article(url: str) -> Dict[str, Any]:
    if not url:
        return {"success": False, "paragraphs": [], "error": "missing_url"}
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            page_html = response.read(2_000_000).decode("utf-8", errors="replace")
            final_url = response.geturl()
        paragraphs = extract_article_paragraphs(page_html)
        return {
            "success": bool(paragraphs),
            "paragraphs": paragraphs,
            "final_url": final_url,
            "error": None if paragraphs else "no_article_paragraphs",
        }
    except Exception as exc:
        return {"success": False, "paragraphs": [], "error": str(exc)}


def enrich_news_with_paragraphs(
    news_items: Sequence[Dict[str, Any]], max_articles: int = 4
) -> Dict[str, Any]:
    enriched = [dict(item) for item in news_items]
    candidates = [
        (index, item.get("source_url") or "")
        for index, item in enumerate(enriched[:max_articles])
        if item.get("source_url")
    ]
    failures = []
    paragraph_count = 0
    with ThreadPoolExecutor(max_workers=min(4, len(candidates) or 1)) as executor:
        futures = {executor.submit(_fetch_article, url): index for index, url in candidates}
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"success": False, "paragraphs": [], "error": str(exc)}
            if result.get("success"):
                enriched[index]["paragraphs"] = result["paragraphs"]
                enriched[index]["resolved_url"] = result.get("final_url")
                paragraph_count += len(result["paragraphs"])
            else:
                failures.append(
                    {
                        "headline": enriched[index].get("headline"),
                        "error": result.get("error"),
                    }
                )
    return {
        "items": enriched,
        "articles_attempted": len(candidates),
        "paragraph_count": paragraph_count,
        "failures": failures,
    }


def _paragraph_chunks(news_items: Sequence[Dict[str, Any]], asset: Dict[str, Any]):
    chunks = []
    for item in news_items:
        paragraphs = item.get("paragraphs") or []
        if not paragraphs:
            summary = re.sub(r"\s+", " ", str(item.get("summary") or "")).strip()
            headline = re.sub(r"\s+", " ", str(item.get("headline") or "")).strip()
            candidate = summary if len(summary) >= 60 else headline
            paragraphs = [candidate] if candidate else []
        for paragraph_index, paragraph in enumerate(paragraphs, start=1):
            text = re.sub(r"\s+", " ", str(paragraph)).strip()
            if len(text) < 8:
                continue
            chunks.append(
                {
                    "asset_symbol": asset["symbol"],
                    "asset_name": asset["asset_name"],
                    "headline": item.get("headline") or text[:140],
                    "chunk_text": text,
                    "source_url": item.get("resolved_url") or item.get("source_url"),
                    "published_at": item.get("published_at"),
                    "paragraph_index": paragraph_index,
                }
            )
    return chunks


class PersistentNewsMemory:
    def __init__(self, database_path: Path, embedding_service: EmbeddingService = None):
        self.store = SQLiteVectorStore(Path(database_path))
        self.embedding = embedding_service or EmbeddingService()

    def _ensure_embeddings(self):
        if not self.embedding.embedding_space:
            self.embedding.embed_texts(["initialize embedding space"])
        missing = self.store.documents_without_embedding(self.embedding.embedding_space)
        if missing:
            vectors = self.embedding.embed_texts([row["chunk_text"] for row in missing])
            self.store.upsert_embeddings(missing, vectors, self.embedding.embedding_space)

    def retrieve(self, query: str, limit: int = 3) -> Dict[str, Any]:
        stats_before = self.store.stats()
        if not stats_before["document_count"]:
            return {
                "success": True,
                "items": [],
                "stats": stats_before,
                "embedding": self.embedding.details(),
                "message": "Vector DB is empty; no history was injected.",
            }
        self._ensure_embeddings()
        vector = self.embedding.embed_texts([query])[0]
        items = self.store.search(vector, self.embedding.embedding_space, limit=limit)
        return {
            "success": True,
            "items": items,
            "stats": self.store.stats(self.embedding.embedding_space),
            "embedding": self.embedding.details(),
            "message": f"Retrieved {len(items)} prior paragraph chunks.",
        }

    def persist_news(self, news_items: Sequence[Dict[str, Any]], asset: Dict[str, Any]):
        chunks = _paragraph_chunks(news_items, asset)
        if not chunks:
            return {
                "success": True,
                "inserted_count": 0,
                "duplicate_count": 0,
                "chunk_count": 0,
                "embedding": self.embedding.details(),
            }
        upserted = self.store.upsert_documents(chunks)
        vectors = self.embedding.embed_texts(
            [document["chunk_text"] for document in upserted["documents"]]
        )
        embedded = self.store.upsert_embeddings(
            upserted["documents"], vectors, self.embedding.embedding_space
        )
        return {
            "success": True,
            "inserted_count": upserted["inserted_count"],
            "duplicate_count": upserted["duplicate_count"],
            "chunk_count": len(chunks),
            "embedded_count": embedded,
            "stats": self.store.stats(self.embedding.embedding_space),
            "embedding": self.embedding.details(),
        }

