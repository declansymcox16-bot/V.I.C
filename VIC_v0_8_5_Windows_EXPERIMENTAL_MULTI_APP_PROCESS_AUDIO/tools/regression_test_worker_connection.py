from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"

def main() -> int:
    source = APP_PATH.read_text(encoding="utf-8")

    required = [
        "DURABLE_CONFIG_FILES",
        'with tmp.open(',
        '"w",',
        "handle.flush()",
        "os.fsync(handle.fileno())",
        "os.replace(tmp, path)",
        "tmp.unlink(missing_ok=True)",
        "if path in DURABLE_CONFIG_FILES",
    ]
    for marker in required:
        if marker not in source:
            raise RuntimeError(f"Missing save repair marker: {marker}")

    forbidden = [
        'tmp.write_text(encoded, encoding="utf-8")',
        'with tmp.open("rb") as handle:',
    ]
    for marker in forbidden:
        if marker in source:
            raise RuntimeError(f"Broken v0.6.0 save pattern remains: {marker}")

    # Import and exercise the real Dashboard using the user's installed Flask.
    import importlib.util
    name = "vic_connection_regression_dashboard"
    spec = importlib.util.spec_from_file_location(name, APP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load dashboard/app.py")
    dashboard = importlib.util.module_from_spec(spec)
    sys.modules[name] = dashboard
    spec.loader.exec_module(dashboard)

    with tempfile.TemporaryDirectory(prefix="vic-heartbeat-test-") as temp:
        temp_root = Path(temp)
        config = temp_root / "config"
        backups = temp_root / "config_backups"
        previews = temp_root / "previews"
        staging = temp_root / "staging"
        logs = temp_root / "logs"
        for folder in (config, backups, previews, staging, logs):
            folder.mkdir(parents=True, exist_ok=True)

        dashboard.CONFIG = config
        dashboard.SOURCES_FILE = config / "sources.json"
        dashboard.JOBS_FILE = config / "jobs.json"
        dashboard.WORKERS_FILE = config / "workers.json"
        dashboard.TRANSFERS_FILE = config / "transfers.json"
        dashboard.SETTINGS_FILE = config / "dashboard.json"
        dashboard.RECORDING_FLAGS_FILE = config / "recording_flags.json"
        dashboard.CONFIG_BACKUP_DIR = backups
        dashboard.PREVIEW_DIR = previews
        dashboard.TRANSFER_STAGE_DIR = staging
        dashboard.DASHBOARD_ERROR_LOG = logs / "dashboard_error.log"
        dashboard.DURABLE_CONFIG_FILES = {
            dashboard.SOURCES_FILE,
            dashboard.SETTINGS_FILE,
            dashboard.RECORDING_FLAGS_FILE,
        }

        def write(path: Path, value) -> None:
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")

        write(dashboard.SOURCES_FILE, [])
        write(dashboard.JOBS_FILE, [])
        write(dashboard.WORKERS_FILE, [])
        write(dashboard.TRANSFERS_FILE, [])
        write(dashboard.RECORDING_FLAGS_FILE, {})
        write(
            dashboard.SETTINGS_FILE,
            {
                "cluster_token": "heartbeat-test-token",
                "worker_offline_seconds": 60,
                "port": 8765,
            },
        )

        for number in range(100):
            dashboard.save_json(
                dashboard.WORKERS_FILE,
                [{"id": "stress-worker", "sequence": number}],
            )

        saved = json.loads(
            dashboard.WORKERS_FILE.read_text(encoding="utf-8")
        )
        if saved[0]["sequence"] != 99:
            raise RuntimeError("Atomic workers.json stress test failed")
        if dashboard.WORKERS_FILE.with_suffix(".json.tmp").exists():
            raise RuntimeError("Temporary workers JSON was not cleaned")
        if list(backups.rglob("workers_*.json")):
            raise RuntimeError("workers.json created heartbeat backup churn")

        client = dashboard.app.test_client()
        heartbeat = {
            "id": "connection-test-worker",
            "name": "Connection Test Worker",
            "worker_version": "0.6.1",
            "host": "TEST-PC",
            "platform": "Windows",
            "is_local_dashboard": False,
            "cpu": 4.0,
            "memory": 8.0,
            "disk_free_gb": 100.0,
            "disk_total_gb": 200.0,
            "recordings": [],
            "recycle_bin": [],
            "devices": {
                "video": [],
                "audio": [],
                "audio_inputs": [],
                "speakers": [],
                "screens": [],
            },
            "active_status": {},
        }

        for _ in range(10):
            response = client.post(
                "/api/worker/heartbeat",
                json=heartbeat,
                headers={"X-VIC-Token": "heartbeat-test-token"},
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Heartbeat returned HTTP {response.status_code}: "
                    + response.get_data(as_text=True)[:300]
                )
            payload = response.get_json()
            if not payload or payload.get("ok") is not True:
                raise RuntimeError("Heartbeat response did not contain ok=true")

        workers = json.loads(
            dashboard.WORKERS_FILE.read_text(encoding="utf-8")
        )
        matches = [
            item for item in workers
            if item.get("id") == "connection-test-worker"
        ]
        if len(matches) != 1:
            raise RuntimeError("Heartbeat worker was not saved exactly once")

    print("VIC worker-connection regression tests passed:")
    print("- 100 atomic workers.json saves")
    print("- no volatile heartbeat backup churn")
    print("- 10 authenticated worker heartbeats returned HTTP 200")
    print("- worker appeared exactly once in workers.json")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
