import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .market_data import (
    get_financial_report,
    get_news_context,
    get_price_history,
    get_price_snapshot,
    search_news,
    utc_now_iso,
)
from .memory import PersistentNewsMemory, enrich_news_with_paragraphs
from .prediction_markets import get_polymarket_context, select_provider


SOURCE_TO_KIND = {
    "yahoo_finance": "market_price",
    "google_news": "news",
    "financial_statements": "financials",
    "price_history": "price_history",
    "vector_db": "vector_history",
    "polymarket": "polymarket",
}


def _status(payload: Dict[str, Any], started: float) -> Dict[str, Any]:
    items = payload.get("items")
    metrics = payload.get("metrics")
    points = payload.get("points")
    count = (
        len(items)
        if isinstance(items, list)
        else len(metrics)
        if isinstance(metrics, list)
        else len(points)
        if isinstance(points, list)
        else 1
        if payload.get("success")
        else 0
    )
    return {
        "status": "blocked"
        if payload.get("access_blocked")
        else "not_applicable"
        if payload.get("not_applicable")
        else "empty"
        if payload.get("retrieval_status") == "empty"
        else "success"
        if payload.get("success")
        else "failed",
        "success": bool(payload.get("success")),
        "started_at": payload.get("started_at"),
        "finished_at": utc_now_iso(),
        "latency_ms": payload.get("latency_ms")
        or round((time.perf_counter() - started) * 1000),
        "record_count": count,
        "source": payload.get("source"),
        "provider": payload.get("provider"),
        "attribution": payload.get("attribution"),
        "api_probes": payload.get("api_probes") or [],
        "filter_rejections": payload.get("filter_rejections") or {},
        "source_url": payload.get("source_url"),
        "error": payload.get("error"),
        "error_code": payload.get("error_code"),
        "retrieval_status": payload.get("retrieval_status"),
        "access_blocked": bool(payload.get("access_blocked", False)),
        "blocking_layer": payload.get("blocking_layer"),
        "provider_error_codes": payload.get("provider_error_codes") or [],
        "diagnostics": payload.get("diagnostics") or [],
        "fallback_used": bool(payload.get("fallback_used", False)),
    }


def _safe_call(function: Callable[[], Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    started = time.perf_counter()
    started_at = utc_now_iso()
    try:
        payload = function()
        payload.setdefault("started_at", started_at)
    except Exception as exc:
        payload = {
            "success": False,
            "started_at": started_at,
            "retrieved_at": utc_now_iso(),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return payload, _status(payload, started)


def _result_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    preview = {
        "success": bool(payload.get("success")),
        "source": payload.get("source"),
        "error_code": payload.get("error_code"),
        "error": payload.get("error"),
    }
    for field in (
        "price",
        "currency",
        "return_1d_pct",
        "kept_count",
        "candidate_count",
        "events_scanned",
        "retrieval_status",
        "search_price",
        "provider",
        "attribution",
        "api_probes",
        "filter_rejections",
        "limitations",
        "stored_count",
        "inserted_count",
        "chunk_count",
        "embedded_count",
        "embedding",
    ):
        if payload.get(field) is not None:
            preview[field] = payload[field]
    rows = payload.get("items")
    if isinstance(rows, list):
        preview["item_count"] = len(rows)
        preview["items"] = [
            {
                key: row.get(key)
                for key in ("headline", "title", "market", "probability", "similarity")
                if row.get(key) is not None
            }
            for row in rows[:3]
            if isinstance(row, dict)
        ]
    points = payload.get("points")
    if isinstance(points, list):
        preview["point_count"] = len(points)
    metrics = payload.get("metrics")
    if isinstance(metrics, list):
        preview["metric_count"] = len(metrics)
    return preview


def _tool_record(
    node: str,
    call_key: str,
    function_name: str,
    arguments: Dict[str, Any],
    payload: Dict[str, Any],
    status: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "tool_call_id": f"tool-{uuid.uuid4().hex[:12]}",
        "node": node,
        "call_key": call_key,
        "function": function_name,
        "arguments": arguments,
        "status": status.get("status"),
        "success": status.get("success"),
        "started_at": status.get("started_at"),
        "finished_at": status.get("finished_at"),
        "latency_ms": status.get("latency_ms"),
        "record_count": status.get("record_count"),
        "source": status.get("source"),
        "provider": status.get("provider"),
        "source_url": status.get("source_url"),
        "error_code": status.get("error_code"),
        "error": status.get("error"),
        "result_preview": _result_preview(payload),
    }


class ResearchTools:
    def __init__(self, data_dir: Path):
        self.memory = PersistentNewsMemory(Path(data_dir) / "news_vectors.sqlite3")

    def collect(
        self,
        asset: Dict[str, Any],
        plan: Dict[str, Any],
        query: str,
        target_price: Optional[float],
        user_notes: List[str],
        user_headlines: List[str],
    ) -> Dict[str, Any]:
        requested = set(plan.get("sources") or [])
        asset_args = {
            "symbol": asset.get("symbol"),
            "asset_name": asset.get("asset_name"),
            "asset_type": asset.get("asset_type"),
        }
        calls: Dict[str, Dict[str, Any]] = {}
        if "yahoo_finance" in requested:
            calls["yahoo_finance"] = {
                "function": "get_price_snapshot",
                "arguments": asset_args,
                "call": lambda: get_price_snapshot(asset),
            }
        if "google_news" in requested:
            calls["google_news"] = {
                "function": "get_news_context",
                "arguments": asset_args,
                "call": lambda: get_news_context(asset),
            }
        if "financial_statements" in requested:
            calls["financial_statements"] = {
                "function": "get_financial_report",
                "arguments": asset_args,
                "call": lambda: get_financial_report(asset),
            }
        if "price_history" in requested:
            calls["price_history"] = {
                "function": "get_price_history",
                "arguments": {**asset_args, "range": "6mo", "interval": "1d"},
                "call": lambda: get_price_history(asset),
            }
        if "vector_db" in requested:
            memory_query = f"{asset['asset_name']} {asset['symbol']} {query}"
            calls["vector_db"] = {
                "function": "PersistentNewsMemory.retrieve",
                "arguments": {"query": memory_query, "limit": 3},
                "call": lambda: self.memory.retrieve(memory_query, limit=3),
            }

        payloads: Dict[str, Dict[str, Any]] = {}
        statuses: Dict[str, Dict[str, Any]] = {}
        tool_calls: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as executor:
            futures = {
                executor.submit(_safe_call, descriptor["call"]): (name, descriptor)
                for name, descriptor in calls.items()
            }
            for future in as_completed(futures):
                name, descriptor = futures[future]
                payload, status = future.result()
                payloads[name] = payload
                statuses[name] = status
                tool_calls.append(
                    _tool_record(
                        "collect_evidence",
                        name,
                        descriptor["function"],
                        descriptor["arguments"],
                        payload,
                        status,
                    )
                )

        news_payload = payloads.get("google_news")
        if news_payload and news_payload.get("success"):
            enrichment = enrich_news_with_paragraphs(news_payload.get("items") or [])
            news_payload["items"] = enrichment["items"]
            news_payload["article_enrichment"] = {
                key: value for key, value in enrichment.items() if key != "items"
            }
            write_payload, write_status = _safe_call(
                lambda: self.memory.persist_news(news_payload["items"], asset)
            )
            payloads["vector_db_write"] = write_payload
            statuses["vector_db_write"] = write_status
            tool_calls.append(
                _tool_record(
                    "collect_evidence",
                    "vector_db_write",
                    "PersistentNewsMemory.persist_news",
                    {
                        "symbol": asset.get("symbol"),
                        "paragraph_count": len(news_payload["items"]),
                    },
                    write_payload,
                    write_status,
                )
            )

        current_price = (payloads.get("yahoo_finance") or {}).get("price")
        if "polymarket" in requested:
            provider = select_provider(plan.get("prediction_provider"))
            crowd_payload, crowd_status = _safe_call(
                lambda: get_polymarket_context(
                    asset["symbol"],
                    asset["asset_name"],
                    current_price=current_price,
                    target_price=target_price,
                    provider=provider,
                )
            )
            payloads["polymarket"] = crowd_payload
            statuses["polymarket"] = crowd_status
            tool_calls.append(
                _tool_record(
                    "collect_evidence",
                    "polymarket",
                    "get_polymarket_context",
                    {
                        "symbol": asset.get("symbol"),
                        "asset_name": asset.get("asset_name"),
                        "current_price": current_price,
                        "target_price": target_price,
                        "provider": provider,
                    },
                    crowd_payload,
                    crowd_status,
                )
            )

        raw = [
            {"kind": SOURCE_TO_KIND[name], "payload": payload}
            for name, payload in payloads.items()
            if name in SOURCE_TO_KIND
        ]
        if user_notes:
            raw.append(
                {
                    "kind": "user_notes",
                    "payload": {"items": user_notes, "retrieved_at": utc_now_iso()},
                }
            )
        if user_headlines:
            raw.append(
                {
                    "kind": "user_headlines",
                    "payload": {"items": user_headlines, "retrieved_at": utc_now_iso()},
                }
            )
        return {
            "raw_evidence": raw,
            "source_status": statuses,
            "payloads": payloads,
            "tool_calls": tool_calls,
        }

    def targeted_retrieval(
        self,
        asset: Dict[str, Any],
        searches: List[Dict[str, Any]],
        existing_raw: List[Dict[str, Any]],
        target_price: Optional[float],
        attempt: int,
    ) -> Dict[str, Any]:
        price_payloads = [
            row.get("payload") or {}
            for row in existing_raw
            if row.get("kind") == "market_price" and (row.get("payload") or {}).get("success")
        ]
        current_price = price_payloads[-1].get("price") if price_payloads else None
        calls: Dict[str, Dict[str, Any]] = {}
        for index, search in enumerate(searches):
            source = search.get("source")
            query = search.get("query") or f"{asset['asset_name']} {asset['symbol']}"
            key = f"{source}_retry_{attempt}_{index + 1}"
            if source == "google_news":
                calls[key] = {
                    "kind": "news",
                    "function": "search_news",
                    "arguments": {"query": query, "symbol": asset.get("symbol"), "limit": 12},
                    "call": lambda q=query: search_news(q, asset, limit=12),
                }
            elif source == "yahoo_finance":
                calls[key] = {
                    "kind": "market_price",
                    "function": "get_price_snapshot",
                    "arguments": {"symbol": asset.get("symbol")},
                    "call": lambda: get_price_snapshot(asset),
                }
            elif source == "price_history":
                calls[key] = {
                    "kind": "price_history",
                    "function": "get_price_history",
                    "arguments": {"symbol": asset.get("symbol"), "range": "1y", "interval": "1d"},
                    "call": lambda: get_price_history(asset, "1y", "1d"),
                }
            elif source == "financial_statements":
                calls[key] = {
                    "kind": "financials",
                    "function": "get_financial_report",
                    "arguments": {"symbol": asset.get("symbol")},
                    "call": lambda: get_financial_report(asset),
                }
            elif source == "vector_db":
                calls[key] = {
                    "kind": "vector_history",
                    "function": "PersistentNewsMemory.retrieve",
                    "arguments": {"query": query, "limit": 3},
                    "call": lambda q=query: self.memory.retrieve(q, limit=3),
                }
            elif source == "polymarket":
                provider = select_provider(search.get("provider"))
                calls[key] = {
                    "kind": "polymarket",
                    "function": "get_polymarket_context",
                    "arguments": {
                        "symbol": asset.get("symbol"),
                        "asset_name": asset.get("asset_name"),
                        "current_price": current_price,
                        "target_price": target_price,
                        "extra_query": query,
                        "provider": provider,
                    },
                    "call": lambda q=query, p=provider: get_polymarket_context(
                        asset["symbol"], asset["asset_name"], current_price, target_price, q, provider=p
                    ),
                }

        added_raw: List[Dict[str, Any]] = []
        statuses: Dict[str, Dict[str, Any]] = {}
        tool_calls: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as executor:
            futures = {
                executor.submit(_safe_call, descriptor["call"]): (name, descriptor)
                for name, descriptor in calls.items()
            }
            for future in as_completed(futures):
                name, descriptor = futures[future]
                kind = descriptor["kind"]
                payload, status = future.result()
                tool_calls.append(
                    _tool_record(
                        "targeted_retrieval",
                        name,
                        descriptor["function"],
                        descriptor["arguments"],
                        payload,
                        status,
                    )
                )
                if kind == "news" and payload.get("success"):
                    enrichment = enrich_news_with_paragraphs(payload.get("items") or [], 3)
                    payload["items"] = enrichment["items"]
                    write_payload, write_status = _safe_call(
                        lambda: self.memory.persist_news(payload["items"], asset)
                    )
                    write_key = f"vector_db_write_retry_{attempt}_{name}"
                    statuses[write_key] = write_status
                    tool_calls.append(
                        _tool_record(
                            "targeted_retrieval",
                            write_key,
                            "PersistentNewsMemory.persist_news",
                            {
                                "symbol": asset.get("symbol"),
                                "paragraph_count": len(payload["items"]),
                            },
                            write_payload,
                            write_status,
                        )
                    )
                added_raw.append({"kind": kind, "payload": payload})
                statuses[name] = status
        return {
            "raw_evidence": existing_raw + added_raw,
            "source_status": statuses,
            "tool_calls": tool_calls,
        }
