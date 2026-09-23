from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"

def load_dashboard():
    name = "vic_dashboard_regression_test"
    spec = importlib.util.spec_from_file_location(name, APP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load dashboard/app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

def main() -> int:
    dashboard = load_dashboard()
    with tempfile.TemporaryDirectory(prefix="vic-dashboard-test-") as temp:
        temp_root = Path(temp)
        config = temp_root / "config"
        config.mkdir()
        dashboard.SOURCES_FILE = config / "sources.json"
        dashboard.JOBS_FILE = config / "jobs.json"
        dashboard.WORKERS_FILE = config / "workers.json"
        dashboard.TRANSFERS_FILE = config / "transfers.json"
        dashboard.SETTINGS_FILE = config / "dashboard.json"
        dashboard.PREVIEW_DIR = temp_root / "previews"
        dashboard.TRANSFER_STAGE_DIR = temp_root / "staging"
        dashboard.PREVIEW_DIR.mkdir()
        dashboard.TRANSFER_STAGE_DIR.mkdir()

        now = time.time()
        source_id = "edit-test-source"
        write_json(dashboard.SOURCES_FILE, [{
            "id": source_id,
            "name": r"Windows Screen C:\Test\Display",
            "type": "screen",
            "type_label": "Screen / desktop / window",
            "worker_id": "remote",
            "options": {"target": "desktop", "fps": 30, "audio_mode": "none"},
            "summary": "Entire desktop",
        }])
        write_json(dashboard.JOBS_FILE, [])
        write_json(dashboard.TRANSFERS_FILE, [])
        write_json(dashboard.SETTINGS_FILE, {
            "cluster_token": "test-token",
            "worker_offline_seconds": 60,
            "port": 8765,
        })
        write_json(dashboard.WORKERS_FILE, [
            {
                "id": "main", "name": "Main PC", "host": "MAIN",
                "worker_version": "0.4.3", "is_local_dashboard": True,
                "last_seen_ts": now, "recordings": [],
            },
            {
                "id": "remote", "name": "Second PC", "host": "REMOTE",
                "worker_version": "0.4.3", "is_local_dashboard": False,
                "last_seen_ts": now,
                "recordings": [{
                    "name": "test.mkv",
                    "relative": "Screen/test.mkv",
                    "path": r"C:\VIC\worker_recordings\Screen\test.mkv",
                    "size_mb": 1.0, "modified": "test",
                }],
            },
        ])

        client = dashboard.app.test_client()
        edit_response = client.get(f"/sources/{source_id}/edit")
        if edit_response.status_code != 200:
            raise RuntimeError(f"Edit route failed: HTTP {edit_response.status_code}")

        move_response = client.post(
            "/recordings/move-all-remote-to-main",
            follow_redirects=False,
        )
        if move_response.status_code not in {302, 303}:
            raise RuntimeError(f"Mass-move route failed: HTTP {move_response.status_code}")

        queued = json.loads(dashboard.TRANSFERS_FILE.read_text(encoding="utf-8"))
        if len(queued) != 1:
            raise RuntimeError(f"Expected 1 queued transfer, found {len(queued)}")

        if dashboard.version_tuple("VIC Worker v0.4.3") != (0, 4, 3):
            raise RuntimeError("Version parsing regression")

    print("VIC dashboard regression tests passed:")
    print("- Edit source route returned HTTP 200")
    print("- Mass-move route queued one transfer without HTTP 500")
    print("- Worker version parsing succeeded")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
