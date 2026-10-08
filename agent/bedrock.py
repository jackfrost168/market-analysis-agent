"""AWS Bedrock Converse adapter using the existing structured LLM interface."""

import json
import os
import re
import time
from typing import Any, Dict, Type

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel

from .llm import StructuredResult, _clean_json_text


DEFAULT_MODEL = "qwen.qwen3-32b-v1:0"
DEFAULT_REGION = "us-east-1"
DEFAULT_PRICING = {DEFAULT_MODEL: {"input": 0.15, "output": 0.60}}


def credential_status() -> Dict[str, Any]:
    """Presence check only: no inference calls, keys or credentials in the response."""
    try:
        if boto3.Session().get_credentials() is not None:
            return {"available": True, "status": "credentials_configured", "error": None}
        return {"available": False, "status": "missing_aws_credentials", "error": "AWS credentials are unavailable"}
    except BotoCoreError:
        return {"available": False, "status": "aws_credentials_unavailable", "error": "AWS credentials could not be resolved"}


def pricing_for(model: str, region: str) -> Dict[str, Any]:
    configured = os.environ.get("BEDROCK_PRICING_JSON", "").strip()
    if not configured and region != DEFAULT_REGION:
        raise ValueError("Configure BEDROCK_PRICING_JSON for this AWS region")
    prices = json.loads(configured) if configured else DEFAULT_PRICING
    rates = prices.get(model)
    if rates is None:
        raise ValueError("Bedrock model has no configured token pricing")
    input_rate, output_rate = float(rates["input"]), float(rates["output"])
    if not (0 <= input_rate < float("inf") and 0 <= output_rate < float("inf")):
        raise ValueError("Invalid Bedrock token pricing")
    return {"input_usd_per_million_tokens": input_rate, "output_usd_per_million_tokens": output_rate,
            "mode": "standard_converse", "currency": "USD", "region": region,
            "source": "configured" if configured else "aws_price_list_us-east-1_2026-10-09"}


def _usage(body: Dict[str, Any], pricing: Dict[str, Any]) -> Dict[str, Any]:
    usage = body.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    reported = isinstance(usage.get("inputTokens"), int) and isinstance(usage.get("outputTokens"), int)
    prompt = max(0, usage["inputTokens"]) if isinstance(usage.get("inputTokens"), int) else 0
    completion = max(0, usage["outputTokens"]) if isinstance(usage.get("outputTokens"), int) else 0
    cost = round((prompt * pricing["input_usd_per_million_tokens"]
                  + completion * pricing["output_usd_per_million_tokens"]) / 1_000_000, 8) if reported else None
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
            "metered_api_cost_usd": cost, "usage_reported": reported}


def runtime_client(region: str, timeout_seconds: float):
    # One HTTP attempt: avoid hidden SDK retries adding cost or exceeding the CF budget.
    connect = min(5.0, max(0.1, timeout_seconds / 3))
    return boto3.Session().client("bedrock-runtime", region_name=region,
                                 config=Config(connect_timeout=connect, read_timeout=max(0.1, timeout_seconds - connect),
                                               retries={"total_max_attempts": 1, "mode": "standard"}))


def generate_bedrock(prompt: str, response_model: Type[BaseModel], selected_model: str,
                     route: Dict[str, Any], *, temperature: float,
                     timeout_seconds: float, max_output_tokens: int | None) -> StructuredResult:
    started = time.perf_counter()
    body: Dict[str, Any] = {}
    raw_text = ""
    pricing: Dict[str, Any] = {}
    region = route.get("region") or os.environ.get("BEDROCK_REGION") or DEFAULT_REGION
    model_id = route.get("model_id") or selected_model
    try:
        pricing = pricing_for(model_id, region)
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        body = runtime_client(region, timeout_seconds).converse(
            modelId=model_id,
            system=[{"text": "Return exactly one JSON object matching this JSON Schema. No markdown or extra text. /no_think\n" + schema}],
            messages=[{"role": "user", "content": [{"text": prompt + "\n/no_think"}]}],
            inferenceConfig={"temperature": max(0.0, min(float(temperature), 1.0)),
                             "maxTokens": max(1, min(int(max_output_tokens or 4096), 8192))},
        )
        raw_text = "".join(block.get("text", "") for block in body["output"]["message"]["content"])
        if body.get("stopReason") not in {"end_turn", "stop_sequence"}:
            raise ValueError("Bedrock response incomplete or blocked: " + str(body.get("stopReason")))
        # Qwen's soft no-think instruction can still emit an empty think tag.
        text = re.sub(r"^\s*<think>.*?</think>\s*", "", raw_text, count=1, flags=re.DOTALL)
        validated = response_model.model_validate(json.loads(_clean_json_text(text)))
        return StructuredResult(success=True, model=selected_model, data=validated.model_dump(), raw_text=raw_text,
                                latency_ms=round((time.perf_counter() - started) * 1000), provider="bedrock",
                                pricing_basis="api_usage_times_configured_rates", pricing=pricing, **_usage(body, pricing))
    except Exception as exc:
        if isinstance(exc, ClientError):
            code = exc.response.get("Error", {}).get("Code", "RequestFailed")
            message = f"Bedrock {code}; check IAM permissions, model access, region or quota"
        elif isinstance(exc, BotoCoreError):
            message = f"Bedrock {type(exc).__name__}; check AWS credentials or connection"
        else:
            # Exception types suffice; never expose SDK credentials or request bodies.
            message = f"Bedrock {type(exc).__name__}: structured response or pricing validation failed"
        return StructuredResult(success=False, model=selected_model, error=message, raw_text=raw_text,
                                latency_ms=round((time.perf_counter() - started) * 1000), provider="bedrock",
                                pricing_basis="api_usage_times_configured_rates", pricing=pricing or None, **_usage(body, pricing))
