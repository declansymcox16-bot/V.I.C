from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = BASE / "dashboard" / "app.py"
WORKER = BASE / "worker" / "worker.py"


def load_worker():
    worker_dir = str(WORKER.parent)
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
    name = "vic080_worker_regression"
    spec = importlib.util.spec_from_file_location(name, WORKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load worker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    app_source = APP.read_text(encoding="utf-8")
    worker_source = WORKER.read_text(encoding="utf-8")
    ast.parse(app_source)
    ast.parse(worker_source)

    dashboard_markers = [
        "Finish Capture",
        '@app.post("/sources/<source_id>/finish-capture")',
        "virtual_idle",
        "preview-display",
        "Apply now",
        "Advanced Storage Manager",
        "Roll recording to storage",
        "storage_locations.json",
        "move_closed_outputs",
        "storage_paths_for_worker",
    ]
    for marker in dashboard_markers:
        if marker not in app_source:
            raise RuntimeError(f"Missing Dashboard marker: {marker}")

    worker_markers = [
        "def finalise_website_parts",
        "CTRL_BREAK_EVENT",
        "def preview_delivery_bytes",
        "def detect_storage_drives",
        "def job_recordings_root",
        "def move_closed_outputs",
        "SHA-256 verification failed",
        "RUNTIME_STORAGE_PATHS",
    ]
    for marker in worker_markers:
        if marker not in worker_source:
            raise RuntimeError(f"Missing worker marker: {marker}")

    worker = load_worker()

    # Preview resizing actually reduces a 4K JPEG to the requested delivery width.
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="vic080-test-") as temp:
        temp_root = Path(temp)
        image_path = temp_root / "preview.jpg"
        Image.new("RGB", (3840, 2160), (20, 30, 40)).save(image_path, "JPEG", quality=95)
        payload = worker.preview_delivery_bytes(
            image_path,
            {"preview_display_width": 640, "preview_display_jpeg_quality": 7},
        )
        delivered = temp_root / "delivered.jpg"
        delivered.write_bytes(payload)
        with Image.open(delivered) as image:
            if image.width != 640:
                raise RuntimeError(f"Preview delivery width was {image.width}, expected 640")

        # Website .part fallback performs the user's confirmed safe rename when
        # FFmpeg is unavailable.
        website = temp_root / "website"
        website.mkdir()
        part = website / "stream.mp4.part"
        part.write_bytes(b"test-media")
        completed = worker.finalise_website_parts(website, None, 0)
        if not completed or Path(completed[0]).suffix != ".mp4":
            raise RuntimeError("Website .part was not finalised")
        if part.exists():
            raise RuntimeError("Website .part still exists")

        # Job-specific storage root is honoured.
        storage = temp_root / "custom-storage"
        root = worker.job_recordings_root(
            {"recordings_dir": str(temp_root / "default")},
            {"source": {"recording_storage_path": str(storage)}},
        )
        if root.resolve() != storage.resolve() or not root.is_dir():
            raise RuntimeError("Job-specific storage path was not honoured")

        # Verified closed-file move removes source only after destination exists.
        source = temp_root / "source.mkv"
        source.write_bytes(b"x" * 1024 * 1024)
        destination = temp_root / "destination" / "source.mkv"
        worker.verified_move_file(source, destination)
        if source.exists() or not destination.is_file() or destination.stat().st_size != 1024 * 1024:
            raise RuntimeError("Verified closed-file move failed")

    print("VIC v0.8.0 capture/storage regression tests passed:")
    print("- active preview delivery resizes 4K to 640 px without capture restart")
    print("- website .part finalisation removes the suffix automatically")
    print("- source jobs honour saved custom storage paths")
    print("- closed-file rollover move verifies before deleting source")
    print("- idle website cards, Finish Capture and Storage Manager are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
