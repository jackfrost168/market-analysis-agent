"""FastAPI application retaining the existing web UI and API contract."""

from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .bridge import AgentBridge


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    asset: str | None = None
    asset_input: str | None = None
    query: str | None = None
    question: str | None = None
    horizon: str | None = None
    as_of: str | None = None
    target_price: float | str | None = None
    target_condition: str | None = None
    model: str | None = None
    prediction_provider: str | None = None
    temperature: float = 0.15
    max_retries: int = 2
    notes: str | list[str] | None = None
    headlines: str | list[str] | None = None
    conversation_id: str | None = None


def create_app(bridge: AgentBridge | None = None) -> FastAPI:
    app = FastAPI(title="Stateful Market Agent", version="1.0.0")
    app.state.bridge = bridge or AgentBridge()

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.middleware("http")
    async def no_store_api(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {"success": True, "service": "stateful-market-agent", "framework": "LangGraph"}

    @app.get("/api/graph")
    def graph() -> Dict[str, Any]:
        return app.state.bridge.service.graph_info()

    @app.get("/api/models")
    def models() -> Dict[str, Any]:
        return app.state.bridge.service.ollama_models()

    @app.get("/api/polymarket/providers")
    def prediction_providers() -> Dict[str, Any]:
        return app.state.bridge.service.prediction_providers()

    @app.get("/api/polymarket/status")
    def prediction_status(provider: str | None = None) -> Dict[str, Any]:
        return app.state.bridge.service.polymarket_status(provider)

    @app.get("/api/price")
    def price(asset: str = "", query: str = "") -> Dict[str, Any]:
        return app.state.bridge.service.price_preview(asset, query)

    @app.get("/api/history")
    def history(limit: int = 20) -> Dict[str, Any]:
        return {"runs": app.state.bridge.service.history(limit)}

    @app.post("/api/runs", status_code=202)
    def start_run(payload: AnalysisRequest) -> Dict[str, str]:
        return app.state.bridge.start_run(payload.model_dump(exclude_none=True))

    @app.post("/api/analyze")
    def analyze(payload: AnalysisRequest) -> Dict[str, Any]:
        return app.state.bridge.analyze(payload.model_dump(exclude_none=True))

    @app.get("/api/runs/{run_id}/audit")
    def audit(run_id: str) -> Dict[str, Any]:
        result = app.state.bridge.get_audit(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> Dict[str, Any]:
        result = app.state.bridge.get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_ROOT / "index.html")

    app.mount("/", StaticFiles(directory=WEB_ROOT), name="web")
    return app


app = create_app()
