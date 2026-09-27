"""Explicit provider selection. Access denial never triggers a provider switch."""

import os

from .coinrithm import get_coinrithm_context, get_coinrithm_status
from .polymarket import get_polymarket_context as get_gamma_context
from .polymarket import get_polymarket_status as get_gamma_status


def select_provider(provider=None):
    selected = provider or os.environ.get("PREDICTION_DATA_PROVIDER", "coinrithm")
    if selected not in {"coinrithm", "gamma"}:
        raise ValueError("Prediction data provider must be coinrithm or gamma")
    return selected


def get_polymarket_context(symbol, asset_name, current_price=None, target_price=None, extra_query="", limit=5, provider=None):
    selected = select_provider(provider)
    function = get_coinrithm_context if selected == "coinrithm" else get_gamma_context
    result = function(symbol, asset_name, current_price, target_price, extra_query, limit)
    result["provider"] = selected
    return result


def get_polymarket_status(provider=None):
    selected = select_provider(provider)
    result = get_coinrithm_status() if selected == "coinrithm" else get_gamma_status()
    result["provider"] = selected
    return result
