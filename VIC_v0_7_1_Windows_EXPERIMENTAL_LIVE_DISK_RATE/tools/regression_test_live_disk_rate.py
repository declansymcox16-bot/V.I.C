from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"
WORKER_PATH = BASE / "worker" / "worker.py"


def load_worker():
    worker_dir = str(WORKER_PATH.parent)
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
    name = "vic_live_disk_rate_regression_worker"
    spec = importlib.util.spec_from_file_location(name, WORKER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load worker.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    app_source = APP_PATH.read_text(encoding="utf-8")
    worker_source = WORKER_PATH.read_text(encoding="utf-8")
    ast.parse(app_source)
    ast.parse(worker_source)

    for marker in [
        "def current_recording_file_size",
        "def update_disk_write_rate",
        "DISK_WRITE_RATE_WINDOW_SECONDS",
        "recording_directory_baseline",
        "disk_write_bytes_per_second",
        "Never add it to the same file's",
    ]:
        if marker not in worker_source:
            raise RuntimeError(f"Missing worker marker: {marker}")

    for marker in [
        "Current file size",
        "Actual disk write rate",
        "health-write-",
        "writeRateText",
        "Mb/s",
        "MB/s",
    ]:
        if marker not in app_source:
            raise RuntimeError(f"Missing Dashboard marker: {marker}")

    worker = load_worker()

    with tempfile.TemporaryDirectory(prefix="vic-disk-rate-test-") as temp:
        temp_root = Path(temp)

        recording = temp_root / "recording.mkv"
        recording.write_bytes(b"x" * (2 * 1024 * 1024))
        explicit_local = {
            "is_recording": True,
            "file_size_bytes": 2 * 1024 * 1024,
            "output_paths": [str(recording)],
        }
        measured = worker.current_recording_file_size(explicit_local, 100.0)
        if measured != 2 * 1024 * 1024:
            raise RuntimeError(f"Explicit file was double-counted: {measured}")

        website_folder = temp_root / "website"
        website_folder.mkdir()
        (website_folder / "old_recording.mp4").write_bytes(b"o" * 500_000)
        baseline = worker.directory_file_sizes(website_folder)
        (website_folder / "live.part").write_bytes(b"n" * 1_250_000)
        website_local = {
            "is_recording": True,
            "file_size_bytes": 0,
            "output_paths": [],
            "recording_directory": str(website_folder),
            "recording_directory_baseline": baseline,
            "recording_directory_scan_ts": 0.0,
            "recording_directory_measured_bytes": 0,
        }
        website_size = worker.current_recording_file_size(website_local, 200.0)
        if website_size != 1_250_000:
            raise RuntimeError(f"Website measurement was wrong: {website_size}")

        rate_local = {"disk_write_samples": []}
        worker.update_disk_write_rate(rate_local, 0, 300.0)
        rate = worker.update_disk_write_rate(rate_local, 1024 * 1024, 301.0)
        if not (1_040_000 <= rate <= 1_060_000):
            raise RuntimeError(f"Unexpected write rate: {rate}")

        worker.update_disk_write_rate(rate_local, 1024 * 1024, 303.0)
        stopped_rate = worker.update_disk_write_rate(rate_local, 1024 * 1024, 304.6)
        if stopped_rate != 0:
            raise RuntimeError(f"Paused writer did not return to zero: {stopped_rate}")

    print("VIC live disk-rate regression tests passed:")
    print("- current FFmpeg file size is not double-counted")
    print("- Twitch/website .part growth excludes older recordings")
    print("- one MiB/s file growth is measured correctly")
    print("- a paused writer falls back to zero")
    print("- Health displays both Mb/s and MB/s")
    print("- individual source view displays size and write rate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
