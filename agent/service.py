import os
import uuid
import math
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .graph import build_graph, graph_spec
from .llm import OllamaClient
from .nodes import AgentNodes
from .observability import aggregate_run_metrics, build_run_metrics
from .repositories import RunRepository
from .tools.market_data import get_price_snapshot, resolve_asset, utc_now_iso
from .tools.prediction_markets import get_polymarket_status, select_provider
from .tools.research import ResearchTools


ROOT = Path(__file__).resolve().parents[1]


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    return [line.strip(" -\t") for line in str(value).splitlines() if line.strip(" -\t")]


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    result = float(str(value).replace(",", "").strip())
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Target price must be a finite positive number")
    return result


class AgentService:
    def __init__(
        self,
        data_dir: Optional[Path] = None,
        llm: Optional[OllamaClient] = None,
        research_tools: Optional[ResearchTools] = None,
    ):
        self.data_dir = Path(data_dir or os.environ.get("AGENT_DATA_DIR") or ROOT / "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.llm = llm or OllamaClient()
        self.research_tools = research_tools or ResearchTools(self.data_dir)
        self.nodes = AgentNodes(self.llm, self.research_tools)
        self.graph = build_graph(self.nodes)
        self.runs = RunRepository(self.data_dir / "agent_runs.sqlite3")

    def _initial_state(self, payload: Dict[str, Any], run_id: str) -> Dict[str, Any]:
        asset_input = str(payload.get("asset") or payload.get("asset_input") or "").strip()
        user_query = str(payload.get("query") or payload.get("question") or "").strip()
        if not user_query:
            user_query = f"Analyze the current situation and outlook for {asset_input or 'Bitcoin'}."
        max_retries = max(0, min(int(payload.get("max_retries", 2)), 3))
        temperature = max(0.0, min(float(payload.get("temperature", 0.15)), 1.0))
        target_condition = str(payload.get("target_condition") or "auto")
        if target_condition not in {"auto", "above", "below"}:
            raise ValueError("Target condition must be auto, above, or below")
        return {
            "run_id": run_id,
            "conversation_id": str(payload.get("conversation_id") or uuid.uuid4()),
            "user_query": user_query,
            "asset_input": asset_input,
            "requested_horizon": str(payload.get("horizon") or "").strip(),
            "requested_as_of": str(payload.get("as_of") or "").strip(),
            "target_price": _optional_float(payload.get("target_price")),
            "target_condition": target_condition,
            "model": str(payload.get("model") or "auto"),
            "prediction_provider": select_provider(payload.get("prediction_provider")),
            "temperature": temperature,
            "max_retries": max_retries,
            "user_notes": _string_list(payload.get("notes")),
            "user_headlines": _string_list(payload.get("headlines")),
            "raw_evidence": [],
            "normalized_evidence": [],
            "evidence_groups": [],
            "source_status": {},
            "retrieval_attempts": 0,
            "targeted_queries": [],
            "quantitative_analysis": {},
            "thesis_graph": {},
            "verification": {},
            "report": {},
            "node_trace": [],
            "decision_audit": [],
            "intermediate_results": [],
            "tool_calls": [],
            "errors": [],
            "llm_calls": [],
        }

    def validate_payload(self, payload: Dict[str, Any], run_id: str) -> None:
        """Apply the same input validation used by synchronous runs."""
        self._initial_state(payload, run_id)

    def analyze(
        self,
        payload: Dict[str, Any],
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        started_perf = time.perf_counter()
        started_at = utc_now_iso()
        run_id = run_id or str(uuid.uuid4())
        initial = self._initial_state(payload, run_id)
        config = {
            "configurable": {"thread_id": run_id},
            "recursion_limit": 40,
        }
        final_state = initial
        for snapshot in self.graph.stream(initial, config=config, stream_mode="values"):
            final_state = snapshot
            if progress_callback:
                progress_callback(snapshot)
        completed_at = utc_now_iso()
        final_state["run_metrics"] = build_run_metrics(
            final_state,
            started_at=started_at,
            completed_at=completed_at,
            total_duration_ms=round((time.perf_counter() - started_perf) * 1000),
        )
        if final_state.get("report"):
            final_state["report"]["observability"] = final_state["run_metrics"]
            final_state["report"]["workflow"]["node_trace"] = final_state.get("node_trace") or []
            final_state["report"]["agent_audit"] = {
                "decision_audit": final_state.get("decision_audit") or [],
                "intermediate_results": final_state.get("intermediate_results") or [],
                "tool_calls": final_state.get("tool_calls") or [],
            }
            proof = final_state["report"].setdefault("agent_proof", {})
            proof.update(
                {
                    "actual_nodes_executed": [
                        item.get("node") for item in final_state.get("node_trace") or []
                    ],
                    "decision_records": len(final_state.get("decision_audit") or []),
                    "intermediate_records": len(final_state.get("intermediate_results") or []),
                    "tool_calls": len(final_state.get("tool_calls") or []),
                }
            )
        if progress_callback:
            progress_callback(final_state)
        self.runs.save(final_state)
        return final_state

    def save_failed_run(
        self,
        payload: Dict[str, Any],
        run_id: str,
        snapshot: Dict[str, Any],
        error: str,
        started_at: str,
        total_duration_ms: int,
    ) -> None:
        """Persist a failed run so restarts and completion-rate metrics do not hide it."""
        state = self._initial_state(payload, run_id)
        state.update(snapshot)
        state["run_id"] = run_id
        state["errors"] = list(state.get("errors") or []) + [error]
        state["error"] = error
        state["run_metrics"] = build_run_metrics(
            state,
            started_at=started_at,
            completed_at=utc_now_iso(),
            total_duration_ms=total_duration_ms,
        )
        self.runs.save(state, status="failed")

    def price_preview(self, asset_input: str, query: str = "") -> Dict[str, Any]:
        asset = resolve_asset(asset_input, query)
        price = get_price_snapshot(asset)
        return {"asset": asset, "price": price, "retrieved_at": utc_now_iso()}

    def ollama_models(self) -> Dict[str, Any]:
        try:
            models = self.llm.list_models()
            selected = self.llm.choose_model("auto")
            return {"success": True, "models": models, "auto_selected": selected}
        except Exception as exc:
            return {
                "success": False,
                "models": [],
                "auto_selected": self.llm.choose_model("auto"),
                "error": str(exc),
            }

    def graph_info(self) -> Dict[str, Any]:
        return graph_spec()

    def polymarket_status(self, provider=None) -> Dict[str, Any]:
        return get_polymarket_status(provider)

    def prediction_providers(self):
        return {"default": select_provider(), "providers": ["coinrithm", "gamma"]}

    def history(self, limit: int = 20):
        return self.runs.list_runs(limit)

    def observability(self, limit: int = 50):
        rows = self.runs.list_runs(limit)
        records = []
        for row in rows:
            saved = self.runs.get(row["run_id"])
            if saved:
                records.append(saved)
        return aggregate_run_metrics(records)

    def saved_run(self, run_id: str):
        return self.runs.get(run_id)
