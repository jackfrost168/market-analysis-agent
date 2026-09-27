"""In-process progress tracking for asynchronous Agent runs."""

import logging
import threading
import uuid
from typing import Any, Dict

from .graph import next_node_for_state
from .service import AgentService


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
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id,
                "status": "queued",
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
        with self._lock:
            self._runs[run_id].update(
                {
                    "status": "running",
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
            }
            with self._lock:
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
            with self._lock:
                self._runs[run_id].update(
                    {
                        "status": "failed",
                        "message": str(exc),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    def get(self, run_id: str):
        with self._lock:
            item = self._runs.get(run_id)
            return dict(item) if item else None
