import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ValidationError


DEFAULT_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_TAGS_URL = "http://127.0.0.1:11434/api/tags"


@dataclass
class StructuredResult:
    success: bool
    model: str
    data: Optional[Dict[str, Any]] = None
    latency_ms: int = 0
    error: Optional[str] = None
    raw_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    ollama_total_duration_ms: Optional[float] = None
    estimated_equivalent_cost_usd: Optional[float] = None

    @property
    def usage(self) -> Dict[str, Any]:
        """Normalized usage attached to every persisted LLM call record."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "metered_api_cost_usd": 0.0,
            "estimated_equivalent_cost_usd": self.estimated_equivalent_cost_usd,
            "pricing_basis": (
                "configured_api_equivalent"
                if self.estimated_equivalent_cost_usd is not None
                else "local_ollama_unmetered"
            ),
            "ollama_total_duration_ms": self.ollama_total_duration_ms,
        }


def _usage_fields(body: Dict[str, Any]) -> Dict[str, Any]:
    prompt_tokens = int(body.get("prompt_eval_count") or 0)
    completion_tokens = int(body.get("eval_count") or 0)
    input_rate = os.environ.get("LLM_INPUT_USD_PER_MILLION_TOKENS")
    output_rate = os.environ.get("LLM_OUTPUT_USD_PER_MILLION_TOKENS")
    estimated_cost = None
    if input_rate is not None or output_rate is not None:
        try:
            estimated_cost = round(
                prompt_tokens * float(input_rate or 0) / 1_000_000
                + completion_tokens * float(output_rate or 0) / 1_000_000,
                8,
            )
        except ValueError:
            estimated_cost = None
    total_duration = body.get("total_duration")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "ollama_total_duration_ms": (
            round(float(total_duration) / 1_000_000, 3)
            if isinstance(total_duration, (int, float))
            else None
        ),
        "estimated_equivalent_cost_usd": estimated_cost,
    }


def _clean_json_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    return fence.group(1).strip() if fence else text


class OllamaClient:
    """Small Ollama client with Pydantic-validated structured output."""

    def __init__(
        self,
        generate_url: Optional[str] = None,
        tags_url: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout_seconds: int = 180,
    ):
        self.generate_url = generate_url or os.environ.get(
            "OLLAMA_URL", DEFAULT_GENERATE_URL
        )
        self.tags_url = tags_url or os.environ.get("OLLAMA_TAGS_URL", DEFAULT_TAGS_URL)
        self.default_model = default_model or os.environ.get("OLLAMA_MODEL", "")
        self.timeout_seconds = timeout_seconds

    def list_models(self) -> List[Dict[str, Any]]:
        request = urllib.request.Request(
            self.tags_url, headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = []
        for item in payload.get("models", []):
            name = item.get("name") or item.get("model")
            if name:
                models.append(
                    {
                        "name": name,
                        "size": item.get("size"),
                        "modified_at": item.get("modified_at"),
                    }
                )
        return models

    def choose_model(self, requested: Optional[str] = None) -> str:
        if requested and requested != "auto":
            return requested
        if self.default_model:
            return self.default_model
        try:
            installed = [item["name"] for item in self.list_models()]
        except Exception:
            installed = []
        preferences = (
            "qwen3:8b",
            "qwen2.5:8b",
            "llama3.1:8b",
            "llama3:8b",
            "gemma3:4b",
            "llama3.2:3b",
            "mistral-small",
        )
        for preferred in preferences:
            for model in installed:
                if model == preferred or model.startswith(f"{preferred}:"):
                    return model
        return installed[0] if installed else "qwen3:8b"

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[BaseModel],
        model: Optional[str] = None,
        temperature: float = 0.15,
        *,
        timeout_seconds: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        think: Optional[bool] = None,
    ) -> StructuredResult:
        selected_model = self.choose_model(model)
        request_body = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
            "format": response_model.model_json_schema(),
            "options": {"temperature": max(0.0, min(float(temperature), 1.0))},
            "keep_alive": "10m",
        }
        if max_output_tokens is not None:
            request_body["options"]["num_predict"] = max(1, int(max_output_tokens))
        if think is not None:
            request_body["think"] = think
        started = time.perf_counter()
        request = urllib.request.Request(
            self.generate_url,
            data=json.dumps(request_body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        body: Dict[str, Any] = {}
        raw_text = ""
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds if timeout_seconds is None else timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
            raw_text = body.get("response") or ""
            parsed = json.loads(_clean_json_text(raw_text))
            validated = response_model.model_validate(parsed)
            return StructuredResult(
                success=True,
                model=selected_model,
                data=validated.model_dump(),
                latency_ms=round((time.perf_counter() - started) * 1000),
                raw_text=raw_text,
                **_usage_fields(body),
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValidationError) as exc:
            return StructuredResult(
                success=False,
                model=selected_model,
                latency_ms=round((time.perf_counter() - started) * 1000),
                error=str(exc),
                raw_text=raw_text,
                **_usage_fields(body),
            )
        except Exception as exc:
            return StructuredResult(
                success=False,
                model=selected_model,
                latency_ms=round((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
                raw_text=raw_text,
                **_usage_fields(body),
            )
