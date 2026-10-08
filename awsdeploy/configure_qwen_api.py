"""Run in an interactive AWS SSH terminal; never put the key in shell history."""

import datetime
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"


def main():
    metrics = json.load(urllib.request.urlopen("http://127.0.0.1:8001/api/metrics?limit=1", timeout=10))
    if metrics.get("active_runs"):
        raise SystemExit("An analysis is active. Retry after it finishes before restarting the service.")
    key = getpass.getpass("Paste US Virginia pay-as-you-go Qwen API key (hidden): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,}", key):
        raise SystemExit("Invalid key format. No configuration changed.")
    for model in ("qwen3-8b", "qwen3-30b-a3b"):
        payload = {"model": model, "messages": [{"role": "user", "content": 'Return JSON only: {"ok":true}'}],
                   "enable_thinking": False, "response_format": {"type": "json_object"},
                   "stream": False, "max_tokens": 32}
        request = urllib.request.Request(BASE_URL + "/chat/completions", data=json.dumps(payload).encode(),
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
            result = json.loads(body["choices"][0]["message"]["content"])
            if result.get("ok") is not True:
                raise ValueError()
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{model}: HTTP {exc.code}. Check US-region key, account billing and model permissions. No configuration changed.") from None
        except Exception:
            raise SystemExit(f"{model}: connection or JSON validation failed. No configuration changed.") from None
        print(model + ": API access verified")
    path = ROOT / ".env"
    backup_dir = Path.home() / ".config/financial-agent/backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        backup = backup_dir / ("env-before-qwen-key-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    lines = path.read_text().splitlines() if path.exists() else []
    updates = {"DASHSCOPE_API_KEY": key, "QWEN_API_BASE_URL": BASE_URL, "OLLAMA_MODEL": "qwen3-8b"}
    lines = [line for line in lines if line.split("=", 1)[0] not in updates]
    lines.extend(name + "=" + value for name, value in updates.items())
    fd, temp_path = tempfile.mkstemp(prefix=".qwen-env-", dir=ROOT)
    try:
        with os.fdopen(fd, "w") as out:
            out.write("\n".join(lines) + "\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    print("Key saved in .env (0600). Restart: sudo systemctl restart financial-agent")


if __name__ == "__main__":
    main()
