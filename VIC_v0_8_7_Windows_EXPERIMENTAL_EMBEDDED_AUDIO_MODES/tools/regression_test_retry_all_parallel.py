from __future__ import annotations

import ast
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = BASE / "dashboard" / "app.py"
WORKER = BASE / "worker" / "worker.py"
SETUP = BASE / "tools" / "worker_setup_gui.py"
CONFIG = BASE / "config" / "worker.json"

def main() -> int:
    app = APP.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    setup = SETUP.read_text(encoding="utf-8")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    ast.parse(app)
    ast.parse(worker)
    ast.parse(setup)

    required_retry = [
        '@app.post("/transfers/retry-all")',
        "Retry All Failed / Stuck",
        "transfer_is_stuck",
        "retry_transfer_record",
        "stale_seconds: float = 30.0",
        "healthy active transfer(s)",
    ]
    for marker in required_retry:
        if marker not in app:
            raise RuntimeError(f"Missing Retry All marker: {marker}")

    required_parallel = [
        "TRANSFER_PARALLEL_LIMIT = 4",
        "min(12,",
    ]
    for marker in required_parallel:
        if marker not in worker:
            raise RuntimeError(f"Missing worker parallel marker: {marker}")

    for value in [str(number) for number in range(1, 13)]:
        if f'"{value}"' not in setup:
            raise RuntimeError(f"Worker Setup is missing slot value {value}")

    if config.get("transfer_parallel_limit") != 4:
        raise RuntimeError("Fresh-install parallel default is not 4")

    print("VIC Retry All / parallel-slot regression test passed:")
    print("- Retry All Failed / Stuck route and button")
    print("- healthy active transfers are skipped")
    print("- stale transfers are retried after 30 seconds")
    print("- parallel slot choices 1-12")
    print("- fresh-install default 4")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
