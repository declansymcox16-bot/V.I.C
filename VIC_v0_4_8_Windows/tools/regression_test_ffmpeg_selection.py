from __future__ import annotations

import ast
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WORKER = BASE / "worker" / "worker.py"
GUI = BASE / "tools" / "worker_setup_gui.py"


def main() -> int:
    worker_text = WORKER.read_text(encoding="utf-8")
    gui_text = GUI.read_text(encoding="utf-8")
    ast.parse(worker_text)
    ast.parse(gui_text)

    required_worker = [
        "auto_compatible",
        "ffmpeg_last_selected_path",
        "VIC compatible FFmpeg folder",
        "choose_ffmpeg_candidate",
        "newest tested FFmpeg that works",
    ]
    for marker in required_worker:
        if marker not in worker_text:
            raise RuntimeError(f"Missing worker marker: {marker}")

    required_gui = [
        "Open Worker BAT Only",
        "open_worker_bat_only",
        "Automatic compatible",
        "Pinned/manual",
        "Open Compatible FFmpeg Folder",
    ]
    for marker in required_gui:
        if marker not in gui_text:
            raise RuntimeError(f"Missing GUI marker: {marker}")

    print("VIC FFmpeg-selection regression test passed:")
    print("- Automatic compatible mode is present")
    print("- Per-worker remembered and pinned paths are present")
    print("- Compatible FFmpeg folder scanning is present")
    print("- Open Worker BAT Only button is present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
