"""In-process progress tracking for asynchronous Agent runs."""

import logging
import threading
import time
import uuid
from typing import Any, Dict

from .graph import next_node_for_state
from .service import AgentService
from .tools.market_data import utc_now_iso


LOGGER = logging.getLogger(__name__)


class RunManager:
    def __init__(self, service: AgentService):
        self.service = service
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, payload: Dict[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        # Reject malformed requests before returning a successful 202 response.
        self.service.validate_payload(payload, run_id)
        queued_at = utc_now_iso()
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id,
                "status": "queued",
                "revision": 0,
                "queued_at": queued_at,
                "current_node": "understand_request",
                "message": "Understanding the ordinary question with local Ollama...",
                "node_trace": [],
                "decision_audit": [],
                "intermediate_results": [],
                "tool_calls": [],
                "llm_calls": [],
                "source_status": {},
                "evidence_gate": {},
            }
        thread = threading.Thread(
            target=self._execute,
            args=(run_id, payload),
            daemon=True,
            name=f"agent-run-{run_id[:8]}",
        )
        thread.start()
        return run_id

    def _execute(self, run_id: str, payload: Dict[str, Any]):
        started_perf = time.perf_counter()
        started_at = utc_now_iso()
        with self._lock:
            self._runs[run_id].update(
                {
                    "status": "running",
                    "revision": self._runs[run_id].get("revision", 0) + 1,
                    "started_at": started_at,
                    "current_node": "understand_request",
                    "message": "Understanding the ordinary question with local Ollama...",
                }
            )

        def progress(state: Dict[str, Any]):
            trace = state.get("node_trace") or []
            latest = trace[-1] if trace else None
            snapshot = {
                "status": "running",
                "current_node": next_node_for_state(state),
                "message": latest.get("summary") if latest else "Understanding your question with local Ollama...",
                "node_trace": trace,
                "decision_audit": state.get("decision_audit") or [],
                "intermediate_results": state.get("intermediate_results") or [],
                "tool_calls": state.get("tool_calls") or [],
                "llm_calls": state.get("llm_calls") or [],
                "source_status": state.get("source_status") or {},
                "evidence_gate": state.get("evidence_gate") or {},
                "retrieval_attempts": state.get("retrieval_attempts", 0),
                "model": state.get("model"),
                "run_metrics": state.get("run_metrics") or {},
            }
            if (state.get("counterfactual_tests") or {}).get("status") == "running":
                snapshot["current_node"] = "counterfactual_evidence_test"
                snapshot["message"] = "Testing evidence dependency after analysis..."
            with self._lock:
                snapshot["revision"] = self._runs[run_id].get("revision", 0) + 1
                self._runs[run_id].update(snapshot)

        try:
            self.service.analyze(
                payload, progress_callback=progress, run_id=run_id
            )
            # AgentService persists the completed state before returning. Read it
            # from SQLite thereafter so completed runs do not accumulate in RAM.
            with self._lock:
                self._runs.pop(run_id, None)
        except Exception as exc:
            LOGGER.exception("Agent run %s failed", run_id)
            error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                snapshot = dict(self._runs[run_id])
                snapshot.update({"status": "failed", "message": str(exc), "error": error})
            try:
                self.service.save_failed_run(
                    payload,
                    run_id,
                    snapshot,
                    error,
                    started_at,
                    round((time.perf_counter() - started_perf) * 1000),
                )
                with self._lock:
                    self._runs.pop(run_id, None)
            except Exception:
                LOGGER.exception("Failed to persist failed Agent run %s", run_id)
                with self._lock:
                    snapshot["revision"] = self._runs[run_id].get("revision", 0) + 1
                    self._runs[run_id].update(snapshot)

    def get(self, run_id: str):
        with self._lock:
            item = self._runs.get(run_id)
            return dict(item) if item else None

    def stats(self) -> Dict[str, int]:
        with self._lock:
            statuses = [item.get("status") for item in self._runs.values()]
        return {
            "active_runs": len(statuses),
            "queued_runs": statuses.count("queued"),
            "running_runs": statuses.count("running"),
        }
