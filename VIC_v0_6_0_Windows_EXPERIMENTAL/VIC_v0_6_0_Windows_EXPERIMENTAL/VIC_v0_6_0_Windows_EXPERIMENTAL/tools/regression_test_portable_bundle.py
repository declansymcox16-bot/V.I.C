from __future__ import annotations

import ast
import json
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = BASE / "dashboard" / "app.py"
WORKER = BASE / "worker" / "worker.py"


def main() -> int:
    app = APP.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    ast.parse(app)
    ast.parse(worker)

    app_markers = [
        '@app.get("/sources/manage")',
        '@app.post("/sources/<source_id>/duplicate")',
        '@app.get("/sources/<source_id>/export")',
        '@app.post("/sources/import")',
        '@app.get("/storage")',
        '@app.get("/tools")',
        '@app.post("/recordings/protect")',
        '@app.post("/workers/<worker_id>/restore-recycled")',
        'Automatic post-record',
        'CONFIG_BACKUP_DIR',
    ]
    for marker in app_markers:
        if marker not in app:
            raise RuntimeError(f"Missing Dashboard feature: {marker}")

    worker_markers = [
        'def recycle_recording_file',
        'def scan_recycle_bin',
        'def benchmark_recording_drive',
        'waiting_reconnect',
        '"recycle_bin": scan_recycle_bin(cfg)',
    ]
    for marker in worker_markers:
        if marker not in worker:
            raise RuntimeError(f"Missing Worker feature: {marker}")

    rollback = BASE / "rollback" / "VIC_v0_5_1_Windows.zip"
    if not rollback.is_file():
        raise RuntimeError("Embedded v0.5.1 rollback ZIP is missing")
    with zipfile.ZipFile(rollback, "r") as archive:
        if archive.testzip():
            raise RuntimeError("Embedded rollback ZIP is damaged")

    for bat in [
        "BACKUP_VIC_CONFIG.bat",
        "RESTORE_LATEST_CONFIG_BACKUP.bat",
        "ROLLBACK_TO_V0_5_1.bat",
    ]:
        if not (BASE / bat).is_file():
            raise RuntimeError(f"Missing portable BAT: {bat}")

    print("VIC v0.6.0 portable-bundle regression test passed:")
    print("- source library management")
    print("- storage and disk benchmark")
    print("- protected recordings and recycle bin")
    print("- config backup/restore and embedded v0.5.1 rollback")
    print("- worker copy/support ZIP routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
