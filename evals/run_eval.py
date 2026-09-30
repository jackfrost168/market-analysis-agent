"""Run the labelled benchmark against the real Agent and write a JSON report."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from agent.service import AgentService
from evals.metrics import evaluate_case, summarize_evaluation


ROOT = Path(__file__).resolve().parents[1]


def load_cases(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Evaluation case file must contain a JSON array")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the financial Agent benchmark")
    parser.add_argument("--cases", type=Path, default=ROOT / "evals" / "benchmark.json")
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "latest_report.json")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.limit is not None:
        cases = cases[: max(0, args.limit)]
    results = []
    for case in cases:
        started = time.perf_counter()
        try:
            if args.data_dir:
                state = AgentService(args.data_dir).analyze(case.get("request") or {})
            else:
                with tempfile.TemporaryDirectory(prefix="market-agent-eval-") as directory:
                    state = AgentService(Path(directory)).analyze(case.get("request") or {})
            results.append(
                evaluate_case(
                    case,
                    state,
                    status="completed",
                    wall_duration_ms=round((time.perf_counter() - started) * 1000),
                )
            )
        except Exception as exc:
            results.append(
                evaluate_case(
                    case,
                    {},
                    status="failed",
                    wall_duration_ms=round((time.perf_counter() - started) * 1000),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    report = summarize_evaluation(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False, indent=2))
    print(f"Full report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
