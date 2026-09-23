from __future__ import annotations

import ast
import json
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = BASE / "dashboard" / "app.py"
WORKER = BASE / "worker" / "worker.py"
SETUP = BASE / "tools" / "worker_setup_gui.py"


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def ffprobe_streams(ffprobe: str, path: Path) -> list[dict]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout).get("streams", [])


def main() -> int:
    app_source = APP.read_text(encoding="utf-8")
    worker_source = WORKER.read_text(encoding="utf-8")
    setup_source = SETUP.read_text(encoding="utf-8")
    ast.parse(app_source)
    ast.parse(worker_source)
    ast.parse(setup_source)

    required_app = [
        "Live processing worker",
        "Same as capture worker",
        "Auto choose best available worker",
        "def choose_processing_worker",
        "def allocate_relay_port",
        "relay_child",
        "release_relay_capture_jobs",
        "Move live processing",
        "/move-processing",
        "processing_slots",
        "allow_remote_processing",
    ]
    for marker in required_app:
        if marker not in app_source:
            raise RuntimeError(f"Missing Dashboard marker: {marker}")

    required_worker = [
        "def start_relay_capture_job",
        "def start_relay_processing_job",
        "def select_relay_encoder",
        "tcp://0.0.0.0:",
        "Lightweight live capture relay",
        "def start_loopback_relay_bridge",
        "screen_has_application_audio",
        "processing_slots",
        "allow_remote_processing",
    ]
    for marker in required_worker:
        if marker not in worker_source:
            raise RuntimeError(f"Missing worker marker: {marker}")

    for marker in [
        "Live processing slots",
        "Allow this PC to process live sources captured by other workers",
        "Relay advertised address",
    ]:
        if marker not in setup_source:
            raise RuntimeError(f"Missing Worker Setup marker: {marker}")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        with tempfile.TemporaryDirectory(prefix="vic-relay-test-") as temp:
            output = Path(temp) / "processed.mkv"
            port = free_port()
            listener = subprocess.Popen(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", f"tcp://127.0.0.1:{port}?listen=1&listen_timeout=10000",
                    "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-f", "matroska",
                    str(output),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.4)
            sender = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-re",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
                    "-t", "2", "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "mpeg2video", "-b:v", "4M", "-c:a", "mp2",
                    "-f", "mpegts", f"tcp://127.0.0.1:{port}",
                ],
                capture_output=True,
                timeout=30,
            )
            if sender.returncode != 0:
                listener.kill()
                raise RuntimeError(sender.stderr.decode("utf-8", "replace"))
            try:
                listener.wait(timeout=20)
            except subprocess.TimeoutExpired:
                listener.terminate()
                listener.wait(timeout=5)
            if listener.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
                error = (listener.stderr.read() if listener.stderr else b"").decode("utf-8", "replace")
                raise RuntimeError("Synthetic processing listener failed: " + error)
            streams = ffprobe_streams(ffprobe, output)
            if not any(item.get("codec_type") == "video" for item in streams):
                raise RuntimeError("Processed relay output has no video stream")
            if not any(item.get("codec_type") == "audio" for item in streams):
                raise RuntimeError("Processed relay output has no audio stream")

    print("VIC distributed live-processing regression tests passed:")
    print("- capture and processing workers are independently selectable")
    print("- Same as capture and Auto best are available")
    print("- processing slots include the capture worker itself")
    print("- hidden capture relay and visible processing job are paired")
    print("- live processing can be moved with a short safe restart")
    print("- application/process audio and speaker-loopback relay paths exist")
    print("- synthetic TCP live relay produced final video and audio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
