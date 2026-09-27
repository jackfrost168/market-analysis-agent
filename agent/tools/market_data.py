import html
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


HTTP_TIMEOUT_SECONDS = 15
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Stateful-Market-Agent/1.0 your-email@example.com"
)
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


COMMON_ASSETS = {
    "apple": ("AAPL", "Apple Inc.", "stock"),
    "aapl": ("AAPL", "Apple Inc.", "stock"),
    "google": ("GOOGL", "Alphabet Inc.", "stock"),
    "alphabet": ("GOOGL", "Alphabet Inc.", "stock"),
    "googl": ("GOOGL", "Alphabet Inc.", "stock"),
    "goog": ("GOOG", "Alphabet Inc.", "stock"),
    "tesla": ("TSLA", "Tesla, Inc.", "stock"),
    "tsla": ("TSLA", "Tesla, Inc.", "stock"),
    "nvidia": ("NVDA", "NVIDIA Corporation", "stock"),
    "nvda": ("NVDA", "NVIDIA Corporation", "stock"),
    "bitcoin": ("BTC", "Bitcoin", "crypto"),
    "btc": ("BTC", "Bitcoin", "crypto"),
    "btc-usd": ("BTC", "Bitcoin", "crypto"),
    "ethereum": ("ETH", "Ethereum", "crypto"),
    "eth": ("ETH", "Ethereum", "crypto"),
    "eth-usd": ("ETH", "Ethereum", "crypto"),
    "spy": ("SPY", "SPDR S&P 500 ETF Trust", "etf"),
    "qqq": ("QQQ", "Invesco QQQ Trust", "etf"),
}


_CACHE: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _cache_get(key: Tuple[Any, ...]) -> Any:
    with _CACHE_LOCK:
        row = _CACHE.get(key)
        if not row:
            return None
        expires_at, value = row
        if expires_at <= time.monotonic():
            _CACHE.pop(key, None)
            return None
        return value


def _cache_set(key: Tuple[Any, ...], value: Any, ttl: int) -> Any:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + ttl, value)
    return value


def _fetch_json(url: str, headers: Optional[Dict[str, str]] = None, ttl: int = 0):
    key = ("json", url)
    cached = _cache_get(key) if ttl else None
    if cached is not None:
        return cached, True
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = {
        "payload": payload,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
    if ttl:
        _cache_set(key, result, ttl)
    return result, False


def _fetch_text(url: str, headers: Optional[Dict[str, str]] = None, ttl: int = 0):
    key = ("text", url)
    cached = _cache_get(key) if ttl else None
    if cached is not None:
        return cached, True
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = response.read().decode("utf-8", errors="replace")
    result = {
        "payload": payload,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
    if ttl:
        _cache_set(key, result, ttl)
    return result, False


def _quote_symbol(symbol: str) -> str:
    return {"BTC": "BTC-USD", "ETH": "ETH-USD"}.get(symbol, symbol)


def _asset_type(quote_type: str, symbol: str) -> str:
    if symbol in {"BTC", "ETH"}:
        return "crypto"
    return {
        "equity": "stock",
        "etf": "etf",
        "cryptocurrency": "crypto",
        "index": "index",
    }.get((quote_type or "").lower(), "stock")


def resolve_asset(raw_asset: str, query: str = "") -> Dict[str, Any]:
    raw = (raw_asset or "").strip()
    lowered = raw.lower()
    if lowered in COMMON_ASSETS:
        symbol, name, asset_type = COMMON_ASSETS[lowered]
        return {
            "symbol": symbol,
            "asset_name": name,
            "asset_type": asset_type,
            "quote_symbol": _quote_symbol(symbol),
            "source": "built_in_mapping",
        }

    candidate = raw or query
    if not candidate:
        candidate = "Bitcoin"
    url = (
        f"{YAHOO_SEARCH_URL}?q={urllib.parse.quote(candidate)}&lang=en-US"
        "&region=US&quotesCount=8&newsCount=0"
    )
    try:
        response, _ = _fetch_json(url, ttl=900)
        for quote in response["payload"].get("quotes", []):
            quote_type = quote.get("quoteType") or ""
            if quote_type.lower() not in {"equity", "etf", "cryptocurrency", "index"}:
                continue
            yahoo_symbol = str(quote.get("symbol") or "").upper()
            if not yahoo_symbol:
                continue
            symbol = yahoo_symbol.removesuffix("-USD") if yahoo_symbol.endswith("-USD") else yahoo_symbol
            return {
                "symbol": symbol,
                "asset_name": quote.get("longname") or quote.get("shortname") or raw or symbol,
                "asset_type": _asset_type(quote_type, symbol),
                "quote_symbol": yahoo_symbol,
                "source": "finance.yahoo.search",
            }
    except Exception:
        pass

    ticker = re.sub(r"[^A-Za-z0-9.^=-]", "", raw).upper() or "BTC"
    symbol = ticker.removesuffix("-USD") if ticker.endswith("-USD") else ticker
    return {
        "symbol": symbol,
        "asset_name": raw or symbol,
        "asset_type": "crypto" if symbol in {"BTC", "ETH"} else "stock",
        "quote_symbol": _quote_symbol(symbol),
        "source": "input_fallback",
    }


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _percent_change(current: Optional[float], previous: Optional[float]):
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 3)


def get_price_snapshot(asset: Dict[str, Any]) -> Dict[str, Any]:
    quote_symbol = asset["quote_symbol"]
    encoded = urllib.parse.quote(quote_symbol, safe="")
    url = f"{YAHOO_CHART_URL.format(symbol=encoded)}?interval=1d&range=1mo"
    try:
        response, cache_hit = _fetch_json(url, ttl=15)
        result = (response["payload"].get("chart", {}).get("result") or [])[0]
        meta = result.get("meta") or {}
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        timestamps = result.get("timestamp") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        sessions = []
        for index, close in enumerate(closes):
            value = _safe_float(close)
            if value is None:
                continue
            sessions.append(
                {
                    "timestamp": timestamps[index] if index < len(timestamps) else None,
                    "close": value,
                    "volume": _safe_float(volumes[index]) if index < len(volumes) else None,
                }
            )
        price = _safe_float(meta.get("regularMarketPrice"))
        if price is None and sessions:
            price = sessions[-1]["close"]
        if price is None:
            raise ValueError("Yahoo response did not contain a usable price")
        market_timestamp = _safe_float(meta.get("regularMarketTime"))
        latest_bar_is_current = False
        if sessions:
            latest_timestamp = _safe_float(sessions[-1].get("timestamp"))
            if latest_timestamp is not None and market_timestamp is not None:
                latest_bar_is_current = abs(market_timestamp - latest_timestamp) <= 36 * 3600
            if not latest_bar_is_current:
                tolerance = max(abs(price), 1.0) * 0.0005
                latest_bar_is_current = abs(sessions[-1]["close"] - price) <= tolerance

        day_index = -2 if latest_bar_is_current else -1
        week_index = -6 if latest_bar_is_current else -5
        previous_close = (
            sessions[day_index]["close"] if len(sessions) >= abs(day_index) else None
        )
        week_close = (
            sessions[week_index]["close"] if len(sessions) >= abs(week_index) else None
        )
        completed_sessions = sessions[:-1] if latest_bar_is_current else sessions
        recent_volumes = [row["volume"] for row in completed_sessions[-5:] if row["volume"]]
        regular_volume = _safe_float(meta.get("regularMarketVolume"))
        average_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else None
        timestamp = (
            datetime.fromtimestamp(market_timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()
            if market_timestamp
            else utc_now_iso()
        )
        return {
            "success": True,
            "symbol": asset["symbol"],
            "quote_symbol": quote_symbol,
            "asset_name": asset["asset_name"],
            "price": round(price, 6),
            "currency": meta.get("currency") or "USD",
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
            "market_state": meta.get("marketState") or "",
            "return_1d_pct": _percent_change(price, previous_close),
            "return_1w_pct": _percent_change(price, week_close),
            "volume": int(regular_volume) if regular_volume is not None else None,
            "average_volume_5d": round(average_volume) if average_volume else None,
            "volume_vs_5d_avg_pct": _percent_change(regular_volume, average_volume),
            "timestamp": timestamp,
            "retrieved_at": utc_now_iso(),
            "source": "finance.yahoo.chart",
            "source_url": url,
            "latency_ms": response["latency_ms"],
            "cache_hit": cache_hit,
            "fallback_used": False,
        }
    except Exception as exc:
        return {
            "success": False,
            "symbol": asset["symbol"],
            "source": "finance.yahoo.chart",
            "source_url": url,
            "retrieved_at": utc_now_iso(),
            "error": str(exc),
            "fallback_used": False,
        }


def get_price_history(
    asset: Dict[str, Any], range_value: str = "6mo", interval: str = "1d"
) -> Dict[str, Any]:
    encoded = urllib.parse.quote(asset["quote_symbol"], safe="")
    url = (
        f"{YAHOO_CHART_URL.format(symbol=encoded)}?interval={urllib.parse.quote(interval)}"
        f"&range={urllib.parse.quote(range_value)}"
    )
    try:
        response, cache_hit = _fetch_json(url, ttl=120)
        result = (response["payload"].get("chart", {}).get("result") or [])[0]
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        points = []
        for timestamp, close in zip(timestamps, closes):
            value = _safe_float(close)
            if timestamp and value is not None:
                points.append({"time": int(timestamp), "price": value})
        if len(points) < 2:
            raise ValueError("Yahoo returned fewer than two history points")
        return {
            "success": True,
            "symbol": asset["symbol"],
            "points": points,
            "range": range_value,
            "interval": interval,
            "source": "finance.yahoo.chart",
            "source_url": url,
            "retrieved_at": utc_now_iso(),
            "latency_ms": response["latency_ms"],
            "cache_hit": cache_hit,
        }
    except Exception as exc:
        return {
            "success": False,
            "symbol": asset["symbol"],
            "points": [],
            "source": "finance.yahoo.chart",
            "source_url": url,
            "retrieved_at": utc_now_iso(),
            "error": str(exc),
        }


def _plain_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _normalize_news_date(raw_date: str) -> str:
    try:
        parsed = parsedate_to_datetime(raw_date)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except Exception:
        return utc_now_iso()


def search_news(query: str, asset: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    encoded = urllib.parse.quote_plus(f"{query} when:14d")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        response, cache_hit = _fetch_text(url, ttl=120)
        root = ET.fromstring(response["payload"])
        items = []
        seen = set()
        for node in root.findall("./channel/item"):
            title = html.unescape((node.findtext("title") or "").strip())
            if not title:
                continue
            key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            source_node = node.find("source")
            source_name = (
                source_node.text.strip()
                if source_node is not None and source_node.text
                else "Google News"
            )
            items.append(
                {
                    "headline": title,
                    "summary": _plain_text(node.findtext("description") or ""),
                    "source_name": source_name,
                    "source_url": (node.findtext("link") or "").strip(),
                    "published_at": _normalize_news_date(node.findtext("pubDate") or ""),
                    "retrieved_at": utc_now_iso(),
                }
            )
            if len(items) >= limit:
                break
        return {
            "success": bool(items),
            "items": items,
            "query": query,
            "source": "news.google.rss",
            "source_url": url,
            "retrieved_at": utc_now_iso(),
            "latency_ms": response["latency_ms"],
            "cache_hit": cache_hit,
            "error": None if items else "No matching Google News items",
        }
    except Exception as exc:
        return {
            "success": False,
            "items": [],
            "query": query,
            "source": "news.google.rss",
            "source_url": url,
            "retrieved_at": utc_now_iso(),
            "error": str(exc),
        }


def get_news_context(asset: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    name = asset["asset_name"]
    symbol = asset["symbol"]
    return search_news(f'"{name}" OR "{symbol}" financial stock price', asset, limit)


def _sec_headers() -> Dict[str, str]:
    return {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}


def _resolve_sec_company(symbol: str) -> Dict[str, Any]:
    response, cache_hit = _fetch_json(SEC_TICKERS_URL, headers=_sec_headers(), ttl=86400)
    records = response["payload"].values()
    normalized = symbol.upper().replace(".", "-")
    for row in records:
        if str(row.get("ticker") or "").upper().replace(".", "-") == normalized:
            return {
                "cik": int(row["cik_str"]),
                "company_name": row.get("title") or symbol,
                "cache_hit": cache_hit,
            }
    raise LookupError(f"No SEC registrant found for {symbol}")


FINANCIAL_CONCEPTS = {
    "revenue": (
        "Revenue",
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ],
        ["USD"],
        True,
    ),
    "net_income": ("Net income", ["NetIncomeLoss", "ProfitLoss"], ["USD"], True),
    "operating_income": ("Operating income", ["OperatingIncomeLoss"], ["USD"], True),
    "diluted_eps": (
        "Diluted EPS",
        ["EarningsPerShareDiluted"],
        ["USD/shares", "USD / shares"],
        True,
    ),
    "total_assets": ("Total assets", ["Assets"], ["USD"], False),
    "cash": (
        "Cash and equivalents",
        [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ],
        ["USD"],
        False,
    ),
}


def _row_date(row: Dict[str, Any], key: str) -> datetime:
    try:
        return datetime.fromisoformat(str(row.get(key) or "1900-01-01"))
    except ValueError:
        return datetime(1900, 1, 1)


def _duration_days(row: Dict[str, Any]) -> Optional[int]:
    if not row.get("start") or not row.get("end"):
        return None
    return (_row_date(row, "end") - _row_date(row, "start")).days


def _candidate_rows(
    facts: Dict[str, Any], concepts: Iterable[str], units: Iterable[str], duration: bool
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for concept in concepts:
        concept_data = facts.get(concept) or {}
        for unit in units:
            for row in (concept_data.get("units") or {}).get(unit, []):
                value = _safe_float(row.get("val"))
                if value is None or row.get("form") not in {"10-Q", "10-K", "20-F", "6-K"}:
                    continue
                copied = dict(row)
                copied.update({"value": value, "unit": unit, "concept": concept})
                if duration:
                    days = _duration_days(copied)
                    if days is None or not (55 <= days <= 400):
                        continue
                    copied["duration_days"] = days
                rows.append(copied)
        if rows:
            break
    rows.sort(key=lambda row: (_row_date(row, "end"), _row_date(row, "filed")), reverse=True)
    return rows


def _select_metric(
    facts: Dict[str, Any], key: str, definition: Tuple[Any, ...]
) -> Optional[Dict[str, Any]]:
    label, concepts, units, duration = definition
    rows = _candidate_rows(facts, concepts, units, duration)
    if not rows:
        return None
    if duration:
        framed = [row for row in rows if row.get("frame")]
        current = framed[0] if framed else rows[0]
    else:
        current = rows[0]
    previous = None
    for row in rows[1:]:
        if _row_date(row, "end") >= _row_date(current, "end"):
            continue
        if duration:
            current_days = current.get("duration_days") or 0
            row_days = row.get("duration_days") or 0
            if abs(current_days - row_days) > 35:
                continue
            current_frame = str(current.get("frame") or "")
            row_frame = str(row.get("frame") or "")
            current_suffix = re.sub(r"CY\d{4}", "", current_frame)
            row_suffix = re.sub(r"CY\d{4}", "", row_frame)
            if current_suffix and row_suffix and current_suffix != row_suffix:
                continue
        previous = row
        break
    return {
        "key": key,
        "label": label,
        "value": current["value"],
        "previous_value": previous["value"] if previous else None,
        "growth_pct": _percent_change(current["value"], previous["value"] if previous else None),
        "unit": current["unit"],
        "period_end": current.get("end"),
        "filed_at": current.get("filed"),
        "form": current.get("form"),
        "concept": current.get("concept"),
    }


def get_financial_report(asset: Dict[str, Any]) -> Dict[str, Any]:
    if asset.get("asset_type") not in {"stock", "etf"}:
        return {
            "success": False,
            "not_applicable": True,
            "metrics": [],
            "source": "sec.companyfacts",
            "retrieved_at": utc_now_iso(),
            "error": "SEC financial statements are not applicable to this asset type",
        }
    started = time.perf_counter()
    try:
        company = _resolve_sec_company(asset["symbol"])
        url = SEC_FACTS_URL.format(cik=company["cik"])
        response, cache_hit = _fetch_json(url, headers=_sec_headers(), ttl=1800)
        facts = response["payload"].get("facts", {}).get("us-gaap", {})
        metrics = []
        for key, definition in FINANCIAL_CONCEPTS.items():
            metric = _select_metric(facts, key, definition)
            if metric:
                metrics.append(metric)
        if not metrics:
            raise LookupError("No comparable SEC financial metrics were available")
        latest_filed = max((item.get("filed_at") or "" for item in metrics), default="")
        return {
            "success": True,
            "company_name": company["company_name"],
            "cik": company["cik"],
            "metrics": metrics,
            "latest_filed_at": latest_filed,
            "source": "sec.companyfacts",
            "source_url": url,
            "retrieved_at": utc_now_iso(),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "cache_hit": cache_hit,
        }
    except Exception as exc:
        return {
            "success": False,
            "metrics": [],
            "source": "sec.companyfacts",
            "retrieved_at": utc_now_iso(),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }
