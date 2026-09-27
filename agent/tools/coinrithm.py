"""Read CoinRithm's published aggregation dataset, not a Gamma proxy."""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .market_data import utc_now_iso
from .polymarket import (
    TAG_SLUGS, _asset_terms, _contains_term, _float, _is_price_market,
    _parse_time, _price_relevance, _reference_distance, extract_price_levels,
)


BASE_URL = "https://api.coinrithm.com/api/prediction-markets/events"
ATTRIBUTION = "Data by CoinRithm"
MAX_AGE_SECONDS = 24 * 60 * 60


def _fetch_events(term, limit=50):
    params = urllib.parse.urlencode({
        "status": "open", "source": "polymarket", "q": term, "limit": limit,
    })
    url = f"{BASE_URL}?{params}"
    request = urllib.request.Request(url, headers={
        "User-Agent": "MarketResearchAgent/1.0", "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("CoinRithm response must contain a data list")
    return payload["data"], url


def _outcome_levels(name):
    # Grouped events encode individual strikes as bare numbers or arrow labels.
    numeric = re.fullmatch(r"\s*[\u2191\u2193<>]?\s*\$?\s*(\d[\d,]*(?:\.\d+)?\s*[kKmMbB]?)\s*", name)
    return extract_price_levels("$" + numeric.group(1)) if numeric else extract_price_levels(name)


def _candidates(event, symbol, asset_name, current_price, target_price, now):
    for field in ("source", "freshness", "quality", "decisionSupport"):
        if event.get(field) is not None and not isinstance(event[field], dict):
            return [], "invalid_event_shape"
    if not isinstance(event.get("outcomes"), list):
        return [], "invalid_event_shape"
    source = event.get("source") or {}
    if not isinstance(source, dict) or source.get("id") != "polymarket":
        return [], "wrong_venue"
    end_at = _parse_time(event.get("endDate"))
    if event.get("status") != "open" or event.get("resolvedAt") or not end_at or end_at <= now:
        return [], "not_ongoing_or_missing_end_date"
    freshness = event.get("freshness") or {}
    updated = _parse_time(freshness.get("asOf"))
    if not updated or not -300 <= (now - updated).total_seconds() <= MAX_AGE_SECONDS:
        return [], "stale_or_missing_timestamp"
    quality = event.get("quality") or {}
    flags = (event.get("decisionSupport") or {}).get("flags") or {}
    if not isinstance(flags, dict):
        return [], "invalid_event_shape"
    if freshness.get("status") == "stale" or quality.get("decisionEligible") is False or flags.get("staleData") or flags.get("inactiveMarket"):
        return [], "provider_quality_rejection"
    title = str(event.get("title") or "").strip()
    if not _contains_term(title, _asset_terms(symbol, asset_name)):
        return [], "wrong_asset"
    if not _is_price_market(title, []) and not re.search(r"\b(hit|reach|trade|close)\b.*\$", title, re.IGNORECASE):
        return [], "not_price_related"
    outcomes = [row for row in event.get("outcomes") or [] if isinstance(row, dict)]
    yes = next((row for row in outcomes if str(row.get("name", "")).lower() == "yes"), None)
    if yes:
        outcomes = [yes]
    up = next((row for row in outcomes if str(row.get("name", "")).lower() == "up"), None)
    if up and "up or down" in title.lower():
        outcomes = [up]
    quotes_by_label = {}
    for row in outcomes:
        value = _float(row.get("probability"))
        if value is not None and 0 < value < 100:
            quotes_by_label.setdefault(str(row.get("name", "")).strip().lower(), set()).add(value)
    items = []
    reference = target_price if target_price is not None else current_price
    for outcome in outcomes:
        name = str(outcome.get("name") or "").strip()
        probability = _float(outcome.get("probability"))
        # The aggregation API publishes percentages, including values below 1%.
        # Its event-level open flag may still contain settled/rounded 0% or 100% legs.
        if not name or isinstance(outcome.get("probability"), bool) or probability is None or not 0 < probability < 100:
            continue
        if len(quotes_by_label.get(name.lower(), ())) > 1:
            continue
        if outcome.get("closed") or outcome.get("resolvedAt"):
            continue
        levels = _outcome_levels(name) or extract_price_levels(title)
        if not levels and name.lower() not in {"yes", "no", "up", "down"}:
            continue
        if not levels and "up or down" not in title.lower():
            continue
        if re.search(r"_{2,}", title) and levels:
            question = re.sub(r"\s*_{2,}\s*", f" {name} ", title).strip()
            question = re.sub(r"\s+([?.,])", r"\1", question)
        else:
            question = title if name.lower() in {"yes", "no"} else f"{title} [Outcome: {name}]"
        direction = -1 if "\u2193" in name or name.lower() == "down" or "below" in title.lower() else 1 if "\u2191" in name or name.lower() == "up" or "above" in title.lower() else 0
        if name.lower() == "no":
            direction *= -1
        slug = str(event.get("slug") or "")
        item_id = str(outcome.get("externalMarketId") or outcome.get("tokenId") or name)
        items.append({
            "market": question, "event_title": title, "event_slug": slug,
            "market_slug": f"{slug}:{item_id}", "status": "open",
            "probability": probability / 100, "probability_outcome": name,
            "probabilities": [{"outcome": name, "probability": probability / 100}],
            "outcome_direction": direction,
            "matched_thresholds": levels,
            "target_distance": _reference_distance(levels, target_price),
            "current_distance": _reference_distance(levels, current_price),
            "price_relevance": _price_relevance(levels, reference),
            "match_score": _price_relevance(levels, reference),
            "volume": _float(event.get("volume")) or 0,
            "liquidity": _float(event.get("liquidity")) or 0,
            "end_at": end_at.isoformat(), "data_as_of": updated.isoformat(),
            "retrieved_at": utc_now_iso(), "freshness": freshness,
            "source": ATTRIBUTION, "provider": "coinrithm", "venue": "polymarket",
            "source_url": f"{BASE_URL}/polymarket/{urllib.parse.quote(slug, safe='')}",
            "attribution_url": "https://www.coinrithm.com/en/prediction-markets/api",
            "original_market_url": event.get("externalUrl"),
            "limitation": "Third-party aggregated quote; individual outcome tradability is not verified against Gamma. Not a trading instruction.",
        })
    return items, None if items else "no_usable_outcomes"


def get_coinrithm_context(symbol, asset_name, current_price=None, target_price=None, extra_query="", limit=5):
    started = time.perf_counter()
    base = TAG_SLUGS.get(symbol.upper()) or (asset_name.split() or [symbol])[0]
    reference = target_price if target_price is not None else current_price
    terms = list(dict.fromkeys([base, symbol, *([f"{base} {round(reference)}"] if reference is not None else [])]))[:3]
    if extra_query.strip() and extra_query.strip()[:160] not in terms:
        terms.append(extra_query.strip()[:160])
    events, probes = {}, []
    for term in terms:
        try:
            rows, url = _fetch_events(term)
            probes.append({"query": term, "url": url, "http_status": 200, "event_count": len(rows), "success": True})
            for event in rows:
                if isinstance(event, dict):
                    key = event.get("id") or event.get("slug")
                    if key:
                        events[str(key)] = event
        except urllib.error.HTTPError as exc:
            probes.append({"query": term, "http_status": exc.code, "success": False, "error": str(exc)})
            # Respect this provider's access and rate limits; never switch routes.
            if exc.code in {401, 403, 429, 451}:
                break
        except Exception as exc:
            probes.append({"query": term, "http_status": None, "success": False, "error": f"{type(exc).__name__}: {exc}"})
    candidates, rejected = {}, {}
    now = datetime.now(timezone.utc)
    for event in events.values():
        rows, reason = _candidates(event, symbol, asset_name, current_price, target_price, now)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
        for row in rows:
            candidates[row["market_slug"]] = row
    distance_key = "target_distance" if target_price is not None else "current_distance"
    ranked = sorted(candidates.values(), key=lambda row: (
        row[distance_key] is None,
        row[distance_key] if row[distance_key] is not None else float("inf"),
        row["end_at"], -row["volume"], row["market_slug"],
    ))
    items = ranked[:max(1, min(limit, 10))]
    reachable = any(probe["success"] for probe in probes)
    blocked = not reachable and any(probe.get("http_status") in {401, 403, 451} for probe in probes)
    status = "success" if items else "blocked" if blocked else "empty" if reachable else "failed"
    return {
        "success": bool(items), "retrieval_status": status, "items": items,
        "kept_count": len(items), "candidate_count": len(ranked), "events_scanned": len(events),
        "search_terms": terms, "search_price": round(reference) if reference is not None else None,
        "source": ATTRIBUTION, "provider": "coinrithm", "venue": "polymarket",
        "source_url": BASE_URL, "attribution": ATTRIBUTION,
        "attribution_url": "https://www.coinrithm.com/en/prediction-markets/api",
        "retrieved_at": utc_now_iso(), "latency_ms": round((time.perf_counter() - started) * 1000),
        "api_probes": probes, "filter_rejections": rejected,
        "diagnostics": [probe["error"] for probe in probes if not probe["success"]],
        "error_code": None if items else "coinrithm_access_blocked" if blocked else "no_matching_ongoing_price_markets" if reachable else "coinrithm_retrieval_failed",
        "error": None if items else "No fresh, ongoing, asset-matched price outcomes in the aggregation dataset." if reachable else "CoinRithm request failed; no other provider was tried.",
        "access_blocked": blocked, "fallback_used": False, "gamma_called": False,
        "limitations": ["Third-party aggregated market quotes, not Gamma direct data.", "Quotes older than 24 hours and missing timestamps are excluded; 0%/100% outcome quotes are excluded.", "Event open status does not verify each outcome is currently tradable."],
    }


def get_coinrithm_status():
    started = time.perf_counter()
    probe = {"endpoint": "CoinRithm published dataset", "path": "/api/prediction-markets/events"}
    try:
        rows, _ = _fetch_events("Bitcoin", limit=1)
        probe.update(success=True, http_status=200, event_count=len(rows))
    except urllib.error.HTTPError as exc:
        probe.update(success=False, http_status=exc.code, error=str(exc))
    except Exception as exc:
        probe.update(success=False, http_status=None, error=str(exc))
    probe["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return {
        "configured": True, "configuration_source": "explicit_provider_selection",
        "provider": "coinrithm", "base_url": BASE_URL, "authentication_required": False,
        "success": probe["success"],
        "status": "reachable" if probe["success"] else "access_blocked" if probe["http_status"] in {401, 403, 451} else "unreachable",
        "message": "CoinRithm aggregation API is reachable. This does not mean Gamma direct access succeeded." if probe["success"] else "CoinRithm API unavailable; no provider switch was attempted.",
        "probes": [probe], "checked_at": utc_now_iso(), "gamma_called": False,
    }
