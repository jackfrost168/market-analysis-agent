import json
import math
import os
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .market_data import utc_now_iso


OFFICIAL_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
OFFICIAL_GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
GAMMA_BASE_URL = os.environ.get("POLYMARKET_GAMMA_URL", OFFICIAL_GAMMA_BASE_URL).rstrip("/")
HTTP_TIMEOUT_SECONDS = 15
TAG_SLUGS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "AAPL": "apple",
    "GOOG": "google",
    "GOOGL": "google",
    "TSLA": "tesla",
    "NVDA": "nvidia",
}


class GammaAPIError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        url: str,
        detail: str,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.status_code = status_code
        self.url = url
        self.detail = detail
        self.headers = headers or {}
        super().__init__(f"Gamma API HTTP {status_code}: {detail or 'request rejected'}")


def _fetch_json(url: str) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        headers = {key: value for key, value in exc.headers.items()} if exc.headers else {}
        raise GammaAPIError(exc.code, url, detail, headers) from exc
    return {
        "payload": payload,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "url": url,
    }


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


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


def _end_time(market: Dict[str, Any], event: Dict[str, Any]) -> Optional[datetime]:
    for key in ("endDate", "end_date_iso", "endDateIso", "end_date"):
        parsed = _parse_time(market.get(key))
        if parsed:
            return parsed
    for key in ("endDate", "end_date_iso", "endDateIso", "end_date"):
        parsed = _parse_time(event.get(key))
        if parsed:
            return parsed
    return None


def _is_ongoing(market: Dict[str, Any], event: Dict[str, Any]) -> bool:
    if market.get("closed") is True or market.get("archived") is True:
        return False
    if event.get("closed") is True or event.get("archived") is True:
        return False
    if market.get("active") is False or event.get("active") is False:
        return False
    end_at = _end_time(market, event)
    return not end_at or end_at > datetime.now(timezone.utc)


def _asset_terms(symbol: str, asset_name: str) -> List[str]:
    terms = [symbol.lower(), asset_name.lower()]
    aliases = {
        "BTC": ["bitcoin", "btc"],
        "ETH": ["ethereum", "ether", "eth"],
        "AAPL": ["apple", "aapl"],
        "GOOGL": ["google", "alphabet", "googl"],
        "GOOG": ["google", "alphabet", "goog"],
        "TSLA": ["tesla", "tsla"],
        "NVDA": ["nvidia", "nvda"],
    }
    terms.extend(aliases.get(symbol.upper(), []))
    first_name = re.split(r"[\s,]+", asset_name.strip().lower())[0]
    if len(first_name) >= 4:
        terms.append(first_name)
    return list(dict.fromkeys(term for term in terms if len(term) >= 3))


def _contains_term(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered) for term in terms)


def extract_price_levels(text: str) -> List[float]:
    raw = text or ""
    patterns = [
        r"\$\s*(\d[\d,]*(?:\.\d+)?)(?:\s*([kKmMbB])\b)?",
        r"(?:above|below|over|under|hit|reach|trade at|close at)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)(?:\s*([kKmMbB])\b)?",
        r"(\d[\d,]*(?:\.\d+)?)\s*([kKmMbB])\b",
    ]
    levels: List[float] = []
    for pattern in patterns:
        for number, suffix in re.findall(pattern, raw, flags=re.IGNORECASE):
            value = _float(number.replace(",", ""))
            if value is None:
                continue
            multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
                suffix.lower(), 1
            )
            value *= multiplier
            if value > 0 and value not in levels:
                levels.append(value)
    return levels


def _is_price_market(text: str, levels: List[float]) -> bool:
    lowered = text.lower()
    price_phrases = (
        "price",
        "above",
        "below",
        "higher",
        "lower",
        "up or down",
        "hit $",
        "reach $",
        "trade at",
        "close above",
        "close below",
        "all-time high",
    )
    if levels:
        return True
    return any(phrase in lowered for phrase in price_phrases)


def _search_terms(
    symbol: str,
    asset_name: str,
    current_price: Optional[float],
    target_price: Optional[float],
    extra_query: str = "",
) -> List[str]:
    base = "Bitcoin" if symbol.upper() == "BTC" else asset_name
    terms = [base, symbol]
    if target_price is not None:
        rounded = round(target_price)
        terms.extend([f"{base} {rounded}", f"{base} above {rounded}", f"{base} price"])
    elif current_price is not None:
        rounded = round(current_price)
        terms.extend([f"{base} {rounded}", f"{base} price", f"{base} up or down"])
    else:
        terms.append(f"{base} price")
    if extra_query:
        terms.insert(0, extra_query)
    return list(dict.fromkeys(term.strip() for term in terms if term.strip()))[:6]


def _events_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("events", "data"):
        if isinstance(payload.get(key), list):
            return [item for item in payload[key] if isinstance(item, dict)]
    # /events/slug/{slug} returns one event object, unlike the list/search endpoints.
    if isinstance(payload.get("markets"), list) and (payload.get("id") or payload.get("slug")):
        return [payload]
    return []


def _provider_error_code(detail: str) -> Optional[str]:
    for pattern in (
        r"error\s+code\s*:\s*(\d+)",
        r'"error_code"\s*:\s*(\d+)',
        r"\bError\s+(\d+)\s*:",
    ):
        match = re.search(pattern, detail or "", flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _http_error_probe(endpoint: str, url: str, exc: GammaAPIError) -> Dict[str, Any]:
    provider_code = _provider_error_code(exc.detail)
    error = (
        "HTTP 451 was returned by the upstream edge before Polymarket market data was available."
        if exc.status_code == 451
        else f"Polymarket HTTP {exc.status_code}: request rejected"
    )
    return {
        "endpoint": endpoint,
        "path": urllib.parse.urlparse(url).path,
        "success": False,
        "http_status": exc.status_code,
        "error_code": "legal_block" if exc.status_code == 451 else "polymarket_http_error",
        "provider_error_code": provider_code,
        "provider": exc.headers.get("Server"),
        "edge_ray": exc.headers.get("CF-RAY"),
        "response_detail": exc.detail,
        "error": error,
    }


def _fetch_search_events(term: str) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    url = (
        f"{GAMMA_BASE_URL}/public-search?q={urllib.parse.quote_plus(term)}"
        "&events_status=active&limit_per_type=50&keep_closed_markets=0"
        "&search_profiles=false&search_tags=false"
    )
    try:
        response = _fetch_json(url)
        return (
            _events_from_payload(response["payload"]),
            response["latency_ms"],
            [],
        )
    except GammaAPIError as exc:
        return [], 0, [f"HTTP {exc.status_code} | public-search | {exc}"]
    except Exception as exc:
        return [], 0, [f"public-search | {type(exc).__name__}: {exc}"]


def _fetch_active_events(
    symbol: str,
    asset_name: str,
) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    tag_slug = TAG_SLUGS.get(symbol.upper())
    if not tag_slug:
        tag_slug = re.sub(r"[^a-z0-9]+", "-", asset_name.lower()).strip("-")
    queries = []
    if tag_slug:
        queries.append(
            (
                "events-by-tag",
                f"{GAMMA_BASE_URL}/events?active=true&closed=false&limit=100"
                f"&order=volume24hr&ascending=false&tag_slug={urllib.parse.quote_plus(tag_slug)}",
            )
        )
    queries.append(
        (
            "active-events",
            f"{GAMMA_BASE_URL}/events?active=true&closed=false&limit=100"
            "&order=volume24hr&ascending=false",
        )
    )
    events: List[Dict[str, Any]] = []
    latency = 0
    errors = []
    for endpoint, url in queries:
        try:
            response = _fetch_json(url)
            latency += response["latency_ms"]
            events.extend(_events_from_payload(response["payload"]))
            if events:
                break
        except GammaAPIError as exc:
            errors.append(f"HTTP {exc.status_code} | {endpoint} | {exc}")
        except Exception as exc:
            errors.append(f"{endpoint} | {type(exc).__name__}: {exc}")
    return events, latency, errors


def get_polymarket_status() -> Dict[str, Any]:
    started = time.perf_counter()
    probes = [
        (
            "public-search",
            f"{GAMMA_BASE_URL}/public-search?q=Bitcoin&events_status=active"
            "&limit_per_type=1&keep_closed_markets=0&search_profiles=false&search_tags=false",
        ),
        (
            "active-events",
            f"{GAMMA_BASE_URL}/events?active=true&closed=false&limit=1&tag_slug=bitcoin",
        ),
    ]
    results = []
    for endpoint, url in probes:
        try:
            response = _fetch_json(url)
            results.append(
                {
                    "endpoint": endpoint,
                    "path": urllib.parse.urlparse(url).path,
                    "success": True,
                    "http_status": 200,
                    "latency_ms": response["latency_ms"],
                    "event_count": len(_events_from_payload(response["payload"])),
                }
            )
        except GammaAPIError as exc:
            results.append(_http_error_probe(endpoint, url, exc))
        except Exception as exc:
            results.append(
                {
                    "endpoint": endpoint,
                    "path": urllib.parse.urlparse(url).path,
                    "success": False,
                    "http_status": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    try:
        geoblock_response = _fetch_json(OFFICIAL_GEOBLOCK_URL)
        geoblock_payload = geoblock_response.get("payload") or {}
        geoblock_probe = {
            "endpoint": "official-geoblock",
            "path": "/api/geoblock",
            "success": True,
            "http_status": 200,
            "latency_ms": geoblock_response.get("latency_ms"),
            "blocked": bool(geoblock_payload.get("blocked")),
            "country": geoblock_payload.get("country"),
            "region": geoblock_payload.get("region"),
        }
    except GammaAPIError as exc:
        geoblock_probe = _http_error_probe(
            "official-geoblock", OFFICIAL_GEOBLOCK_URL, exc
        )
    except Exception as exc:
        geoblock_probe = {
            "endpoint": "official-geoblock",
            "path": "/api/geoblock",
            "success": False,
            "http_status": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    reachable = any(item["success"] for item in results)
    access_blocked = bool(results) and all(
        item.get("http_status") == 451 for item in results
    )
    state = "reachable" if reachable else "access_blocked" if access_blocked else "unreachable"
    message = (
        "Gamma public market data is reachable."
        if reachable
        else (
            "Both official Gamma discovery endpoints returned HTTP 451 before any market payload. "
            "The official geoblock diagnostic is also blocked at the upstream edge."
            if geoblock_probe.get("http_status") == 451
            else "Both official Gamma discovery endpoints returned HTTP 451 before any market payload."
        )
        if access_blocked
        else "Gamma is configured, but the live probes did not return data."
    )
    return {
        "configured": True,
        "configuration_source": "POLYMARKET_GAMMA_URL" if os.environ.get("POLYMARKET_GAMMA_URL") else "official_default",
        "base_url": GAMMA_BASE_URL,
        "official_base_url": OFFICIAL_GAMMA_BASE_URL,
        "authentication_required": False,
        "status": state,
        "success": reachable,
        "access_blocked": access_blocked,
        "blocking_layer": "upstream_edge" if access_blocked else None,
        "message": message,
        "probes": results,
        "geoblock_probe": geoblock_probe,
        "checked_at": utc_now_iso(),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


def _hydrate_event(event: Dict[str, Any]) -> Dict[str, Any]:
    if event.get("markets"):
        return event
    slug = event.get("slug")
    if not slug:
        return event
    response = _fetch_json(
        f"{GAMMA_BASE_URL}/events/slug/{urllib.parse.quote(str(slug), safe='')}"
    )
    rows = _events_from_payload(response["payload"])
    return {**event, **rows[0]} if rows else event


def _probabilities(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    outcomes = _json_list(market.get("outcomes"))
    prices = _json_list(market.get("outcomePrices"))
    rows = []
    for outcome, price in zip(outcomes, prices):
        probability = _float(price)
        if probability is None:
            continue
        rows.append({"outcome": str(outcome), "probability": round(probability, 4)})
    return rows


def _reference_distance(levels: List[float], reference: Optional[float]) -> Optional[float]:
    if not levels or reference in (None, 0):
        return None
    return min(abs(level - reference) for level in levels)


def _price_relevance(levels: List[float], reference: Optional[float]) -> int:
    distance = _reference_distance(levels, reference)
    if distance is None or reference in (None, 0):
        return 0
    relative_gap = distance / abs(reference)
    return round(max(0, min(100, 100 / (1 + relative_gap * 12))))


def _market_candidate(
    event: Dict[str, Any],
    market: Dict[str, Any],
    terms: List[str],
    current_price: Optional[float],
    target_price: Optional[float],
) -> Optional[Dict[str, Any]]:
    if not _is_ongoing(market, event):
        return None
    market_question = str(market.get("question") or market.get("title") or "").strip()
    event_title = str(event.get("title") or event.get("question") or "").strip()
    description = str(market.get("description") or event.get("description") or "")
    combined = " ".join([event_title, market_question, description])
    if not market_question or not _contains_term(combined, terms):
        return None
    levels = extract_price_levels(combined)
    if not _is_price_market(combined, levels):
        return None
    probability_rows = _probabilities(market)
    if not probability_rows:
        return None
    yes_row = next(
        (row for row in probability_rows if row["outcome"].strip().lower() == "yes"),
        None,
    )
    lead = yes_row or max(probability_rows, key=lambda row: row["probability"])
    reference = target_price if target_price is not None else current_price
    distance = _reference_distance(levels, reference)
    relative_distance = distance / abs(reference) if distance is not None and reference else None
    liquidity = _float(market.get("liquidity")) or 0.0
    volume = _float(market.get("volume")) or 0.0
    score = 4.0
    score += 3.0 if levels else 1.0
    score += min(math.log10(1 + liquidity) / 2, 2.0)
    score += min(math.log10(1 + volume) / 3, 1.5)
    if relative_distance is not None:
        score += max(0.0, 5.0 - relative_distance * 20)
    end_at = _end_time(market, event)
    event_slug = event.get("slug") or market.get("eventSlug") or ""
    return {
        "market": market_question,
        "event_title": event_title,
        "event_slug": event_slug,
        "market_slug": market.get("slug") or "",
        "source_url": f"https://polymarket.com/event/{event_slug}" if event_slug else "https://polymarket.com",
        "probability": lead["probability"],
        "probability_outcome": lead["outcome"],
        "probabilities": probability_rows,
        "matched_thresholds": levels[:5],
        "target_distance": _reference_distance(levels, target_price),
        "current_distance": _reference_distance(levels, current_price),
        "reference_relative_distance": relative_distance,
        "price_relevance": _price_relevance(levels, reference),
        "end_at": end_at.replace(microsecond=0).isoformat() if end_at else None,
        "liquidity": liquidity,
        "volume": volume,
        "match_score": round(score, 3),
        "status": "open",
        "retrieved_at": utc_now_iso(),
        "source": "gamma-api.polymarket.com",
    }


def get_polymarket_context(
    symbol: str,
    asset_name: str,
    current_price: Optional[float] = None,
    target_price: Optional[float] = None,
    extra_query: str = "",
    limit: int = 5,
) -> Dict[str, Any]:
    started = time.perf_counter()
    search_terms = _search_terms(
        symbol, asset_name, current_price, target_price, extra_query
    )
    raw_events: List[Dict[str, Any]] = []
    errors: List[str] = []
    search_latency = 0
    first_events, first_latency, first_errors = _fetch_search_events(search_terms[0])
    raw_events.extend(first_events)
    search_latency += first_latency
    errors.extend(first_errors)
    active_events, active_latency, active_errors = _fetch_active_events(symbol, asset_name)
    raw_events.extend(active_events)
    search_latency += active_latency
    errors.extend(active_errors)
    initial_errors = first_errors + active_errors
    initial_access_blocked = (
        not raw_events
        and bool(first_errors)
        and bool(active_errors)
        and all(error.startswith("HTTP 451") for error in initial_errors)
    )
    if initial_access_blocked:
        provider_codes = [
            code for code in (_provider_error_code(error) for error in errors) if code
        ]
        return {
            "success": False,
            "items": [],
            "retrieval_status": "blocked",
            "kept_count": 0,
            "candidate_count": 0,
            "events_scanned": 0,
            "search_terms": search_terms,
            "search_price": round(target_price if target_price is not None else current_price)
            if (target_price is not None or current_price is not None)
            else None,
            "source": "gamma-api.polymarket.com",
            "source_url": GAMMA_BASE_URL,
            "api_configuration": {
                "base_url": GAMMA_BASE_URL,
                "authentication_required": False,
                "search_paths": ["/public-search", "/events"],
            },
            "retrieved_at": utc_now_iso(),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "network_latency_ms": search_latency,
            "error_code": "gamma_access_blocked_451",
            "error": "All official Gamma search probes returned HTTP 451 at the upstream edge before any market data could be filtered.",
            "access_blocked": True,
            "blocking_layer": "upstream_edge",
            "provider_error_codes": list(dict.fromkeys(provider_codes)),
            "diagnostics": errors[:6],
            "fallback_used": False,
        }

    remaining_terms = search_terms[1:]
    with ThreadPoolExecutor(max_workers=min(4, len(remaining_terms) or 1)) as executor:
        futures = {executor.submit(_fetch_search_events, term): term for term in remaining_terms}
        for future in as_completed(futures):
            try:
                events, latency, query_errors = future.result()
                raw_events.extend(events)
                search_latency += latency
                errors.extend(query_errors)
            except Exception as exc:
                errors.append(f"{futures[future]}: {exc}")

    deduped_events: Dict[str, Dict[str, Any]] = {}
    for event in raw_events:
        key = str(event.get("id") or event.get("slug") or event.get("title") or "")
        if key:
            deduped_events[key] = event

    events = list(deduped_events.values())[:40]
    hydrated: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(_hydrate_event, event) for event in events]
        for future in as_completed(futures):
            try:
                hydrated.append(future.result())
            except Exception as exc:
                errors.append(str(exc))

    match_terms = _asset_terms(symbol, asset_name)
    candidates: Dict[str, Dict[str, Any]] = {}
    for event in hydrated:
        for market in event.get("markets") or []:
            candidate = _market_candidate(
                event, market, match_terms, current_price, target_price
            )
            if not candidate:
                continue
            key = candidate["market_slug"] or candidate["market"]
            existing = candidates.get(key)
            if not existing or candidate["match_score"] > existing["match_score"]:
                candidates[key] = candidate

    ranked = list(candidates.values())
    if target_price is not None:
        ranked.sort(
            key=lambda item: (
                item["target_distance"] is None,
                item["target_distance"] if item["target_distance"] is not None else float("inf"),
                -item["match_score"],
            )
        )
    elif current_price is not None:
        ranked.sort(
            key=lambda item: (
                item["current_distance"] is None,
                item["current_distance"] if item["current_distance"] is not None else float("inf"),
                -item["match_score"],
            )
        )
    else:
        ranked.sort(key=lambda item: -item["match_score"])

    result_items = ranked[:limit]
    http_errors = [error for error in errors if error.startswith("HTTP ")]
    access_blocked = (
        not hydrated
        and bool(http_errors)
        and len(http_errors) == len(errors)
        and all(error.startswith("HTTP 451") for error in http_errors)
    )
    retrieval_failed = not hydrated and bool(errors)
    error_code = "gamma_access_blocked_451" if access_blocked else "gamma_retrieval_failed" if retrieval_failed else "no_matching_ongoing_price_markets"
    error_message = (
        "Gamma API returned HTTP 451 at the upstream edge before any market data could be filtered."
        if access_blocked and not hydrated
        else "Gamma retrieval failed before usable event data was available; this is not a no-match result."
        if retrieval_failed
        else "No ongoing, asset-matched price market was found"
    )
    provider_codes = [
        code for code in (_provider_error_code(error) for error in errors) if code
    ]
    return {
        "success": bool(result_items),
        "retrieval_status": "success" if result_items else "blocked" if access_blocked else "failed" if retrieval_failed else "empty",
        "items": result_items,
        "kept_count": len(result_items),
        "candidate_count": len(ranked),
        "events_scanned": len(hydrated),
        "search_terms": search_terms,
        "search_price": round(target_price if target_price is not None else current_price)
        if (target_price is not None or current_price is not None)
        else None,
        "source": "gamma-api.polymarket.com",
        "source_url": GAMMA_BASE_URL,
        "api_configuration": {
            "base_url": GAMMA_BASE_URL,
            "authentication_required": False,
            "search_paths": ["/public-search", "/events"],
        },
        "retrieved_at": utc_now_iso(),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "network_latency_ms": search_latency,
        "error_code": None if result_items else error_code,
        "error": None if result_items else error_message,
        "access_blocked": access_blocked,
        "blocking_layer": "upstream_edge" if access_blocked else None,
        "provider_error_codes": list(dict.fromkeys(provider_codes)),
        "diagnostics": errors[:6],
        "fallback_used": False,
    }
