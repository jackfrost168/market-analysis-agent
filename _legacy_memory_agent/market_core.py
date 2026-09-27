import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


HOST = "127.0.0.1"
PORT = 8001
CACHE_TTL_SECONDS = 180
HTTP_TIMEOUT_SECONDS = 12
OLLAMA_API_URL = "http://127.0.0.1:11434/api/generate"


PRICE_FIXTURES = {
    "BTC": {"name": "Bitcoin", "type": "crypto", "price": 94150.0, "change_pct": 1.8},
    "ETH": {"name": "Ethereum", "type": "crypto", "price": 1825.0, "change_pct": 0.9},
    "SPY": {"name": "SPDR S&P 500 ETF Trust", "type": "etf", "price": 521.4, "change_pct": -0.4},
    "QQQ": {"name": "Invesco QQQ Trust", "type": "etf", "price": 445.2, "change_pct": 0.3},
    "NVDA": {"name": "NVIDIA", "type": "stock", "price": 128.6, "change_pct": 2.1},
    "TSLA": {"name": "Tesla", "type": "stock", "price": 171.9, "change_pct": -1.2},
    "AAPL": {"name": "Apple", "type": "stock", "price": 196.4, "change_pct": 0.5},
}

NEWS_FIXTURES = {
    "BTC": [
        {"headline": "ETF inflow narrative remains supportive for Bitcoin", "sentiment": "bullish", "relevance": 0.83},
        {"headline": "Macro rate uncertainty keeps risk assets sensitive", "sentiment": "bearish", "relevance": 0.75},
    ],
    "ETH": [
        {"headline": "Developers focus on scaling milestones", "sentiment": "bullish", "relevance": 0.74},
        {"headline": "Crypto risk appetite still inconsistent across majors", "sentiment": "mixed", "relevance": 0.67},
    ],
    "NVDA": [
        {"headline": "AI infrastructure demand stays strong", "sentiment": "bullish", "relevance": 0.84},
        {"headline": "Valuation concerns rise after strong run", "sentiment": "bearish", "relevance": 0.7},
    ],
    "TSLA": [
        {"headline": "EV demand questions pressure growth expectations", "sentiment": "bearish", "relevance": 0.82},
        {"headline": "Autonomy story remains a longer-term support", "sentiment": "bullish", "relevance": 0.65},
    ],
}

POLYMARKET_FIXTURES = {
    "BTC": [
        {"market": "Will Bitcoin trade above 100k this quarter?", "probability": 0.42, "relevance": 0.86, "status": "open"},
        {"market": "Will Bitcoin finish the month above 95k?", "probability": 0.58, "relevance": 0.79, "status": "open"},
    ],
    "ETH": [
        {"market": "Will ETH reclaim 2k this month?", "probability": 0.37, "relevance": 0.78, "status": "open"},
    ],
    "NVDA": [
        {"market": "Will NVIDIA make a new all-time high this quarter?", "probability": 0.46, "relevance": 0.73, "status": "open"},
    ],
    "TSLA": [
        {"market": "Will Tesla recover above 200 this month?", "probability": 0.31, "relevance": 0.75, "status": "open"},
    ],
}


CACHE = {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def now_ms() -> int:
    return int(time.time() * 1000)


def cache_get(key):
    record = CACHE.get(key)
    if not record:
        return None
    if time.time() - record["stored_at"] > CACHE_TTL_SECONDS:
        CACHE.pop(key, None)
        return None
    return record["value"]


def cache_set(key, value):
    CACHE[key] = {"stored_at": time.time(), "value": value}
    return value


def fetch_json(url: str, headers=None, method="GET", body=None):
    cache_key = ("json", method, url, body.decode("utf-8") if isinstance(body, bytes) else body)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached, True

    request = urllib.request.Request(url=url, method=method, headers=headers or {}, data=body)
    started = now_ms()
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latency_ms = max(1, now_ms() - started)
    result = {"payload": payload, "latency_ms": latency_ms}
    cache_set(cache_key, result)
    return result, False


def fetch_text(url: str, headers=None):
    cache_key = ("text", url)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached, True

    request = urllib.request.Request(url=url, headers=headers or {})
    started = now_ms()
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = response.read().decode("utf-8")
    latency_ms = max(1, now_ms() - started)
    result = {"payload": payload, "latency_ms": latency_ms}
    cache_set(cache_key, result)
    return result, False


def parse_number(raw: str):
    if not raw:
        return None
    try:
        return float(raw.replace(",", "").strip())
    except ValueError:
        return None


def normalize_symbol(raw_symbol: str, question: str) -> str:
    candidate = (raw_symbol or "").strip().upper()
    if candidate:
        return candidate
    match = re.search(r"\b(BTC|ETH|SPY|QQQ|NVDA|TSLA|AAPL)\b", question.upper())
    return match.group(1) if match else "BTC"


def resolve_asset_metadata(symbol: str, asset_name_override=None):
    data = PRICE_FIXTURES.get(symbol, {})
    default_name = asset_name_override or data.get("name", symbol)
    asset_type = data.get("type", "asset")
    quote_symbol = symbol
    if symbol == "BTC":
        quote_symbol = "BTC-USD"
    elif symbol == "ETH":
        quote_symbol = "ETH-USD"
    return {"symbol": symbol, "asset_name": default_name, "asset_type": asset_type, "quote_symbol": quote_symbol}


def resolve_user_asset(raw_asset: str, question: str):
    raw_asset = (raw_asset or "").strip()
    fallback_symbol = normalize_symbol(raw_asset, question)
    fallback_metadata = resolve_asset_metadata(fallback_symbol)
    if not raw_asset:
        return fallback_metadata

    exact = PRICE_FIXTURES.get(raw_asset.upper())
    if exact:
        return resolve_asset_metadata(raw_asset.upper())

    common_names = {
        "apple": ("AAPL", "Apple"),
        "nvidia": ("NVDA", "NVIDIA"),
        "tesla": ("TSLA", "Tesla"),
        "google": ("GOOGL", "Alphabet"),
        "alphabet": ("GOOGL", "Alphabet"),
        "bitcoin": ("BTC", "Bitcoin"),
        "ethereum": ("ETH", "Ethereum"),
    }
    lowered = raw_asset.lower()
    if lowered in common_names:
        symbol, name = common_names[lowered]
        return resolve_asset_metadata(symbol, asset_name_override=name)

    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(raw_asset)}&lang=en-US&region=US&quotesCount=6&newsCount=0"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response, _ = fetch_json(url, headers=headers)
        quotes = response["payload"].get("quotes", [])
        for quote in quotes:
            symbol = (quote.get("symbol") or "").upper()
            if not symbol:
                continue
            quote_type = (quote.get("quoteType") or "").lower()
            if quote_type not in ["equity", "etf", "cryptocurrency", "index"]:
                continue
            shortname = quote.get("shortname") or quote.get("longname") or raw_asset
            return resolve_asset_metadata(symbol, asset_name_override=shortname)
    except Exception:
        pass

    return fallback_metadata


def sentiment_from_headline(text: str):
    lowered = text.lower()
    bullish_words = ["beats", "surges", "gain", "up", "approval", "inflow", "growth", "demand", "bull"]
    bearish_words = ["drops", "down", "lawsuit", "cut", "weak", "risk", "selloff", "bear", "delay"]
    bullish_hits = sum(word in lowered for word in bullish_words)
    bearish_hits = sum(word in lowered for word in bearish_words)
    if bullish_hits > bearish_hits:
        return "bullish"
    if bearish_hits > bullish_hits:
        return "bearish"
    return "mixed"


def get_asset_name(symbol: str):
    metadata = resolve_asset_metadata(symbol)
    return {
        "success": True,
        "symbol": metadata["symbol"],
        "asset_name": metadata["asset_name"],
        "asset_type": metadata["asset_type"],
        "resolution_confidence": 0.96,
        "source": "local.symbol-map",
        "latency_ms": 5,
        "cache_hit": True,
    }


def fallback_price(symbol: str, error_code: str):
    data = PRICE_FIXTURES.get(symbol)
    if not data:
        return {
            "success": False,
            "error_code": error_code,
            "latency_ms": 0,
            "cache_hit": False,
            "source": "none",
        }
    return {
        "success": True,
        "price": data["price"],
        "change_pct": data["change_pct"],
        "timestamp": utc_now_iso(),
        "source": "fixture.market",
        "latency_ms": 0,
        "cache_hit": True,
        "fallback_used": True,
    }


def get_market_price(symbol: str):
    metadata = resolve_asset_metadata(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(metadata['quote_symbol'])}?interval=1d&range=5d"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response, cache_hit = fetch_json(url, headers=headers)
        payload = response["payload"]
        result = payload["chart"]["result"][0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None:
            return fallback_price(symbol, "price_missing")
        change_pct = 0.0
        if previous:
            change_pct = ((price - previous) / previous) * 100
        return {
            "success": True,
            "price": round(float(price), 4),
            "change_pct": round(float(change_pct), 2),
            "timestamp": utc_now_iso(),
            "source": "finance.yahoo.chart",
            "latency_ms": response["latency_ms"],
            "cache_hit": cache_hit,
            "fallback_used": False,
        }
    except Exception:
        return fallback_price(symbol, "live_price_unavailable")


def fallback_news(symbol: str, error_code: str):
    fixtures = NEWS_FIXTURES.get(symbol, [])
    items = [
        {
            "headline": item["headline"],
            "sentiment": item["sentiment"],
            "relevance": item["relevance"],
            "published_at": utc_now_iso(),
            "source": "fixture.news",
        }
        for item in fixtures
    ]
    return {
        "success": bool(items),
        "error_code": None if items else error_code,
        "items": items,
        "kept_count": len(items),
        "latency_ms": 0,
        "cache_hit": True,
        "fallback_used": bool(items),
    }


def parse_google_news_rss(xml_text: str, asset_name: str):
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    items = []
    for item in channel.findall("item")[:6]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not title:
            continue
        relevance = 0.9 if asset_name.lower() in title.lower() else 0.72
        items.append(
            {
                "headline": title,
                "sentiment": sentiment_from_headline(title),
                "relevance": round(relevance, 2),
                "published_at": pub_date or utc_now_iso(),
                "source": link or "news.google.com",
            }
        )
    return items


def get_news_context(symbol: str, asset_name: str):
    query = urllib.parse.quote(f'"{asset_name}" OR "{symbol}" when:7d')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response, cache_hit = fetch_text(url, headers=headers)
        items = parse_google_news_rss(response["payload"], asset_name)
        if not items:
            return fallback_news(symbol, "no_relevant_news")
        return {
            "success": True,
            "items": items,
            "kept_count": len(items),
            "latency_ms": response["latency_ms"],
            "cache_hit": cache_hit,
            "fallback_used": False,
            "source": "news.google.rss",
        }
    except Exception:
        return fallback_news(symbol, "news_fetch_failed")


def fallback_polymarket(symbol: str, error_code: str):
    return {
        "success": False,
        "error_code": error_code,
        "items": [],
        "kept_count": 0,
        "latency_ms": 0,
        "cache_hit": False,
        "fallback_used": False,
        "source": "none",
    }


def build_polymarket_event_queries(symbol: str, asset_name: str):
    queries = []
    seen = set()
    for term in build_asset_match_terms(symbol, asset_name):
        if len(term) < 3:
            continue
        if term in seen:
            continue
        seen.add(term)
        queries.append(term)
    if asset_name.lower() not in seen:
        queries.insert(0, asset_name.lower())
    if symbol.lower() not in seen:
        queries.insert(0, symbol.lower())
    return queries[:6]


def try_polymarket_query(symbol: str, asset_name: str):
    queries = build_polymarket_event_queries(symbol, asset_name)
    candidates = [f"https://gamma-api.polymarket.com/events?limit=100&closed=false&search={urllib.parse.quote(query)}" for query in queries]
    candidates.append("https://gamma-api.polymarket.com/events?limit=300&closed=false")
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    last_error = None
    collected = []
    seen_ids = set()
    total_latency = 0
    any_cache_hit = False
    for url in candidates:
        try:
            response, cache_hit = fetch_json(url, headers=headers)
            total_latency += response["latency_ms"]
            any_cache_hit = any_cache_hit or cache_hit
            payload = response["payload"]
            items = None
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                for key in ["data", "events"]:
                    if isinstance(payload.get(key), list):
                        items = payload[key]
                        break
            if items is None:
                continue
            for item in items:
                item_id = item.get("id") or item.get("slug") or item.get("ticker")
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                collected.append(item)
        except Exception as exc:
            last_error = exc
    if collected:
        return collected, total_latency, any_cache_hit
    raise last_error or RuntimeError("polymarket_query_failed")


def parse_iso_datetime(raw_value):
    if not raw_value:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def is_current_polymarket_item(item):
    if item.get("closed") is True:
        return False
    if item.get("archived") is True:
        return False
    end_at = (
        parse_iso_datetime(item.get("endDate"))
        or parse_iso_datetime(item.get("end_date_iso"))
        or parse_iso_datetime(item.get("closedTime"))
    )
    if end_at is not None:
        now = datetime.now(timezone.utc)
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        if end_at < now:
            return False
    return True


def parse_market_thresholds(text: str):
    if not text:
        return []
    text_lower = text.lower()
    patterns = [
        r"(?:hit|hits|reach|reaches|above|below|over|under|touch|at)\s+\$?\s*(\d+(?:\.\d+)?)\s*([kmb])",
        r"\$\s*(\d+(?:\.\d+)?)\s*([kmb]?)",
    ]
    values = []
    multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    for pattern in patterns:
        matches = re.findall(pattern, text_lower)
        for number, suffix in matches:
            try:
                value = float(number)
            except ValueError:
                continue
            if suffix:
                value *= multipliers[suffix]
            elif value < 1000:
                continue
            values.append(value)
    return values


def flatten_current_event_markets(event):
    if not is_current_polymarket_item(event):
        return []
    flattened = []
    for market in event.get("markets", []) or []:
        if not is_current_polymarket_item(market):
            continue
        merged = dict(market)
        merged["event_title"] = event.get("title") or event.get("ticker") or event.get("slug")
        merged["event_slug"] = event.get("slug")
        merged["event_end_date"] = event.get("endDate")
        flattened.append(merged)
    return flattened


def build_asset_match_terms(symbol: str, asset_name: str):
    terms = {symbol.lower(), asset_name.lower()}
    synonyms = {
        "btc": ["bitcoin"],
        "eth": ["ethereum"],
        "nvda": ["nvidia"],
        "tsla": ["tesla"],
        "aapl": ["apple"],
        "googl": ["google", "alphabet"],
        "goog": ["google", "alphabet"],
    }
    for alias in synonyms.get(symbol.lower(), []):
        terms.add(alias)
    for part in asset_name.lower().replace("&", " ").split():
        if len(part) > 2:
            terms.add(part)
    return [term for term in terms if term]


def build_price_reference_list(current_price=None, target_price=None):
    refs = []
    if isinstance(target_price, (int, float)) and target_price > 0:
        refs.append(round(float(target_price)))
    if isinstance(current_price, (int, float)) and current_price > 0:
        refs.append(round(float(current_price)))
    return refs


def compute_price_relevance(thresholds, reference_prices):
    if not thresholds or not reference_prices:
        return 0
    best = 0.0
    for threshold in thresholds:
        for ref in reference_prices:
            if ref <= 0:
                continue
            distance_ratio = abs(threshold - ref) / ref
            score = max(0.0, 1.0 - min(distance_ratio, 1.2))
            if score > best:
                best = score
    return int(round(best * 100))


def is_price_market_text(text: str):
    lowered = (text or "").lower()
    keywords = ["hit", "reach", "above", "below", "over", "under", "price", "trade at", "trading at"]
    return any(keyword in lowered for keyword in keywords)


def compute_target_distance(thresholds, target_price):
    if not thresholds or not isinstance(target_price, (int, float)) or target_price <= 0:
        return None
    best = None
    for threshold in thresholds:
        distance = abs(threshold - target_price)
        if best is None or distance < best:
            best = distance
    return best


def compute_current_distance(thresholds, current_price):
    if not thresholds or not isinstance(current_price, (int, float)) or current_price <= 0:
        return None
    best = None
    for threshold in thresholds:
        distance = abs(threshold - current_price)
        if best is None or distance < best:
            best = distance
    return best


def passes_price_distance_filter(thresholds, current_price=None, target_price=None):
    if not thresholds:
        return False
    if isinstance(target_price, (int, float)) and target_price > 0:
        for threshold in thresholds:
            if abs(threshold - target_price) / target_price <= 0.6:
                return True
        return False
    if isinstance(current_price, (int, float)) and current_price > 0:
        for threshold in thresholds:
            if abs(threshold - current_price) / current_price <= 1.0:
                return True
        return False
    return True


def normalize_polymarket_items(items, query_text, match_terms=None, current_price=None, target_price=None):
    normalized = []
    lowered = query_text.lower()
    match_terms = match_terms or [lowered]
    reference_prices = build_price_reference_list(current_price=current_price, target_price=target_price)
    for item in items:
        for market in flatten_current_event_markets(item):
            title = market.get("question") or market.get("title") or market.get("slug") or ""
            event_title = market.get("event_title") or ""
            combined_text = f"{event_title} {title}".strip()
            if not combined_text:
                continue
            combined_lower = combined_text.lower()
            score = 0.0
            matched_terms = []
            for term in match_terms:
                pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
                if re.search(pattern, combined_lower):
                    matched_terms.append(term)
            if lowered and re.search(r"(?<![a-z0-9])" + re.escape(lowered) + r"(?![a-z0-9])", combined_lower):
                score = 0.95
            elif matched_terms:
                score = 0.72 + min(0.2, 0.06 * len(set(matched_terms)))
            else:
                continue
            thresholds = parse_market_thresholds(combined_text)
            price_score = compute_price_relevance(thresholds, reference_prices)
            price_market = is_price_market_text(combined_text)
            if not price_market or not thresholds:
                continue
            if not passes_price_distance_filter(thresholds, current_price=current_price, target_price=target_price):
                continue
            if reference_prices:
                score += 0.08
            if price_score > 0:
                score = max(score, 0.68 + (price_score / 100.0) * 0.32)
            score = max(0.0, min(score, 0.99))
            target_distance = compute_target_distance(thresholds, target_price)
            current_distance = compute_current_distance(thresholds, current_price)
            probability = (
                market.get("lastTradePrice")
                or market.get("outcomePrices")
                or market.get("probability")
                or market.get("price")
            )
            if isinstance(probability, list) and probability:
                probability = probability[0]
            if isinstance(probability, str) and probability.startswith("["):
                try:
                    parsed_prices = json.loads(probability)
                    probability = parsed_prices[0] if parsed_prices else None
                except Exception:
                    probability = None
            try:
                probability = float(probability)
            except Exception:
                probability = None
            normalized.append(
                {
                    "event": event_title or title,
                    "market": title,
                    "probability": probability,
                    "relevance": round(score, 2),
                    "status": "open",
                    "end_date": market.get("endDate") or market.get("endDateIso") or market.get("event_end_date"),
                    "volume": market.get("volumeNum") or market.get("volume"),
                    "price_relevance": price_score,
                    "matched_thresholds": thresholds[:3],
                    "target_distance": target_distance,
                    "current_distance": current_distance,
                }
            )
    normalized.sort(
        key=lambda x: (
            x.get("target_distance") if x.get("target_distance") is not None else float("inf"),
            x.get("current_distance") if x.get("current_distance") is not None else float("inf"),
            -x.get("price_relevance", 0),
            -x["relevance"],
            float(x["volume"]) if x.get("volume") not in [None, ""] else 0.0,
        ),
    )
    return normalized[:5]


def normalize_polymarket_items_relaxed(items, query_text, match_terms=None, current_price=None, target_price=None):
    normalized = []
    lowered = query_text.lower()
    match_terms = match_terms or [lowered]
    reference_prices = build_price_reference_list(current_price=current_price, target_price=target_price)
    for item in items:
        for market in flatten_current_event_markets(item):
            title = market.get("question") or market.get("title") or market.get("slug") or ""
            event_title = market.get("event_title") or ""
            combined_text = f"{event_title} {title}".strip()
            if not combined_text:
                continue
            combined_lower = combined_text.lower()
            matched_terms = []
            for term in match_terms:
                pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
                if re.search(pattern, combined_lower):
                    matched_terms.append(term)
            if not matched_terms and not (lowered and re.search(r"(?<![a-z0-9])" + re.escape(lowered) + r"(?![a-z0-9])", combined_lower)):
                continue
            thresholds = parse_market_thresholds(combined_text)
            if not is_price_market_text(combined_text) or not thresholds:
                continue
            price_score = compute_price_relevance(thresholds, reference_prices)
            probability = (
                market.get("lastTradePrice")
                or market.get("outcomePrices")
                or market.get("probability")
                or market.get("price")
            )
            if isinstance(probability, list) and probability:
                probability = probability[0]
            if isinstance(probability, str) and probability.startswith("["):
                try:
                    parsed_prices = json.loads(probability)
                    probability = parsed_prices[0] if parsed_prices else None
                except Exception:
                    probability = None
            try:
                probability = float(probability)
            except Exception:
                probability = None
            normalized.append(
                {
                    "event": event_title or title,
                    "market": title,
                    "probability": probability,
                    "relevance": 0.8 + min(0.19, 0.05 * len(set(matched_terms))),
                    "status": "open",
                    "end_date": market.get("endDate") or market.get("endDateIso") or market.get("event_end_date"),
                    "volume": market.get("volumeNum") or market.get("volume"),
                    "price_relevance": price_score,
                    "matched_thresholds": thresholds[:3],
                    "target_distance": compute_target_distance(thresholds, target_price),
                    "current_distance": compute_current_distance(thresholds, current_price),
                }
            )
    normalized.sort(
        key=lambda x: (
            -x.get("price_relevance", 0),
            x.get("target_distance") if x.get("target_distance") is not None else float("inf"),
            x.get("current_distance") if x.get("current_distance") is not None else float("inf"),
            -float(x["volume"]) if x.get("volume") not in [None, ""] else 0.0,
        ),
    )
    return normalized[:5]


def get_crowd_probability(symbol: str, asset_name: str, current_price=None, target_price=None):
    match_terms = build_asset_match_terms(symbol, asset_name)
    try:
        items, latency_ms, cache_hit = try_polymarket_query(symbol, asset_name)
        normalized = normalize_polymarket_items(
            items,
            asset_name,
            match_terms=match_terms,
            current_price=current_price,
            target_price=target_price,
        )
        if not normalized:
            normalized = normalize_polymarket_items_relaxed(
                items,
                asset_name,
                match_terms=match_terms,
                current_price=current_price,
                target_price=target_price,
            )
        if not normalized:
            return fallback_polymarket(symbol, "no_related_markets")
        return {
            "success": True,
            "items": normalized,
            "kept_count": len(normalized),
            "latency_ms": latency_ms,
            "cache_hit": cache_hit,
            "fallback_used": False,
            "source": "polymarket.gamma",
        }
    except Exception:
        return fallback_polymarket(symbol, "polymarket_fetch_failed")


def infer_question_type(question: str, target_price):
    lowered = question.lower()
    if target_price is not None:
        return "target_reachability"
    if any(token in lowered for token in ["target", "reach", "hit ", "hit?", "above ", "below "]):
        return "target_reachability"
    return "directional"


def build_planner(question: str, symbol: str, target_price):
    question_type = infer_question_type(question, target_price)
    selected_tools = ["get_asset_name", "get_market_price", "get_news_context", "get_crowd_probability"]
    return {
        "question_type": question_type,
        "asset_symbol": symbol,
        "target_price": target_price,
        "time_horizon": "near_term",
        "required_evidence": ["market_price", "news_context", "crowd_probability"],
        "selected_tools": selected_tools,
        "open_questions": [
            "Are recent headlines mostly supportive or cautionary?",
            "Do crowd probabilities align with the current price setup?",
            "Is a second evidence pass needed because a source failed or the signals conflict?",
        ],
        "stop_after_this_round": False,
        "planner_reason": "Collect live price, recent news, and Polymarket-style crowd probability evidence before the reasoning step.",
    }


def safe_json_loads(raw_text: str):
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw_text[start : end + 1])
        raise


def extract_output_text(response_payload):
    if isinstance(response_payload.get("output_text"), str) and response_payload["output_text"].strip():
        return response_payload["output_text"]
    parts = []
    for item in response_payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                parts.append(text)
    return "\n".join(parts)


def build_reasoning_schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "directional_view": {"type": "string"},
            "confidence": {"type": "number"},
            "supporting_factors": {"type": "array", "items": {"type": "string"}},
            "opposing_factors": {"type": "array", "items": {"type": "string"}},
            "market_narrative": {"type": "string"},
            "reasoning_trace": {"type": "array", "items": {"type": "string"}},
            "stop_reason": {"type": "string"},
        },
        "required": [
            "directional_view",
            "confidence",
            "supporting_factors",
            "opposing_factors",
            "market_narrative",
            "reasoning_trace",
            "stop_reason",
        ],
    }


def llm_reasoning_ollama(question: str, planner, state, evidence_payload):
    model = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
    prompt = (
        "You are a market-analysis reasoning engine. Use only the supplied evidence. "
        "Be explicit about uncertainty. Never invent missing data.\n\n"
        "Return JSON only with these keys: directional_view, confidence, supporting_factors, "
        "opposing_factors, market_narrative, reasoning_trace, stop_reason.\n\n"
        f"Question: {question}\n\nEvidence:\n{json.dumps(evidence_payload)}"
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": build_reasoning_schema(),
        "options": {
            "temperature": 0.2,
        },
    }
    headers = {"Content-Type": "application/json"}
    body = json.dumps(payload).encode("utf-8")
    try:
        response, _ = fetch_json(OLLAMA_API_URL, headers=headers, method="POST", body=body)
        raw_text = response["payload"].get("response", "")
        parsed = safe_json_loads(raw_text)
        return {
            "success": True,
            "mode": "ollama_generate",
            "model": model,
            "latency_ms": response["latency_ms"],
            "result": parsed,
        }
    except Exception as exc:
        return {
            "success": False,
            "mode": "ollama_unavailable",
            "error_code": "ollama_request_failed",
            "error_detail": str(exc),
            "model": model,
            "latency_ms": 0,
        }


def llm_reasoning(question: str, planner, state):
    evidence_payload = {
        "planner": planner,
        "asset_symbol": state["asset_symbol"],
        "asset_name": state["asset_name"],
        "target_price": state["target_price"],
        "market_price": state["evidence"]["market_price"],
        "news_context": state["evidence"]["news_context"][:5],
        "crowd_probability": state["evidence"]["crowd_probability"][:5],
        "conflicts": state["conflicts"],
    }

    ollama_result = llm_reasoning_ollama(question, planner, state, evidence_payload)
    if ollama_result["success"]:
        return ollama_result

    return {
        "success": False,
        "mode": "rule_based_fallback",
        "error_code": ollama_result.get("error_code") or "no_llm_available",
        "error_detail": ollama_result.get("error_detail"),
        "model": ollama_result.get("model"),
        "latency_ms": 0,
        "providers_tried": [ollama_result.get("mode")],
    }


def build_rule_based_answer(planner, state, tools):
    bullish = []
    bearish = []
    price_info = state["evidence"]["market_price"] or {}

    if price_info.get("success"):
        change_pct = price_info.get("change_pct", 0.0)
        if change_pct > 0:
            bullish.append(f"Price is up {change_pct:.1f}% on the latest snapshot.")
        elif change_pct < 0:
            bearish.append(f"Price is down {abs(change_pct):.1f}% on the latest snapshot.")

    for item in state["evidence"]["news_context"]:
        if item["sentiment"] == "bullish":
            bullish.append(item["headline"])
        elif item["sentiment"] == "bearish":
            bearish.append(item["headline"])

    crowd_items = state["evidence"]["crowd_probability"]
    if crowd_items:
        top_market = max(crowd_items, key=lambda item: item.get("relevance", 0))
        prob = top_market.get("probability")
        if isinstance(prob, (int, float)):
            if prob >= 0.55:
                bullish.append(f"Crowd probability leans constructive: {top_market['market']} ({prob:.0%}).")
            elif prob <= 0.45:
                bearish.append(f"Crowd probability is not strongly supportive yet: {top_market['market']} ({prob:.0%}).")

    if planner["question_type"] == "target_reachability" and state["target_price"] is not None and price_info.get("success"):
        distance_pct = ((state["target_price"] - price_info["price"]) / price_info["price"]) * 100
        if distance_pct <= 3:
            directional_view = "plausible_near_term"
            conclusion = "The target is nearby and looks reachable if current momentum holds."
        elif distance_pct <= 10:
            directional_view = "possible_but_needs_confirmation"
            conclusion = "The target is plausible, but it still needs supportive news flow and follow-through."
        else:
            directional_view = "unlikely_near_term"
            conclusion = "The target is possible, but current evidence does not strongly support a near-term move that large."
        if distance_pct > 0:
            bullish.insert(0, f"The target sits about {distance_pct:.1f}% above the current price.")
        else:
            bullish.insert(0, f"The target is already below the current price by {abs(distance_pct):.1f}%.")
    else:
        if len(bullish) > len(bearish):
            directional_view = "modestly_bullish"
            conclusion = "Near-term evidence leans modestly bullish."
        elif len(bearish) > len(bullish):
            directional_view = "cautious_to_bearish"
            conclusion = "Near-term evidence leans cautious to bearish."
        else:
            directional_view = "mixed"
            conclusion = "Near-term evidence is mixed and does not justify a strong directional call."

    confidence = 0.68
    if state["conflicts"]:
        confidence -= 0.18
    if not state["evidence"]["news_context"]:
        confidence -= 0.12
    if not state["evidence"]["crowd_probability"]:
        confidence -= 0.08
    confidence = max(0.22, min(confidence, 0.9))

    runtime_ms = sum(entry["result"].get("latency_ms", 0) for entry in tools)
    narrative_parts = [
        conclusion,
        f"The agent used {len(tools)} tools in round {state['tool_round']}.",
        f"Confidence is {int(confidence * 100)}%.",
    ]
    if state["conflicts"]:
        narrative_parts.append("Conflicting evidence remained in the final state, so the conclusion stays cautious.")

    return {
        "directional_view": directional_view,
        "confidence": round(confidence, 2),
        "supporting_factors": bullish[:4],
        "opposing_factors": bearish[:4],
        "market_narrative": " ".join(narrative_parts),
        "reasoning_trace": [
            "Summarized recent price movement.",
            "Converted headline mix into bullish or bearish factors.",
            "Compared crowd probability with the rest of the evidence.",
            "Reduced confidence when evidence conflicted or a source was missing.",
        ],
        "stop_reason": state["stop_reason"],
        "runtime_ms": runtime_ms,
    }


def analyze(question: str, symbol: str, target_price, asset_name_override=None):
    planner = build_planner(question, symbol, target_price)
    state = {
        "user_question": question,
        "analysis_mode": planner["question_type"],
        "asset_symbol": symbol,
        "asset_name": asset_name_override,
        "target_price": target_price,
        "time_horizon": planner["time_horizon"],
        "tool_round": 1,
        "tool_history": [],
        "evidence": {"market_price": None, "news_context": [], "crowd_probability": []},
        "open_questions": planner["open_questions"][:],
        "conflicts": [],
        "confidence": None,
        "stop_reason": None,
        "final_answer": None,
    }

    tools = []

    asset_info = get_asset_name(symbol)
    if asset_name_override:
        asset_info["asset_name"] = asset_name_override
    state["asset_name"] = asset_info["asset_name"]
    tools.append({"tool": "get_asset_name", "result": asset_info})
    state["tool_history"].append({"tool": "get_asset_name", "success": asset_info["success"]})

    price_info = get_market_price(symbol)
    tools.append({"tool": "get_market_price", "result": price_info})
    state["tool_history"].append({"tool": "get_market_price", "success": price_info["success"]})
    if price_info["success"]:
        state["evidence"]["market_price"] = price_info

    news_info = get_news_context(symbol, state["asset_name"])
    tools.append({"tool": "get_news_context", "result": news_info})
    state["tool_history"].append({"tool": "get_news_context", "success": news_info["success"]})
    if news_info["success"]:
        state["evidence"]["news_context"] = news_info["items"]

    current_price = price_info["price"] if price_info.get("success") else None
    crowd_info = get_crowd_probability(
        symbol,
        state["asset_name"],
        current_price=current_price,
        target_price=state["target_price"],
    )
    tools.append({"tool": "get_crowd_probability", "result": crowd_info})
    state["tool_history"].append({"tool": "get_crowd_probability", "success": crowd_info["success"]})
    if crowd_info["success"]:
        state["evidence"]["crowd_probability"] = crowd_info["items"]

    if state["evidence"]["news_context"] and state["evidence"]["crowd_probability"]:
        news_mix = {item["sentiment"] for item in state["evidence"]["news_context"]}
        crowd_probs = [item.get("probability") for item in state["evidence"]["crowd_probability"] if isinstance(item.get("probability"), (int, float))]
        if "bullish" in news_mix and "bearish" in news_mix:
            state["conflicts"].append("News flow contains both bullish and bearish catalysts.")
        if crowd_probs and max(crowd_probs) < 0.55 and min(crowd_probs) <= 0.45:
            state["conflicts"].append("Polymarket probabilities are not strongly aligned with a bullish breakout.")

    second_round_attempted = False
    if (not state["evidence"]["news_context"] or not state["evidence"]["crowd_probability"]) and state["tool_round"] == 1:
        second_round_attempted = True
        state["tool_round"] = 2
        state["open_questions"].append("Second round triggered because one evidence source was missing.")
        if not state["evidence"]["news_context"]:
            news_retry = get_news_context(symbol, symbol)
            tools.append({"tool": "get_news_context_retry", "result": news_retry})
            state["tool_history"].append({"tool": "get_news_context_retry", "success": news_retry["success"]})
            if news_retry["success"]:
                state["evidence"]["news_context"] = news_retry["items"]
        if not state["evidence"]["crowd_probability"]:
            crowd_retry = get_crowd_probability(
                symbol,
                symbol,
                current_price=current_price,
                target_price=state["target_price"],
            )
            tools.append({"tool": "get_crowd_probability_retry", "result": crowd_retry})
            state["tool_history"].append({"tool": "get_crowd_probability_retry", "success": crowd_retry["success"]})
            if crowd_retry["success"]:
                state["evidence"]["crowd_probability"] = crowd_retry["items"]

    state["stop_reason"] = "Enough evidence collected after the current retrieval rounds."
    if state["conflicts"]:
        state["stop_reason"] = "Stopped with mixed signals after evidence gathering; confidence was reduced."
    elif second_round_attempted:
        state["stop_reason"] = "Stopped after a second retrieval round because additional evidence remained limited."

    llm_result = llm_reasoning(question, planner, state)
    rule_based = build_rule_based_answer(planner, state, tools)

    if llm_result["success"]:
        reasoning = llm_result["result"]
        state["confidence"] = reasoning["confidence"]
        final_answer = {
            "asset": state["asset_name"] or symbol,
            "symbol": symbol,
            "analysis_mode": state["analysis_mode"],
            "confidence": reasoning["confidence"],
            "supporting_factors": reasoning["supporting_factors"][:4],
            "opposing_factors": reasoning["opposing_factors"][:4],
            "narrative": reasoning["market_narrative"],
            "runtime_ms": rule_based["runtime_ms"] + llm_result["latency_ms"],
            "workflow_steps": [],
            "logic_summary": [],
            "reasoning_trace": reasoning["reasoning_trace"],
            "llm_mode": llm_result["mode"],
            "llm_model": llm_result["model"],
        }
        state["stop_reason"] = reasoning.get("stop_reason") or state["stop_reason"]
    else:
        state["confidence"] = rule_based["confidence"]
        final_answer = {
            "asset": state["asset_name"] or symbol,
            "symbol": symbol,
            "analysis_mode": state["analysis_mode"],
            "confidence": rule_based["confidence"],
            "supporting_factors": rule_based["supporting_factors"],
            "opposing_factors": rule_based["opposing_factors"],
            "narrative": rule_based["market_narrative"],
            "runtime_ms": rule_based["runtime_ms"],
            "workflow_steps": [],
            "logic_summary": [],
            "reasoning_trace": rule_based["reasoning_trace"],
            "llm_mode": llm_result["mode"],
            "llm_model": llm_result.get("model"),
            "llm_error_code": llm_result.get("error_code"),
        }

    workflow_steps = [
        f"Parsed the user question and normalized the asset symbol as {symbol}.",
        f"Classified the task as {planner['question_type']}.",
        "Built a plan requesting asset name, live market price, recent news context, and Polymarket evidence.",
        "Executed the selected tools and stored their outputs in agent state.",
    ]
    if second_round_attempted:
        workflow_steps.append("Triggered a second evidence round because at least one source was missing after the first pass.")
    workflow_steps.append("Checked the collected evidence for conflicts, missing coverage, and confidence implications.")
    if llm_result["success"]:
        if llm_result["mode"] == "ollama_generate":
            workflow_steps.append(f"Sent the evidence bundle to local Ollama model {llm_result['model']} for final reasoning.")
    else:
        workflow_steps.append("Skipped live LLM reasoning and used the local fallback synthesis because Ollama was unavailable.")
    workflow_steps.append(f"Stopped after round {state['tool_round']} because: {state['stop_reason']}")

    live_data_used = []
    for tool in tools:
        result = tool["result"]
        if result.get("success") and not result.get("fallback_used", False):
            live_data_used.append(tool["tool"])

    logic_summary = [
        "Question -> planner -> live tool calls -> evidence state -> conflict check -> optional second round -> LLM reasoning -> final answer",
        "Market prices are fetched from Yahoo Finance chart data when available, otherwise the app falls back to fixture data.",
        "News context is fetched from Google News RSS when available, otherwise the app falls back to fixture headlines.",
        "Polymarket context is fetched from public Gamma endpoints when available, otherwise the app falls back to fixture markets.",
        "The final narrative uses local Ollama reasoning when available and falls back to rule-based synthesis if Ollama is unavailable.",
    ]

    final_answer["workflow_steps"] = workflow_steps
    final_answer["logic_summary"] = logic_summary
    final_answer["live_data_used"] = live_data_used
    final_answer["reasoning_trace"] = final_answer["reasoning_trace"][:6]
    final_answer["filtered_news"] = [
        f"{item['headline']} (sentiment: {item['sentiment']}, relevance: {item['relevance']})"
        for item in state["evidence"]["news_context"][:5]
    ]
    final_answer["filtered_polymarket"] = [
        (
            f"{item['market']} "
            f"(probability: {item['probability']:.0%}, relevance: {item['relevance']}, price match: {item.get('price_relevance', 0)}%)"
            if isinstance(item.get("probability"), (int, float))
            else f"{item['market']} (relevance: {item['relevance']}, price match: {item.get('price_relevance', 0)}%)"
        )
        for item in state["evidence"]["crowd_probability"][:5]
    ]
    final_answer["llm_display"] = final_answer["llm_model"] or "rule-based fallback"
    final_answer["current_price_display"] = None
    if state["evidence"]["market_price"] and state["evidence"]["market_price"].get("success"):
        price_value = state["evidence"]["market_price"]["price"]
        final_answer["current_price_display"] = f"{price_value:,.2f}"

    state["final_answer"] = final_answer
    return {"planner": planner, "state": state, "tools": tools, "llm_result": llm_result}


def build_price_preview(raw_asset: str, question: str):
    asset_metadata = resolve_user_asset(raw_asset, question)
    price_info = get_market_price(asset_metadata["symbol"])
    return {
        "asset_input": raw_asset,
        "symbol": asset_metadata["symbol"],
        "asset_name": asset_metadata["asset_name"],
        "price": price_info.get("price"),
        "price_display": f"{price_info['price']:,.2f}" if price_info.get("success") and isinstance(price_info.get("price"), (int, float)) else None,
        "change_pct": price_info.get("change_pct"),
        "success": bool(price_info.get("success")),
        "source": price_info.get("source"),
    }


def render_page(result=None, form_values=None):
    form_values = form_values or {}
    asset_input = html.escape(form_values.get("asset_input", "BTC"))
    symbol = html.escape(form_values.get("symbol", "BTC"))
    question = html.escape(form_values.get("question", "Is BTC likely to move up or down in the near term?"))
    target_price = html.escape(form_values.get("target_price", ""))

    result_html = ""
    if result:
        final_answer = result["state"]["final_answer"]
        planner_json = html.escape(json.dumps(result["planner"], indent=2))
        state_json = html.escape(json.dumps(result["state"], indent=2))
        llm_json = html.escape(json.dumps(result["llm_result"], indent=2))

        tool_cards = []
        for tool in result["tools"]:
            payload = html.escape(json.dumps(tool["result"], indent=2))
            tool_cards.append(
                f"""
                <article class="card tool-card">
                  <h4>{html.escape(tool['tool'])}</h4>
                  <pre>{payload}</pre>
                </article>
                """
            )

        support_items = "".join(f"<li>{html.escape(item)}</li>" for item in final_answer["supporting_factors"]) or "<li>No strong supporting factors collected.</li>"
        oppose_items = "".join(f"<li>{html.escape(item)}</li>" for item in final_answer["opposing_factors"]) or "<li>No major opposing factors collected.</li>"
        conflicts = "".join(f"<li>{html.escape(item)}</li>" for item in result["state"]["conflicts"]) or "<li>No major conflicts detected.</li>"
        workflow_items = "".join(f"<li>{html.escape(item)}</li>" for item in final_answer["workflow_steps"])
        logic_items = "".join(f"<li>{html.escape(item)}</li>" for item in final_answer["logic_summary"])
        trace_items = "".join(f"<li>{html.escape(item)}</li>" for item in final_answer["reasoning_trace"])
        live_tool_items = "".join(f"<li>{html.escape(item)}</li>" for item in final_answer["live_data_used"]) or "<li>No live source succeeded; fallback data was used.</li>"
        filtered_news_items = "".join(f"<li>{html.escape(item)}</li>" for item in final_answer["filtered_news"]) or "<li>No news items remained after filtering.</li>"
        filtered_market_items = "".join(f"<li>{html.escape(item)}</li>" for item in final_answer["filtered_polymarket"]) or "<li>No Polymarket items remained after filtering.</li>"

        result_html = f"""
        <section class="results">
          <div class="hero-grid">
            <article class="card summary-card">
              <p class="eyebrow">Final View</p>
              <h2>{html.escape(final_answer['asset'])} ({html.escape(final_answer['symbol'])})</h2>
              <p class="lead">{html.escape(final_answer['narrative'])}</p>
              <div class="pill-row">
                <span class="pill">{html.escape(final_answer['analysis_mode'])}</span>
                <span class="pill">price {html.escape(final_answer['current_price_display'] or 'n/a')}</span>
                <span class="pill">confidence {int(float(final_answer['confidence']) * 100)}%</span>
                <span class="pill">runtime {final_answer['runtime_ms']} ms</span>
                <span class="pill">{html.escape(final_answer['llm_mode'])}</span>
                <span class="pill">{html.escape(final_answer['llm_display'])}</span>
              </div>
            </article>
            <article class="card">
              <p class="eyebrow">Current Price</p>
              <p><strong>{html.escape(final_answer['current_price_display'] or 'n/a')}</strong></p>
              <p class="eyebrow">LLM Used</p>
              <p><strong>{html.escape(final_answer['llm_display'])}</strong></p>
              <p class="eyebrow">Reasoning Mode</p>
              <p>{html.escape(final_answer['llm_mode'])}</p>
              <p class="eyebrow">Stop Reason</p>
              <p>{html.escape(result['state']['stop_reason'])}</p>
              <p class="eyebrow">Conflicts</p>
              <ul>{conflicts}</ul>
            </article>
          </div>

          <div class="grid two-up">
            <article class="card">
              <h3>Supporting Factors</h3>
              <ul>{support_items}</ul>
            </article>
            <article class="card">
              <h3>Opposing Factors</h3>
              <ul>{oppose_items}</ul>
            </article>
          </div>

          <div class="grid two-up">
            <article class="card">
              <h3>Current Workflow</h3>
              <ul>{workflow_items}</ul>
            </article>
            <article class="card">
              <h3>Current Logic</h3>
              <ul>{logic_items}</ul>
            </article>
          </div>

          <div class="grid two-up">
            <article class="card">
              <h3>Reasoning Trace</h3>
              <ul>{trace_items}</ul>
            </article>
            <article class="card">
              <h3>Live Sources Used</h3>
              <ul>{live_tool_items}</ul>
            </article>
          </div>

          <div class="grid two-up">
            <article class="card">
              <h3>News Left After Filtering</h3>
              <ul>{filtered_news_items}</ul>
            </article>
            <article class="card">
              <h3>Polymarket Left After Filtering</h3>
              <ul>{filtered_market_items}</ul>
            </article>
          </div>

          <div class="grid two-up">
            <article class="card">
              <h3>Planner Output</h3>
              <pre>{planner_json}</pre>
            </article>
            <article class="card">
              <h3>Agent State</h3>
              <pre>{state_json}</pre>
            </article>
          </div>

          <div class="grid two-up">
            <article class="card">
              <h3>LLM Result</h3>
              <pre>{llm_json}</pre>
            </article>
            <article class="card">
              <h3>How To Make It Fully Live</h3>
              <ul>
                <li>Start Ollama locally and set <code>OLLAMA_MODEL</code> if you want a specific model.</li>
                <li>The app already tries live market, news, and Polymarket retrieval first.</li>
              </ul>
            </article>
          </div>

          <section class="tool-section">
            <h3>Tool Trace</h3>
            <div class="grid two-up">
              {''.join(tool_cards)}
            </div>
          </section>
        </section>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Market Analysis Agent</title>
  <style>
    :root {{
      --bg: #f3efe4;
      --panel: rgba(255, 250, 241, 0.92);
      --ink: #1d2433;
      --muted: #566074;
      --accent: #0f766e;
      --line: rgba(29, 36, 51, 0.12);
      --shadow: 0 18px 45px rgba(43, 54, 78, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(217, 119, 6, 0.18), transparent 24%),
        linear-gradient(180deg, #f8f4ea 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .shell {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    .masthead {{
      display: grid;
      gap: 18px;
      margin-bottom: 24px;
    }}
    .masthead h1 {{
      margin: 0;
      font-size: clamp(2.4rem, 5vw, 4.4rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
      max-width: 11ch;
    }}
    .masthead p {{
      margin: 0;
      max-width: 68ch;
      color: var(--muted);
      font-size: 1.05rem;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      padding: 22px;
      backdrop-filter: blur(10px);
    }}
    form.card {{
      padding: 24px;
    }}
    .grid {{
      display: grid;
      gap: 18px;
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: 1.3fr 0.7fr;
      gap: 18px;
      margin-bottom: 18px;
    }}
    .two-up {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-bottom: 18px;
    }}
    .form-grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr 0.9fr;
      gap: 16px;
    }}
    label {{
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    input, textarea {{
      width: 100%;
      border: 1px solid rgba(29, 36, 51, 0.16);
      background: rgba(255, 255, 255, 0.78);
      border-radius: 16px;
      padding: 14px 16px;
      font: inherit;
      color: var(--ink);
    }}
    .preview-box {{
      width: 100%;
      min-height: 52px;
      display: flex;
      align-items: center;
      border: 1px solid rgba(29, 36, 51, 0.16);
      background: rgba(255, 255, 255, 0.78);
      border-radius: 16px;
      padding: 14px 16px;
      color: var(--ink);
    }}
    .preview-box.muted {{
      color: var(--muted);
    }}
    textarea {{
      min-height: 132px;
      resize: vertical;
      grid-column: 1 / -1;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      background: linear-gradient(135deg, var(--accent), #155e75);
      color: white;
      font: inherit;
      padding: 14px 22px;
      cursor: pointer;
      justify-self: start;
    }}
    code {{
      font-family: Consolas, "Courier New", monospace;
    }}
    .eyebrow {{
      margin: 0 0 10px;
      color: var(--accent);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.78rem;
    }}
    .lead {{
      font-size: 1.05rem;
      color: var(--muted);
    }}
    .pill-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}
    .quick-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 6px;
    }}
    .pill {{
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(15, 118, 110, 0.1);
      color: #0b5f58;
      font-size: 0.92rem;
    }}
    .quick-button {{
      border: 1px solid rgba(15, 118, 110, 0.18);
      background: rgba(255, 255, 255, 0.7);
      color: var(--ink);
      padding: 10px 14px;
    }}
    .quick-button:hover {{
      background: rgba(15, 118, 110, 0.08);
    }}
    h2, h3, h4 {{
      margin-top: 0;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: Consolas, "Courier New", monospace;
      font-size: 0.86rem;
      color: #243041;
      background: rgba(29, 36, 51, 0.04);
      border-radius: 16px;
      padding: 14px;
    }}
    .tool-section {{
      margin-top: 8px;
    }}
    .summary-card {{
      position: relative;
      overflow: hidden;
    }}
    .summary-card::after {{
      content: "";
      position: absolute;
      inset: auto -40px -50px auto;
      width: 180px;
      height: 180px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(217, 119, 6, 0.18), transparent 65%);
    }}
    .footer-note {{
      margin-top: 16px;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    button:disabled {{
      opacity: 0.72;
      cursor: wait;
    }}
    .loading-overlay {{
      position: fixed;
      inset: 0;
      background: rgba(29, 36, 51, 0.32);
      display: grid;
      place-items: center;
      z-index: 30;
    }}
    .loading-overlay[hidden] {{
      display: none !important;
    }}
    .loading-card {{
      background: #fffaf1;
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
      padding: 22px 26px;
      min-width: 300px;
      text-align: center;
    }}
    .spinner {{
      width: 36px;
      height: 36px;
      border-radius: 50%;
      border: 4px solid rgba(15, 118, 110, 0.18);
      border-top-color: var(--accent);
      margin: 0 auto 14px;
      animation: spin 0.8s linear infinite;
    }}
    @keyframes spin {{
      to {{
        transform: rotate(360deg);
      }}
    }}
    @media (max-width: 860px) {{
      .hero-grid, .two-up, .form-grid {{
        grid-template-columns: 1fr;
      }}
      .shell {{
        width: min(100% - 20px, 1120px);
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="masthead">
      <p class="eyebrow">Autonomous Analysis Prototype</p>
      <h1>Market analysis that now tries live data first.</h1>
      <p>This local web app now attempts real market prices, recent news, Polymarket-related context, and a local Ollama reasoning step. When a live source or Ollama is unavailable, it falls back gracefully and shows exactly what happened.</p>
    </section>

    <form class="card grid" method="post" action="/analyze" id="analysis-form">
      <div class="form-grid">
        <label>
          Company Name Or Stock Symbol
          <input type="text" id="asset-input" name="asset_input" value="{asset_input}" placeholder="Apple, AAPL, NVIDIA, BTC" required>
        </label>
        <label>
          Current Price
          <div class="preview-box muted" id="price-preview-text">Enter a company name or symbol.</div>
        </label>
        <label>
          Target Price
          <input type="text" name="target_price" value="{target_price}" placeholder="Optional">
        </label>
        <label>
          Market Question
          <textarea name="question" id="question-input" placeholder="Is Bitcoin likely to move up or down in the near term?">{question}</textarea>
        </label>
      </div>
      <div class="quick-row">
        <button type="button" class="quick-button" data-asset="Bitcoin">Bitcoin</button>
        <button type="button" class="quick-button" data-asset="Apple">Apple</button>
        <button type="button" class="quick-button" data-asset="Google">Google</button>
        <button type="button" class="quick-button" data-asset="Tesla">Tesla</button>
      </div>
      <button type="submit" id="analyze-button">Run Agent Analysis</button>
      <p class="footer-note">Runs on port 8001. The reasoning step uses local Ollama and falls back to rule-based synthesis only if Ollama is unavailable. The page shows which path was used.</p>
    </form>

    {result_html}
  </main>
  <div class="loading-overlay" id="loading-overlay" hidden>
    <div class="loading-card">
      <div class="spinner" aria-hidden="true"></div>
      <p>Analyzing live market, news, and Polymarket evidence...</p>
    </div>
  </div>
  <script>
    const form = document.getElementById("analysis-form");
    const button = document.getElementById("analyze-button");
    const overlay = document.getElementById("loading-overlay");
    const assetInput = document.getElementById("asset-input");
    const questionInput = document.getElementById("question-input");
    const pricePreviewText = document.getElementById("price-preview-text");
    const quickButtons = document.querySelectorAll(".quick-button");
    let questionWasEdited = false;
    let previewTimer = null;
    const buildQuestion = (asset) => {{
      const value = (asset || "").trim();
      if (!value) return "";
      return `Is ${{value}} likely to move up or down in the near term?`;
    }};
    const updatePricePreview = async () => {{
      if (!assetInput || !pricePreviewText) return;
      const asset = assetInput.value.trim();
      if (!asset) {{
        pricePreviewText.textContent = "Enter a company name or symbol.";
        pricePreviewText.classList.add("muted");
        return;
      }}
      pricePreviewText.textContent = "Fetching current price...";
      pricePreviewText.classList.add("muted");
      try {{
        const response = await fetch(`/price-preview?asset=${{encodeURIComponent(asset)}}`);
        const data = await response.json();
        if (data.success && data.price_display) {{
          const sign = typeof data.change_pct === "number" && data.change_pct >= 0 ? "+" : "";
          const change = typeof data.change_pct === "number" ? ` (${{sign}}${{data.change_pct.toFixed(2)}}%)` : "";
          pricePreviewText.textContent = `${{data.asset_name}} (${{data.symbol}}): $${{data.price_display}}${{change}}`;
          pricePreviewText.classList.remove("muted");
        }} else {{
          pricePreviewText.textContent = "Current price could not be fetched for that input.";
          pricePreviewText.classList.add("muted");
        }}
      }} catch (_error) {{
        pricePreviewText.textContent = "Current price could not be fetched for that input.";
        pricePreviewText.classList.add("muted");
      }}
    }};
    const syncButtonState = () => {{
      if (!button || !assetInput) return;
      button.disabled = !assetInput.value.trim();
    }};
    if (assetInput) {{
      assetInput.addEventListener("input", () => {{
        syncButtonState();
        if (!questionInput) return;
        const currentQuestion = questionInput.value.trim();
        if (!questionWasEdited || !currentQuestion) {{
          questionInput.value = buildQuestion(assetInput.value);
        }}
        clearTimeout(previewTimer);
        previewTimer = setTimeout(updatePricePreview, 250);
      }});
    }}
    if (questionInput) {{
      questionInput.addEventListener("input", () => {{
        const expected = buildQuestion(assetInput ? assetInput.value : "");
        questionWasEdited = questionInput.value.trim() !== "" && questionInput.value.trim() !== expected;
      }});
    }}
    quickButtons.forEach((quickButton) => {{
      quickButton.addEventListener("click", () => {{
        if (!assetInput) return;
        assetInput.value = quickButton.dataset.asset || "";
        if (questionInput && !questionWasEdited) {{
          questionInput.value = buildQuestion(assetInput.value);
        }}
        assetInput.focus();
        syncButtonState();
        clearTimeout(previewTimer);
        previewTimer = setTimeout(updatePricePreview, 50);
      }});
    }});
    if (form && button && overlay && assetInput) {{
      form.addEventListener("submit", () => {{
        if (!assetInput.value.trim()) {{
          button.disabled = false;
          return;
        }}
        button.disabled = true;
        button.textContent = "Analyzing...";
        overlay.hidden = false;
      }});
    }}
    if (assetInput && questionInput && !questionInput.value.trim()) {{
      questionInput.value = buildQuestion(assetInput.value);
    }}
    if (assetInput && assetInput.value.trim()) {{
      updatePricePreview();
    }}
    syncButtonState();
  </script>
</body>
</html>
"""


class MarketAnalysisHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str, status: int = 200):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/price-preview"):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            raw_asset = params.get("asset", [""])[0].strip()
            preview = build_price_preview(raw_asset, "")
            self._send_json(preview)
            return
        if self.path not in ["/", "/index.html"]:
            self._send_html(render_page(), status=404)
            return
        self._send_html(render_page())

    def do_POST(self):
        if self.path != "/analyze":
            self._send_html(render_page(), status=404)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        form = parse_qs(raw_body)

        question = form.get("question", [""])[0].strip() or "Is BTC likely to move up or down in the near term?"
        asset_input = form.get("asset_input", [""])[0].strip()
        asset_metadata = resolve_user_asset(asset_input, question)
        symbol = asset_metadata["symbol"]
        target_price_raw = form.get("target_price", [""])[0]
        target_price = parse_number(target_price_raw)

        result = analyze(question, symbol, target_price, asset_name_override=asset_metadata["asset_name"])
        form_values = {
            "asset_input": asset_input or asset_metadata["asset_name"] or symbol,
            "symbol": symbol,
            "question": question,
            "target_price": target_price_raw,
        }
        self._send_html(render_page(result=result, form_values=form_values))

    def log_message(self, fmt, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), MarketAnalysisHandler)
    print(f"Market Analysis Agent running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
