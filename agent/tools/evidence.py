import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple


RELIABILITY = {
    "market_price": 0.90,
    "price_history": 0.90,
    "financial_statement": 0.96,
    "news": 0.72,
    "crowd_expectation": 0.66,
    "historical_news": 0.58,
    "user_context": 0.55,
}

POSITIVE_WORDS = {
    "beat", "beats", "growth", "surge", "surges", "gain", "gains", "upgrade", "record", "strong",
    "higher", "rise", "rises", "rally", "rallies", "bullish", "inflow", "expands", "profit",
}
NEGATIVE_WORDS = {
    "miss", "misses", "decline", "drop", "downgrade", "weak", "risk", "cut",
    "lower", "fall", "falls", "bearish", "outflow", "lawsuit", "loss",
}


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _direction(text: str, explicit: Any = None, focus_terms: Optional[List[str]] = None) -> int:
    if explicit in {-1, 0, 1}:
        return int(explicit)
    if isinstance(explicit, str):
        mapped = {"bullish": 1, "positive": 1, "bearish": -1, "negative": -1, "mixed": 0}
        if explicit.lower() in mapped:
            return mapped[explicit.lower()]
    lowered = (text or "").lower()
    if focus_terms:
        clauses = re.split(r"[,;|]", lowered)
        focused_score = 0
        for clause in clauses:
            if not any(term.lower() in clause for term in focus_terms if term):
                continue
            focused_tokens = set(re.findall(r"[a-z]+", clause))
            focused_score += len(focused_tokens & POSITIVE_WORDS)
            focused_score -= len(focused_tokens & NEGATIVE_WORDS)
        if focused_score:
            return 1 if focused_score > 0 else -1
    tokens = set(re.findall(r"[a-z]+", lowered))
    positive = len(tokens & POSITIVE_WORDS)
    negative = len(tokens & NEGATIVE_WORDS)
    return 1 if positive > negative else -1 if negative > positive else 0


def _freshness(timestamp: Any, evidence_type: str, as_of: datetime) -> str:
    if evidence_type == "historical_news":
        return "historical"
    parsed = _parse_time(timestamp)
    if not parsed:
        return "unknown"
    age_days = max(0.0, (as_of - parsed).total_seconds() / 86400)
    if age_days <= 3:
        return "fresh"
    if age_days <= 14:
        return "current"
    if age_days <= 120:
        return "aging"
    return "historical"


def _fingerprint(title: str, content: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", f"{title} {content}".lower()).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _base_item(
    evidence_type: str,
    source: str,
    title: str,
    content: str,
    timestamp: Any,
    source_url: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    explicit_direction: Any = None,
    focus_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "evidence_type": evidence_type,
        "source": source,
        "title": title,
        "content": content,
        "timestamp": timestamp,
        "source_url": source_url,
        "direction": _direction(
            f"{title} {content}", explicit_direction, focus_terms=focus_terms
        ),
        "reliability": RELIABILITY[evidence_type],
        "metadata": metadata or {},
    }


def _polymarket_direction(item: Dict[str, Any]) -> int:
    question = str(item.get("market") or "").lower()
    probability = item.get("probability")
    if not isinstance(probability, (int, float)):
        return 0
    if "outcome_direction" in item:
        direction = item["outcome_direction"]
        return direction if probability >= 0.55 else -direction if probability <= 0.45 else 0
    positive_question = any(word in question for word in ("above", "higher", "hit", "reach", "up or down"))
    negative_question = any(word in question for word in ("below", "lower"))
    if positive_question:
        return 1 if probability >= 0.55 else -1 if probability <= 0.45 else 0
    if negative_question:
        return -1 if probability >= 0.55 else 1 if probability <= 0.45 else 0
    return 0


def normalize_evidence(
    raw_evidence: List[Dict[str, Any]],
    as_of_value: str,
    asset_terms: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    as_of = _parse_time(as_of_value) or datetime.now(timezone.utc)
    candidates: List[Dict[str, Any]] = []
    for row in raw_evidence:
        kind = row.get("kind")
        payload = row.get("payload") or {}
        if kind == "market_price" and payload.get("success"):
            content = (
                f"Current price {payload.get('price')} {payload.get('currency')}; "
                f"1-day return {payload.get('return_1d_pct')}%; "
                f"1-week return {payload.get('return_1w_pct')}%."
            )
            candidates.append(
                _base_item(
                    "market_price", payload.get("source", "Yahoo Finance"),
                    "Live market price and momentum", content, payload.get("timestamp"),
                    payload.get("source_url", ""), payload,
                    1 if (payload.get("return_1d_pct") or 0) > 0 else -1 if (payload.get("return_1d_pct") or 0) < 0 else 0,
                )
            )
        elif kind == "price_history" and payload.get("success"):
            candidates.append(
                _base_item(
                    "price_history", payload.get("source", "Yahoo Finance"),
                    f"{payload.get('range', 'historical')} price history",
                    f"Yahoo returned {len(payload.get('points') or [])} historical price observations.",
                    payload.get("retrieved_at"), payload.get("source_url", ""),
                    {"point_count": len(payload.get("points") or []), "range": payload.get("range")},
                )
            )
        elif kind == "news":
            for item in payload.get("items", []):
                candidates.append(
                    _base_item(
                        "news", item.get("source_name") or "Google News",
                        item.get("headline") or "News item",
                        item.get("summary") or item.get("headline") or "",
                        item.get("published_at"), item.get("resolved_url") or item.get("source_url") or "",
                        {"paragraph_count": len(item.get("paragraphs") or [])},
                        focus_terms=asset_terms,
                    )
                )
        elif kind == "financials" and payload.get("success"):
            for metric in payload.get("metrics", []):
                growth = metric.get("growth_pct")
                content = f"{metric.get('label')}: {metric.get('value')} {metric.get('unit')}"
                if growth is not None:
                    content += f"; comparable-period growth {growth}%."
                candidates.append(
                    _base_item(
                        "financial_statement", payload.get("source", "SEC"),
                        metric.get("label") or metric.get("key") or "Financial metric", content,
                        metric.get("filed_at") or metric.get("period_end"), payload.get("source_url", ""),
                        metric, 1 if isinstance(growth, (int, float)) and growth > 0 else -1 if isinstance(growth, (int, float)) and growth < 0 else 0,
                    )
                )
        elif kind == "polymarket":
            for item in payload.get("items", []):
                content = (
                    f"Selected outcome {item.get('probability_outcome')} has probability "
                    f"{item.get('probability')}; this is market expectation, not a fact."
                )
                if item.get("provider") == "coinrithm":
                    content += f" Data by CoinRithm; original venue: Polymarket. Data as of {item.get('data_as_of')}; event ends {item.get('end_at')}. {item.get('limitation', '')}"
                candidates.append(
                    _base_item(
                        "crowd_expectation", payload.get("source", "Polymarket Gamma API"),
                        item.get("market") or "Polymarket price market", content,
                        item.get("data_as_of") or item.get("retrieved_at"), item.get("source_url", ""), item,
                        _polymarket_direction(item),
                    )
                )
        elif kind == "vector_history":
            for item in payload.get("items", []):
                candidates.append(
                    _base_item(
                        "historical_news", "Persistent vector DB",
                        item.get("headline") or "Historical news paragraph",
                        item.get("chunk_text") or "", item.get("published_at"),
                        item.get("source_url") or "", {"similarity": item.get("similarity"), "document_id": item.get("id")},
                        focus_terms=asset_terms,
                    )
                )
        elif kind in {"user_notes", "user_headlines"}:
            for text in payload.get("items", []):
                candidates.append(
                    _base_item(
                        "user_context", "User-provided context", str(text)[:180], str(text),
                        payload.get("retrieved_at"), metadata={"kind": kind},
                    )
                )

    deduped: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in candidates:
        key = _fingerprint(item["title"], item["content"])
        if key in seen:
            continue
        seen.add(key)
        item["freshness"] = _freshness(item.get("timestamp"), item["evidence_type"], as_of)
        evidence_time = _parse_time(item.get("timestamp"))
        item["temporal_valid"] = not (
            evidence_time and evidence_time > as_of + timedelta(minutes=5)
        )
        item["id"] = f"EV-{len(deduped) + 1:03d}"
        deduped.append(item)

    groups = _group_news(deduped)
    return deduped, groups


def _group_news(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    for item in [row for row in evidence if row["evidence_type"] == "news"]:
        tokens = set(re.findall(r"[a-z]{4,}", item["title"].lower()))
        matched = None
        for group in groups:
            union = tokens | group["tokens"]
            similarity = len(tokens & group["tokens"]) / len(union) if union else 0
            if similarity >= 0.45:
                matched = group
                break
        if matched:
            matched["evidence_ids"].append(item["id"])
            matched["tokens"].update(tokens)
        else:
            groups.append(
                {
                    "group_id": f"NEWS-G{len(groups) + 1:02d}",
                    "label": item["title"],
                    "evidence_ids": [item["id"]],
                    "tokens": tokens,
                }
            )
    for group in groups:
        group["tokens"] = sorted(group["tokens"])
    return groups


def evaluate_gate(
    evidence: List[Dict[str, Any]], critical_types: List[str], retries: int, max_retries: int
) -> Dict[str, Any]:
    present = {item["evidence_type"] for item in evidence if item.get("temporal_valid", True)}
    missing = [item for item in critical_types if item not in present]
    optional_present = sorted(present - set(critical_types))
    coverage = (len(critical_types) - len(missing)) / len(critical_types) if critical_types else 1.0
    sufficient = not missing
    retry_allowed = bool(missing) and retries < max_retries
    return {
        "sufficient": sufficient,
        "degraded": bool(missing) and not retry_allowed,
        "critical_types": critical_types,
        "present_types": sorted(present),
        "optional_present_types": optional_present,
        "missing_critical": missing,
        "coverage_pct": round(coverage * 100),
        "retry_allowed": retry_allowed,
        "decision": "retrieve_more" if retry_allowed else "analyze",
        "reason": (
            "All critical evidence types are present."
            if sufficient
            else "Retry limit reached; continue with explicit limitations."
            if not retry_allowed
            else f"Missing critical evidence: {', '.join(missing)}."
        ),
    }
