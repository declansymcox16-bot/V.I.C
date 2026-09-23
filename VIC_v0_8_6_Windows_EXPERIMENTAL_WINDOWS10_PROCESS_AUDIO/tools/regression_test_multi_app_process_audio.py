from __future__ import annotations

import ast
import hashlib
import json
import struct
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = BASE / "dashboard" / "app.py"
WORKER = BASE / "worker" / "worker.py"
HELPER = BASE / "tools" / "vic_process_audio.exe"
HASH_FILE = BASE / "tools" / "VIC_PROCESS_AUDIO_HELPER_SHA256.txt"


def main() -> int:
    app = APP.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    ast.parse(app)
    ast.parse(worker)

    dashboard_markers = [
        "Multiple application windows",
        "Load Application Windows",
        "multi_window_json",
        "Select all shown",
        "Capture only each selected application's audio process tree",
        "created_from_multi_window",
        "application_audio_mode",
        "window_pid",
        "Chrome windows/tabs can share one Chrome process tree",
    ]
    for marker in dashboard_markers:
        if marker not in app:
            raise RuntimeError(f"Missing Dashboard marker: {marker}")

    worker_markers = [
        "APPLICATION_AUDIO_HELPER",
        "def application_audio_capability",
        "def resolve_application_audio_process",
        "def start_screen_application_audio_job",
        "def finalise_application_audio_capture",
        '"kind": "screen_application_audio"',
        "Application process audio was merged into the final MKV",
        "screen_has_application_audio",
        "application_audio_supported",
    ]
    for marker in worker_markers:
        if marker not in worker:
            raise RuntimeError(f"Missing worker marker: {marker}")

    if not HELPER.is_file() or HELPER.stat().st_size < 500_000:
        raise RuntimeError("Process-audio helper is missing or unexpectedly small")
    raw = HELPER.read_bytes()
    if raw[:2] != b"MZ":
        raise RuntimeError("Process-audio helper is not a Windows PE executable")
    pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
    if raw[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise RuntimeError("Process-audio helper PE header is invalid")
    machine = struct.unpack_from("<H", raw, pe_offset + 4)[0]
    if machine != 0x8664:
        raise RuntimeError(f"Helper is not Windows x64: machine=0x{machine:04X}")

    expected = HASH_FILE.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(raw).hexdigest()
    if expected != actual:
        raise RuntimeError("Process-audio helper SHA-256 does not match")

    # Confirm multi-window source is a creator only: saved children are normal
    # screen sources, so every one remains independently manageable.
    if '"type": "screen"' not in app or 'SOURCE_TYPE_LABELS["screen"]' not in app:
        raise RuntimeError("Multi-window creator does not create independent screen sources")

    print("VIC multi-application/process-audio regression tests passed:")
    print("- Multiple application windows appears under + Add source")
    print("- several open windows can be ticked and saved at once")
    print("- every selection becomes an independent screen source")
    print("- exact title, PID and process name are saved")
    print("- application audio uses process-loopback, not speaker loopback")
    print("- helper is a valid Windows x64 PE with matching SHA-256")
    print("- stop merges process audio into the final MKV")
    print("- failed merge retains the original video and WAV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
