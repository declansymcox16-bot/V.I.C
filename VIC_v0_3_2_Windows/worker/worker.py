from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import psutil

BASE = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE / "config" / "worker.json"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
ACTIVE: dict[str, dict[str, Any]] = {}
ACTIVE_LOCK = threading.RLock()


def load_config() -> dict[str, Any]:
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not cfg.get("worker_id"):
        cfg["worker_id"] = uuid.uuid4().hex
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def post_json(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-VIC-Token": token},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def find_ffmpeg(cfg: dict[str, Any]) -> str | None:
    candidates: list[Path] = []
    configured = str(cfg.get("ffmpeg_path", "")).strip()
    if configured:
        candidates.append(Path(configured))
    candidates.append(BASE / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    normal = shutil.which("ffmpeg")
    if normal:
        candidates.append(Path(normal))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if packages.exists():
            candidates.extend(packages.rglob("ffmpeg.exe"))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:
            pass
    return None


def recordings_root(cfg: dict[str, Any]) -> Path:
    path = Path(str(cfg.get("recordings_dir", "worker_recordings")))
    if not path.is_absolute():
        path = BASE / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def enumerate_screens() -> list[dict[str, Any]]:
    screens: list[dict[str, Any]] = []
    if os.name != "nt":
        return screens
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        monitor_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(RECT),
            wintypes.LPARAM,
        )

        def callback(hmonitor, _hdc, _rect, _data):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                rect = info.rcMonitor
                index = len(screens) + 1
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                device = str(info.szDevice)
                primary = bool(info.dwFlags & 1)
                screens.append(
                    {
                        "id": device or f"monitor-{index}",
                        "name": f"Display {index}" + (" (Primary)" if primary else ""),
                        "device": device,
                        "x": int(rect.left),
                        "y": int(rect.top),
                        "width": width,
                        "height": height,
                        "label": f"Display {index}{' (Primary)' if primary else ''} — {width}x{height} at ({rect.left},{rect.top})",
                    }
                )
            return True

        ctypes.windll.user32.EnumDisplayMonitors(0, 0, monitor_proc(callback), 0)
    except Exception:
        pass

    if not screens:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            x = int(user32.GetSystemMetrics(76))
            y = int(user32.GetSystemMetrics(77))
            width = int(user32.GetSystemMetrics(78))
            height = int(user32.GetSystemMetrics(79))
            screens.append(
                {
                    "id": "virtual-desktop",
                    "name": "Virtual desktop",
                    "device": "",
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "label": f"Virtual desktop — {width}x{height} at ({x},{y})",
                }
            )
        except Exception:
            pass
    return screens


def detect_devices(ffmpeg: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "video": [],
        "audio": [],
        "audio_inputs": [],
        "speakers": [],
        "screens": enumerate_screens(),
        "speaker_capture_available": False,
        "speaker_error": "",
    }
    if ffmpeg:
        try:
            completed = subprocess.run(
                [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            text = completed.stderr or completed.stdout
            for line in text.splitlines():
                match = re.search(r'"(.+?)"\s+\((video|audio)\)', line)
                if match:
                    name, kind = match.groups()
                    if name not in result[kind]:
                        result[kind].append(name)
        except Exception:
            pass
    result["audio_inputs"] = list(result["audio"])
    try:
        import soundcard as sc
        default = sc.default_speaker()
        default_id = str(getattr(default, "id", "")) if default else ""
        for speaker in sc.all_speakers():
            speaker_id = str(getattr(speaker, "id", ""))
            name = str(getattr(speaker, "name", "") or speaker_id or "Speaker")
            is_default = bool(default_id and speaker_id == default_id)
            result["speakers"].append({
                "id": speaker_id or name,
                "name": name,
                "is_default": is_default,
                "label": name + (" (Default)" if is_default else ""),
            })
        result["speaker_capture_available"] = True
    except Exception as exc:
        result["speaker_error"] = str(exc)
    return result


def scan_recordings(cfg: dict[str, Any], limit: int = 150) -> list[dict[str, Any]]:
    roots: list[tuple[str, Path]] = [("Worker recordings", recordings_root(cfg))]
    legacy = BASE / "recordings"
    if legacy.exists() and legacy.resolve() != roots[0][1].resolve():
        roots.append(("Legacy recordings", legacy))

    items: list[dict[str, Any]] = []
    for root_label, root_path in roots:
        try:
            for path in root_path.rglob("*"):
                if not path.is_file() or path.name == ".gitkeep":
                    continue
                stat = path.stat()
                items.append(
                    {
                        "name": path.name,
                        "relative": str(path.relative_to(root_path)),
                        "path": str(path.resolve()),
                        "folder": str(path.parent.resolve()),
                        "root_label": root_label,
                        "size_mb": round(stat.st_size / (1024 * 1024), 2),
                        "modified_ts": stat.st_mtime,
                        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    }
                )
        except OSError:
            continue
    items.sort(key=lambda item: item["modified_ts"], reverse=True)
    return items[:limit]


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" ._")
    return cleaned or "source"


def resolve_screen(screen_id: str) -> dict[str, Any] | None:
    return next((item for item in enumerate_screens() if item.get("id") == screen_id), None)


def source_input_args(source: dict[str, Any], test: bool = False) -> list[str]:
    kind = source.get("type")
    options = source.get("options", {})
    if kind == "media_file":
        args: list[str] = []
        if options.get("realtime", True):
            args.append("-re")
        if options.get("loop") and not test:
            args += ["-stream_loop", "-1"]
        return args + ["-i", str(options.get("path", ""))]

    if kind == "screen":
        args = ["-f", "gdigrab", "-framerate", str(options.get("fps", 30))]
        target_kind = options.get("target", "desktop")
        if target_kind == "monitor":
            screen = resolve_screen(str(options.get("screen_id", "")))
            if not screen:
                raise ValueError("The selected monitor is no longer available on this worker.")
            args += [
                "-offset_x", str(screen["x"]),
                "-offset_y", str(screen["y"]),
                "-video_size", f'{screen["width"]}x{screen["height"]}',
                "-i", "desktop",
            ]
        else:
            width = int(options.get("width", 0))
            height = int(options.get("height", 0))
            if width and height:
                args += [
                    "-offset_x", str(options.get("offset_x", 0)),
                    "-offset_y", str(options.get("offset_y", 0)),
                    "-video_size", f"{width}x{height}",
                ]
            target = (
                f"title={options.get('window_title', '')}"
                if target_kind == "window"
                else "desktop"
            )
            args += ["-i", target]
        if options.get("audio_device"):
            args += ["-f", "dshow", "-i", f"audio={options['audio_device']}"]
        return args

    if kind == "camera":
        args = ["-f", "dshow", "-framerate", str(options.get("fps", 30))]
        if options.get("resolution"):
            args += ["-video_size", str(options["resolution"])]
        device = f"video={options.get('video_device', '')}"
        if options.get("audio_device"):
            device += f":audio={options['audio_device']}"
        return args + ["-i", device]

    if kind == "audio_device":
        return ["-f", "dshow", "-i", f"audio={options.get('audio_device', '')}"]
    if kind == "rtsp":
        return ["-rtsp_transport", str(options.get("transport", "tcp")), "-i", str(options.get("url", ""))]
    if kind == "network":
        return ["-i", str(options.get("url", ""))]
    raise ValueError(f"Unsupported FFmpeg source type: {kind}")


def output_args(source: dict[str, Any], output: Path) -> list[str]:
    kind = source.get("type")
    options = source.get("options", {})
    if kind == "audio_device":
        return ["-map", "0:a:0?", "-c:a", "flac", "-f", "matroska", str(output.with_suffix(".mka"))]
    if kind in {"screen", "camera"}:
        mapping = ["-map", "0:v:0", "-map", "0:a:0?"]
        if kind == "screen" and options.get("audio_device"):
            mapping = ["-map", "0:v:0", "-map", "1:a:0?"]
        return mapping + [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-f", "matroska",
            str(output),
        ]
    return ["-map", "0", "-c", "copy", "-f", "matroska", str(output)]


def source_has_video(source: dict[str, Any]) -> bool:
    return source.get("type") in {"media_file", "screen", "camera", "rtsp", "network"}


def source_audio_map(source: dict[str, Any]) -> str | None:
    kind = source.get("type")
    options = source.get("options", {})
    if kind == "audio_device":
        return "0:a:0"
    if kind == "screen" and options.get("audio_device"):
        return "1:a:0"
    if kind == "camera" and options.get("audio_device"):
        return "0:a:0"
    return None


def update_job(cfg: dict[str, Any], job_id: str, **fields: Any) -> None:
    payload = {"job_id": job_id, **fields}
    try:
        post_json(cfg["dashboard_url"].rstrip("/") + "/api/worker/job-update", cfg["cluster_token"], payload)
    except Exception as exc:
        print("Unable to update job:", exc)



def start_speaker_job(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    source = job["source"]
    options = source.get("options", {})
    speaker_id = str(options.get("speaker_id", ""))
    speaker_name = str(options.get("speaker_name", "") or speaker_id or "Speaker output")
    samplerate = int(options.get("samplerate", 48000))
    try:
        import numpy as np
        import soundcard as sc
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Speaker capture packages are missing: {exc}")
        return
    try:
        loopback = sc.get_microphone(id=speaker_id, include_loopback=True)
    except Exception:
        try:
            loopback = sc.get_microphone(id=speaker_name, include_loopback=True)
        except Exception as exc:
            update_job(cfg, job["id"], state="failed", message=f"Could not open speaker loopback {speaker_name}: {exc}")
            return

    stop_event = threading.Event()
    folder = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.wav"
    test_mode = job.get("mode") == "test"

    local: dict[str, Any] = {
        "kind": "thread",
        "stop": stop_event,
        "output": "3-second speaker test" if test_mode else str(output),
        "audio_level_db": -70.0,
        "audio_updated_ts": 0.0,
    }

    def loop() -> None:
        import wave
        started = time.time()
        wav = None
        try:
            raw_channels = getattr(loopback, "channels", 2) or 2
            if isinstance(raw_channels, (list, tuple)):
                channels = len(raw_channels)
            else:
                channels = int(raw_channels)
            channels = max(1, min(2, channels))
            if not test_mode:
                wav = wave.open(str(output), "wb")
                wav.setnchannels(channels)
                wav.setsampwidth(2)
                wav.setframerate(samplerate)
            update_job(cfg, job["id"], state="running", message=f"Capturing speaker output: {speaker_name}", output=local["output"], audio_level_db=-70.0)
            with loopback.recorder(samplerate=samplerate, channels=channels, blocksize=2048) as recorder:
                while not stop_event.is_set():
                    frames = recorder.record(numframes=2048)
                    samples = np.asarray(frames, dtype=np.float32)
                    if samples.ndim == 1:
                        samples = samples.reshape(-1, 1)
                    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
                    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
                    db = max(-70.0, min(0.0, 20.0 * float(np.log10(max(rms, 1e-7)))))
                    local["audio_level_db"] = round(db, 1)
                    local["audio_updated_ts"] = time.time()
                    if wav is not None:
                        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
                        wav.writeframes(pcm.tobytes())
                    if test_mode and time.time() - started >= 3.0:
                        break
            if test_mode:
                update_job(cfg, job["id"], state="finished", message=f"Speaker test passed. Latest level {local['audio_level_db']} dB", output=local["output"], audio_level_db=local["audio_level_db"])
            else:
                update_job(cfg, job["id"], state="stopped", message="Speaker loopback stopped", output=str(output), audio_level_db=local["audio_level_db"])
        except Exception as exc:
            update_job(cfg, job["id"], state="failed", message=f"Speaker loopback failed: {exc}", output=str(output), audio_level_db=local.get("audio_level_db"))
        finally:
            if wav is not None:
                wav.close()
            with ACTIVE_LOCK:
                ACTIVE.pop(job["id"], None)

    thread = threading.Thread(target=loop, daemon=True, name=f"VIC-Speaker-{job['id'][:8]}")
    local["thread"] = thread
    with ACTIVE_LOCK:
        ACTIVE[job["id"]] = local
    thread.start()


def start_folder_job(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    source = job["source"]
    watch_path = Path(str(source.get("options", {}).get("path", "")))
    if not watch_path.is_dir():
        update_job(cfg, job["id"], state="failed", message=f"Folder does not exist: {watch_path}")
        return
    if job.get("mode") == "test":
        update_job(cfg, job["id"], state="finished", message=f"Folder exists and can be watched: {watch_path}")
        return

    destination = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}" / "ingested"
    destination.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()
    extensions = {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm",
        ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg",
        ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    }

    def loop() -> None:
        known: set[str] = set()
        update_job(cfg, job["id"], state="running", message="Watching folder", output=str(destination))
        while not stop_event.is_set():
            try:
                for item in watch_path.iterdir():
                    key = str(item.resolve())
                    if item.is_file() and item.suffix.lower() in extensions and key not in known:
                        known.add(key)
                        target = destination / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{item.name}"
                        shutil.copy2(item, target)
                        update_job(cfg, job["id"], state="running", message=f"Copied {item.name}", output=str(target))
            except OSError as exc:
                update_job(cfg, job["id"], state="running", message=f"Folder watch warning: {exc}")
            stop_event.wait(2)
        update_job(cfg, job["id"], state="stopped", message="Folder watch stopped", output=str(destination))

    thread = threading.Thread(target=loop, daemon=True, name=f"VIC-Watch-{job['id'][:8]}")
    with ACTIVE_LOCK:
        ACTIVE[job["id"]] = {
            "kind": "thread",
            "thread": thread,
            "stop": stop_event,
            "output": str(destination),
            "audio_level_db": None,
        }
    thread.start()


def start_website_job(cfg: dict[str, Any], ffmpeg: str | None, job: dict[str, Any]) -> None:
    source = job["source"]
    url = str(source.get("options", {}).get("url", ""))
    if job.get("mode") == "test":
        command = [sys.executable, "-m", "yt_dlp", "--simulate", "--no-playlist", "--", url]
        output = "Website test"
    else:
        folder = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}"
        folder.mkdir(parents=True, exist_ok=True)
        template = folder / "%(title).120s_%(id)s.%(ext)s"
        command = [
            sys.executable, "-m", "yt_dlp", "--no-playlist", "--newline",
            "--merge-output-format", "mkv", "-o", str(template),
        ]
        if ffmpeg:
            command += ["--ffmpeg-location", ffmpeg]
        if source.get("options", {}).get("live_from_start"):
            command.append("--live-from-start")
        command += ["--", url]
        output = str(folder)
    launch_process(cfg, job, command, output)


def stderr_reader(local: dict[str, Any], stream, log_handle) -> None:
    meter_pattern = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")
    try:
        for line in iter(stream.readline, ""):
            log_handle.write(line)
            log_handle.flush()
            match = meter_pattern.search(line)
            if match:
                try:
                    level = float(match.group(1))
                    with ACTIVE_LOCK:
                        local["audio_level_db"] = max(-70.0, min(0.0, level))
                        local["audio_updated_ts"] = time.time()
                except ValueError:
                    pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def launch_process(
    cfg: dict[str, Any],
    job: dict[str, Any],
    command: list[str],
    output: str,
    preview_path: Path | None = None,
    meter_enabled: bool = False,
) -> None:
    log_path = LOG_DIR / f"job_{job['id']}.log"
    log_handle = log_path.open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n{time.ctime()}\n{subprocess.list2cmdline(command)}\n")
    log_handle.flush()

    try:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.PIPE if meter_enabled else log_handle,
            text=meter_enabled,
            bufsize=1 if meter_enabled else -1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        log_handle.close()
        update_job(cfg, job["id"], state="failed", message=str(exc))
        return

    local: dict[str, Any] = {
        "kind": "process",
        "process": process,
        "log": log_handle,
        "output": output,
        "mode": job.get("mode", "record"),
        "preview_path": str(preview_path) if preview_path else "",
        "preview_last_sent": 0.0,
        "audio_level_db": -70.0 if meter_enabled else None,
        "audio_updated_ts": 0.0,
        "meter_enabled": meter_enabled,
    }
    with ACTIVE_LOCK:
        ACTIVE[job["id"]] = local

    if meter_enabled and process.stderr is not None:
        thread = threading.Thread(
            target=stderr_reader,
            args=(local, process.stderr, log_handle),
            daemon=True,
            name=f"VIC-Meter-{job['id'][:8]}",
        )
        local["stderr_thread"] = thread
        thread.start()

    update_job(
        cfg,
        job["id"],
        state="running",
        message="Worker process started",
        output=output,
        pid=process.pid,
        audio_level_db=local["audio_level_db"],
        preview_available=bool(preview_path),
    )


def start_ffmpeg_job(cfg: dict[str, Any], ffmpeg: str | None, job: dict[str, Any]) -> None:
    if not ffmpeg:
        update_job(cfg, job["id"], state="failed", message="FFmpeg was not found on this worker.")
        return
    source = job["source"]
    try:
        input_args = source_input_args(source, test=job.get("mode") == "test")
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=str(exc))
        return

    if job.get("mode") == "test":
        test_maps = ["-map", "0:v:0?"]
        audio_map = source_audio_map(source)
        if audio_map:
            test_maps += ["-map", audio_map]
        else:
            test_maps += ["-map", "0:a:0?"]
        command = (
            [ffmpeg, "-hide_banner", "-loglevel", "error"]
            + input_args
            + ["-t", "3"]
            + test_maps
            + ["-f", "null", "-"]
        )
        launch_process(cfg, job, command, "3-second source test")
        return

    folder = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.mkv"
    command = [ffmpeg, "-hide_banner", "-loglevel", "info", "-y"] + input_args + output_args(source, output)

    preview_path: Path | None = None
    if source_has_video(source):
        preview_path = LOG_DIR / f"live_{job['id']}.jpg"
        command += [
            "-map", "0:v:0",
            "-vf", "fps=1/2,scale=480:-2",
            "-q:v", "7",
            "-update", "1",
            str(preview_path),
        ]

    audio_map = source_audio_map(source)
    meter_enabled = bool(audio_map)
    if audio_map:
        command += [
            "-map", audio_map,
            "-filter:a", "ebur128=framelog=verbose",
            "-f", "null", "-",
        ]

    launch_process(
        cfg,
        job,
        command,
        str(output),
        preview_path=preview_path,
        meter_enabled=meter_enabled,
    )


def open_recordings_folder(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    folder = recordings_root(cfg)
    try:
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        update_job(cfg, job["id"], state="finished", message="Opened recordings folder", output=str(folder))
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Could not open folder: {exc}", output=str(folder))


def start_job(cfg: dict[str, Any], ffmpeg: str | None, job: dict[str, Any]) -> None:
    with ACTIVE_LOCK:
        if job["id"] in ACTIVE:
            return
    update_job(cfg, job["id"], state="starting", message="Worker accepted job")
    if job.get("mode") == "open_recordings":
        open_recordings_folder(cfg, job)
        return
    kind = job.get("source", {}).get("type")
    if kind == "folder_watch":
        start_folder_job(cfg, job)
    elif kind == "speaker_output":
        start_speaker_job(cfg, job)
    elif kind == "website":
        start_website_job(cfg, ffmpeg, job)
    else:
        start_ffmpeg_job(cfg, ffmpeg, job)


def stop_job(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    with ACTIVE_LOCK:
        local = ACTIVE.get(job["id"])
    if not local:
        update_job(cfg, job["id"], state="stopped", message="Job was not running on this worker")
        return

    if local["kind"] == "thread":
        local["stop"].set()
        local["thread"].join(timeout=4)
    else:
        process = local["process"]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        thread = local.get("stderr_thread")
        if thread:
            thread.join(timeout=3)
        local["log"].close()
        update_job(
            cfg,
            job["id"],
            state="stopped",
            message="Stopped by dashboard",
            output=local.get("output", ""),
            audio_level_db=local.get("audio_level_db"),
        )
    with ACTIVE_LOCK:
        ACTIVE.pop(job["id"], None)


def monitor_jobs(cfg: dict[str, Any]) -> None:
    with ACTIVE_LOCK:
        snapshot = list(ACTIVE.items())
    for job_id, local in snapshot:
        if local["kind"] != "process":
            continue
        process = local["process"]
        code = process.poll()
        if code is None:
            continue
        thread = local.get("stderr_thread")
        if thread:
            thread.join(timeout=3)
        try:
            local["log"].close()
        except Exception:
            pass
        state = "finished" if code == 0 else "failed"
        message = (
            "Test passed"
            if local.get("mode") == "test" and code == 0
            else "Process finished"
            if code == 0
            else f"Process exited with code {code}. See logs/job_{job_id}.log"
        )
        update_job(
            cfg,
            job_id,
            state=state,
            message=message,
            output=local.get("output", ""),
            audio_level_db=local.get("audio_level_db"),
        )
        with ACTIVE_LOCK:
            ACTIVE.pop(job_id, None)


def active_status_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    with ACTIVE_LOCK:
        snapshot = list(ACTIVE.items())
    for job_id, local in snapshot:
        item: dict[str, Any] = {
            "audio_level_db": local.get("audio_level_db"),
            "audio_updated_ts": local.get("audio_updated_ts", 0),
            "output": local.get("output", ""),
        }
        preview_text = str(local.get("preview_path", ""))
        if preview_text:
            preview = Path(preview_text)
            try:
                modified = preview.stat().st_mtime
                if modified > float(local.get("preview_last_sent", 0)):
                    item["preview_b64"] = base64.b64encode(preview.read_bytes()).decode("ascii")
                    item["preview_modified_ts"] = modified
                    local["preview_last_sent"] = modified
            except OSError:
                pass
        payload[job_id] = item
    return payload


def dashboard_is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    cfg = load_config()
    dashboard = str(cfg["dashboard_url"]).rstrip("/")
    token = str(cfg["cluster_token"])
    worker_id = str(cfg["worker_id"])
    local_dashboard = dashboard_is_local(dashboard)
    configured_name = str(cfg.get("worker_name") or "").strip()
    worker_name = configured_name or (
        f"Local PC (this computer) — {socket.gethostname()}"
        if local_dashboard
        else socket.gethostname()
    )
    poll = max(1, int(cfg.get("poll_seconds", 2)))
    ffmpeg = find_ffmpeg(cfg)
    devices = detect_devices(ffmpeg)
    last_device_scan = time.time()
    last_recording_scan = 0.0
    recording_inventory: list[dict[str, Any]] = []
    psutil.cpu_percent(interval=None)

    print("=" * 64)
    print("VIC Worker v0.3.2")
    print("Name:", worker_name)
    print("Dashboard:", dashboard)
    print("FFmpeg:", ffmpeg or "NOT FOUND")
    print("Recordings:", recordings_root(cfg))
    print("=" * 64)

    while True:
        monitor_jobs(cfg)
        now = time.time()
        if now - last_device_scan >= 30:
            ffmpeg = find_ffmpeg(cfg)
            devices = detect_devices(ffmpeg)
            last_device_scan = now
        if now - last_recording_scan >= 5:
            recording_inventory = scan_recordings(cfg)
            last_recording_scan = now

        disk = psutil.disk_usage(str(recordings_root(cfg)))
        heartbeat = {
            "id": worker_id,
            "name": worker_name,
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "is_local_dashboard": local_dashboard,
            "cpu": round(psutil.cpu_percent(interval=None), 1),
            "memory": round(psutil.virtual_memory().percent, 1),
            "disk_free_gb": round(disk.free / (1024 ** 3), 1),
            "recordings_root": str(recordings_root(cfg)),
            "ffmpeg": ffmpeg or "",
            "devices": devices,
            "recordings": recording_inventory,
            "active_status": active_status_payload(),
        }
        try:
            response = post_json(dashboard + "/api/worker/heartbeat", token, heartbeat)
            remote_jobs = response.get("jobs", [])
            for job in remote_jobs:
                if job.get("desired_state") == "stopped":
                    stop_job(cfg, job)
                elif job.get("state") in {"pending", "starting", "running"}:
                    start_job(cfg, ffmpeg, job)
        except URLError as exc:
            print(time.strftime("%H:%M:%S"), "Dashboard connection failed:", exc)
        except Exception as exc:
            print(time.strftime("%H:%M:%S"), "Worker error:", exc)
        time.sleep(poll)


if __name__ == "__main__":
    main()
