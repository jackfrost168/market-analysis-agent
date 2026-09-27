import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional


class RunRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.database_path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    user_query TEXT NOT NULL,
                    symbol TEXT,
                    task_type TEXT,
                    status TEXT NOT NULL,
                    report_json TEXT,
                    state_json TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_created ON agent_runs(created_at DESC)"
            )

    def save(self, state: Dict[str, Any], status: str = "completed"):
        task = state.get("task") or {}
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id, conversation_id, created_at, user_query, symbol,
                    task_type, status, report_json, state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    report_json = excluded.report_json,
                    state_json = excluded.state_json
                """,
                (
                    state.get("run_id"),
                    state.get("conversation_id") or state.get("run_id"),
                    (state.get("report") or {}).get("generated_at") or task.get("as_of") or "",
                    state.get("user_query") or "",
                    task.get("symbol"),
                    task.get("task_type"),
                    status,
                    json.dumps(state.get("report") or {}, ensure_ascii=False, default=str),
                    json.dumps(state, ensure_ascii=False, default=str),
                ),
            )

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, conversation_id, created_at, user_query, symbol,
                       task_type, status
                FROM agent_runs ORDER BY created_at DESC LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json") or "{}")
        item["state"] = json.loads(item.pop("state_json") or "{}")
        return item
