"""Run-level metrics derived from persisted Agent audit records."""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, Iterable, List, Optional


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def build_run_metrics(
    state: Dict[str, Any],
    *,
    started_at: str,
    completed_at: str,
    total_duration_ms: int,
) -> Dict[str, Any]:
    llm_calls = state.get("llm_calls") or []
    tool_calls = state.get("tool_calls") or []
    node_trace = state.get("node_trace") or []
    prompt_tokens = sum(int((call.get("usage") or {}).get("prompt_tokens") or 0) for call in llm_calls)
    completion_tokens = sum(int((call.get("usage") or {}).get("completion_tokens") or 0) for call in llm_calls)
    equivalent_costs = [
        (call.get("usage") or {}).get("estimated_equivalent_cost_usd")
        for call in llm_calls
        if (call.get("usage") or {}).get("estimated_equivalent_cost_usd") is not None
    ]
    costs = [(call.get("usage") or {}).get("metered_api_cost_usd") for call in llm_calls]
    unreported = sum((call.get("usage") or {}).get("provider") in {"qwen_api", "bedrock"}
                     and (call.get("usage") or {}).get("metered_api_cost_usd") is None for call in llm_calls)
    node_durations = [
        {"node": item.get("node"), "duration_ms": int(item.get("duration_ms") or 0)}
        for item in node_trace
    ]
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "total_duration_ms": int(total_duration_ms),
        "node_executions": len(node_trace),
        "node_durations": node_durations,
        "slowest_nodes": sorted(node_durations, key=lambda item: item["duration_ms"], reverse=True)[:3],
        "tool_calls": {
            "total": len(tool_calls),
            "successful": sum(bool(call.get("success")) for call in tool_calls),
            "failed": sum(not bool(call.get("success")) for call in tool_calls),
            "total_latency_ms": round(sum(_number(call.get("latency_ms")) for call in tool_calls)),
        },
        "model_calls": {
            "total": len(llm_calls),
            "successful": sum(bool(call.get("success")) for call in llm_calls),
            "fallbacks": sum(not bool(call.get("success")) for call in llm_calls),
            "total_latency_ms": round(sum(_number(call.get("latency_ms")) for call in llm_calls)),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "metered_api_cost_usd": round(sum(float(cost) for cost in costs if cost is not None), 8),
            "api_cost_unreported_calls": unreported,
            "estimated_equivalent_cost_usd": (
                round(sum(float(value) for value in equivalent_costs), 8)
                if equivalent_costs
                else None
            ),
            "cost_note": "API cost is an estimate from provider-reported usage and recorded token rates, not an invoice. Unknown usage is excluded. Credits, taxes, embeddings, hardware and electricity are excluded. Local Ollama has no metered token fee.",
        },
        "retrieval_attempts": int(state.get("retrieval_attempts") or 0),
        "evidence_coverage_pct": int((state.get("evidence_gate") or {}).get("coverage_pct") or 0),
        "verification_passed": bool((state.get("verification") or {}).get("passed")),
    }


def aggregate_run_metrics(states: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    states = list(states)
    completed = [state for state in states if state.get("status") == "completed"]
    durations = [
        int(((state.get("state") or {}).get("run_metrics") or {}).get("total_duration_ms") or 0)
        for state in completed
    ]
    durations = [value for value in durations if value > 0]
    model_calls = [
        ((state.get("state") or {}).get("run_metrics") or {}).get("model_calls") or {}
        for state in states
    ]
    return {
        "persisted_runs": len(states),
        "completed_runs": len(completed),
        "failed_runs": sum(state.get("status") == "failed" for state in states),
        "completion_rate_pct": round(len(completed) / len(states) * 100, 2) if states else None,
        "average_runtime_ms": round(mean(durations)) if durations else None,
        "model_calls_total": sum(int(item.get("total") or 0) for item in model_calls),
        "tokens_total": sum(int(item.get("total_tokens") or 0) for item in model_calls),
        "metered_api_cost_usd": round(sum(_number(item.get("metered_api_cost_usd")) for item in model_calls), 8),
        "api_cost_unreported_calls": sum(int(item.get("api_cost_unreported_calls") or 0) for item in model_calls),
        "estimated_equivalent_cost_usd": (
            round(sum(float(item["estimated_equivalent_cost_usd"]) for item in model_calls if item.get("estimated_equivalent_cost_usd") is not None), 8)
            if any(item.get("estimated_equivalent_cost_usd") is not None for item in model_calls)
            else None
        ),
    }
