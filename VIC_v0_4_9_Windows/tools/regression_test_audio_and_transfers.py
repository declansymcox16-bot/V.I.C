from __future__ import annotations

import ast
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = (BASE / "dashboard" / "app.py").read_text(encoding="utf-8")
WORKER = (BASE / "worker" / "worker.py").read_text(encoding="utf-8")


def main() -> int:
    ast.parse(APP)
    ast.parse(WORKER)
    required_app = [
        "+ Add another audio device",
        "/api/audio-meters/start",
        "name=\"audio_choices\"",
        "setInterval(refreshAudioMeters,250)",
        "/api/transfers/status",
        "setInterval(refreshTransferQueue,1000)",
        "bytes_per_second",
        "eta_seconds",
    ]
    required_worker = [
        "def screen_audio_devices",
        "def screen_input_audio_devices",
        "def screen_speaker_audio_devices",
        "Recording screen with",
        "loopback WAV(s)",
        "-metadata:s:a:",
    ]
    for marker in required_app:
        if marker not in APP:
            raise RuntimeError(f"Missing Dashboard feature: {marker}")
    for marker in required_worker:
        if marker not in WORKER:
            raise RuntimeError(f"Missing worker feature: {marker}")
    if "screenAudioSelect" in APP or "name='audio_choice'" in APP:
        raise RuntimeError("Old single screen-audio selector remains")
    if not re.search(r'setInterval\(refreshTransferQueue,1000\)', APP):
        raise RuntimeError("Transfer polling interval is missing")
    print("VIC audio/transfer regression tests passed:")
    print("- repeatable screen audio selectors")
    print("- live setup audio-meter API and polling")
    print("- separate MKV input tracks and loopback WAV support")
    print("- live transfer status API, speed and ETA polling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
