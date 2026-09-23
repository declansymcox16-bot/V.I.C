from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE / "config"
LOGS_DIR = BASE / "logs"
DEFAULT_RECORDINGS_DIR = BASE / "recordings"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SOURCES_FILE = CONFIG_DIR / "sources.json"

for folder in (CONFIG_DIR, LOGS_DIR, DEFAULT_RECORDINGS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

recording_jobs: dict[str, subprocess.Popen] = {}
watch_jobs: dict[str, tuple[threading.Thread, threading.Event]] = {}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def settings() -> dict[str, Any]:
    return load_json(
        SETTINGS_FILE,
        {
            "port": 8765,
            "recordings_dir": "recordings",
            "ffmpeg_path": "",
            "open_browser_on_start": True,
        },
    )


def recordings_root() -> Path:
    configured = str(settings().get("recordings_dir", "recordings")).strip()
    path = Path(configured)
    if not path.is_absolute():
        path = BASE / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_sources() -> list[dict[str, Any]]:
    return load_json(SOURCES_FILE, [])


def save_sources(sources: list[dict[str, Any]]) -> None:
    save_json(SOURCES_FILE, sources)


def find_ffmpeg() -> str | None:
    configured = str(settings().get("ffmpeg_path", "")).strip()
    candidates: list[Path] = []

    if configured:
        candidates.append(Path(configured))

    candidates.append(BASE / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")

    normal = shutil.which("ffmpeg")
    if normal:
        candidates.append(Path(normal))

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        winget_root = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
        if winget_root.exists():
            candidates.extend(winget_root.rglob("ffmpeg.exe"))

    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:
            continue
    return None


def safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def input_arguments(source: dict[str, Any], for_test: bool = False) -> list[str]:
    source_type = source.get("type", "")
    options = source.get("options", {})

    if source_type == "media_file":
        args: list[str] = []
        if options.get("realtime", True):
            args.append("-re")
        if options.get("loop", False) and not for_test:
            args.extend(["-stream_loop", "-1"])
        args.extend(["-i", str(options.get("path", ""))])
        return args

    if source_type == "screen":
        fps = safe_int(options.get("fps"), 30, 1, 120)
        args = ["-f", "gdigrab", "-framerate", str(fps)]

        width = safe_int(options.get("width"), 0, 0, 16384)
        height = safe_int(options.get("height"), 0, 0, 16384)
        offset_x = safe_int(options.get("offset_x"), 0, -16384, 16384)
        offset_y = safe_int(options.get("offset_y"), 0, -16384, 16384)

        if width and height:
            args.extend([
                "-offset_x", str(offset_x),
                "-offset_y", str(offset_y),
                "-video_size", f"{width}x{height}",
            ])

        if options.get("target") == "window":
            args.extend(["-i", f'title={str(options.get("window_title", "")).strip()}'])
        else:
            args.extend(["-i", "desktop"])

        audio_device = str(options.get("audio_device", "")).strip()
        if audio_device:
            args.extend(["-f", "dshow", "-i", f"audio={audio_device}"])
        return args

    if source_type in {"camera", "audio_device"}:
        video_device = str(options.get("video_device", "")).strip()
        audio_device = str(options.get("audio_device", "")).strip()
        fps = safe_int(options.get("fps"), 30, 1, 120)
        resolution = str(options.get("resolution", "")).strip()

        args = ["-f", "dshow"]
        if source_type == "camera":
            args.extend(["-framerate", str(fps)])
            if resolution:
                args.extend(["-video_size", resolution])
            combined = f"video={video_device}"
            if audio_device:
                combined += f":audio={audio_device}"
            args.extend(["-i", combined])
        else:
            args.extend(["-i", f"audio={audio_device}"])
        return args

    if source_type == "rtsp":
        return [
            "-rtsp_transport",
            str(options.get("transport", "tcp")),
            "-i",
            str(options.get("url", "")),
        ]

    if source_type == "network":
        return ["-i", str(options.get("url", ""))]

    raise ValueError(f"Unsupported FFmpeg source type: {source_type}")


def output_arguments(source: dict[str, Any], output_file: Path) -> list[str]:
    source_type = source.get("type", "")
    options = source.get("options", {})

    if source_type == "audio_device":
        return [
            "-map", "0:a:0?",
            "-c:a", "flac",
            "-f", "matroska",
            str(output_file.with_suffix(".mka")),
        ]

    if source_type in {"screen", "camera"}:
        mapping = ["-map", "0:v:0", "-map", "0:a:0?"]
        if source_type == "screen" and options.get("audio_device"):
            mapping = ["-map", "0:v:0", "-map", "1:a:0?"]

        return mapping + [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-f", "matroska",
            str(output_file),
        ]

    return [
        "-map", "0",
        "-c", "copy",
        "-f", "matroska",
        str(output_file),
    ]


def source_status(source: dict[str, Any]) -> str:
    source_id = source["id"]

    if source.get("type") == "folder_watch":
        job = watch_jobs.get(source_id)
        return "RUNNING" if job and job[0].is_alive() else "STOPPED"

    process = recording_jobs.get(source_id)
    return "RECORDING" if process and process.poll() is None else "STOPPED"


def source_output_folder(source: dict[str, Any]) -> Path:
    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", source["name"]).strip(" ._") or "source"
    folder = recordings_root() / f'{safe_name}_{source["id"]}'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def append_log(filename: str, text: str) -> None:
    with (LOGS_DIR / filename).open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(text.rstrip() + "\n")


def start_ffmpeg_source(source: dict[str, Any]) -> tuple[bool, str]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False, "FFmpeg was not found. Run INSTALL_FFMPEG.bat, close VIC, then reopen it."

    existing = recording_jobs.get(source["id"])
    if existing and existing.poll() is None:
        return False, "This source is already recording."

    output_file = source_output_folder(source) / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.mkv"

    try:
        command = (
            [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]
            + input_arguments(source)
            + output_arguments(source, output_file)
        )
    except ValueError as exc:
        return False, str(exc)

    log_path = LOGS_DIR / f"source_{source['id']}.log"
    log_handle = log_path.open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n{time.ctime()} START\n{subprocess.list2cmdline(command)}\n")
    log_handle.flush()

    try:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        log_handle.close()
        return False, str(exc)

    recording_jobs[source["id"]] = process
    return True, "Recording started."


def stop_ffmpeg_source(source_id: str) -> None:
    process = recording_jobs.get(source_id)
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
    recording_jobs.pop(source_id, None)


def folder_watch_loop(source: dict[str, Any], stop_event: threading.Event) -> None:
    watch_path = Path(str(source.get("options", {}).get("path", "")))
    destination = source_output_folder(source) / "ingested"
    destination.mkdir(parents=True, exist_ok=True)
    known: set[str] = set()
    extensions = {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm",
        ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg",
        ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    }

    while not stop_event.is_set():
        try:
            for item in watch_path.iterdir():
                resolved = str(item.resolve())
                if item.is_file() and item.suffix.lower() in extensions and resolved not in known:
                    known.add(resolved)
                    target = destination / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{item.name}"
                    shutil.copy2(item, target)
                    append_log(
                        f"source_{source['id']}.log",
                        f"{time.ctime()} COPIED {item} -> {target}",
                    )
        except OSError as exc:
            append_log(f"source_{source['id']}.log", f"{time.ctime()} WATCH ERROR {exc}")
        stop_event.wait(2)


def start_folder_watch(source: dict[str, Any]) -> tuple[bool, str]:
    path = Path(str(source.get("options", {}).get("path", "")))
    if not path.is_dir():
        return False, "The watched folder does not exist."

    existing = watch_jobs.get(source["id"])
    if existing and existing[0].is_alive():
        return False, "This folder watch is already running."

    stop_event = threading.Event()
    thread = threading.Thread(
        target=folder_watch_loop,
        args=(source, stop_event),
        daemon=True,
        name=f"VIC-FolderWatch-{source['id']}",
    )
    watch_jobs[source["id"]] = (thread, stop_event)
    thread.start()
    return True, "Folder watching started."


def stop_folder_watch(source_id: str) -> None:
    job = watch_jobs.pop(source_id, None)
    if job:
        job[1].set()
        job[0].join(timeout=3)


def stop_source_job(source: dict[str, Any]) -> None:
    if source.get("type") == "folder_watch":
        stop_folder_watch(source["id"])
    else:
        stop_ffmpeg_source(source["id"])


def test_source(source: dict[str, Any]) -> tuple[bool, str]:
    if source.get("type") == "folder_watch":
        path = Path(str(source.get("options", {}).get("path", "")))
        return (
            (True, f"Folder exists and can be watched: {path}")
            if path.is_dir()
            else (False, f"Folder does not exist: {path}")
        )

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False, "FFmpeg was not found."

    command = (
        [ffmpeg, "-hide_banner", "-loglevel", "error"]
        + input_arguments(source, for_test=True)
        + ["-t", "3", "-map", "0:v:0?", "-map", "0:a:0?", "-f", "null", "-"]
    )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "The test timed out."
    except OSError as exc:
        return False, str(exc)

    if result.returncode == 0:
        return True, "Source test succeeded. VIC received media for 3 seconds."

    return False, (result.stderr or result.stdout or "Unknown FFmpeg error").strip()[-2500:]


def create_snapshot(source: dict[str, Any]) -> tuple[bool, str | Path]:
    if source.get("type") in {"audio_device", "folder_watch"}:
        return False, "This source type does not produce a video preview."

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False, "FFmpeg was not found."

    preview_path = LOGS_DIR / f"preview_{source['id']}.jpg"
    command = (
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        + input_arguments(source, for_test=True)
        + ["-frames:v", "1", "-q:v", "2", str(preview_path)]
    )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "Preview timed out."
    except OSError as exc:
        return False, str(exc)

    if result.returncode == 0 and preview_path.exists():
        return True, preview_path
    return False, (result.stderr or "Unable to create preview.")[-2500:]


def detect_dshow_devices() -> dict[str, list[str]]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {"video": [], "audio": [], "error": ["FFmpeg was not found."]}

    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"video": [], "audio": [], "error": [str(exc)]}

    video: list[str] = []
    audio: list[str] = []
    for line in (result.stderr or result.stdout).splitlines():
        match = re.search(r'"(.+?)"\s+\((video|audio)\)', line)
        if match:
            name, kind = match.groups()
            target = video if kind == "video" else audio
            if name not in target:
                target.append(name)

    return {"video": video, "audio": audio, "error": []}
