"""Connect HTTP routes to the existing LangGraph Agent service."""

from typing import Any, Dict, Optional

from agent.run_manager import RunManager
from agent.service import AgentService


class AgentBridge:
    def __init__(self, service: Optional[AgentService] = None):
        self.service = service or AgentService()
        self.runs = RunManager(self.service)

    def start_run(self, payload: Dict[str, Any]) -> Dict[str, str]:
        run_id = self.runs.start(payload)
        return {"run_id": run_id, "status": "queued"}

    def analyze(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = self.service.analyze(payload)
        return {"run_id": state["run_id"], "report": state.get("report"), "state": state}

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        active = self.runs.get(run_id)
        if active:
            return active
        saved = self.service.saved_run(run_id)
        if not saved:
            return None
        state = saved.get("state") or {}
        return {
            "run_id": run_id,
            "status": saved.get("status"),
            "report": saved.get("report"),
            "current_node": "END" if saved.get("status") == "completed" else state.get("current_node"),
            "message": "Report ready." if saved.get("status") == "completed" else state.get("message"),
            "error": state.get("error"),
            "revision": len(state.get("node_trace") or []) + 2,
            "node_trace": state.get("node_trace") or [],
            "decision_audit": state.get("decision_audit") or [],
            "intermediate_results": state.get("intermediate_results") or [],
            "tool_calls": state.get("tool_calls") or [],
            "llm_calls": state.get("llm_calls") or [],
            "source_status": state.get("source_status") or {},
            "evidence_gate": state.get("evidence_gate") or {},
            "retrieval_attempts": state.get("retrieval_attempts", 0),
            "run_metrics": state.get("run_metrics") or {},
        }

    def observability(self, limit: int = 50) -> Dict[str, Any]:
        result = self.service.observability(limit)
        result.update(self.runs.stats())
        return result

    def get_audit(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.get_run(run_id)
        if not run:
            return None
        return {
            "run_id": run_id,
            "status": run.get("status"),
            "decision_audit": run.get("decision_audit") or [],
            "intermediate_results": run.get("intermediate_results") or [],
            "tool_calls": run.get("tool_calls") or [],
            "llm_calls": run.get("llm_calls") or [],
        }
