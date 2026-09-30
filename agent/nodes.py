import copy
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .llm import OllamaClient
from .schemas import (
    RequestUnderstanding,
    SemanticPlan,
    TargetedSearchPlan,
    ThesisGraph,
)
from .state import AgentState
from .tools.evidence import evaluate_gate, normalize_evidence
from .tools.market_data import resolve_asset, utc_now_iso
from .tools.quantitative import analyze_quantitatively
from .tools.research import ResearchTools
from .tools.targets import enforce_target_summary


SUPPORTED_TASKS = (
    "company_analysis",
    "earnings_analysis",
    "move_explanation",
    "technical_analysis",
    "company_outlook",
    "event_impact",
    "general_research",
)

RESEARCH_SOURCES = (
    "yahoo_finance",
    "google_news",
    "polymarket",
    "vector_db",
    "financial_statements",
    "price_history",
)


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def _price_forecast_requested(query: str) -> bool:
    lowered = query.lower()
    price = bool(re.search(r"\b(?:price|prices)\b|股价|币价|价格|价位", lowered))
    forecast = bool(re.search(r"\b(?:predict(?:ion|ions|ed|ing|s)?|forecast(?:s|ing|ed)?|target)\b|预测|预估|目标", lowered))
    return price and forecast


def _explicit_task_type(query: str) -> Optional[str]:
    """Return a rule-backed task only when the wording is unambiguous."""
    lowered = query.lower()
    if any(
        term in lowered
        for term in (
            "why did", "why is", "why has", "moved today", "move today",
            "price move", "dropped", "fell today", "rallied", "selloff",
        )
    ):
        return "move_explanation"
    if any(
        term in lowered
        for term in (
            "impact of", "effect of", "impact on", "effect on",
            "what happens if", "event impact",
        )
    ):
        return "event_impact"
    if any(
        term in lowered
        for term in (
            "earnings", "revenue", "eps", "quarterly results", "10-q", "10-k",
            "income statement", "cash flow", "operating margin", "net margin",
        )
    ):
        return "earnings_analysis"
    if _price_forecast_requested(query):
        return "company_outlook"
    if any(
        term in lowered
        for term in (
            "technical analysis", "price trend", "historical price", "price history",
            "volatility", "drawdown", "support level", "resistance level",
            "moving average", "momentum indicator",
        )
    ):
        return "technical_analysis"
    if any(
        term in lowered
        for term in (
            "outlook", "forecast", "future expectation", "next year",
            "long-term view", "long term view",
        )
    ):
        return "company_outlook"
    if any(
        term in lowered
        for term in (
            "fundamental analysis", "company analysis", "business analysis",
            "valuation", "competitive position", "business model",
        )
    ):
        return "company_analysis"
    return None


def _select_research_sources(
    task: Dict[str, Any],
    query: str,
    target_price: Optional[float],
    llm_suggestions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    task_type = task.get("task_type") or "general_research"
    asset_type = task.get("asset_type") or "unknown"
    suggestions = [item for item in (llm_suggestions or []) if item in RESEARCH_SOURCES]
    broad_markers = (
        "analyze", "analysis", "why", "driver", "risk", "outlook", "forecast",
        "future", "earnings", "revenue", "profit", "margin", "news", "event",
        "trend", "momentum", "volatility", "target", "probability", "prediction",
    )
    price_markers = ("current price", "latest price", "quote", "trading at", "price now")
    price_only = _contains_any(query, price_markers) and not _contains_any(query, broad_markers)
    news_markers = (
        "current", "latest", "news", "why", "driver", "catalyst", "event",
        "risk", "outlook", "impact", "guidance", "announcement", "launch",
        "regulation", "tariff", "lawsuit",
    )
    history_markers = (
        "price trend", "historical price", "price history", "momentum", "technical",
        "volatility", "return", "drawdown", "support", "resistance", "moving average",
        "market reaction", "post-earnings move",
    )
    fundamental_markers = (
        "earnings", "revenue", "profit", "margin", "eps", "fundamental",
        "valuation", "balance sheet", "cash flow", "10-q", "10-k", "financial statement",
    )
    crowd_markers = (
        "polymarket", "prediction market", "probability", "odds", "crowd",
        "market-implied", "will reach", "will hit", "above $", "below $",
        "预测市场", "隐含概率", "赔率",
    )
    memory_markers = (
        "history", "historical", "previous", "precedent", "memory", "past", "similar",
    )
    price_forecast = _price_forecast_requested(query)
    crowd_requested = target_price is not None or price_forecast or _contains_any(query, crowd_markers)
    price_only = price_only and not crowd_requested

    selected: List[str] = ["yahoo_finance"]
    rationale: Dict[str, Dict[str, Any]] = {
        source: {"selected": False, "reason": "Not required by this request.", "origin": []}
        for source in RESEARCH_SOURCES
    }
    rationale["yahoo_finance"] = {
        "selected": True,
        "reason": "Current price is the common market anchor for every analysis.",
        "origin": ["required_rule"],
    }

    def choose(source: str, reason: str, origin: str):
        if source not in selected:
            selected.append(source)
        item = rationale[source]
        item["selected"] = True
        if item["reason"] == "Not required by this request.":
            item["reason"] = reason
        elif reason not in item["reason"]:
            item["reason"] = f"{item['reason']} {reason}"
        if origin not in item["origin"]:
            item["origin"].append(origin)

    if not price_only:
        news_needed = task_type in {
            "earnings_analysis", "move_explanation", "company_outlook", "event_impact"
        } or _contains_any(query, news_markers)
        if news_needed:
            choose(
                "google_news",
                "Fresh reporting, events, or management guidance are material to this task.",
                "task_relevance_rule",
            )

        history_needed = (
            task_type in {"move_explanation", "technical_analysis"}
            or target_price is not None
            or price_forecast
            or _contains_any(query, history_markers)
            or (
                asset_type in {"crypto", "index"}
                and task_type in {"company_outlook", "general_research"}
                and _contains_any(query, ("risk", "outlook", "forecast", "analyze", "analysis"))
            )
        )
        if history_needed:
            choose(
                "price_history",
                "Historical prices are needed for deterministic trend, move, target, or risk calculations.",
                "task_relevance_rule",
            )

        financial_needed = asset_type in {"stock", "etf"} and (
            task_type in {"company_analysis", "earnings_analysis"}
            or _contains_any(query, fundamental_markers)
        )
        if financial_needed:
            choose(
                "financial_statements",
                "Filed company fundamentals are directly relevant to this stock or ETF task.",
                "asset_task_rule",
            )

        polymarket_needed = crowd_requested
        if polymarket_needed:
            choose(
                "polymarket",
                "The request needs a price forecast, a target, or crowd-implied probabilities; ongoing price markets provide expectation evidence only.",
                "probability_task_rule",
            )

        if "google_news" in selected or _contains_any(query, memory_markers):
            choose(
                "vector_db",
                "Historical RAG context supports comparison with prior news paragraphs.",
                "memory_rule",
            )

    news_eligible = not price_only and (
        task_type in {"earnings_analysis", "move_explanation", "company_outlook", "event_impact"}
        or _contains_any(query, news_markers)
    )
    history_eligible = not price_only and (
        task_type in {"move_explanation", "technical_analysis"}
        or target_price is not None
        or price_forecast
        or _contains_any(query, history_markers)
        or (
            asset_type in {"crypto", "index"}
            and task_type in {"company_outlook", "general_research"}
        )
    )
    financial_eligible = asset_type in {"stock", "etf"} and (
        task_type in {"company_analysis", "earnings_analysis"}
        or _contains_any(query, fundamental_markers)
    )
    polymarket_eligible = crowd_requested
    memory_eligible = "google_news" in selected or _contains_any(query, memory_markers)

    eligible_llm_additions = {
        "google_news": news_eligible,
        "price_history": history_eligible,
        "financial_statements": financial_eligible,
        "polymarket": polymarket_eligible,
        "vector_db": memory_eligible,
        "yahoo_finance": True,
    }
    for source in suggestions:
        if eligible_llm_additions.get(source, False):
            choose(source, "The local LLM proposed this source and the capability guard accepted it.", "llm_proposal")
        elif source in rationale:
            rationale[source]["llm_proposed_but_rejected"] = True
            rationale[source]["rejection_reason"] = "The proposal did not pass asset/task relevance guardrails."

    if "polymarket" not in selected:
        rationale["polymarket"]["reason"] = "Not called: no price-forecast, target-price, probability, or prediction-market request was identified. This is a planning decision, not an API connection failure."

    skipped = [source for source in RESEARCH_SOURCES if source not in selected]
    return {
        "sources": selected,
        "skipped_sources": skipped,
        "source_rationale": rationale,
        "price_only_request": price_only,
        "llm_suggested_sources": suggestions,
    }


def _trace(
    node: str,
    started: float,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
    status: str = "completed",
) -> Dict[str, Any]:
    return {
        "node": node,
        "status": status,
        "summary": summary,
        "details": details or {},
        "finished_at": utc_now_iso(),
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }


def _decision(
    node: str,
    mode: str,
    makers: List[str],
    inputs: Dict[str, Any],
    rules: List[str],
    decision: Dict[str, Any],
    output: Dict[str, Any],
    tools: Optional[List[str]] = None,
    llm: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "node": node,
        "recorded_at": utc_now_iso(),
        "decision_mode": mode,
        "decision_makers": makers,
        "input_snapshot": inputs,
        "rules_applied": rules,
        "llm": llm or {"used": False},
        "tools": tools or [],
        "decision": decision,
        "output_snapshot": output,
    }


def _intermediate(node: str, output: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "node": node,
        "recorded_at": utc_now_iso(),
        "output": output,
    }


def _evidence_snapshot(evidence: List[Dict[str, Any]], limit: int = 30) -> List[Dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "type": item.get("evidence_type"),
            "source": item.get("source"),
            "title": item.get("title"),
            "direction": item.get("direction"),
            "reliability": item.get("reliability"),
            "freshness": item.get("freshness"),
        }
        for item in evidence[:limit]
    ]


def _fallback_task(query: str) -> str:
    return _explicit_task_type(query) or "general_research"


def _fallback_horizon(query: str, requested: str) -> str:
    if requested:
        return requested
    lowered = query.lower()
    if any(term in lowered for term in ("today", "intraday", "now")):
        return "1 day"
    if "week" in lowered:
        return "1 week"
    if any(term in lowered for term in ("quarter", "earnings")):
        return "1 quarter"
    if any(term in lowered for term in ("year", "long term", "long-term")):
        return "1 year"
    return "1-3 months"


def _normalize_as_of(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return utc_now_iso()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return utc_now_iso()


def _evidence_for_prompt(evidence: List[Dict[str, Any]], limit: int = 24):
    ordered = sorted(
        evidence,
        key=lambda item: (
            item.get("evidence_type") == "historical_news",
            -float(item.get("reliability") or 0),
        ),
    )
    return [
        {
            "id": item.get("id"),
            "type": item.get("evidence_type"),
            "source": item.get("source"),
            "title": item.get("title"),
            "content": str(item.get("content") or "")[:500],
            "timestamp": item.get("timestamp"),
            "direction": item.get("direction"),
            "reliability": item.get("reliability"),
            "freshness": item.get("freshness"),
        }
        for item in ordered[:limit]
    ]


def _collect_numbers(value: Any, output: List[float]):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        parsed = float(value)
        output.append(parsed)
        if 0 <= parsed <= 1:
            output.append(parsed * 100)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_numbers(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_numbers(item, output)


def _tagged_numbers(text: str):
    values = []
    pattern = r"(?P<currency>\$)\s*(?P<dollars>\d[\d,]*(?:\.\d+)?)|(?P<percent>\d+(?:\.\d+)?)\s*%"
    for match in re.finditer(pattern, text or ""):
        if match.group("dollars"):
            values.append(("currency", float(match.group("dollars").replace(",", ""))))
        elif match.group("percent"):
            values.append(("percent", float(match.group("percent"))))
    return values


def _unverified_numeric_paths(thesis: Dict[str, Any], state: AgentState):
    allowed: List[float] = []
    _collect_numbers(state.get("quantitative_analysis") or {}, allowed)
    _collect_numbers({"target_price": state.get("target_price")}, allowed)
    for item in state.get("normalized_evidence") or []:
        _collect_numbers(item.get("metadata") or {}, allowed)
        allowed.extend(value for _, value in _tagged_numbers(f"{item.get('title', '')} {item.get('content', '')}"))

    invalid = []

    def walk(value: Any, path: str):
        if isinstance(value, str):
            for kind, number in _tagged_numbers(value):
                tolerance = max(0.25, abs(number) * (0.002 if kind == "currency" else 0.01))
                if not any(abs(number - candidate) <= tolerance for candidate in allowed):
                    invalid.append({"path": path, "value": number, "kind": kind})
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(thesis, "")
    return invalid


def _asserts_price_causality(text: str) -> bool:
    # Exclude narrow, explicit disclaimers without hiding assertions elsewhere in the sentence.
    text = re.sub(
        r"\b(?:does not|do not|cannot) (?:establish|prove|show) (?:a |the )?(?:fundamental )?cause\b"
        r"|\b(?:is not|are not|never) (?:a |the )?(?:fundamental )?cause\b",
        "", text.lower(),
    )
    return bool(re.search(r"\b(cause|causes|caused|because|drives|driven)\b", text))


class AgentNodes:
    def __init__(self, llm: OllamaClient, tools: ResearchTools):
        self.llm = llm
        self.tools = tools

    def understand_request(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        query = (state.get("user_query") or "").strip()
        asset_input = (state.get("asset_input") or "").strip()
        as_of = _normalize_as_of(state.get("requested_as_of") or "")
        fallback = {
            "asset": asset_input or query or "Bitcoin",
            "horizon": _fallback_horizon(query, state.get("requested_horizon") or ""),
            "as_of": as_of,
            "task_type": _fallback_task(query),
            "important_requirements": [],
        }
        prompt = f"""
Understand this ordinary user financial question and return only the requested JSON schema.
Allowed task_type values: {', '.join(SUPPORTED_TASKS)}. Never invent another type.
Use the supplied as-of time unless the user explicitly provides an earlier cutoff.
Preserve concrete requirements from the user's natural-language question in important_requirements.

Asset field: {asset_input or 'not separately supplied'}
Structured target price: {state.get('target_price')}; condition: {state.get('target_condition', 'auto')}.
The structured target field takes precedence over a stale target number in the question.
User query: {query}
Requested horizon: {state.get('requested_horizon') or 'not supplied'}
As-of time: {as_of}
""".strip()
        result = self.llm.generate_structured(
            prompt,
            RequestUnderstanding,
            model=state.get("model"),
            temperature=0.05,
        )
        understood = result.data if result.success and result.data else fallback
        understood["as_of"] = _normalize_as_of(
            state.get("requested_as_of") or understood.get("as_of") or as_of
        )
        understood["horizon"] = state.get("requested_horizon") or understood.get("horizon") or fallback["horizon"]
        if understood.get("task_type") not in SUPPORTED_TASKS:
            understood["task_type"] = fallback["task_type"]
        explicit_task = _explicit_task_type(query)
        task_rule_override = None
        if explicit_task and understood.get("task_type") != explicit_task:
            task_rule_override = {
                "llm_task_type": understood.get("task_type"),
                "rule_task_type": explicit_task,
                "reason": "The request contains an unambiguous task phrase.",
            }
            understood["task_type"] = explicit_task
        asset = resolve_asset(asset_input or understood.get("asset") or "", query)
        selected_model = result.model
        explanation = (
            "The local Ollama model understood the normal question and extracted the asset, task, horizon, and requirements."
            if result.success
            else "Ollama was unavailable or returned an invalid result, so local request-understanding rules were used."
        )
        if task_rule_override:
            explanation += f" An explicit task phrase fixed the route as {explicit_task.replace('_', ' ')}."
        task = {
            **understood,
            **asset,
            "original_request": query,
            "classification_mode": "local_ollama_understanding" if result.success else "deterministic_request_understanding",
            "classification_explanation": explanation,
            "classification_error": result.error,
            "task_rule_override": task_rule_override,
        }
        call = {
            "node": "understand_request",
            "model": selected_model,
            "success": result.success,
            "latency_ms": result.latency_ms,
            "purpose": "Understand a normal user question and extract a validated financial task",
            "input_manifest": ["asset_input", "user_query", "requested_horizon", "as_of"],
            "prompt_preview": prompt,
            "usage": result.usage,
            "error": result.error,
        }
        return {
            "task": task,
            "model": selected_model,
            "llm_calls": [call],
            "decision_audit": [
                _decision(
                    "understand_request",
                    "llm_with_rule_fallback",
                    ["LLM", "rules"],
                    {
                        "asset_input": asset_input,
                        "user_query": query,
                        "requested_horizon": state.get("requested_horizon") or None,
                        "requested_as_of": state.get("requested_as_of") or None,
                    },
                    [
                        "Task type must be one of the fixed supported labels.",
                        "Unambiguous task phrases override a conflicting LLM task label.",
                        "As-of time is normalized to UTC.",
                        "A deterministic classifier is used if structured Ollama output fails.",
                    ],
                    {
                        "interpreted_request": query,
                        "task_type": task["task_type"],
                        "classification_mode": task["classification_mode"],
                        "task_rule_override": task_rule_override,
                        "horizon": task["horizon"],
                        "next_node": "plan_research",
                    },
                    {"task": task},
                    llm={
                        "used": True,
                        "model": selected_model,
                        "success": result.success,
                        "purpose": call["purpose"],
                    },
                )
            ],
            "intermediate_results": [_intermediate("understand_request", {"task": task})],
            "node_trace": [
                _trace(
                    "understand_request",
                    started,
                    f"Understood the user request as {task['task_type']} for {task['asset_name']} ({task['symbol']}).",
                    {"task_type": task["task_type"], "horizon": task["horizon"], "mode": task["classification_mode"]},
                )
            ],
        }

    def plan_research(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        task = state["task"]
        task_type = task["task_type"]
        fallback_questions = {
            "company_analysis": [
                "What is the current market setup?",
                "Are revenue, profit, and margins improving or deteriorating?",
                "Which current catalysts and risks transmit into future results?",
            ],
            "earnings_analysis": [
                "What changed in the latest comparable financial period?",
                "Does post-earnings price behavior agree with the fundamentals?",
                "Which expectations could be reset next?",
            ],
            "move_explanation": [
                "How unusual is the move relative to recent history?",
                "Which fresh events plausibly preceded the move?",
                "Are crowd expectations aligned or conflicting?",
            ],
            "technical_analysis": [
                "What do recent returns and trend measurements show?",
                "How large are realized volatility and drawdown?",
                "Which price-derived signals would invalidate the current setup?",
            ],
            "company_outlook": [
                "What is the current operating trajectory?",
                "Which triggers could improve or impair that trajectory?",
                "What would invalidate each scenario?",
            ],
            "event_impact": [
                "What changed and when?",
                "How can the event transmit to revenue, costs, or risk premium?",
                "What evidence would confirm or reject the impact?",
            ],
            "general_research": [
                "What is the current market and information state?",
                "Which evidence is most reliable and recent?",
                "Where do sources agree or conflict?",
            ],
        }[task_type]
        prompt = f"""
Plan focused research for this ordinary user question. Return only the requested JSON schema.
Propose only sources that materially help answer the question; do not select every source by default.
The source list is a proposal and will be checked by deterministic capability rules.

Source capabilities:
- yahoo_finance: current quote and market metadata
- google_news: fresh events, catalysts, and reporting
- price_history: returns, trend, volatility, and drawdown calculations
- financial_statements: SEC company fundamentals for supported US stocks
- polymarket: ongoing price markets for price forecasts, targets, or crowd expectations; no guarantee that a matching time horizon exists
- vector_db: similar historical news paragraphs already stored locally

User question: {state['user_query']}
Asset: {task['asset_name']} ({task['symbol']})
Asset type: {task['asset_type']}
Task type: {task_type}
Horizon: {task['horizon']}
Target price: {state.get('target_price') if state.get('target_price') is not None else 'not supplied'}
""".strip()
        llm_result = self.llm.generate_structured(
            prompt,
            SemanticPlan,
            model=state.get("model"),
            temperature=0.05,
        )
        semantic = llm_result.data if llm_result.success and llm_result.data else {}
        questions = [
            str(item).strip()
            for item in semantic.get("research_questions", [])
            if str(item).strip()
        ][:6] or fallback_questions
        source_selection = _select_research_sources(
            task,
            state["user_query"],
            state.get("target_price"),
            semantic.get("optional_sources", []),
        )
        if source_selection["price_only_request"]:
            questions = [
                f"What is the latest available price for {task['asset_name']} ({task['symbol']})?",
                "When was the quote retrieved and which source supplied it?",
            ]
        sources = source_selection["sources"]
        critical = ["market_price"]
        if "google_news" in sources:
            critical.append("news")
        if "price_history" in sources and (task_type in {"move_explanation", "technical_analysis"} or _price_forecast_requested(state["user_query"])):
            critical.append("price_history")
        if "financial_statements" in sources:
            critical.append("financial_statement")

        plan = {
            "task_type": task_type,
            "prediction_provider": state.get("prediction_provider", "coinrithm"),
            "sources": sources,
            "skipped_sources": source_selection["skipped_sources"],
            "route_signature": f"{task_type}: {' + '.join(sources)}",
            "source_rationale": source_selection["source_rationale"],
            "llm_suggested_sources": source_selection["llm_suggested_sources"],
            "critical_evidence_types": critical,
            "research_questions": questions,
            "parallel_groups": [
                [source for source in sources if source != "polymarket"],
                ["polymarket_after_price"] if "polymarket" in sources else [],
            ],
            "quantitative_analysis": True,
            "planning_mode": (
                "local_llm_proposal_with_rule_guardrails"
                if llm_result.success
                else "deterministic_relevance_rules"
            ),
            "price_only_request": source_selection["price_only_request"],
        }
        call = {
            "node": "plan_research",
            "model": llm_result.model,
            "success": llm_result.success,
            "latency_ms": llm_result.latency_ms,
            "purpose": "Propose focused research questions and candidate sources for the user's request",
            "input_manifest": {
                "user_query": state["user_query"],
                "task_type": task_type,
                "asset_type": task.get("asset_type"),
                "target_price": state.get("target_price"),
            },
            "prompt_preview": prompt,
            "usage": llm_result.usage,
            "output_manifest": {
                "llm_suggested_sources": source_selection["llm_suggested_sources"],
                "rule_accepted_sources": sources,
                "rule_skipped_sources": source_selection["skipped_sources"],
            },
            "error": llm_result.error,
        }
        return {
            "research_plan": plan,
            "llm_calls": [call],
            "decision_audit": [
                _decision(
                    "plan_research",
                    "local_llm_source_proposal_with_rule_guardrails",
                    ["LLM", "rules"],
                    {"task": task, "user_query": state["user_query"], "target_price": state.get("target_price")},
                    [
                        "Yahoo Finance is the minimum current-price anchor.",
                        "The LLM proposes sources but cannot bypass asset/task capability guards.",
                        "Polymarket is selected only for explicit target or crowd-probability needs.",
                        "Financial statements are selected only for relevant stock or ETF tasks.",
                        "Unselected sources remain visible as skipped rather than being called.",
                    ],
                    {
                        "selected_sources": plan["sources"],
                        "skipped_sources": plan["skipped_sources"],
                        "next_node": "collect_evidence",
                    },
                    {"research_plan": plan},
                    llm={
                        "used": True,
                        "model": llm_result.model,
                        "success": llm_result.success,
                        "purpose": call["purpose"],
                    },
                )
            ],
            "intermediate_results": [_intermediate("plan_research", {"research_plan": plan})],
            "node_trace": [
                _trace(
                    "plan_research",
                    started,
                    f"Selected {len(plan['sources'])} relevant sources and skipped {len(plan['skipped_sources'])} unrelated sources.",
                    {
                        "selected_sources": plan["sources"],
                        "skipped_sources": plan["skipped_sources"],
                        "critical": critical,
                        "planning_mode": plan["planning_mode"],
                    },
                )
            ],
        }

    def collect_evidence(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        result = self.tools.collect(
            state["task"],
            state["research_plan"],
            state["user_query"],
            state.get("target_price"),
            state.get("user_notes") or [],
            state.get("user_headlines") or [],
        )
        successful = sum(item.get("success", False) for item in result["source_status"].values())
        raw_summary = [
            {
                "kind": row.get("kind"),
                "success": bool((row.get("payload") or {}).get("success")),
                "record_count": len((row.get("payload") or {}).get("items") or [])
                if isinstance((row.get("payload") or {}).get("items"), list)
                else None,
            }
            for row in result["raw_evidence"]
        ]
        tool_names = [item.get("function") for item in result.get("tool_calls") or []]
        return {
            "raw_evidence": result["raw_evidence"],
            "source_status": result["source_status"],
            "tool_calls": result.get("tool_calls") or [],
            "retrieval_attempts": state.get("retrieval_attempts", 0),
            "targeted_queries": state.get("targeted_queries") or [],
            "decision_audit": [
                _decision(
                    "collect_evidence",
                    "parallel_tool_execution",
                    ["tools"],
                    {
                        "sources_requested": state["research_plan"].get("sources") or [],
                        "symbol": state["task"].get("symbol"),
                        "target_price": state.get("target_price"),
                    },
                    [
                        "Independent sources run in parallel.",
                        "Polymarket runs after current price is available.",
                        "Vector retrieval occurs before current news is persisted.",
                        "A failed optional source degrades status instead of aborting the graph.",
                    ],
                    {
                        "successful_operations": successful,
                        "total_operations": len(result["source_status"]),
                        "route": "normalize_and_evidence_gate",
                    },
                    {"source_status": result["source_status"], "raw_evidence": raw_summary},
                    tools=tool_names,
                )
            ],
            "intermediate_results": [
                _intermediate(
                    "collect_evidence",
                    {"source_status": result["source_status"], "raw_evidence": raw_summary},
                )
            ],
            "node_trace": [
                _trace(
                    "collect_evidence",
                    started,
                    f"Collected the first evidence round; {successful}/{len(result['source_status'])} source operations succeeded.",
                    {"source_status": result["source_status"]},
                )
            ],
        }

    def normalize_and_gate(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        evidence, groups = normalize_evidence(
            state.get("raw_evidence") or [],
            state["task"]["as_of"],
            [state["task"].get("symbol", ""), state["task"].get("asset_name", "")],
        )
        gate = evaluate_gate(
            evidence,
            state["research_plan"]["critical_evidence_types"],
            state.get("retrieval_attempts", 0),
            state.get("max_retries", 2),
        )
        next_step = (
            "targeted_retrieval" if gate["decision"] == "retrieve_more" else "financial_quantitative_analysis"
        )
        return {
            "normalized_evidence": evidence,
            "evidence_groups": groups,
            "evidence_gate": gate,
            "next_step": next_step,
            "decision_audit": [
                _decision(
                    "normalize_and_evidence_gate",
                    "deterministic_rule_gate",
                    ["rules", "Python"],
                    {
                        "raw_evidence_count": len(state.get("raw_evidence") or []),
                        "critical_evidence_types": state["research_plan"]["critical_evidence_types"],
                        "retrieval_attempts": state.get("retrieval_attempts", 0),
                        "max_retries": state.get("max_retries", 2),
                    },
                    [
                        "Normalize source-specific payloads into a common evidence schema.",
                        "Reject future-dated evidence and deduplicate repeated news.",
                        "Request targeted retrieval only when critical evidence is missing and retries remain.",
                    ],
                    {
                        "gate_decision": gate.get("decision"),
                        "missing_critical": gate.get("missing_critical") or [],
                        "route": next_step,
                    },
                    {
                        "evidence_gate": gate,
                        "evidence_count": len(evidence),
                        "news_group_count": len(groups),
                    },
                )
            ],
            "intermediate_results": [
                _intermediate(
                    "normalize_and_evidence_gate",
                    {
                        "evidence_gate": gate,
                        "evidence": _evidence_snapshot(evidence),
                        "news_groups": groups[:10],
                    },
                )
            ],
            "node_trace": [
                _trace(
                    "normalize_and_evidence_gate",
                    started,
                    f"Normalized {len(evidence)} evidence items; gate decision: {gate['decision']}.",
                    {"gate": gate, "news_groups": len(groups)},
                    "degraded" if gate.get("degraded") else "completed",
                )
            ],
        }

    def targeted_retrieval(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        attempt = state.get("retrieval_attempts", 0) + 1
        missing = list((state.get("evidence_gate") or {}).get("missing_critical") or [])
        verification_sources = list((state.get("verification") or {}).get("requested_sources") or [])
        source_map = {
            "market_price": "yahoo_finance",
            "news": "google_news",
            "financial_statement": "financial_statements",
            "price_history": "price_history",
            "historical_news": "vector_db",
            "crowd_expectation": "polymarket",
        }
        fallback_searches = []
        for evidence_type in missing:
            source = source_map.get(evidence_type)
            if source:
                fallback_searches.append(
                    {
                        "source": source,
                        "query": f"{state['task']['asset_name']} {state['task']['symbol']} {evidence_type.replace('_', ' ')} {state['task']['horizon']}",
                        "reason": f"The evidence gate is missing {evidence_type}.",
                    }
                )
        for source in verification_sources:
            if source in source_map.values() and source not in {row["source"] for row in fallback_searches}:
                fallback_searches.append(
                    {
                        "source": source,
                        "query": f"{state['task']['asset_name']} latest catalyst risk outlook",
                        "reason": "Verification found an unsupported or weak thesis link.",
                    }
                )
        if not fallback_searches:
            fallback_searches.append(
                {
                    "source": "google_news",
                    "query": f"{state['task']['asset_name']} latest catalyst risk financial outlook",
                    "reason": "Verification requested stronger causal evidence.",
                }
            )

        prompt = f"""
Generate focused retrieval queries for missing evidence. Use only these source values:
yahoo_finance, google_news, polymarket, vector_db, financial_statements, price_history, user_context.
Do not request sources unrelated to the listed gaps. Return at most five searches.

Asset: {state['task']['asset_name']} ({state['task']['symbol']})
Task: {state['task']['task_type']}
Horizon: {state['task']['horizon']}
Missing evidence: {missing}
Verification requested sources: {verification_sources}
Research questions: {state['research_plan']['research_questions']}
""".strip()
        llm_result = self.llm.generate_structured(
            prompt,
            TargetedSearchPlan,
            model=state.get("model"),
            temperature=0.1,
        )
        searches = llm_result.data.get("searches", []) if llm_result.success and llm_result.data else []
        allowed = {row["source"] for row in fallback_searches}
        searches = [row for row in searches if row.get("source") in allowed]
        for fallback in fallback_searches:
            if fallback["source"] not in {row.get("source") for row in searches}:
                searches.append(fallback)
        searches = searches[:5]
        for search in searches:
            if search.get("source") == "polymarket":
                search["provider"] = state.get("prediction_provider", "coinrithm")
        retrieval = self.tools.targeted_retrieval(
            state["task"],
            searches,
            state.get("raw_evidence") or [],
            state.get("target_price"),
            attempt,
        )
        statuses = dict(state.get("source_status") or {})
        statuses.update(retrieval["source_status"])
        call = {
            "node": "targeted_retrieval",
            "model": llm_result.model,
            "success": llm_result.success,
            "latency_ms": llm_result.latency_ms,
            "purpose": "Generate constrained search queries for explicit evidence gaps",
            "input_manifest": {"missing_evidence": missing, "verification_sources": verification_sources},
            "prompt_preview": prompt,
            "usage": llm_result.usage,
            "error": llm_result.error,
        }
        return {
            "raw_evidence": retrieval["raw_evidence"],
            "source_status": statuses,
            "tool_calls": retrieval.get("tool_calls") or [],
            "retrieval_attempts": attempt,
            "targeted_queries": (state.get("targeted_queries") or []) + searches,
            "llm_calls": [call],
            "decision_audit": [
                _decision(
                    "targeted_retrieval",
                    "llm_constrained_by_rules_then_tools",
                    ["LLM", "rules", "tools"],
                    {
                        "missing_evidence": missing,
                        "verification_requested_sources": verification_sources,
                        "attempt": attempt,
                    },
                    [
                        "The LLM may only choose source types tied to an explicit evidence gap.",
                        "Unsupported LLM source choices are discarded.",
                        "Deterministic fallback queries fill any source the LLM omitted.",
                        "The next route always returns to normalization and the evidence gate.",
                    ],
                    {"searches": searches, "route": "normalize_and_evidence_gate"},
                    {
                        "source_status": retrieval["source_status"],
                        "total_raw_evidence": len(retrieval["raw_evidence"]),
                    },
                    tools=[item.get("function") for item in retrieval.get("tool_calls") or []],
                    llm={
                        "used": True,
                        "model": llm_result.model,
                        "success": llm_result.success,
                        "purpose": call["purpose"],
                    },
                )
            ],
            "intermediate_results": [
                _intermediate(
                    "targeted_retrieval",
                    {
                        "attempt": attempt,
                        "searches": searches,
                        "source_status": retrieval["source_status"],
                        "total_raw_evidence": len(retrieval["raw_evidence"]),
                    },
                )
            ],
            "node_trace": [
                _trace(
                    "targeted_retrieval",
                    started,
                    f"Completed targeted retrieval attempt {attempt} with {len(searches)} constrained queries.",
                    {"queries": searches, "source_status": retrieval["source_status"]},
                )
            ],
        }

    def financial_quantitative_analysis(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        analysis = analyze_quantitatively(
            state.get("raw_evidence") or [],
            state.get("normalized_evidence") or [],
            state.get("target_price"),
            query=state.get("user_query", ""),
            target_condition=state.get("target_condition", "auto"),
            horizon=(state.get("task") or {}).get("horizon", ""),
        )
        return {
            "quantitative_analysis": analysis,
            "decision_audit": [
                _decision(
                    "financial_quantitative_analysis",
                    "deterministic_python",
                    ["Python"],
                    {
                        "raw_evidence_count": len(state.get("raw_evidence") or []),
                        "normalized_evidence_count": len(state.get("normalized_evidence") or []),
                        "target_price": state.get("target_price"),
                    },
                    [
                        "Returns, volatility, growth, margins, target distance, and probability statistics are calculated in Python.",
                        "The LLM is not allowed to calculate or invent these values.",
                    ],
                    {"signal_count": len(analysis["signals"]), "route": "generate_thesis_graph"},
                    {"quantitative_analysis": analysis},
                )
            ],
            "intermediate_results": [
                _intermediate("financial_quantitative_analysis", {"quantitative_analysis": analysis})
            ],
            "node_trace": [
                _trace(
                    "financial_quantitative_analysis",
                    started,
                    f"Calculated {len(analysis['signals'])} deterministic financial and market signals.",
                    {"calculation_mode": analysis["calculation_mode"], "signals": analysis["signals"]},
                )
            ],
        }

    def generate_thesis_graph(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        prompt_evidence = _evidence_for_prompt(state.get("normalized_evidence") or [])
        prompt = f"""
Build a dual-branch financial thesis graph using only the supplied evidence and deterministic calculations.
Every factual chain step must cite valid evidence IDs from the bundle. Do not invent IDs or facts.
Yahoo price momentum is context, never a fundamental cause.
Polymarket is expectation evidence, never confirmation that an event will happen.
Do not fabricate bearish evidence for symmetry. Downside status must be one of ACTIVE, VULNERABILITY,
CONDITIONAL, NOT_SUPPORTED, and may be NOT_SUPPORTED when the evidence does not support it.
Write current_view as a concise explanation, not a one-word directional label.
For technical analysis, explain the supplied period return, realized volatility, and maximum drawdown
when available. These are historical measurements, not forecasts or evidence of a causal catalyst.
Keep why to at most four concise reasons. State what future evidence could change the view.
Add a compact decision_brief that contributes new synthesis instead of repeating current_view.
Its key insight should identify the most decision-relevant tension, asymmetry, or leading indicator.
Add at most three watch_items with a specific observable signal, why it matters, what would confirm
the interpretation, and what would invalidate it. Cite real evidence IDs for the key insight and every
watch item. Keep next_research_action to one concrete sentence. Avoid generic advice and boilerplate.
Do not discuss a data source or probabilities absent from the evidence bundle, even as boilerplate.
When target_assessment is supplied, obey its direction and current condition status.
The structured target overrides any stale target number in the user query.
An already-met current threshold is not difficult to reach now, but does not guarantee future settlement.
For an extreme target request confirmation, not a made-up probability. Do not assign difficulty from distance alone.
Nearby market odds are context only, not the probability of the requested target unless all contract terms match.

Task: {json.dumps(state['task'], ensure_ascii=False)}
User query: {state['user_query']}
Evidence gate: {json.dumps(state.get('evidence_gate') or {}, ensure_ascii=False)}
Deterministic quantitative analysis: {json.dumps(state.get('quantitative_analysis') or {}, ensure_ascii=False)}
Evidence bundle: {json.dumps(prompt_evidence, ensure_ascii=False)}
""".strip()
        result = self.llm.generate_structured(
            prompt,
            ThesisGraph,
            model=state.get("model"),
            temperature=state.get("temperature", 0.15),
        )
        raw_thesis = result.data if result.success and result.data else self._fallback_thesis(state)
        thesis = self._repair_thesis(raw_thesis, state)
        target = (state.get("quantitative_analysis") or {}).get("target_assessment")
        thesis = enforce_target_summary(thesis, target)
        repair_applied = thesis != raw_thesis
        call = {
            "node": "generate_thesis_graph",
            "model": result.model,
            "success": result.success,
            "latency_ms": result.latency_ms,
            "purpose": "Generate evidence-linked upside and downside transmission chains",
            "input_manifest": {
                "evidence_ids": [item["id"] for item in prompt_evidence],
                "history_chunk_ids": [item["id"] for item in prompt_evidence if item["type"] == "historical_news"],
                "quantitative_sections": list((state.get("quantitative_analysis") or {}).keys()),
            },
            "prompt_preview": prompt,
            "usage": result.usage,
            "error": result.error,
        }
        return {
            "thesis_graph": thesis,
            "llm_calls": [call],
            "decision_audit": [
                _decision(
                    "generate_thesis_graph",
                    "llm_structured_output_with_rule_repair",
                    ["LLM", "rules"],
                    {
                        "task": state["task"],
                        "evidence_gate": state.get("evidence_gate") or {},
                        "quantitative_analysis": state.get("quantitative_analysis") or {},
                        "evidence_bundle": prompt_evidence,
                    },
                    [
                        "Every factual chain step must cite an evidence ID in the supplied bundle.",
                        "Yahoo momentum cannot be asserted as a fundamental cause.",
                        "Polymarket probabilities are expectations, not confirmed facts.",
                        "Empty or source-incompatible LLM branches are replaced deterministically.",
                    ],
                    {
                        "llm_success": result.success,
                        "repair_applied": repair_applied,
                        "target_summary_guard_applied": bool(target),
                        "route": "verify_and_calibrate",
                    },
                    {"thesis_graph": thesis},
                    llm={
                        "used": True,
                        "model": result.model,
                        "success": result.success,
                        "purpose": call["purpose"],
                    },
                )
            ],
            "intermediate_results": [
                _intermediate(
                    "generate_thesis_graph",
                    {
                        "llm_raw_output": raw_thesis,
                        "rule_repair_applied": repair_applied,
                        "post_repair_thesis": thesis,
                    },
                )
            ],
            "node_trace": [
                _trace(
                    "generate_thesis_graph",
                    started,
                    f"Generated upside and downside branches using {result.model if result.success else 'deterministic fallback'}.",
                    {"llm_success": result.success, "downside_status": thesis.get("downside", {}).get("status")},
                    "completed" if result.success else "degraded",
                )
            ],
        }

    def _repair_thesis(self, thesis: Dict[str, Any], state: AgentState) -> Dict[str, Any]:
        """Replace structurally empty or source-incompatible LLM branches."""
        repaired = copy.deepcopy(thesis)
        fallback = self._fallback_thesis(state)
        evidence = state.get("normalized_evidence") or []
        evidence_by_id = {item["id"]: item for item in evidence}
        present_types = {item["evidence_type"] for item in evidence}

        semantic_requirements = {
            "crowd_expectation": ("polymarket", "prediction market", "crowd probability", "implied probability"),
            "financial_statement": ("reported revenue", "reported profit", "reported margin", "sec filing", "earnings per share"),
        }

        def branch_needs_replacement(branch: Dict[str, Any]) -> bool:
            chain = branch.get("chain") or []
            valid_link_count = sum(
                1
                for step in chain
                for evidence_id in step.get("evidence_ids") or []
                if evidence_id in evidence_by_id
            )
            branch_text = json.dumps(branch, ensure_ascii=False).lower()
            for evidence_type, phrases in semantic_requirements.items():
                if evidence_type not in present_types and any(phrase in branch_text for phrase in phrases):
                    return True
            status = branch.get("status")
            return not valid_link_count and status not in {"NOT_SUPPORTED", "CONDITIONAL"}

        for branch_name in ("upside", "downside"):
            branch = repaired.get(branch_name) or {}
            fallback_branch = copy.deepcopy(fallback[branch_name])
            if branch_needs_replacement(branch):
                fallback_branch["title"] = branch.get("title") or fallback_branch["title"]
                repaired[branch_name] = fallback_branch
                continue
            branch["supporting_evidence_ids"] = [
                evidence_id
                for evidence_id in branch.get("supporting_evidence_ids") or []
                if evidence_id in evidence_by_id
            ]
            for field in ("weak_links", "triggers", "invalidation_conditions"):
                if not branch.get(field):
                    branch[field] = fallback_branch.get(field) or []
            repaired[branch_name] = branch

        def mentions_missing_source(text):
            return any(
                evidence_type not in present_types and any(phrase in str(text).lower() for phrase in phrases)
                for evidence_type, phrases in semantic_requirements.items()
            )

        for field in ("current_view", "future_expectation"):
            if not repaired.get(field) or mentions_missing_source(repaired[field]):
                repaired[field] = fallback[field]
        repaired["why"] = [reason for reason in repaired.get("why") or [] if not mentions_missing_source(reason)] or fallback["why"]
        brief = repaired.get("decision_brief") or {}
        brief_ids = [item for item in brief.get("evidence_ids") or [] if item in evidence_by_id]
        watch_items = []
        for item in brief.get("watch_items") or []:
            valid_ids = [evidence_id for evidence_id in item.get("evidence_ids") or [] if evidence_id in evidence_by_id]
            if valid_ids:
                watch_items.append({**item, "evidence_ids": valid_ids})
        if not brief.get("key_insight") or not brief_ids:
            repaired["decision_brief"] = copy.deepcopy(fallback["decision_brief"])
        else:
            brief["evidence_ids"] = brief_ids
            brief["watch_items"] = watch_items or copy.deepcopy(fallback["decision_brief"]["watch_items"])
            brief["next_research_action"] = brief.get("next_research_action") or fallback["decision_brief"]["next_research_action"]
            repaired["decision_brief"] = brief
        return repaired

    def _fallback_thesis(self, state: AgentState) -> Dict[str, Any]:
        evidence = state.get("normalized_evidence") or []
        positive = sorted(
            [item for item in evidence if item.get("direction") == 1],
            key=lambda item: -float(item.get("reliability") or 0),
        )
        negative = sorted(
            [item for item in evidence if item.get("direction") == -1],
            key=lambda item: -float(item.get("reliability") or 0),
        )
        balance = (state.get("quantitative_analysis") or {}).get("source_consistency", {}).get("directional_balance", 0)
        view = "Evidence is constructive but not conclusive." if balance > 0.2 else "Evidence is cautious and risk-weighted." if balance < -0.2 else "Evidence is mixed, so a strong directional conclusion is not justified."
        why = [item["title"] for item in (positive + negative)[:4]]
        future = "Future direction depends on whether the listed triggers confirm one branch and invalidate the other."
        if (state.get("task") or {}).get("task_type") == "technical_analysis":
            market = (state.get("quantitative_analysis") or {}).get("market") or {}
            measurements = [
                f"{label}: {market[key]:.3f}%."
                for key, label in (
                    ("period_return_pct", "Retrieved-history return"),
                    ("annualized_volatility_pct", "Annualized realized volatility"),
                    ("max_drawdown_pct", "Close-to-close maximum drawdown"),
                ) if isinstance(market.get(key), (int, float))
            ]
            if measurements:
                view = " ".join(measurements) + " These describe historical performance, not a forecast."
                why = measurements
            future = "Watch whether subsequent price observations extend or reverse the historical trend. Price history alone cannot establish a future catalyst or a reliable price forecast."

        def branch(items, title, scenario):
            chain = []
            for item in items[:3]:
                evidence_type = item["evidence_type"]
                mechanism = {
                    "financial_statement": "The reported operating change can transmit through revenue, margins, cash flow, and valuation expectations.",
                    "news": "If the reported event is confirmed, it can alter demand, costs, execution expectations, or the risk premium.",
                    "market_price": "The move shows current positioning and reaction, but does not establish a fundamental cause.",
                    "price_history": "Historical behavior frames magnitude and risk, but does not establish a fundamental cause.",
                    "crowd_expectation": "Prediction-market pricing indicates expectations only, not a confirmed outcome.",
                    "historical_news": "The prior paragraph provides precedent, subject to changed conditions and recency limits.",
                }.get(evidence_type, "The evidence can affect expectations if independently confirmed.")
                chain.append(
                    {
                        "claim": item["title"],
                        "evidence_ids": [item["id"]],
                        "transmission_mechanism": mechanism,
                    }
                )
            ids = [item["id"] for item in items[:3]]
            return {
                "title": title,
                "scenario": scenario,
                "chain": chain,
                "supporting_evidence_ids": ids,
                "weak_links": ["Causal attribution remains conditional on independent confirmation."],
                "triggers": ["New financial disclosures or fresh company-specific news"],
                "invalidation_conditions": ["Subsequent primary-source evidence contradicts the cited chain"],
            }

        upside = branch(positive, "Upside thesis", "Supportive evidence persists and transmits into stronger expectations.")
        downside = branch(negative, "Downside thesis", "Negative evidence persists and transmits into weaker expectations.")
        volatility = (state.get("quantitative_analysis") or {}).get("market", {}).get("annualized_volatility_pct")
        if len(negative) >= 2:
            downside["status"] = "ACTIVE"
        elif negative:
            downside["status"] = "VULNERABILITY"
        elif isinstance(volatility, (int, float)) and volatility >= 50:
            downside["status"] = "CONDITIONAL"
            downside["scenario"] = "High realized volatility creates a conditional drawdown vulnerability, but no direct bearish catalyst was collected."
            downside["chain"] = []
            downside["supporting_evidence_ids"] = []
        else:
            downside["status"] = "NOT_SUPPORTED"
            downside["scenario"] = "No direct downside evidence was collected; this branch is intentionally not asserted."
            downside["chain"] = []
            downside["supporting_evidence_ids"] = []
        ranked = positive + negative
        lead = ranked[0] if ranked else None
        key_insight = (
            f"The most decision-relevant evidence is: {lead['title']}"
            if lead
            else "No evidence-backed leading indicator is available yet."
        )
        watch_items = []
        for item in ranked[:3]:
            watch_items.append(
                {
                    "signal": item["title"],
                    "why_it_matters": "A fresh change in this evidence can shift the balance between the upside and downside scenarios.",
                    "confirm_if": "A newer independent source confirms the same direction and transmission mechanism.",
                    "invalidate_if": "A newer primary or higher-reliability source contradicts the cited evidence.",
                    "evidence_ids": [item["id"]],
                }
            )
        decision_brief = {
            "key_insight": key_insight,
            "why_it_matters": "It has the highest available reliability among the directional evidence collected for this request." if lead else "More task-specific evidence is required before drawing a decision-relevant conclusion.",
            "evidence_ids": [lead["id"]] if lead else [],
            "watch_items": watch_items,
            "next_research_action": (
                "Refresh the highest-priority evidence after the next material company, market, or filing update."
                if lead
                else "Retrieve the missing critical evidence type before extending the conclusion."
            ),
        }
        return {
            "current_view": view,
            "why": why,
            "future_expectation": future,
            "upside": upside,
            "downside": downside,
            "decision_brief": decision_brief,
        }

    def verify_and_calibrate(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        thesis = copy.deepcopy(state.get("thesis_graph") or {})
        evidence = state.get("normalized_evidence") or []
        evidence_by_id = {item["id"]: item for item in evidence}
        issues: List[Dict[str, Any]] = []
        numeric_issues = _unverified_numeric_paths(thesis, state)
        if numeric_issues:
            fallback = self._fallback_thesis(state)
            affected = {item["path"].split(".", 1)[0].split("[", 1)[0] for item in numeric_issues}
            for field in affected:
                if field in {"upside", "downside", "current_view", "future_expectation", "why", "decision_brief"}:
                    thesis[field] = copy.deepcopy(fallback[field])
            issues.append(
                {
                    "type": "unverified_numeric_claim_repaired",
                    "details": numeric_issues,
                }
            )
        referenced: List[str] = []
        total_references = 0

        for branch_name in ("upside", "downside"):
            branch = thesis.get(branch_name) or {}
            cleaned_steps = []
            for step in branch.get("chain") or []:
                original_ids = list(step.get("evidence_ids") or [])
                total_references += len(original_ids)
                valid_ids = [evidence_id for evidence_id in original_ids if evidence_id in evidence_by_id]
                referenced.extend(valid_ids)
                if len(valid_ids) != len(original_ids):
                    issues.append({"type": "invalid_evidence_id", "branch": branch_name, "claim": step.get("claim")})
                if not valid_ids:
                    issues.append({"type": "unsupported_claim", "branch": branch_name, "claim": step.get("claim")})
                    continue
                step["evidence_ids"] = valid_ids
                source_types = {evidence_by_id[item]["evidence_type"] for item in valid_ids}
                causal_language = f"{step.get('claim', '')} {step.get('transmission_mechanism', '')}".lower()
                if source_types <= {"market_price", "price_history"} and _asserts_price_causality(causal_language):
                    issues.append({"type": "momentum_used_as_fundamental_cause", "branch": branch_name, "claim": step.get("claim")})
                if "crowd_expectation" in source_types and re.search(r"\b(proves|confirms|certain|will happen|guarantees)\b", causal_language):
                    issues.append({"type": "polymarket_treated_as_fact", "branch": branch_name, "claim": step.get("claim")})
                cleaned_steps.append(step)
            branch["chain"] = cleaned_steps
            branch["supporting_evidence_ids"] = [
                item for item in branch.get("supporting_evidence_ids") or [] if item in evidence_by_id
            ]
            thesis[branch_name] = branch

        brief = thesis.get("decision_brief") or {}
        brief_original_ids = list(brief.get("evidence_ids") or [])
        total_references += len(brief_original_ids)
        brief_valid_ids = [item for item in brief_original_ids if item in evidence_by_id]
        referenced.extend(brief_valid_ids)
        if len(brief_valid_ids) != len(brief_original_ids):
            issues.append({"type": "invalid_evidence_id", "section": "decision_brief", "claim": brief.get("key_insight")})
        if brief.get("key_insight") and not brief_valid_ids:
            issues.append({"type": "unsupported_claim", "section": "decision_brief", "claim": brief.get("key_insight")})
        brief["evidence_ids"] = brief_valid_ids
        cleaned_watch_items = []
        for item in brief.get("watch_items") or []:
            original_ids = list(item.get("evidence_ids") or [])
            total_references += len(original_ids)
            valid_ids = [evidence_id for evidence_id in original_ids if evidence_id in evidence_by_id]
            referenced.extend(valid_ids)
            if len(valid_ids) != len(original_ids):
                issues.append({"type": "invalid_evidence_id", "section": "watch_item", "claim": item.get("signal")})
            if not valid_ids:
                issues.append({"type": "unsupported_claim", "section": "watch_item", "claim": item.get("signal")})
                continue
            cleaned_watch_items.append({**item, "evidence_ids": valid_ids})
        brief["watch_items"] = cleaned_watch_items
        thesis["decision_brief"] = brief

        negative = [item for item in evidence if item.get("direction") == -1 and item.get("temporal_valid", True)]
        fresh_negative = [item for item in negative if item.get("freshness") in {"fresh", "current"}]
        downside = thesis.get("downside") or {}
        volatility = (state.get("quantitative_analysis") or {}).get("market", {}).get("annualized_volatility_pct")
        if len(fresh_negative) >= 2 and any(item.get("reliability", 0) >= 0.7 for item in fresh_negative):
            downside["status"] = "ACTIVE"
        elif negative:
            downside["status"] = "VULNERABILITY"
        elif isinstance(volatility, (int, float)) and volatility >= 50:
            downside["status"] = "CONDITIONAL"
            downside["chain"] = []
            downside["supporting_evidence_ids"] = []
        else:
            downside["status"] = "NOT_SUPPORTED"
            downside["chain"] = []
            downside["supporting_evidence_ids"] = []
        thesis["downside"] = downside

        target = (state.get("quantitative_analysis") or {}).get("target_assessment")
        target_repaired = enforce_target_summary(thesis, target)
        if target_repaired != thesis:
            issues.append({"type": "target_summary_repaired", "status": target.get("status")})
        thesis = target_repaired

        temporal_issues = [item["id"] for item in evidence if not item.get("temporal_valid", True)]
        if temporal_issues:
            issues.append({"type": "future_dated_evidence", "evidence_ids": temporal_issues})

        quality_values = [float(item.get("reliability") or 0) for item in evidence]
        evidence_quality = round(sum(quality_values) / len(quality_values) * 100) if quality_values else 0
        completeness = int((state.get("evidence_gate") or {}).get("coverage_pct") or 0)
        valid_reference_ratio = len(referenced) / max(1, total_references)
        chain_support = round(valid_reference_ratio * (0.55 + evidence_quality / 100 * 0.45) * 100)
        positive_count = sum(item.get("direction") == 1 for item in evidence)
        negative_count = sum(item.get("direction") == -1 for item in evidence)
        directional = positive_count + negative_count
        conflict = round(2 * min(positive_count, negative_count) / directional * 100) if directional else 0
        composite = chain_support * 0.4 + evidence_quality * 0.3 + completeness * 0.3 - conflict * 0.15
        quality_label = "strong" if composite >= 78 else "moderate" if composite >= 58 else "guarded" if composite >= 38 else "low"
        conflicts = []
        if positive_count and negative_count:
            conflicts.append(f"Evidence contains {positive_count} positive and {negative_count} negative directional items.")
        if conflict >= 60:
            conflicts.append("Directional evidence is highly balanced; confidence is reduced.")

        retry_allowed = state.get("retrieval_attempts", 0) < state.get("max_retries", 2)
        unsupported = any(issue["type"] == "unsupported_claim" for issue in issues)
        gate_missing = (state.get("evidence_gate") or {}).get("missing_critical") or []
        needs_more = retry_allowed and (bool(gate_missing) or unsupported)
        requested_sources = []
        if gate_missing:
            mapping = {"market_price": "yahoo_finance", "news": "google_news", "financial_statement": "financial_statements", "price_history": "price_history"}
            requested_sources.extend(mapping[item] for item in gate_missing if item in mapping)
        if unsupported and "google_news" not in requested_sources:
            requested_sources.append("google_news")

        scores = {
            "chain_support_confidence": max(0, min(100, chain_support)),
            "evidence_quality": max(0, min(100, evidence_quality)),
            "evidence_completeness": max(0, min(100, completeness)),
            "evidence_conflict": max(0, min(100, conflict)),
            "quality_label": quality_label,
            "explanation": {
                "chain_support_confidence": "Share of thesis references that resolve to real evidence IDs, adjusted for evidence reliability.",
                "evidence_quality": "Mean source reliability across normalized evidence.",
                "evidence_completeness": "Coverage of task-specific critical evidence types.",
                "evidence_conflict": "Directional disagreement; a higher value means more conflict, not better quality.",
            },
        }
        verification = {
            "passed": not any(issue["type"] in {"unsupported_claim", "invalid_evidence_id"} for issue in issues),
            "issues": issues,
            "scores": scores,
            "conflicts": conflicts,
            "needs_more_evidence": needs_more,
            "requested_sources": list(dict.fromkeys(requested_sources)),
            "reference_stats": {
                "total_mentions": total_references,
                "valid_mentions": len(referenced),
                "invalid_mentions": max(0, total_references - len(referenced)),
                "invalid_reference_rate_pct": round(
                    max(0, total_references - len(referenced)) / total_references * 100,
                    2,
                ) if total_references else None,
            },
            "numeric_stats": {
                "unsupported_numeric_claims_detected": len(numeric_issues),
                "method": "Currency and percentage claims are checked against deterministic calculations and normalized evidence within a bounded tolerance.",
            },
            "checks": {
                "evidence_ids_exist": not any(issue["type"] == "invalid_evidence_id" for issue in issues),
                "claims_have_support": not unsupported,
                "chronology_valid": not temporal_issues,
                "yahoo_not_causal": not any(issue["type"] == "momentum_used_as_fundamental_cause" for issue in issues),
                "polymarket_is_expectation": not any(issue["type"] == "polymarket_treated_as_fact" for issue in issues),
                "numbers_calculated_by_python": not _unverified_numeric_paths(thesis, state),
                "target_condition_consistent": not target or thesis.get("current_view") == target["summary"],
            },
        }
        verification_route = "targeted_retrieval" if needs_more else "build_report"
        return {
            "thesis_graph": thesis,
            "verification": verification,
            "next_step": verification_route,
            "decision_audit": [
                _decision(
                    "verify_and_calibrate",
                    "deterministic_rules_and_python_checks",
                    ["rules", "Python"],
                    {
                        "thesis_graph": state.get("thesis_graph") or {},
                        "available_evidence_ids": list(evidence_by_id),
                        "evidence_gate": state.get("evidence_gate") or {},
                        "retrieval_attempts": state.get("retrieval_attempts", 0),
                    },
                    [
                        "Remove claims whose evidence IDs do not resolve.",
                        "Repair unsupported numeric claims from deterministic evidence.",
                        "Reject causal misuse of price momentum and factual misuse of Polymarket.",
                        "Request more evidence only when a support gap exists and retry budget remains.",
                    ],
                    {
                        "passed": verification["passed"],
                        "issue_count": len(issues),
                        "quality_label": quality_label,
                        "route": verification_route,
                    },
                    {"verification": verification, "post_verification_thesis": thesis},
                )
            ],
            "intermediate_results": [
                _intermediate(
                    "verify_and_calibrate",
                    {"verification": verification, "post_verification_thesis": thesis},
                )
            ],
            "node_trace": [
                _trace(
                    "verify_and_calibrate",
                    started,
                    f"Ran thesis checks; quality is {quality_label} and next step is {'retrieval' if needs_more else 'report'}.",
                    {"scores": scores, "issue_count": len(issues), "needs_more_evidence": needs_more},
                    "degraded" if issues else "completed",
                )
            ],
        }

    def build_report(self, state: AgentState) -> Dict[str, Any]:
        started = time.perf_counter()
        evidence = state.get("normalized_evidence") or []
        raw = state.get("raw_evidence") or []
        price = next(
            (
                row.get("payload")
                for row in reversed(raw)
                if row.get("kind") == "market_price" and (row.get("payload") or {}).get("success")
            ),
            {},
        )
        history_chunks = [item for item in evidence if item.get("evidence_type") == "historical_news"][:3]
        failures = [
            {"source": name, "status": item.get("status"), "error": item.get("error")}
            for name, item in (state.get("source_status") or {}).items()
            if item.get("status") != "success"
        ]
        limitations = []
        if (state.get("evidence_gate") or {}).get("degraded"):
            limitations.append((state.get("evidence_gate") or {}).get("reason"))
        limitations.extend(
            f"{item['source']}: {item['error'] or item['status']}" for item in failures
        )
        for row in raw:
            payload = row.get("payload") or {}
            limitations.extend(
                f"{payload.get('source', row.get('kind'))}: {limitation}"
                for limitation in payload.get("limitations") or []
                if isinstance(limitation, str)
            )
        if any(not call.get("success") for call in state.get("llm_calls") or []):
            limitations.append("At least one Ollama step failed and used a deterministic fallback.")
        report = {
            "run_id": state["run_id"],
            "conversation_id": state["conversation_id"],
            "generated_at": utc_now_iso(),
            "task_summary": {
                "query": state["user_query"],
                "task_type": state["task"]["task_type"],
                "asset_name": state["task"]["asset_name"],
                "symbol": state["task"]["symbol"],
                "asset_type": state["task"]["asset_type"],
                "horizon": state["task"]["horizon"],
                "as_of": state["task"]["as_of"],
                "requirements": state["task"].get("important_requirements") or [],
                "interpretation_mode": state["task"].get("classification_mode"),
                "interpretation_explanation": state["task"].get("classification_explanation"),
            },
            "current_situation": {
                "view": (state.get("thesis_graph") or {}).get("current_view"),
                "why": (state.get("thesis_graph") or {}).get("why") or [],
                "price": price.get("price"),
                "currency": price.get("currency"),
                "price_timestamp": price.get("timestamp"),
                "market_state": price.get("market_state"),
                "target_price": state.get("target_price"),
            },
            "quantitative_signals": state.get("quantitative_analysis") or {},
            "target_assessment": (state.get("quantitative_analysis") or {}).get("target_assessment"),
            "upside_thesis": (state.get("thesis_graph") or {}).get("upside") or {},
            "downside_thesis": (state.get("thesis_graph") or {}).get("downside") or {},
            "future_expectation": (state.get("thesis_graph") or {}).get("future_expectation"),
            "decision_brief": (state.get("thesis_graph") or {}).get("decision_brief") or {},
            "confidence_scores": (state.get("verification") or {}).get("scores") or {},
            "evidence_conflicts": (state.get("verification") or {}).get("conflicts") or [],
            "verification": state.get("verification") or {},
            "evidence_gate": state.get("evidence_gate") or {},
            "evidence": evidence,
            "history_chunks_used": history_chunks,
            "targeted_queries": state.get("targeted_queries") or [],
            "research_plan": state.get("research_plan") or {},
            "data_source_status": state.get("source_status") or {},
            "limitations": list(dict.fromkeys(item for item in limitations if item)),
            "llm": {
                "selected_model": state.get("model"),
                "calls": state.get("llm_calls") or [],
                "input_explanation": "The thesis LLM receives the classified task, evidence gate, deterministic quantitative results, current normalized evidence, and up to three vector-retrieved historical paragraphs. Raw external data not present in that bundle is not available to the model.",
            },
            "workflow": {
                "framework": "LangGraph",
                "node_trace": state.get("node_trace") or [],
                "retrieval_attempts": state.get("retrieval_attempts", 0),
                "nodes": [
                    "Understand Request", "Plan Research", "Collect Evidence",
                    "Normalize and Evidence Gate", "Targeted Retrieval",
                    "Financial and Quantitative Analysis", "Generate Thesis Graph",
                    "Verify and Calibrate", "Build Report",
                ],
            },
            "agent_proof": {
                "framework": "langgraph.graph.StateGraph",
                "actual_nodes_executed": [
                    item.get("node") for item in state.get("node_trace") or []
                ],
                "decision_records": len(state.get("decision_audit") or []),
                "intermediate_records": len(state.get("intermediate_results") or []),
                "tool_calls": len(state.get("tool_calls") or []),
                "observable_scope": "The application records node inputs, explicit rules, next workflow nodes, structured outputs, and tool results. A next node is workflow progression, not the user's intent. Hidden token-by-token chain-of-thought is not requested or exposed.",
            },
            "agent_audit": {
                "decision_audit": state.get("decision_audit") or [],
                "intermediate_results": state.get("intermediate_results") or [],
                "tool_calls": state.get("tool_calls") or [],
            },
            "disclaimer": "Research output only. Prediction-market probabilities are expectations, and this report is not financial advice.",
        }
        report_summary = {
            "run_id": state["run_id"],
            "evidence_count": len(evidence),
            "history_chunk_count": len(history_chunks),
            "quality_label": ((state.get("verification") or {}).get("scores") or {}).get("quality_label"),
            "report_sections": list(report.keys()),
        }
        return {
            "report": report,
            "decision_audit": [
                _decision(
                    "build_report",
                    "deterministic_report_template",
                    ["Python"],
                    {
                        "verified_thesis": state.get("thesis_graph") or {},
                        "verification": state.get("verification") or {},
                        "evidence_count": len(evidence),
                    },
                    [
                        "Only the verified thesis is rendered.",
                        "Source failures and evidence conflicts remain visible as limitations.",
                        "All Agent audit records are persisted with the final run.",
                    ],
                    {"route": "END", "report_generated": True},
                    report_summary,
                )
            ],
            "intermediate_results": [_intermediate("build_report", report_summary)],
            "node_trace": [
                _trace(
                    "build_report",
                    started,
                    f"Built the structured report with {len(evidence)} evidence records and {len(history_chunks)} historical chunks.",
                )
            ],
        }
