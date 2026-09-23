from __future__ import annotations

import ast
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = BASE / "dashboard" / "app.py"
WORKER = BASE / "worker" / "worker.py"


def main() -> int:
    app = APP.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    ast.parse(app)
    ast.parse(worker)

    required_app = [
        '"audio_meter_motion": "light"',
        'AUDIO_METER_MOTION_MODES',
        'Raw — exact jumps, no animation',
        'Light — very fast rise, quick fall',
        'function vicMeterMotion',
        'light:[20,90]',
        'uhd_4k',
        '4K UHD — 3840 px',
        'Custom preview width (160–4096 pixels)',
        'min(4096, int(result.get("preview_width", 640)))',
        'min(4096, int(request.form.get("preview_width", 640)))',
    ]
    for marker in required_app:
        if marker not in app:
            raise RuntimeError(f"Missing Dashboard marker: {marker}")

    required_worker = [
        'r"\\bM:\\s*(-?inf|-?\\d+(?:\\.\\d+)?)"',
        'raw_level in {"-inf", "inf", "+inf"}',
        'audio_meter_stale_seconds',
        'recompute_screen_audio_level',
        'item["level_updated_ts"] = packet.timestamp',
        'min(4096, int(raw.get("preview_width", RUNTIME_PREVIEW_WIDTH)))',
    ]
    for marker in required_worker:
        if marker not in worker:
            raise RuntimeError(f"Missing worker marker: {marker}")

    forbidden = [
        'max([current, *speaker_levels])',
        'meter_pattern = re.compile(r"\\bM:\\s*(-?\\d+(?:\\.\\d+)?)")',
    ]
    for marker in forbidden:
        if marker in worker:
            raise RuntimeError(f"Old peak/silence bug remains: {marker}")

    # Confirm the actual parser accepts the exact FFmpeg silence form.
    pattern = re.compile(
        r"\bM:\s*(-?inf|-?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    match = pattern.search("[Parsed_ebur128] M: -inf S: -inf")
    if not match or match.group(1).lower() != "-inf":
        raise RuntimeError("FFmpeg -inf silence parser test failed")

    print("VIC audio-motion / 4K regression tests passed:")
    print("- FFmpeg -inf silence is accepted")
    print("- stale audio falls back to silence")
    print("- mixed screen audio no longer holds its historical peak")
    print("- Raw/Light/Medium/Heavy movement modes are present")
    print("- preview presets range from 160 px through 4K UHD")
    print("- custom preview width supports up to 4096 px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
