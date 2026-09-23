from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WORKER_PATH = BASE / "worker" / "worker.py"


def load_worker():
    worker_dir = str(WORKER_PATH.parent)
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)

    name = "vic_windows10_process_audio_regression"
    spec = importlib.util.spec_from_file_location(name, WORKER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load worker.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    source = WORKER_PATH.read_text(encoding="utf-8")
    ast.parse(source)

    required = [
        "APPLICATION_AUDIO_COMPAT_MIN_BUILD = 19041",
        "APPLICATION_AUDIO_OFFICIAL_MIN_BUILD = 20348",
        "def application_audio_capability_for_build",
        "Windows 10 compatibility mode",
        "the actual process-audio API result will decide",
        "application_audio_compatibility_mode",
    ]
    for marker in required:
        if marker not in source:
            raise RuntimeError(
                f"Missing Windows 10 compatibility marker: {marker}"
            )

    worker = load_worker()
    capability = worker.application_audio_capability_for_build

    supported, status = capability(
        19045,
        is_windows=True,
        helper_exists=True,
    )
    if not supported:
        raise RuntimeError(
            "Windows 10 build 19045 was incorrectly rejected"
        )
    if "compatibility mode" not in status.casefold():
        raise RuntimeError(
            "Windows 10 build 19045 did not receive compatibility status"
        )

    supported, status = capability(
        20348,
        is_windows=True,
        helper_exists=True,
    )
    if not supported or "Ready" not in status:
        raise RuntimeError(
            "Official build 20348 was not accepted as ready"
        )

    supported, status = capability(
        19040,
        is_windows=True,
        helper_exists=True,
    )
    if supported:
        raise RuntimeError(
            "A pre-Windows-10-2004 build was incorrectly accepted"
        )

    supported, status = capability(
        19045,
        is_windows=True,
        helper_exists=False,
    )
    if supported or "Missing helper" not in status:
        raise RuntimeError(
            "Missing process-audio helper was not detected"
        )

    supported, status = capability(
        0,
        is_windows=True,
        helper_exists=True,
    )
    if not supported or "could not read" not in status:
        raise RuntimeError(
            "Unknown Windows build was not allowed to attempt the real API"
        )

    supported, status = capability(
        22631,
        is_windows=False,
        helper_exists=True,
    )
    if supported:
        raise RuntimeError(
            "A non-Windows worker was incorrectly accepted"
        )

    print("VIC Windows 10 process-audio compatibility tests passed:")
    print("- build 19045 is attempted instead of rejected")
    print("- build 19045 is clearly labelled compatibility mode")
    print("- build 20348+ remains the official ready path")
    print("- builds below 19041 remain blocked")
    print("- missing helper and non-Windows systems remain blocked")
    print("- unknown build numbers attempt the real API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
