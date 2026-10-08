"""One short Converse smoke test after attaching the EC2 IAM role."""

import json
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel
from agent.bedrock import DEFAULT_MODEL, DEFAULT_PRICING, credential_status, generate_bedrock


class Probe(BaseModel):
    ok: bool


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=list(DEFAULT_PRICING), default=DEFAULT_MODEL)
    parser.add_argument("--all", action="store_true", help="One short paid request per configured Bedrock model")
    args = parser.parse_args()
    status = credential_status()
    if not status["available"]:
        print(json.dumps(status))
        return 2
    success = True
    for model in DEFAULT_PRICING if args.all else [args.model]:
        result = generate_bedrock('Return JSON only: {"ok":true}', Probe, model, {"region": "us-east-1"},
                                 temperature=0, timeout_seconds=30, max_output_tokens=64)
        valid = result.success and result.data == {"ok": True}
        success = success and valid
        print(json.dumps({"success": valid, "model": result.model, "latency_ms": result.latency_ms,
                          "usage": result.usage, "error": result.error}), flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
