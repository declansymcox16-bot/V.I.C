from __future__ import annotations

import ast
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WORKER = BASE / "worker" / "worker.py"
DASHBOARD = BASE / "dashboard" / "app.py"
SETUP = BASE / "tools" / "worker_setup_gui.py"
CONFIG = BASE / "config" / "worker.json"

def main() -> int:
    worker = WORKER.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    setup = SETUP.read_text(encoding="utf-8")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    ast.parse(worker)
    ast.parse(dashboard)
    ast.parse(setup)

    required_worker = [
        "TRANSFER_PARALLEL_LIMIT = 3",
        "threading.BoundedSemaphore",
        "TRANSFER_CHUNK_SIZE = 4 * 1024 * 1024",
        "TRANSFER_PROGRESS_INTERVAL = 0.25",
        "transfer_progress_sender",
        "VIC-Transfer-Progress-Sender",
        "TRANSFER_SLOTS.acquire",
    ]
    for marker in required_worker:
        if marker not in worker:
            raise RuntimeError(f"Missing worker fast-transfer marker: {marker}")

    required_dashboard = [
        "TRANSFER_CHUNK_SIZE = 4 * 1024 * 1024",
        "TRANSFER_STATUS_REFRESH_MS = 250",
        "setInterval(refreshTransferQueue,250)",
        '"Cache-Control"',
        "bytes_per_second=0",
        "rate_sample_bytes=0",
    ]
    for marker in required_dashboard:
        if marker not in dashboard:
            raise RuntimeError(f"Missing Dashboard fast-transfer marker: {marker}")

    required_setup = [
        "Simultaneous file transfers",
        'values=["1", "2", "3", "4", "5", "6"]',
        '"transfer_parallel_limit"',
    ]
    for marker in required_setup:
        if marker not in setup:
            raise RuntimeError(f"Missing Worker Setup marker: {marker}")

    if config.get("transfer_parallel_limit") != 3:
        raise RuntimeError("Default parallel transfer limit is not 3")

    print("VIC fast-transfer regression test passed:")
    print("- three simultaneous transfers by default")
    print("- configurable 1-6 transfer slots")
    print("- 4 MB transfer chunks")
    print("- non-blocking coalesced progress sender")
    print("- 250 ms Dashboard progress polling")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
