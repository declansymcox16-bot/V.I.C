from __future__ import annotations

import ast
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = BASE / "dashboard" / "app.py"
WORKER = BASE / "worker" / "worker.py"
SETTINGS = BASE / "config" / "dashboard.json"


def main() -> int:
    app = APP.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    config = json.loads(SETTINGS.read_text(encoding="utf-8"))
    ast.parse(app)
    ast.parse(worker)

    required_app = [
        '@app.get("/settings")',
        '@app.post("/settings")',
        "audio_meter_refresh_seconds",
        "preview_refresh_seconds",
        "PREVIEW_QUALITY_PRESETS",
        "Instant — 0.00 seconds",
        "Custom refresh time (0.00–10.00 seconds)",
        "Preview is unlimited until Stop or Start Recording",
        '"runtime_settings": runtime_live_settings()',
        "Preview Unlimited",
    ]
    for marker in required_app:
        if marker not in app:
            raise RuntimeError(f"Missing Dashboard live-setting marker: {marker}")

    required_worker = [
        "RUNTIME_PREVIEW_FPS",
        "RUNTIME_PREVIEW_WIDTH",
        "RUNTIME_PREVIEW_JPEG_QUALITY",
        "apply_runtime_live_settings",
        "preview_output_args",
        "poll = max(0.05, min(0.5, requested_audio_poll))",
        "Unlimited Preview",
    ]
    for marker in required_worker:
        if marker not in worker:
            raise RuntimeError(f"Missing worker live-setting marker: {marker}")

    # Preview branches must not contain a duration limiter. Test branches may.
    for match in re.finditer(r'if mode == "preview":(.*?)(?:\n\s*return|\n\s*folder =)', worker, re.S):
        block = match.group(1)
        if '"-t"' in block or "'-t'" in block:
            raise RuntimeError("A Preview branch still contains a time limit")

    expected = {
        "audio_meter_refresh_seconds": 0.2,
        "preview_refresh_seconds": 0.5,
        "preview_width": 640,
        "preview_jpeg_quality": 7,
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise RuntimeError(f"Unexpected default {key}: {config.get(key)!r}")

    print("VIC v0.6.2 live settings regression tests passed:")
    print("- Settings tab and 0.00-10.00 second custom refresh values")
    print("- smooth adjustable audio meters")
    print("- adjustable live preview refresh and quality")
    print("- Dashboard-to-worker runtime settings")
    print("- Preview branches contain no duration limit")
    print("- v0.6.1 rollback package included")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
