import html as html_lib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence

import market_core as base_agent

try:
    from .vector_store import EmbeddingService, SQLiteVectorStore, utc_now_iso
except ImportError:
    from vector_store import EmbeddingService, SQLiteVectorStore, utc_now_iso


OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_LLM_MODEL = "qwen3:8b"
HISTORY_LIMIT = 3
REINDEX_BATCH_SIZE = 24


class _ArticleParagraphParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._paragraph_depth = 0
        self._buffer = []
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        lowered = tag.lower()
        if lowered in ["script", "style", "noscript", "svg"]:
            self._ignored_depth += 1
        elif lowered == "p" and not self._ignored_depth:
            self._paragraph_depth += 1
            if self._paragraph_depth == 1:
                self._buffer = []

    def handle_endtag(self, tag):
        lowered = tag.lower()
        if lowered in ["script", "style", "noscript", "svg"] and self._ignored_depth:
            self._ignored_depth -= 1
        elif lowered == "p" and self._paragraph_depth:
            self._paragraph_depth -= 1
            if self._paragraph_depth == 0:
                paragraph = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
                if len(paragraph) >= 60 and paragraph not in self.paragraphs:
                    self.paragraphs.append(paragraph)
                self._buffer = []

    def handle_data(self, data):
        if self._paragraph_depth and not self._ignored_depth:
            text = data.strip()
            if text:
                self._buffer.append(text)


def extract_article_paragraphs(page_html: str, limit: int = 12) -> List[str]:
    parser = _ArticleParagraphParser()
    parser.feed(page_html or "")
    blocked_phrases = [
        "accept all cookies",
        "enable javascript",
        "sign up for our newsletter",
        "privacy policy",
    ]
    paragraphs = []
    for paragraph in parser.paragraphs:
        lowered = paragraph.lower()
        if any(phrase in lowered for phrase in blocked_phrases):
            continue
        paragraphs.append(paragraph)
        if len(paragraphs) >= limit:
            break
    return paragraphs


def _fetch_article_paragraphs(source_url: str) -> Dict:
    parsed = urllib.parse.urlparse(source_url or "")
    if parsed.scheme not in ["http", "https"]:
        return {"success": False, "error": "unsupported_article_url"}
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MarketMemoryAgent/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    timeout = int(os.environ.get("RAG_ARTICLE_TIMEOUT_SECONDS", "8"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ["text/html", "application/xhtml+xml"]:
                return {"success": False, "error": "article_not_html"}
            charset = response.headers.get_content_charset() or "utf-8"
            page_html = response.read(2_000_000).decode(charset, errors="replace")
            resolved_url = response.geturl()
        paragraphs = extract_article_paragraphs(page_html)
        return {
            "success": bool(paragraphs),
            "paragraphs": paragraphs,
            "resolved_url": resolved_url,
            "error": None if paragraphs else "no_article_paragraphs",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def enrich_news_with_article_paragraphs(news_items: Sequence[Dict]):
    started = time.time()
    enriched = [dict(item) for item in news_items]
    limit = max(0, int(os.environ.get("RAG_ARTICLE_FETCH_LIMIT", "4")))
    candidates = [
        (index, item.get("source"))
        for index, item in enumerate(enriched[:limit])
        if item.get("source")
    ]
    successes = 0
    paragraph_count = 0
    errors = []
    if candidates:
        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as executor:
            future_map = {
                executor.submit(_fetch_article_paragraphs, source): index
                for index, source in candidates
            }
            for future in as_completed(future_map):
                index = future_map[future]
                try:
                    article = future.result()
                except Exception as exc:
                    article = {"success": False, "error": str(exc)}
                if article.get("success"):
                    enriched[index]["paragraphs"] = article["paragraphs"]
                    enriched[index]["article_url"] = article.get("resolved_url")
                    successes += 1
                    paragraph_count += len(article["paragraphs"])
                else:
                    errors.append(article.get("error") or "article_fetch_failed")
    return enriched, {
        "success": successes > 0,
        "source": "publisher.article_html",
        "attempted_count": len(candidates),
        "article_count": successes,
        "paragraph_count": paragraph_count,
        "headline_fallback_count": max(0, len(enriched) - successes),
        "errors": errors[:4],
        "latency_ms": int((time.time() - started) * 1000),
        "fallback_used": successes < len(enriched),
        "cache_hit": False,
    }


def _plain_text(raw_text: str) -> str:
    text = raw_text or ""
    text = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html_lib.unescape(text).replace("\r\n", "\n").replace("\r", "\n")


def split_paragraphs(raw_text: str) -> List[str]:
    cleaned = _plain_text(raw_text)
    blocks = re.split(r"\n\s*\n+", cleaned)
    paragraphs = []
    for block in blocks:
        paragraph = re.sub(r"\s+", " ", block).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def news_items_to_chunks(
    news_items: Sequence[Dict], asset_symbol: str, asset_name: str
) -> List[Dict]:
    chunks = []
    for item in news_items:
        headline = re.sub(r"\s+", " ", (item.get("headline") or "").strip())
        supplied_paragraphs = item.get("paragraphs")
        if isinstance(supplied_paragraphs, list):
            paragraphs = []
            for supplied in supplied_paragraphs:
                paragraphs.extend(split_paragraphs(str(supplied)))
        else:
            content = (
                item.get("content")
                or item.get("summary")
                or item.get("description")
                or headline
            )
            paragraphs = split_paragraphs(content)
        if not paragraphs and headline:
            paragraphs = [headline]
        for paragraph_index, paragraph in enumerate(paragraphs, start=1):
            if len(paragraph) < 8:
                continue
            chunks.append(
                {
                    "asset_symbol": asset_symbol,
                    "asset_name": asset_name,
                    "headline": headline or paragraph[:140],
                    "chunk_text": paragraph,
                    "paragraph_index": paragraph_index,
                    "source_url": item.get("article_url") or item.get("source"),
                    "published_at": item.get("published_at"),
                }
            )
    return chunks


def build_memory_query(
    question: str,
    symbol: str,
    asset_name: str,
    target_price,
    market_price: Optional[Dict],
    news_items: Sequence[Dict],
) -> str:
    parts = [
        f"Asset: {asset_name} ({symbol})",
        f"Question: {question}",
    ]
    if isinstance(target_price, (int, float)):
        parts.append(f"Target price: {target_price:,.2f}")
    if market_price and isinstance(market_price.get("price"), (int, float)):
        parts.append(f"Current price: {market_price['price']:,.4f}")
    headlines = [item.get("headline") for item in news_items if item.get("headline")]
    if headlines:
        parts.append("Current headlines: " + " | ".join(headlines[:6]))
    return "\n".join(parts)


def _document_embedding_text(document: Dict) -> str:
    parts = [
        document.get("asset_name") or "",
        document.get("asset_symbol") or "",
        document.get("headline") or "",
        document.get("chunk_text") or "",
    ]
    return "\n".join(part for part in parts if part)


def _embed_and_store_documents(
    store: SQLiteVectorStore,
    embedder: EmbeddingService,
    documents: Sequence[Dict],
) -> int:
    stored_count = 0
    for start in range(0, len(documents), REINDEX_BATCH_SIZE):
        batch = documents[start : start + REINDEX_BATCH_SIZE]
        texts = [_document_embedding_text(document) for document in batch]
        vectors = embedder.embed_texts(texts)
        stored_count += store.upsert_embeddings(
            batch, vectors, embedder.embedding_space
        )
    return stored_count


def retrieve_then_persist_memory(
    store: SQLiteVectorStore,
    embedder: EmbeddingService,
    memory_query: str,
    current_chunks: Sequence[Dict],
) -> Dict:
    started = time.time()
    before_unscoped = store.stats()
    query_vector = embedder.embed_texts([memory_query])[0]
    embedding_space = embedder.embedding_space

    missing_documents = store.documents_without_embedding(embedding_space)
    reindexed_count = _embed_and_store_documents(store, embedder, missing_documents)
    before = store.stats(embedding_space)

    historical_matches = []
    if before_unscoped["document_count"] > 0:
        historical_matches = store.search(
            query_vector, embedding_space, limit=HISTORY_LIMIT
        )

    upsert_result = store.upsert_documents(current_chunks)
    current_documents = upsert_result["documents"]
    embedded_current_count = _embed_and_store_documents(
        store, embedder, current_documents
    )
    after = store.stats(embedding_space)

    return {
        "success": True,
        "source": "sqlite.cosine_vector_store",
        "database_path": str(store.database_path.resolve()),
        "query": memory_query,
        "historical_matches": historical_matches,
        "retrieved_count": len(historical_matches),
        "documents_before": before_unscoped["document_count"],
        "active_vectors_before": before["active_embedding_count"],
        "inserted_count": upsert_result["inserted_count"],
        "duplicate_count": upsert_result["duplicate_count"],
        "embedded_current_count": embedded_current_count,
        "reindexed_count": reindexed_count,
        "documents_after": after["document_count"],
        "active_vectors_after": after["active_embedding_count"],
        "embedding": embedder.details(),
        "latency_ms": int((time.time() - started) * 1000),
        "fallback_used": embedder.details()["fallback_used"],
        "cache_hit": False,
    }


def _parse_datetime(raw_value) -> Optional[datetime]:
    if not raw_value:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(raw_value) -> Optional[float]:
    parsed = _parse_datetime(raw_value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)


def _freshness_value(age_days: Optional[float], is_price: bool = False) -> float:
    if age_days is None:
        return 45.0
    if is_price:
        if age_days <= 1.0 / 24.0:
            return 100.0
        if age_days <= 1.0:
            return 75.0
        return 35.0
    if age_days <= 1.0:
        return 100.0
    if age_days <= 3.0:
        return 85.0
    if age_days <= 7.0:
        return 65.0
    if age_days <= 30.0:
        return 35.0
    return 10.0


def _score_label(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 60:
        return "moderate"
    return "low"


def build_quality_report(
    state: Dict,
    tools_by_name: Dict[str, Dict],
    memory_result: Dict,
    answer: Dict,
    valid_evidence_ids: Sequence[str],
    raw_citations: Sequence[str],
    claim_validation: Dict,
) -> Dict:
    price = state["evidence"].get("market_price") or {}
    news = state["evidence"].get("news_context") or []
    crowd = state["evidence"].get("crowd_probability") or []

    coverage = 0.0
    if price.get("success"):
        coverage += 40.0
    coverage += 35.0 * min(len(news), 3) / 3.0
    coverage += 25.0 * min(len(crowd), 2) / 2.0

    freshness_values = []
    if price.get("success"):
        freshness_values.append(
            _freshness_value(_age_days(price.get("timestamp")), is_price=True)
        )
    freshness_values.extend(
        _freshness_value(_age_days(item.get("published_at"))) for item in news
    )
    freshness = (
        sum(freshness_values) / len(freshness_values)
        if freshness_values
        else 0.0
    )

    reliability_weights = {
        "get_market_price": 40.0,
        "get_news_context": 35.0,
        "get_crowd_probability": 25.0,
    }
    source_reliability = 0.0
    for name, weight in reliability_weights.items():
        result = tools_by_name.get(name, {})
        if result.get("success") and not result.get("fallback_used", False):
            source_reliability += weight
        elif result.get("success"):
            source_reliability += weight * 0.45

    article_result = tools_by_name.get("extract_article_paragraphs", {})
    article_count = int(article_result.get("article_count", 0) or 0)
    attempted_articles = int(article_result.get("attempted_count", 0) or 0)
    if not news:
        news_depth = 0.0
    elif article_result.get("paragraph_count", 0):
        denominator = max(1, min(len(news), attempted_articles or len(news)))
        news_depth = 40.0 + 60.0 * min(1.0, article_count / denominator)
    elif article_result.get("source") == "publisher.article_html":
        news_depth = 35.0
    else:
        news_depth = 15.0

    historical_matches = memory_result.get("historical_matches") or []
    history_available = memory_result.get("documents_before", 0) > 0
    historical_retrieval = None
    if history_available:
        similarities = [
            max(0.0, min(1.0, float(item.get("similarity", 0.0))))
            for item in historical_matches
        ]
        similarity_score = (
            sum(similarities) / len(similarities) * 100.0 if similarities else 0.0
        )
        result_count_score = min(len(historical_matches), HISTORY_LIMIT) / HISTORY_LIMIT * 100.0
        historical_retrieval = similarity_score * 0.8 + result_count_score * 0.2

    required_sections = ["current_view", "why", "risks", "future_expectation"]
    present_sections = 0
    for section in required_sections:
        value = answer.get(section)
        if isinstance(value, list) and value:
            present_sections += 1
        elif isinstance(value, str) and value.strip():
            present_sections += 1
    answer_completeness = present_sections / len(required_sections) * 100.0

    valid_set = set(valid_evidence_ids)
    cited = [citation for citation in raw_citations if citation in valid_set]
    unique_cited = set(cited)
    citation_target = min(4, len(valid_set))
    citation_coverage = (
        min(1.0, len(unique_cited) / citation_target) * 100.0
        if citation_target
        else 0.0
    )
    citation_validity = (
        len(cited) / len(raw_citations) * 100.0 if raw_citations else 0.0
    )
    grounding = citation_coverage * 0.75 + citation_validity * 0.25
    claim_validation_score = max(
        0.0, 100.0 - 30.0 * len(claim_validation.get("removed_claims", []))
    )

    components = {
        "evidence_coverage": round(coverage, 1),
        "freshness": round(freshness, 1),
        "source_reliability": round(source_reliability, 1),
        "news_depth": round(news_depth, 1),
        "historical_retrieval": (
            round(historical_retrieval, 1)
            if historical_retrieval is not None
            else None
        ),
        "answer_completeness": round(answer_completeness, 1),
        "grounding": round(grounding, 1),
        "claim_validation": round(claim_validation_score, 1),
    }
    weights = {
        "evidence_coverage": 0.20,
        "freshness": 0.15,
        "source_reliability": 0.15,
        "news_depth": 0.10,
        "answer_completeness": 0.10,
        "grounding": 0.10,
        "claim_validation": 0.10,
    }
    if historical_retrieval is not None:
        weights["historical_retrieval"] = 0.10
    total_weight = sum(weights.values())
    overall = sum(components[name] * weight for name, weight in weights.items()) / total_weight

    return {
        "overall": round(overall, 1),
        "label": _score_label(overall),
        "components": components,
        "weights": weights,
        "valid_citations": sorted(unique_cited),
        "invalid_citations": sorted(set(raw_citations) - valid_set),
        "explanation": (
            "This score measures evidence coverage, freshness, source health, "
            "retrieval similarity, output completeness, citation grounding, and "
            "unsupported-source claim checks. "
            "It does not measure whether the market forecast will be correct."
        ),
    }


def detect_conflicts(news_items: Sequence[Dict], crowd_items: Sequence[Dict]) -> List[str]:
    conflicts = []
    news_mix = {item.get("sentiment") for item in news_items}
    if "bullish" in news_mix and "bearish" in news_mix:
        conflicts.append("Current news contains both bullish and bearish signals.")
    probabilities = [
        item.get("probability")
        for item in crowd_items
        if isinstance(item.get("probability"), (int, float))
    ]
    if probabilities and min(probabilities) <= 0.45 and max(probabilities) >= 0.55:
        conflicts.append("Relevant Polymarket probabilities disagree with one another.")
    return conflicts


def build_reasoning_schema() -> Dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "current_view": {"type": "string"},
            "why": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "future_expectation": {"type": "string"},
            "confidence": {"type": "number"},
            "evidence_used": {"type": "array", "items": {"type": "string"}},
            "reasoning_trace": {"type": "array", "items": {"type": "string"}},
            "stop_reason": {"type": "string"},
        },
        "required": [
            "current_view",
            "why",
            "risks",
            "future_expectation",
            "confidence",
            "evidence_used",
            "reasoning_trace",
            "stop_reason",
        ],
    }


def call_ollama_reasoning(
    question: str, evidence_payload: Dict, model: str
) -> Dict:
    prompt = (
        "You are the final reasoning stage of an autonomous market-analysis agent. "
        "Use only the supplied evidence and never invent prices, events, dates, or causes. "
        "Treat historical_context as potentially stale background: prioritize current evidence, "
        "and use history only when its similarity and date make it useful. This is analysis, not "
        "trade execution or a guaranteed forecast.\n\n"
        "Organize the answer into: current_view (present assessment), why (specific evidence-backed "
        "reasons), risks (downside, uncertainty, and missing evidence), and future_expectation "
        "(conditional scenarios, not certainty). confidence must be between 0 and 1. In "
        "evidence_used, return only evidence_id values that appear in the payload. Return JSON only.\n\n"
        f"User question: {question}\n\n"
        f"Evidence payload:\n{json.dumps(evidence_payload, ensure_ascii=True)}"
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": build_reasoning_schema(),
        "options": {"temperature": 0.15, "num_predict": 1200},
        "keep_alive": "10m",
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("OLLAMA_GENERATE_URL", OLLAMA_GENERATE_URL),
        method="POST",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    timeout = int(os.environ.get("OLLAMA_GENERATE_TIMEOUT_SECONDS", "180"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        raw_text = response_payload.get("response") or ""
        parsed = base_agent.safe_json_loads(raw_text)
        return {
            "success": True,
            "mode": "ollama_generate_with_history_rag",
            "model": model,
            "latency_ms": int((time.time() - started) * 1000),
            "result": parsed,
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        error_detail = f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        error_detail = str(exc)
    return {
        "success": False,
        "mode": "rule_based_fallback",
        "model": model,
        "latency_ms": int((time.time() - started) * 1000),
        "error_code": "ollama_reasoning_failed",
        "error_detail": error_detail,
    }


def _future_expectation(state: Dict) -> str:
    price = state["evidence"].get("market_price") or {}
    current_price = price.get("price")
    target_price = state.get("target_price")
    if isinstance(current_price, (int, float)) and isinstance(target_price, (int, float)):
        distance = (target_price - current_price) / current_price * 100.0
        if distance > 0:
            return (
                f"The target is {distance:.1f}% above the current price. Reaching it requires "
                "continued catalysts and price follow-through; weaker news would reduce that path's plausibility."
            )
        return (
            f"The target is already {abs(distance):.1f}% below the current price. Future analysis "
            "should focus on whether the asset can hold above it rather than first reaching it."
        )
    return (
        "The near-term path remains conditional on new catalysts and follow-through in price. "
        "A new run should reassess the view as fresh evidence enters the memory store."
    )


def build_rule_based_fallback(planner: Dict, state: Dict, tools: Sequence[Dict]) -> Dict:
    base_answer = base_agent.build_rule_based_answer(planner, state, tools)
    why = base_answer.get("supporting_factors") or []
    if not why:
        why = ["The available evidence did not produce a strong supporting factor."]
    risks = list(base_answer.get("opposing_factors") or [])
    risks.extend(state.get("conflicts") or [])
    if not risks:
        risks = ["Market outcomes remain uncertain even when the collected signals agree."]
    evidence_ids = []
    if state["evidence"].get("market_price"):
        evidence_ids.append("price-1")
    evidence_ids.extend(
        f"news-{index}"
        for index, _ in enumerate(state["evidence"].get("news_context", [])[:2], start=1)
    )
    if state["evidence"].get("crowd_probability"):
        evidence_ids.append("poly-1")
    if state["evidence"].get("historical_context"):
        evidence_ids.append(
            f"history-{state['evidence']['historical_context'][0]['id']}"
        )
    return {
        "current_view": base_answer["market_narrative"],
        "why": why[:5],
        "risks": risks[:5],
        "future_expectation": _future_expectation(state),
        "confidence": base_answer["confidence"],
        "evidence_used": evidence_ids,
        "reasoning_trace": base_answer["reasoning_trace"],
        "stop_reason": state["stop_reason"],
    }


def normalize_answer(raw_answer: Dict, fallback: Dict) -> Dict:
    answer = {}
    answer["current_view"] = str(
        raw_answer.get("current_view") or fallback["current_view"]
    ).strip()
    for key in ["why", "risks", "evidence_used", "reasoning_trace"]:
        value = raw_answer.get(key)
        if not isinstance(value, list):
            value = fallback[key]
        answer[key] = [str(item).strip() for item in value if str(item).strip()]
    answer["future_expectation"] = str(
        raw_answer.get("future_expectation") or fallback["future_expectation"]
    ).strip()
    try:
        confidence = float(raw_answer.get("confidence"))
    except (TypeError, ValueError):
        confidence = float(fallback["confidence"])
    answer["confidence"] = round(max(0.0, min(1.0, confidence)), 2)
    answer["stop_reason"] = str(
        raw_answer.get("stop_reason") or fallback["stop_reason"]
    ).strip()
    return answer


def validate_answer_against_evidence(
    answer: Dict, evidence_payload: Dict, fallback: Dict
):
    validated = dict(answer)
    for key in ["why", "risks", "evidence_used", "reasoning_trace"]:
        validated[key] = list(answer.get(key) or [])
    removed_claims = []

    source_rules = [
        {
            "source": "Polymarket",
            "available": bool(evidence_payload.get("current_polymarket")),
            "terms": ["polymarket", "crowd probabil", "prediction market"],
            "missing_risk": (
                "No relevant ongoing price-related Polymarket market was found, "
                "so crowd-probability evidence is missing."
            ),
        },
        {
            "source": "historical memory",
            "available": bool(evidence_payload.get("historical_context")),
            "terms": ["historical context", "historical evidence", "history shows", "prior chunk"],
            "missing_risk": (
                "No prior historical chunk was available for this run, so the view "
                "relies on current evidence only."
            ),
        },
        {
            "source": "current news",
            "available": bool(evidence_payload.get("current_news")),
            "terms": ["current news", "recent news", "headline"],
            "missing_risk": (
                "No current news evidence was available, which limits catalyst analysis."
            ),
        },
    ]

    def mentions_terms(text, terms):
        lowered = str(text or "").lower()
        return any(term in lowered for term in terms)

    for rule in source_rules:
        if rule["available"]:
            continue
        for section in ["current_view", "future_expectation"]:
            if mentions_terms(validated.get(section), rule["terms"]):
                removed_claims.append(
                    {
                        "source": rule["source"],
                        "section": section,
                        "text": validated[section],
                    }
                )
                validated[section] = fallback[section]
        for section in ["why", "risks"]:
            retained = []
            for item in validated[section]:
                if mentions_terms(item, rule["terms"]):
                    removed_claims.append(
                        {
                            "source": rule["source"],
                            "section": section,
                            "text": item,
                        }
                    )
                else:
                    retained.append(item)
            validated[section] = retained
        if rule["missing_risk"] not in validated["risks"]:
            validated["risks"].append(rule["missing_risk"])

    if not validated["why"]:
        validated["why"] = list(fallback["why"])
    if not validated["risks"]:
        validated["risks"] = list(fallback["risks"])
    if removed_claims:
        validated["reasoning_trace"].append(
            f"Removed {len(removed_claims)} source-specific claim(s) that lacked evidence."
        )
    return validated, {
        "passed": not removed_claims,
        "removed_claim_count": len(removed_claims),
        "removed_claims": removed_claims,
    }


def _with_evidence_ids(items: Sequence[Dict], prefix: str) -> List[Dict]:
    output = []
    for index, item in enumerate(items, start=1):
        copied = dict(item)
        copied["evidence_id"] = f"{prefix}-{index}"
        output.append(copied)
    return output


def _news_with_evidence_ids(items: Sequence[Dict]) -> List[Dict]:
    output = []
    allowed_keys = [
        "headline",
        "sentiment",
        "relevance",
        "published_at",
        "source",
        "article_url",
    ]
    for index, item in enumerate(items, start=1):
        copied = {key: item.get(key) for key in allowed_keys if item.get(key) is not None}
        paragraphs = item.get("paragraphs")
        if isinstance(paragraphs, list):
            copied["paragraphs"] = [
                str(paragraph)[:900] for paragraph in paragraphs[:3]
            ]
        copied["evidence_id"] = f"news-{index}"
        output.append(copied)
    return output


def analyze_with_memory(
    question: str,
    symbol: str,
    target_price,
    asset_name_override: Optional[str],
    model: str,
    store: SQLiteVectorStore,
    embedder: EmbeddingService,
) -> Dict:
    started = time.time()
    planner = base_agent.build_planner(question, symbol, target_price)
    state = {
        "user_question": question,
        "analysis_mode": planner["question_type"],
        "asset_symbol": symbol,
        "asset_name": asset_name_override,
        "target_price": target_price,
        "time_horizon": planner["time_horizon"],
        "tool_round": 1,
        "tool_history": [],
        "evidence": {
            "market_price": None,
            "news_context": [],
            "crowd_probability": [],
            "historical_context": [],
        },
        "open_questions": list(planner["open_questions"]),
        "conflicts": [],
        "confidence": None,
        "stop_reason": None,
        "final_answer": None,
    }
    tools = []

    asset_info = base_agent.get_asset_name(symbol)
    if asset_name_override:
        asset_info["asset_name"] = asset_name_override
    state["asset_name"] = asset_info["asset_name"]
    tools.append({"tool": "get_asset_name", "result": asset_info})

    price_info = base_agent.get_market_price(symbol)
    tools.append({"tool": "get_market_price", "result": price_info})
    if price_info.get("success"):
        state["evidence"]["market_price"] = price_info

    news_info = base_agent.get_news_context(symbol, state["asset_name"])
    tools.append({"tool": "get_news_context", "result": news_info})
    if news_info.get("success"):
        current_news = news_info.get("items", [])
        if not news_info.get("fallback_used", False):
            current_news, article_info = enrich_news_with_article_paragraphs(
                current_news
            )
        else:
            article_info = {
                "success": False,
                "source": "none",
                "attempted_count": 0,
                "article_count": 0,
                "paragraph_count": 0,
                "headline_fallback_count": len(current_news),
                "errors": ["fixture_news_is_not_persisted"],
                "latency_ms": 0,
                "fallback_used": True,
                "cache_hit": True,
            }
        state["evidence"]["news_context"] = current_news
        tools.append({"tool": "extract_article_paragraphs", "result": article_info})

    current_price = price_info.get("price") if price_info.get("success") else None
    crowd_info = base_agent.get_crowd_probability(
        symbol,
        state["asset_name"],
        current_price=current_price,
        target_price=target_price,
    )
    tools.append({"tool": "get_crowd_probability", "result": crowd_info})
    if crowd_info.get("success"):
        state["evidence"]["crowd_probability"] = crowd_info.get("items", [])

    state["conflicts"] = detect_conflicts(
        state["evidence"]["news_context"],
        state["evidence"]["crowd_probability"],
    )

    memory_query = build_memory_query(
        question,
        symbol,
        state["asset_name"],
        target_price,
        state["evidence"]["market_price"],
        state["evidence"]["news_context"],
    )
    current_chunks = []
    if news_info.get("success") and not news_info.get("fallback_used", False):
        current_chunks = news_items_to_chunks(
            state["evidence"]["news_context"], symbol, state["asset_name"]
        )
    memory_result = retrieve_then_persist_memory(
        store, embedder, memory_query, current_chunks
    )
    tools.append({"tool": "retrieve_and_persist_news_memory", "result": memory_result})
    state["evidence"]["historical_context"] = memory_result["historical_matches"]

    state["stop_reason"] = "Live evidence and available historical memory were collected."
    if state["conflicts"]:
        state["stop_reason"] = (
            "Stopped after collecting live and historical evidence; conflicting signals remain."
        )

    price_evidence = dict(state["evidence"]["market_price"] or {})
    if price_evidence:
        price_evidence["evidence_id"] = "price-1"
    news_evidence = _news_with_evidence_ids(state["evidence"]["news_context"][:6])
    crowd_evidence = _with_evidence_ids(
        state["evidence"]["crowd_probability"][:5], "poly"
    )
    historical_evidence = []
    for item in state["evidence"]["historical_context"]:
        historical_evidence.append(
            {
                "evidence_id": f"history-{item['id']}",
                "asset_symbol": item.get("asset_symbol"),
                "headline": item.get("headline"),
                "paragraph": item.get("chunk_text"),
                "published_at": item.get("published_at"),
                "similarity": item.get("similarity"),
                "source_url": item.get("source_url"),
                "context_role": "historical background; may be stale",
            }
        )

    evidence_payload = {
        "planner": planner,
        "asset_symbol": symbol,
        "asset_name": state["asset_name"],
        "target_price": target_price,
        "market_price": price_evidence or None,
        "current_news": news_evidence,
        "current_polymarket": crowd_evidence,
        "historical_context": historical_evidence,
        "conflicts": state["conflicts"],
        "memory_policy": (
            "Historical chunks are the top three prior paragraphs by cosine similarity. "
            "They were retrieved before this run's news was inserted."
        ),
    }
    valid_evidence_ids = []
    if price_evidence:
        valid_evidence_ids.append("price-1")
    valid_evidence_ids.extend(item["evidence_id"] for item in news_evidence)
    valid_evidence_ids.extend(item["evidence_id"] for item in crowd_evidence)
    valid_evidence_ids.extend(item["evidence_id"] for item in historical_evidence)

    fallback_answer = build_rule_based_fallback(planner, state, tools)
    llm_result = call_ollama_reasoning(question, evidence_payload, model)
    raw_answer = llm_result.get("result") if llm_result.get("success") else fallback_answer
    answer = normalize_answer(raw_answer or {}, fallback_answer)
    answer, claim_validation = validate_answer_against_evidence(
        answer, evidence_payload, fallback_answer
    )
    raw_citations = list(answer["evidence_used"])
    answer["evidence_used"] = [
        citation for citation in raw_citations if citation in set(valid_evidence_ids)
    ]
    state["confidence"] = answer["confidence"]
    state["stop_reason"] = answer["stop_reason"]

    tools_by_name = {entry["tool"]: entry["result"] for entry in tools}
    quality = build_quality_report(
        state,
        tools_by_name,
        memory_result,
        answer,
        valid_evidence_ids,
        raw_citations,
        claim_validation,
    )

    workflow_steps = [
        "Resolved the user input and collected current price, news, and ongoing Polymarket evidence.",
        "Extracted publisher paragraphs when available and used headlines as explicit fallback chunks when blocked.",
        "Split current live news into paragraph chunks without storing fixture fallback news.",
        "Embedded the analysis query and migrated any old chunks missing the active vector space.",
        "Retrieved the top three prior chunks by cosine similarity before inserting current news.",
        "Persisted and deduplicated current chunks for future runs.",
        f"Sent live evidence plus {len(historical_evidence)} historical chunks to {model} for structured reasoning.",
        "Calculated evidence/process quality separately from the model's forecast confidence.",
    ]
    if not llm_result.get("success"):
        workflow_steps[-2] = (
            f"Ollama model {model} was unavailable, so the structured rule-based fallback was used."
        )

    final_answer = {
        **answer,
        "asset": state["asset_name"] or symbol,
        "symbol": symbol,
        "analysis_mode": state["analysis_mode"],
        "current_price_display": (
            f"{current_price:,.2f}" if isinstance(current_price, (int, float)) else None
        ),
        "llm_model": model,
        "llm_mode": llm_result["mode"],
        "runtime_ms": int((time.time() - started) * 1000),
        "quality": quality,
        "claim_validation": claim_validation,
        "workflow_steps": workflow_steps,
    }
    state["final_answer"] = final_answer
    state["tool_history"] = [
        {"tool": entry["tool"], "success": entry["result"].get("success", False)}
        for entry in tools
    ]
    return {
        "planner": planner,
        "state": state,
        "tools": tools,
        "memory": memory_result,
        "llm_input": evidence_payload,
        "llm_result": llm_result,
        "quality": quality,
        "claim_validation": claim_validation,
        "generated_at": utc_now_iso(),
    }
