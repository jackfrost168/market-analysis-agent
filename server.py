import json
import mimetypes
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, unquote, urlparse

from agent.service import AgentService
from agent.run_manager import RunManager


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8001"))


SERVICE = AgentService()
RUNS = RunManager(SERVICE)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "StatefulMarketAgent/1.0"

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _json(self, payload: Any, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 1_000_000:
            raise ValueError("Request body must be between 1 byte and 1 MB")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _serve_file(self, path: Path):
        try:
            resolved = path.resolve()
            if WEB_ROOT.resolve() not in resolved.parents and resolved != WEB_ROOT.resolve():
                self.send_error(403)
                return
            if not resolved.is_file():
                self.send_error(404)
                return
            body = resolved.read_bytes()
            # Windows registry mappings may label .js as text/plain, which breaks ES modules.
            content_type = {".js": "text/javascript", ".mjs": "text/javascript"}.get(resolved.suffix.lower())
            content_type = content_type or mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith(("text/", "application/javascript")) else content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            self._json({"error": str(exc)}, 500)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self._json(
                    {
                        "success": True,
                        "service": "stateful-market-agent",
                        "framework": "LangGraph",
                        "port": PORT,
                    }
                )
                return
            if path == "/api/graph":
                self._json(SERVICE.graph_info())
                return
            if path == "/api/models":
                self._json(SERVICE.ollama_models())
                return
            if path == "/api/polymarket/status":
                self._json(SERVICE.polymarket_status((query.get("provider") or [None])[0]))
                return
            if path == "/api/polymarket/providers":
                self._json(SERVICE.prediction_providers())
                return
            if path == "/api/price":
                asset = (query.get("asset") or [""])[0]
                question = (query.get("query") or [""])[0]
                self._json(SERVICE.price_preview(asset, question))
                return
            if path == "/api/history":
                limit = int((query.get("limit") or ["20"])[0])
                self._json({"runs": SERVICE.history(limit)})
                return
            if path.startswith("/api/runs/") and path.endswith("/audit"):
                run_id = path.strip("/").split("/")[-2]
                active = RUNS.get(run_id)
                if active:
                    self._json(
                        {
                            "run_id": run_id,
                            "status": active.get("status"),
                            "decision_audit": active.get("decision_audit") or [],
                            "intermediate_results": active.get("intermediate_results") or [],
                            "tool_calls": active.get("tool_calls") or [],
                            "llm_calls": active.get("llm_calls") or [],
                        }
                    )
                    return
                saved = SERVICE.saved_run(run_id)
                if saved:
                    state = saved.get("state") or {}
                    self._json(
                        {
                            "run_id": run_id,
                            "status": saved.get("status"),
                            "decision_audit": state.get("decision_audit") or [],
                            "intermediate_results": state.get("intermediate_results") or [],
                            "tool_calls": state.get("tool_calls") or [],
                            "llm_calls": state.get("llm_calls") or [],
                        }
                    )
                    return
                self._json({"error": "Run not found"}, 404)
                return
            if path.startswith("/api/runs/"):
                run_id = path.rsplit("/", 1)[-1]
                active = RUNS.get(run_id)
                if active:
                    self._json(active)
                    return
                saved = SERVICE.saved_run(run_id)
                if saved:
                    self._json(
                        {
                            "run_id": run_id,
                            "status": saved.get("status"),
                            "report": saved.get("report"),
                            "node_trace": (saved.get("state") or {}).get("node_trace") or [],
                            "decision_audit": (saved.get("state") or {}).get("decision_audit") or [],
                            "intermediate_results": (saved.get("state") or {}).get("intermediate_results") or [],
                            "tool_calls": (saved.get("state") or {}).get("tool_calls") or [],
                            "llm_calls": (saved.get("state") or {}).get("llm_calls") or [],
                            "source_status": (saved.get("state") or {}).get("source_status") or {},
                        }
                    )
                    return
                self._json({"error": "Run not found"}, 404)
                return
            if path in {"/", "/index.html"}:
                self._serve_file(WEB_ROOT / "index.html")
                return
            relative = path.lstrip("/")
            self._serve_file(WEB_ROOT / relative)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/runs":
                run_id = RUNS.start(payload)
                self._json({"run_id": run_id, "status": "queued"}, 202)
                return
            if parsed.path == "/api/analyze":
                state = SERVICE.analyze(payload)
                self._json({"run_id": state["run_id"], "report": state.get("report"), "state": state})
                return
            self._json({"error": "Endpoint not found"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json(
                {"error": f"{type(exc).__name__}: {exc}", "debug": traceback.format_exc(limit=5)},
                500,
            )


def main():
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Stateful Market Agent is running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
