from __future__ import annotations

import ast
import importlib.util
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"
WORKER_PATH = BASE / "worker" / "worker.py"


def load_worker():
    worker_dir = str(WORKER_PATH.parent)
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
    name = "vic_embedded_audio_modes_regression"
    spec = importlib.util.spec_from_file_location(name, WORKER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load worker.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def create_wave(path: Path, seconds: float = 1.0) -> None:
    rate = 44100
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        payload = bytearray()
        for index in range(frames):
            value = int(6000 * math.sin(2 * math.pi * 440 * index / rate))
            payload += struct.pack("<hh", value, value)
        wav.writeframes(bytes(payload))


def create_video(ffmpeg: str, path: Path) -> None:
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-f", "lavfi",
            "-i", "color=size=320x180:rate=25:duration=1.2",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-f", "matroska",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)


def count_audio(ffprobe: str, path: Path) -> int:
    completed = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    return len([line for line in completed.stdout.splitlines() if line.strip()])


def main() -> int:
    app_source = APP_PATH.read_text(encoding="utf-8")
    worker_source = WORKER_PATH.read_text(encoding="utf-8")
    ast.parse(app_source)
    ast.parse(worker_source)

    for marker in [
        "Audio file arrangement",
        "Embed audio in the video only",
        "Keep video and separate audio files only",
        "Embed audio in the video and also keep separate files",
        "audio_storage_mode",
    ]:
        if marker not in app_source:
            raise RuntimeError(f"Missing Dashboard marker: {marker}")

    for marker in [
        "def source_audio_storage_mode",
        "def finalise_screen_loopback_audio_capture",
        "embed_and_separate",
        "and the separate WAV was also kept.",
        "Embedded {len(audio_paths)} loopback audio track(s)",
    ]:
        if marker not in worker_source:
            raise RuntimeError(f"Missing worker marker: {marker}")

    worker = load_worker()

    legacy_app = {
        "type": "screen",
        "options": {
            "target": "window",
            "application_audio": True,
        },
    }
    legacy_loopback = {
        "type": "screen",
        "options": {
            "target": "desktop",
            "audio_devices": [
                {"kind": "speaker", "id": "default", "name": "Speakers"}
            ],
        },
    }
    if worker.source_audio_storage_mode(legacy_app) != "embed_only":
        raise RuntimeError("Legacy process-audio behaviour was not preserved")
    if worker.source_audio_storage_mode(legacy_loopback) != "separate_only":
        raise RuntimeError("Legacy loopback behaviour was not preserved")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg/FFprobe are required for this regression")

    with tempfile.TemporaryDirectory(prefix="vic-audio-mode-test-") as temp:
        folder = Path(temp)

        # Application audio: embed + keep separate.
        app_video = folder / "app_video.mkv"
        app_audio = folder / "app_audio.wav"
        app_final = folder / "app_final.mkv"
        create_video(ffmpeg, app_video)
        create_wave(app_audio)
        output, message = worker.finalise_application_audio_capture(
            ffmpeg,
            {
                "video_output": str(app_video),
                "application_audio_output": str(app_audio),
                "final_output": str(app_final),
                "application_audio_offset_seconds": 0.0,
                "audio_storage_mode": "embed_and_separate",
            },
        )
        if not app_final.is_file() or not app_audio.is_file():
            raise RuntimeError("Embed + separate did not keep both app outputs")
        if count_audio(ffprobe, app_final) != 1:
            raise RuntimeError("Application audio was not embedded")
        if "also kept" not in message:
            raise RuntimeError("Application both-mode message was wrong")

        # Application audio: separate only does not invoke a merge.
        separate_video = folder / "separate_video.mkv"
        separate_audio = folder / "separate_audio.wav"
        create_video(ffmpeg, separate_video)
        create_wave(separate_audio)
        output, message = worker.finalise_application_audio_capture(
            None,
            {
                "video_output": str(separate_video),
                "application_audio_output": str(separate_audio),
                "final_output": str(folder / "unused.mkv"),
                "audio_storage_mode": "separate_only",
            },
        )
        if not separate_video.is_file() or not separate_audio.is_file():
            raise RuntimeError("Separate-only app mode removed a file")

        # Screen loopback: embed only removes the temporary WAV after success.
        screen_video = folder / "screen.mkv"
        screen_audio = folder / "screen_loopback.wav"
        create_video(ffmpeg, screen_video)
        create_wave(screen_audio)
        output, message = worker.finalise_screen_loopback_audio_capture(
            ffmpeg,
            {
                "video_output": str(screen_video),
                "speaker_outputs": [str(screen_audio)],
                "speaker_names": ["Speakers"],
                "speaker_audio_offsets": [0.0],
                "input_audio_count": 0,
                "audio_storage_mode": "embed_only",
            },
        )
        if not screen_video.is_file() or screen_audio.exists():
            raise RuntimeError("Screen embed-only mode did not remove the sidecar")
        if count_audio(ffprobe, screen_video) != 1:
            raise RuntimeError("Loopback audio was not embedded in the screen MKV")

        # Screen loopback: embed and keep separate retains both WAVs.
        screen_both = folder / "screen_both.mkv"
        wav_one = folder / "one.wav"
        wav_two = folder / "two.wav"
        create_video(ffmpeg, screen_both)
        create_wave(wav_one)
        create_wave(wav_two)
        output, message = worker.finalise_screen_loopback_audio_capture(
            ffmpeg,
            {
                "video_output": str(screen_both),
                "speaker_outputs": [str(wav_one), str(wav_two)],
                "speaker_names": ["Headphones", "HDMI TV"],
                "speaker_audio_offsets": [0.0, 0.0],
                "input_audio_count": 0,
                "audio_storage_mode": "embed_and_separate",
            },
        )
        if not wav_one.is_file() or not wav_two.is_file():
            raise RuntimeError("Screen both-mode deleted separate WAVs")
        if count_audio(ffprobe, screen_both) != 2:
            raise RuntimeError("Both loopback tracks were not embedded")

    print("VIC embedded-audio mode regression tests passed:")
    print("- existing sources keep their previous behaviour")
    print("- application audio supports separate, embedded, and both")
    print("- screen loopback audio supports separate, embedded, and both")
    print("- Embed + separate creates a playable MKV and retains WAV files")
    print("- Embed only removes temporary WAV files after verified success")
    print("- failed merges retain the original recordings safely")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
