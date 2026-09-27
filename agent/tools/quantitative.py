import math
import statistics
from typing import Any, Dict, List, Optional

from .targets import target_assessment


def _number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _round(value: Optional[float], digits: int = 3):
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _find_raw(raw_evidence: List[Dict[str, Any]], kind: str):
    return [row.get("payload") or {} for row in raw_evidence if row.get("kind") == kind]


def analyze_quantitatively(
    raw_evidence: List[Dict[str, Any]],
    normalized_evidence: List[Dict[str, Any]],
    target_price: Optional[float],
    query: str = "",
    target_condition: str = "auto",
    horizon: str = "",
) -> Dict[str, Any]:
    price_rows = _find_raw(raw_evidence, "market_price")
    valid_prices = [row for row in price_rows if row.get("success") is not False and (_number(row.get("price")) or 0) > 0]
    price = valid_prices[-1] if valid_prices else {}
    history_rows = _find_raw(raw_evidence, "price_history")
    history = history_rows[-1] if history_rows else {}
    financial_rows = _find_raw(raw_evidence, "financials")
    financial = financial_rows[-1] if financial_rows else {}
    crowd_payloads = _find_raw(raw_evidence, "polymarket")
    crowd_items = [item for payload in crowd_payloads for item in payload.get("items", [])]

    points = history.get("points") or []
    closes = [_number(point.get("price")) for point in points]
    closes = [value for value in closes if value is not None and value > 0]
    log_returns = [math.log(current / previous) for previous, current in zip(closes, closes[1:])]
    annualized_volatility = (
        statistics.stdev(log_returns) * math.sqrt(252) * 100
        if len(log_returns) >= 3
        else None
    )
    return_1m = (
        (closes[-1] / closes[-22] - 1) * 100 if len(closes) >= 22 else None
    )
    period_return = (closes[-1] / closes[0] - 1) * 100 if len(closes) >= 2 else None
    max_drawdown = None
    if len(closes) >= 2:
        peak = closes[0]
        max_drawdown = 0.0
        for close in closes:
            peak = max(peak, close)
            max_drawdown = min(max_drawdown, (close / peak - 1) * 100)
    current_price = _number(price.get("price"))
    target_distance_pct = (
        (target_price / current_price - 1) * 100
        if target_price is not None and current_price not in (None, 0)
        else None
    )

    metrics_by_key = {
        item.get("key"): item for item in financial.get("metrics", []) if item.get("key")
    }
    revenue = _number((metrics_by_key.get("revenue") or {}).get("value"))
    operating_income = _number(
        (metrics_by_key.get("operating_income") or {}).get("value")
    )
    net_income = _number((metrics_by_key.get("net_income") or {}).get("value"))
    operating_margin = (
        operating_income / revenue * 100
        if operating_income is not None and revenue not in (None, 0)
        else None
    )
    net_margin = (
        net_income / revenue * 100
        if net_income is not None and revenue not in (None, 0)
        else None
    )
    growth = {
        key: _number(item.get("growth_pct"))
        for key, item in metrics_by_key.items()
        if item.get("growth_pct") is not None
    }

    probabilities = [
        _number(item.get("probability"))
        for item in crowd_items
        if _number(item.get("probability")) is not None
    ]
    crowd_mean = statistics.mean(probabilities) if probabilities else None
    crowd_dispersion = statistics.pstdev(probabilities) if len(probabilities) >= 2 else 0.0 if probabilities else None

    directional = [
        int(item.get("direction", 0))
        for item in normalized_evidence
        if item.get("direction") in {-1, 0, 1}
    ]
    positive = sum(value > 0 for value in directional)
    negative = sum(value < 0 for value in directional)
    directional_total = positive + negative
    agreement = (
        max(positive, negative) / directional_total * 100 if directional_total else None
    )
    balance = (
        (positive - negative) / directional_total if directional_total else 0.0
    )

    signals = []
    for label, value, unit in (
        ("1-day return", _number(price.get("return_1d_pct")), "%"),
        ("1-week return", _number(price.get("return_1w_pct")), "%"),
        ("1-month return", return_1m, "%"),
        ("Price-history period return", period_return, "%"),
        ("Maximum drawdown (close-to-close)", max_drawdown, "%"),
        ("Annualized realized volatility", annualized_volatility, "%"),
        ("Target distance", target_distance_pct, "%"),
        ("Operating margin", operating_margin, "%"),
        ("Net margin", net_margin, "%"),
        ("Evidence directional agreement", agreement, "%"),
    ):
        if value is not None:
            signals.append({"label": label, "value": _round(value), "unit": unit})

    return {
        "target_assessment": target_assessment(current_price, target_price, query, target_condition, horizon, price.get("currency") or "USD", price.get("timestamp"), crowd_items),
        "market": {
            "current_price": current_price,
            "return_1d_pct": _round(_number(price.get("return_1d_pct"))),
            "return_1w_pct": _round(_number(price.get("return_1w_pct"))),
            "return_1m_pct": _round(return_1m),
            "period_return_pct": _round(period_return),
            "max_drawdown_pct": _round(max_drawdown),
            "annualized_volatility_pct": _round(annualized_volatility),
            "volume_vs_5d_avg_pct": _round(_number(price.get("volume_vs_5d_avg_pct"))),
            "target_price": target_price,
            "target_distance_pct": _round(target_distance_pct),
            "observations": len(closes),
        },
        "financials": {
            "growth_pct": {key: _round(value) for key, value in growth.items()},
            "operating_margin_pct": _round(operating_margin),
            "net_margin_pct": _round(net_margin),
        },
        "polymarket": {
            "market_count": len(crowd_items),
            "mean_selected_outcome_probability": _round(crowd_mean, 4),
            "probability_dispersion": _round(crowd_dispersion, 4),
            "interpretation": "Expectation evidence only; it is not confirmation of the underlying outcome.",
        },
        "source_consistency": {
            "positive_evidence_count": positive,
            "negative_evidence_count": negative,
            "neutral_evidence_count": len(directional) - directional_total,
            "directional_agreement_pct": _round(agreement),
            "directional_balance": _round(balance),
        },
        "signals": signals,
        "calculation_mode": "deterministic_python",
    }
