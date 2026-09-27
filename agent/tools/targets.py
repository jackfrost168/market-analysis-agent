"""Deterministic target conditions, separate from uncertain future forecasts."""

import copy
import math
import re


def target_assessment(current_price, target_price, query="", condition="auto", horizon="", currency="USD", quote_timestamp=None, crowd_items=()):
    if target_price is None:
        return None
    if not math.isfinite(target_price) or target_price <= 0:
        raise ValueError("Target price must be a finite positive number")
    if condition not in {"auto", "above", "below"}:
        raise ValueError("Target condition must be auto, above, or below")
    inferred_below = bool(re.search(r"\b(?:below|fall to|drop to|decline to)\b|跌至|跌到|跌破|低于", query, re.IGNORECASE))
    direction = ("below" if inferred_below else "above") if condition == "auto" else condition
    operator = ">=" if direction == "above" else "<="
    base = {
        "target_price": target_price, "current_price": current_price,
        "condition": direction, "operator": operator,
        "condition_origin": "query_rule" if condition == "auto" and inferred_below else "default_at_or_above" if condition == "auto" else "user_selection",
        "horizon": horizon, "quote_timestamp": quote_timestamp,
        "probability": None, "probability_status": "not_estimated",
        "evaluation_scope": "latest_available_quote_not_future_settlement",
    }
    future = (
        f"Future target condition: price {operator} {target_price:g} {currency}. "
        f"Requested horizon: {horizon or 'not specified'}. "
        "A current threshold comparison does not prove a future closing price or that the condition will persist. "
        "No calibrated target probability or difficulty rating has been calculated; nearby market odds cannot be substituted for this target's odds."
    )
    if current_price is None or not math.isfinite(current_price) or current_price <= 0:
        return {**base, "status": "quote_unavailable", "condition_met_at_quote": None,
                "extreme_target": False, "summary": "Target assessment unavailable: no valid current quote.",
                "future_expectation": future, "reasons": ["A valid current quote is required before comparing the target."]}
    signed = (target_price / current_price - 1) * 100
    ratio = target_price / current_price
    met = current_price >= target_price if direction == "above" else current_price <= target_price
    extreme = ratio >= 10 or ratio <= 0.1
    comparison = "at or above" if direction == "above" else "at or below"
    if met:
        status = "already_satisfied_at_quote"
        summary = f"Latest available quote {current_price} {currency} is already {comparison} target {target_price:g} {currency}; the current threshold condition is satisfied. This is not a forecast of the future closing price."
    else:
        status = "requires_rise" if direction == "above" else "requires_fall"
        move = "rise" if direction == "above" else "fall"
        summary = f"Latest available quote {current_price} {currency} must {move} by {abs(signed):.2f}% to reach target {target_price:g} {currency}. This is a required price move, not an estimated probability or difficulty rating."
    warning = ""
    if extreme:
        warning = f"Extreme target: {ratio:.2f} times the quote. Confirm the amount, currency, unit and time horizon. The 10x/0.1x cutoff is an input-sanity rule, not proof that the target is impossible."
        summary += " " + warning
    exact = sum(any(abs(float(level) - target_price) <= 0.01 for level in item.get("matched_thresholds", [])) for item in crowd_items)
    reasons = [
        f"Condition checked against the latest quote: price {operator} {target_price:g} {currency}.",
        f"Signed target distance: {signed:.2f}%; absolute distance to the threshold: {abs(signed):.2f}%." + (" No additional move is needed to satisfy the current condition." if met else ""),
        "The probability and difficulty of the future event remain unestimated; strike, direction, deadline and settlement rules must all match before using a market probability.",
    ]
    if warning:
        reasons.append(warning)
    return {**base, "status": status, "condition_met_at_quote": met,
            "signed_distance_pct": signed, "move_magnitude_pct": abs(signed),
            "target_to_quote_ratio": ratio, "extreme_target": extreme, "warning": warning,
            "exact_threshold_candidates": exact, "summary": summary,
            "future_expectation": future, "reasons": reasons}


def enforce_target_summary(thesis, assessment):
    """Target conclusions are computed facts, never a free-form difficulty label."""
    if not assessment:
        return thesis
    result = copy.deepcopy(thesis)
    result["current_view"] = assessment["summary"]
    result["why"] = list(assessment["reasons"])
    result["future_expectation"] = assessment["future_expectation"]
    return result
