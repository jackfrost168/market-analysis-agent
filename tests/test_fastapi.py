import time
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from awsdeploy.api import create_app
from awsdeploy.bridge import AgentBridge


class FixtureService:
    def __init__(self):
        self.saved = {}

    def validate_payload(self, payload, run_id):
        if payload.get("target_condition") == "invalid":
            raise ValueError("Target condition must be auto, above, or below")

    def analyze(self, payload, progress_callback=None, run_id=None):
        state = {
            "run_id": run_id or "sync-run",
            "node_trace": [{"node": "build_report", "summary": "Done"}],
            "report": {"summary": payload.get("query", "")},
            "tool_calls": [{"function": "fixture_tool"}],
        }
        if progress_callback:
            progress_callback(state)
        self.saved[state["run_id"]] = {
            "status": "completed", "report": state["report"], "state": state,
        }
        return state

    def saved_run(self, run_id):
        return self.saved.get(run_id)

    def graph_info(self):
        return {"framework": "LangGraph"}

    def ollama_models(self):
        return {"success": True, "models": []}

    def prediction_providers(self):
        return {"default": "coinrithm", "providers": ["coinrithm", "gamma"]}

    def polymarket_status(self, provider):
        return {"provider": provider or "coinrithm"}

    def price_preview(self, asset, query):
        return {"asset": asset, "query": query}

    def history(self, limit):
        return []

    def observability(self, limit):
        return {"persisted_runs": len(self.saved), "completed_runs": len(self.saved)}


class FastApiTests(unittest.TestCase):
    def setUp(self):
        self.bridge = AgentBridge(FixtureService())
        self.client = TestClient(create_app(self.bridge))

    def test_existing_web_and_read_endpoints(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/app.js").status_code, 200)
        self.assertEqual(self.client.get("/api/health").json()["success"], True)
        self.assertEqual(self.client.get("/api/graph").json()["framework"], "LangGraph")
        self.assertEqual(self.client.get("/api/history").json(), {"runs": []})
        self.assertEqual(self.client.get("/api/price?asset=BTC").json()["asset"], "BTC")
        self.assertEqual(self.client.get("/api/polymarket/providers").status_code, 200)
        self.assertEqual(self.client.get("/api/models").status_code, 200)

    def test_async_run_and_audit_keep_existing_contract(self):
        response = self.client.post("/api/runs", json={"asset": "BTC", "query": "Bitcoin outlook"})
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        for _ in range(100):
            run = self.client.get(f"/api/runs/{run_id}").json()
            if run["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["report"]["summary"], "Bitcoin outlook")
        audit = self.client.get(f"/api/runs/{run_id}/audit")
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.json()["tool_calls"][0]["function"], "fixture_tool")
        self.assertEqual(self.client.get("/api/runs/missing").status_code, 404)

    def test_invalid_request_is_rejected_before_queueing(self):
        response = self.client.post("/api/runs", json={"target_condition": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Target condition", response.json()["error"])

    def test_sync_analyze(self):
        response = self.client.post("/api/analyze", json={"query": "Apple outlook"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report"]["summary"], "Apple outlook")

    def test_metrics_and_sse_completion(self):
        self.assertEqual(self.client.get("/api/metrics").status_code, 200)
        response = self.client.post("/api/runs", json={"asset": "BTC", "query": "Bitcoin outlook"})
        run_id = response.json()["run_id"]
        with self.client.stream("GET", f"/api/runs/{run_id}/events") as stream:
            body = "".join(stream.iter_text())
        self.assertEqual(stream.status_code, 200)
        self.assertIn("text/event-stream", stream.headers["content-type"])
        self.assertIn("event: run.completed", body)

    def test_evaluation_endpoint_exposes_summary_without_case_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "latest_report.json"
            report.write_text(json.dumps({
                "case_count": 1,
                "task_classification_accuracy_pct": 100.0,
                "cases": [{"case_id": "sample", "error": "private fixture detail"}],
            }), encoding="utf-8")
            client = TestClient(create_app(self.bridge, evaluation_report=report))
            payload = client.get("/api/evaluation").json()
            self.assertTrue(payload["available"])
            self.assertEqual(payload["summary"]["case_count"], 1)
            self.assertNotIn("cases", payload["summary"])
            report.unlink()
            self.assertFalse(client.get("/api/evaluation").json()["available"])


if __name__ == "__main__":
    unittest.main()
