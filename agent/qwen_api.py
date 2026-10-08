"""Qwen OpenAI-compatible adapter; the existing graph consumes StructuredResult unchanged."""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Type

from pydantic import BaseModel

from .llm import StructuredResult, _clean_json_text


DEFAULT_BASE_URL = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
# USD per million tokens, Model Studio Virginia / Global / non-thinking.
# Price snapshot: 2026-10-09. Override via QWEN_API_PRICING_JSON for another region.
DEFAULT_PRICING = {
    "qwen3-8b": {"input": 0.072, "output": 0.287},
    "qwen3-30b-a3b": {"input": 0.108, "output": 0.431},
}


def pricing_for(model: str) -> Dict[str, Any]:
    configured = os.environ.get("QWEN_API_PRICING_JSON", "").strip()
    prices = json.loads(configured) if configured else DEFAULT_PRICING
    rates = prices.get(model)
    if rates is None:
        raise ValueError("Qwen model has no configured token pricing")
    input_rate, output_rate = float(rates["input"]), float(rates["output"])
    if not (0 <= input_rate < float("inf") and 0 <= output_rate < float("inf")):
        raise ValueError("Invalid Qwen token pricing")
    return {"input_usd_per_million_tokens": input_rate,
            "output_usd_per_million_tokens": output_rate,
            "mode": "non_thinking", "currency": "USD",
            "source": "configured" if configured else "model_studio_virginia_global_2026-10-09"}


def _usage(body: Dict[str, Any], pricing: Dict[str, Any]) -> Dict[str, Any]:
    usage = body.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    reported = isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int)
    prompt = max(0, usage["prompt_tokens"]) if isinstance(usage.get("prompt_tokens"), int) else 0
    completion = max(0, usage["completion_tokens"]) if isinstance(usage.get("completion_tokens"), int) else 0
    cost = round((prompt * pricing["input_usd_per_million_tokens"]
                  + completion * pricing["output_usd_per_million_tokens"]) / 1_000_000, 8) if reported else None
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion, "metered_api_cost_usd": cost,
            "usage_reported": reported}


def generate_qwen(prompt: str, response_model: Type[BaseModel], selected_model: str,
                  route: Dict[str, Any], *, temperature: float,
                  timeout_seconds: float, max_output_tokens: int | None) -> StructuredResult:
    started = time.perf_counter()
    body: Dict[str, Any] = {}
    raw_text = ""
    pricing: Dict[str, Any] = {}
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    model = route.get("api_model") or selected_model
    try:
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY is not configured on the server")
        pricing = pricing_for(model)
        base_url = (os.environ.get("QWEN_API_BASE_URL") or route.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        if not base_url.startswith("https://"):
            raise ValueError("Qwen API endpoint must use HTTPS")
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        request_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return one JSON object matching this JSON Schema. No markdown or extra text.\n" + schema},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
            "stream": False,
            "temperature": max(0.0, min(float(temperature), 1.0)),
            "max_tokens": max(1, min(int(max_output_tokens or 4096), 8192)),
        }
        request = urllib.request.Request(
            base_url + "/chat/completions", data=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        choice = body["choices"][0]
        raw_text = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length":
            raise ValueError("Qwen output exceeded the token limit")
        validated = response_model.model_validate(json.loads(_clean_json_text(raw_text)))
        return StructuredResult(success=True, model=selected_model, data=validated.model_dump(),
                                raw_text=raw_text, latency_ms=round((time.perf_counter() - started) * 1000),
                                provider="qwen_api", pricing_basis="api_usage_times_configured_rates",
                                pricing=pricing, **_usage(body, pricing))
    except Exception as exc:
        # Do not persist response bodies, Authorization headers or raw credentials in errors.
        if isinstance(exc, urllib.error.HTTPError):
            message = f"Qwen API HTTP {exc.code}; check region, API key, model access or rate limit"
        elif isinstance(exc, (TimeoutError, urllib.error.URLError)):
            message = "Qwen API request timed out or could not connect"
        else:
            message = f"{type(exc).__name__}: {exc}"
        if api_key:
            message = message.replace(api_key, "[redacted]")
            raw_text = raw_text.replace(api_key, "[redacted]")
        return StructuredResult(success=False, model=selected_model, error=message,
                                raw_text=raw_text, latency_ms=round((time.perf_counter() - started) * 1000),
                                provider="qwen_api", pricing_basis="api_usage_times_configured_rates",
                                pricing=pricing or None, **_usage(body, pricing))
