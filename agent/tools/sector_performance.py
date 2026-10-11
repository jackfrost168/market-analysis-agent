"""A sourced US sector ETF comparison, independent of the Agent workflow."""

from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .market_data import YAHOO_SEARCH_URL, _fetch_json, _safe_float, get_price_history, utc_now_iso


SECTOR_ETFS = {
    "technology": ("XLK", "Technology"),
    "communication services": ("XLC", "Communication Services"),
    "consumer cyclical": ("XLY", "Consumer Discretionary"),
    "consumer defensive": ("XLP", "Consumer Staples"),
    "energy": ("XLE", "Energy"),
    "financial services": ("XLF", "Financials"),
    "healthcare": ("XLV", "Health Care"),
    "industrials": ("XLI", "Industrials"),
    "basic materials": ("XLB", "Materials"),
    "real estate": ("XLRE", "Real Estate"),
    "utilities": ("XLU", "Utilities"),
}
US_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "BATS"}
BENCHMARK_SOURCE_URL = "https://www.ssga.com/us/en/intermediary/capabilities/equities/sector-investing/select-sector-etfs"


def compare_histories(stock: Dict[str, Any], benchmark: Dict[str, Any], now=None) -> Dict[str, Any]:
    """Compare raw close returns over exactly the same completed session dates."""
    if not stock.get("success") or not benchmark.get("success"):
        raise ValueError("Price history is unavailable for the stock or sector benchmark.")
    if stock.get("currency") != "USD" or benchmark.get("currency") != "USD":
        raise ValueError("The US sector comparison requires both prices to be quoted in USD.")
    now = now or datetime.now(timezone.utc)

    def sessions(history):
        exchange_timezone = history.get("exchange_timezone")
        if not exchange_timezone:
            raise ValueError("Exchange timezone is unavailable; trading dates cannot be aligned.")
        zone = ZoneInfo(exchange_timezone)
        today = now.astimezone(zone).date()
        result = {}
        for point in history.get("points") or []:
            value, timestamp = _safe_float(point.get("price")), _safe_float(point.get("time"))
            if value is None or value <= 0 or timestamp is None or timestamp <= 0:
                continue
            day = datetime.fromtimestamp(timestamp, zone).date()
            # Conservatively exclude today's bar, even after the market closes.
            if day < today:
                result[day.isoformat()] = value
        return result

    stock_prices, benchmark_prices = sessions(stock), sessions(benchmark)
    shared = sorted(stock_prices.keys() & benchmark_prices.keys())
    if len(shared) < 2:
        raise ValueError("Fewer than two shared completed trading dates are available.")
    start, end = shared[0], shared[-1]
    stock_return = (stock_prices[end] / stock_prices[start] - 1) * 100
    benchmark_return = (benchmark_prices[end] / benchmark_prices[start] - 1) * 100
    return {
        "stock_return_pct": round(stock_return, 4),
        "benchmark_return_pct": round(benchmark_return, 4),
        "excess_return_pp": round(stock_return - benchmark_return, 4),
        "start_date": start,
        "end_date": end,
        "shared_sessions": len(shared),
        "currency": "USD",
        "return_basis": "daily_close_price_return",
        "formula": "(stock_end / stock_start - 1) * 100 - (benchmark_end / benchmark_start - 1) * 100",
    }


def get_sector_performance(asset: Dict[str, Any], range_value="6mo") -> Dict[str, Any]:
    result = {"success": False, "range": range_value, "retrieved_at": utc_now_iso()}
    if asset.get("asset_type") != "stock":
        return {**result, "status": "not_applicable", "reason": "US equity sector benchmarks are available for stocks; this asset is not a stock."}
    symbol = asset["quote_symbol"]
    classification_url = f"{YAHOO_SEARCH_URL}?q={quote(symbol, safe='')}&quotesCount=8&newsCount=0&lang=en-US&region=US"
    try:
        response, _ = _fetch_json(classification_url, ttl=900)
        profile = next((item for item in response["payload"].get("quotes", [])
                        if str(item.get("symbol") or "").upper() == symbol.upper()
                        and item.get("quoteType") == "EQUITY"), None)
        if not profile:
            raise ValueError("An exact stock match and sector classification could not be retrieved.")
        if profile.get("exchange") not in US_EXCHANGES:
            return {**result, "status": "not_applicable", "reason": "Automatic sector comparison currently supports US-listed stocks only."}
        sector = str(profile.get("sector") or profile.get("sectorDisp") or "").strip()
        mapping = SECTOR_ETFS.get(sector.lower())
        if not mapping:
            raise ValueError("No sourced sector classification is available for this stock.")
        benchmark_symbol, benchmark_sector = mapping
        result.update({
            "sector": sector,
            "industry": profile.get("industry") or profile.get("industryDisp"),
            "classification_source_url": classification_url,
            "benchmark": {
                "symbol": benchmark_symbol,
                "sector": benchmark_sector,
                "name": f"{benchmark_sector} Select Sector SPDR ETF",
                "kind": "US sector ETF proxy",
                "source_url": BENCHMARK_SOURCE_URL,
            },
        })
        stock_history = get_price_history(asset, range_value=range_value, interval="1d")
        benchmark_asset = {"symbol": benchmark_symbol, "quote_symbol": benchmark_symbol}
        benchmark_history = get_price_history(benchmark_asset, range_value=range_value, interval="1d")
        comparison = compare_histories(stock_history, benchmark_history)
        return {
            **result, **comparison, "success": True, "status": "available",
            "retrieved_at": utc_now_iso(),
            "price_source_urls": [stock_history["source_url"], benchmark_history["source_url"]],
            "note": "Weighted US sector ETF proxy, not an equal-weight industry average. Same shared trading dates; today's bars excluded. Price returns exclude dividends.",
        }
    except Exception as exc:
        return {**result, "status": "unavailable", "reason": str(exc)}
