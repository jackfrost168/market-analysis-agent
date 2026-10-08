"""One short Converse smoke test after attaching the EC2 IAM role."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel
from agent.bedrock import DEFAULT_MODEL, credential_status, generate_bedrock


class Probe(BaseModel):
    ok: bool


def main():
    status = credential_status()
    if not status["available"]:
        print(json.dumps(status))
        return 2
    result = generate_bedrock('Return JSON only: {"ok":true}', Probe, DEFAULT_MODEL, {"region": "us-east-1"},
                             temperature=0, timeout_seconds=30, max_output_tokens=64)
    print(json.dumps({"success": result.success and result.data == {"ok": True}, "model": result.model,
                      "latency_ms": result.latency_ms, "usage": result.usage, "error": result.error}, indent=2))
    return 0 if result.success and result.data == {"ok": True} else 1


if __name__ == "__main__":
    raise SystemExit(main())
