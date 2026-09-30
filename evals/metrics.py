"""Deterministic metrics over independently labelled Agent benchmark cases."""

from __future__ import annotations

import re
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Sequence


NUMBER_PATTERN = re.compile(r"(?:\$\s*\d[\d,]*(?:\.\d+)?)|(?:\d+(?:\.\d+)?\s*%)")


def _raw_thesis(state: Dict[str, Any]) -> Dict[str, Any]:
    for record in reversed(state.get("intermediate_results") or []):
        if record.get("node") == "generate_thesis_graph":
            return ((record.get("output") or {}).get("llm_raw_output") or {})
    return state.get("thesis_graph") or {}


def _reference_mentions(thesis: Dict[str, Any]) -> List[str]:
    references: List[str] = []
    for branch_name in ("upside", "downside"):
        branch = thesis.get(branch_name) or {}
        references.extend(branch.get("supporting_evidence_ids") or [])
        for step in branch.get("chain") or []:
            references.extend(step.get("evidence_ids") or [])
    brief = thesis.get("decision_brief") or {}
    references.extend(brief.get("evidence_ids") or [])
    for item in brief.get("watch_items") or []:
        references.extend(item.get("evidence_ids") or [])
    return [str(item) for item in references]


def _numeric_claim_count(value: Any) -> int:
    if isinstance(value, str):
        return len(NUMBER_PATTERN.findall(value))
    if isinstance(value, dict):
        return sum(_numeric_claim_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_numeric_claim_count(item) for item in value)
    return 0


def _unsupported_numeric_count(state: Dict[str, Any]) -> int:
    for issue in (state.get("verification") or {}).get("issues") or []:
        if issue.get("type") == "unverified_numeric_claim_repaired":
            return len(issue.get("details") or [])
    return 0


def evaluate_case(
    case: Dict[str, Any],
    state: Dict[str, Any],
    *,
    status: str = "completed",
    wall_duration_ms: Optional[int] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    expected = case.get("expected") or {}
    expected_tools = set(expected.get("tools") or [])
    selected_tools = set((state.get("research_plan") or {}).get("sources") or [])
    tool_true_positive = len(expected_tools & selected_tools)
    tool_false_positive = len(selected_tools - expected_tools)
    tool_false_negative = len(expected_tools - selected_tools)

    critical = set(expected.get("critical_evidence_types") or [])
    present = {
        item.get("evidence_type")
        for item in state.get("normalized_evidence") or []
        if item.get("temporal_valid", True)
    }
    covered = critical & present

    thesis = _raw_thesis(state)
    references = _reference_mentions(thesis)
    evidence_ids = {str(item.get("id")) for item in state.get("normalized_evidence") or []}
    invalid_references = [item for item in references if item not in evidence_ids]
    numeric_claims = _numeric_claim_count(thesis)
    numeric_errors = min(_unsupported_numeric_count(state), numeric_claims) if numeric_claims else 0
    run_metrics = state.get("run_metrics") or {}
    model_metrics = run_metrics.get("model_calls") or {}
    llm_calls = state.get("llm_calls") or []
    duration = wall_duration_ms if wall_duration_ms is not None else run_metrics.get("total_duration_ms")
    completed = bool(
        status == "completed"
        and state.get("report")
        and any(item.get("node") == "build_report" for item in state.get("node_trace") or [])
    )
    return {
        "case_id": case.get("id"),
        "status": status,
        "completed": completed,
        "error": error,
        "task": {
            "expected": expected.get("task_type"),
            "actual": (state.get("task") or {}).get("task_type"),
            "correct": (state.get("task") or {}).get("task_type") == expected.get("task_type"),
        },
        "tools": {
            "expected": sorted(expected_tools),
            "actual": sorted(selected_tools),
            "exact_match": selected_tools == expected_tools,
            "true_positive": tool_true_positive,
            "false_positive": tool_false_positive,
            "false_negative": tool_false_negative,
        },
        "critical_evidence": {
            "expected": sorted(critical),
            "present": sorted(present),
            "covered": len(covered),
            "total": len(critical),
            "coverage_pct": round(len(covered) / len(critical) * 100, 2) if critical else None,
        },
        "references": {
            "total_mentions": len(references),
            "invalid_mentions": len(invalid_references),
            "unique_invalid_ids": sorted(set(invalid_references)),
            "invalid_rate_pct": round(len(invalid_references) / len(references) * 100, 2) if references else None,
        },
        "numeric_claims": {
            "total": numeric_claims,
            "unsupported_or_untraceable": numeric_errors,
            "error_rate_pct": round(numeric_errors / numeric_claims * 100, 2) if numeric_claims else None,
            "method": "Pre-repair currency and percentage claims are checked for traceability to evidence or deterministic Python output.",
        },
        "runtime_ms": int(duration) if isinstance(duration, (int, float)) else None,
        "model_calls": {
            "total": int(model_metrics.get("total") or len(llm_calls)),
            "successful": int(model_metrics.get("successful") or sum(bool(item.get("success")) for item in llm_calls)),
            "fallbacks": int(model_metrics.get("fallbacks") or sum(not bool(item.get("success")) for item in llm_calls)),
            "prompt_tokens": int(model_metrics.get("prompt_tokens") or sum(int((item.get("usage") or {}).get("prompt_tokens") or 0) for item in llm_calls)),
            "completion_tokens": int(model_metrics.get("completion_tokens") or sum(int((item.get("usage") or {}).get("completion_tokens") or 0) for item in llm_calls)),
            "total_tokens": int(model_metrics.get("total_tokens") or sum(int((item.get("usage") or {}).get("total_tokens") or 0) for item in llm_calls)),
            "metered_api_cost_usd": float(model_metrics.get("metered_api_cost_usd") or 0),
            "estimated_equivalent_cost_usd": model_metrics.get("estimated_equivalent_cost_usd"),
        },
    }


def _pct(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator * 100, 2) if denominator else None


def _percentile(values: Sequence[int], percentile: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return int(ordered[index])


def summarize_evaluation(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    results = list(results)
    tool_tp = sum(item["tools"]["true_positive"] for item in results)
    tool_fp = sum(item["tools"]["false_positive"] for item in results)
    tool_fn = sum(item["tools"]["false_negative"] for item in results)
    tool_precision = tool_tp / (tool_tp + tool_fp) if tool_tp + tool_fp else 0
    tool_recall = tool_tp / (tool_tp + tool_fn) if tool_tp + tool_fn else 0
    reference_total = sum(item["references"]["total_mentions"] for item in results)
    invalid_total = sum(item["references"]["invalid_mentions"] for item in results)
    numeric_total = sum(item["numeric_claims"]["total"] for item in results)
    numeric_errors = sum(item["numeric_claims"]["unsupported_or_untraceable"] for item in results)
    critical_total = sum(item["critical_evidence"]["total"] for item in results)
    critical_covered = sum(item["critical_evidence"]["covered"] for item in results)
    durations = [item["runtime_ms"] for item in results if item.get("runtime_ms") is not None]
    model_calls = sum(item["model_calls"]["total"] for item in results)
    tokens = sum(item["model_calls"]["total_tokens"] for item in results)
    equivalent_costs = [
        item["model_calls"]["estimated_equivalent_cost_usd"]
        for item in results
        if item["model_calls"]["estimated_equivalent_cost_usd"] is not None
    ]
    return {
        "case_count": len(results),
        "task_classification_accuracy_pct": _pct(sum(item["task"]["correct"] for item in results), len(results)),
        "tool_selection": {
            "exact_match_accuracy_pct": _pct(sum(item["tools"]["exact_match"] for item in results), len(results)),
            "micro_precision_pct": round(tool_precision * 100, 2),
            "micro_recall_pct": round(tool_recall * 100, 2),
            "micro_f1_pct": round(2 * tool_precision * tool_recall / (tool_precision + tool_recall) * 100, 2) if tool_precision + tool_recall else 0.0,
        },
        "critical_evidence_coverage_pct": _pct(critical_covered, critical_total),
        "invalid_evidence_reference_rate_pct": _pct(invalid_total, reference_total),
        "numeric_error_rate_pct": _pct(numeric_errors, numeric_total),
        "completion_rate_pct": _pct(sum(item["completed"] for item in results), len(results)),
        "runtime": {
            "average_ms": round(mean(durations)) if durations else None,
            "p50_ms": round(median(durations)) if durations else None,
            "p95_ms": _percentile(durations, 0.95),
        },
        "model_calls": {
            "total": model_calls,
            "average_per_task": round(model_calls / len(results), 2) if results else None,
        },
        "token_and_cost": {
            "total_tokens": tokens,
            "average_tokens_per_task": round(tokens / len(results), 2) if results else None,
            "metered_api_cost_usd": round(sum(item["model_calls"]["metered_api_cost_usd"] for item in results), 8),
            "estimated_equivalent_cost_usd": round(sum(float(value) for value in equivalent_costs), 8) if equivalent_costs else None,
            "cost_scope": "Generation calls only; local Ollama has no metered API token fee.",
        },
        "cases": results,
    }
