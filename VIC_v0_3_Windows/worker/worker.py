from __future__ import annotations

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
from urllib.request import Request, urlopen

import psutil

BASE = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE / "config" / "worker.json"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
ACTIVE: dict[str, dict[str, Any]] = {}


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
    with urlopen(request, timeout=10) as response:
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


def detect_devices(ffmpeg: str | None) -> dict[str, list[str]]:
    result = {"video": [], "audio": [], "screens": []}
    try:
        import ctypes
        user32 = ctypes.windll.user32
        virtual_x = user32.GetSystemMetrics(76)
        virtual_y = user32.GetSystemMetrics(77)
        virtual_w = user32.GetSystemMetrics(78)
        virtual_h = user32.GetSystemMetrics(79)
        result["screens"].append(f"Virtual desktop: {virtual_w}x{virtual_h} at ({virtual_x},{virtual_y})")
    except Exception:
        result["screens"].append("Screen details unavailable")
    if not ffmpeg:
        return result
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
    return result


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" ._")
    return cleaned or "source"


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
        width, height = int(options.get("width", 0)), int(options.get("height", 0))
        if width and height:
            args += ["-offset_x", str(options.get("offset_x", 0)), "-offset_y", str(options.get("offset_y", 0)), "-video_size", f"{width}x{height}"]
        target = f"title={options.get('window_title', '')}" if options.get("target") == "window" else "desktop"
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
        return mapping + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-f", "matroska", str(output)]
    return ["-map", "0", "-c", "copy", "-f", "matroska", str(output)]


def update_job(cfg: dict[str, Any], job_id: str, **fields: Any) -> None:
    payload = {"job_id": job_id, **fields}
    try:
        post_json(cfg["dashboard_url"].rstrip("/") + "/api/worker/job-update", cfg["cluster_token"], payload)
    except Exception as exc:
        print("Unable to update job:", exc)


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
    extensions = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}
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
    ACTIVE[job["id"]] = {"kind": "thread", "thread": thread, "stop": stop_event, "output": str(destination)}
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
        command = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--newline", "--merge-output-format", "mkv", "-o", str(template)]
        if ffmpeg:
            command += ["--ffmpeg-location", ffmpeg]
        if source.get("options", {}).get("live_from_start"):
            command.append("--live-from-start")
        command += ["--", url]
        output = str(folder)
    launch_process(cfg, job, command, output)


def launch_process(cfg: dict[str, Any], job: dict[str, Any], command: list[str], output: str) -> None:
    log_path = LOG_DIR / f"job_{job['id']}.log"
    log_handle = log_path.open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n{time.ctime()}\n{subprocess.list2cmdline(command)}\n")
    log_handle.flush()
    try:
        process = subprocess.Popen(command, stdout=log_handle, stderr=log_handle, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        log_handle.close()
        update_job(cfg, job["id"], state="failed", message=str(exc))
        return
    ACTIVE[job["id"]] = {"kind": "process", "process": process, "log": log_handle, "output": output, "mode": job.get("mode", "record")}
    update_job(cfg, job["id"], state="running", message="Worker process started", output=output, pid=process.pid)


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
        command = [ffmpeg, "-hide_banner", "-loglevel", "error"] + input_args + ["-t", "3", "-map", "0:v:0?", "-map", "0:a:0?", "-f", "null", "-"]
        launch_process(cfg, job, command, "3-second source test")
        return
    folder = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.mkv"
    command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"] + input_args + output_args(source, output)
    launch_process(cfg, job, command, str(output))


def start_job(cfg: dict[str, Any], ffmpeg: str | None, job: dict[str, Any]) -> None:
    if job["id"] in ACTIVE:
        return
    update_job(cfg, job["id"], state="starting", message="Worker accepted job")
    kind = job.get("source", {}).get("type")
    if kind == "folder_watch":
        start_folder_job(cfg, job)
    elif kind == "website":
        start_website_job(cfg, ffmpeg, job)
    else:
        start_ffmpeg_job(cfg, ffmpeg, job)


def stop_job(cfg: dict[str, Any], job: dict[str, Any]) -> None:
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
        local["log"].close()
        update_job(cfg, job["id"], state="stopped", message="Stopped by dashboard", output=local.get("output", ""))
    ACTIVE.pop(job["id"], None)


def monitor_jobs(cfg: dict[str, Any]) -> None:
    for job_id, local in list(ACTIVE.items()):
        if local["kind"] != "process":
            continue
        process = local["process"]
        code = process.poll()
        if code is None:
            continue
        local["log"].close()
        state = "finished" if code == 0 else "failed"
        message = "Test passed" if local.get("mode") == "test" and code == 0 else ("Process finished" if code == 0 else f"Process exited with code {code}. See logs/job_{job_id}.log")
        update_job(cfg, job_id, state=state, message=message, output=local.get("output", ""))
        ACTIVE.pop(job_id, None)


def main() -> None:
    cfg = load_config()
    dashboard = str(cfg["dashboard_url"]).rstrip("/")
    token = str(cfg["cluster_token"])
    worker_id = str(cfg["worker_id"])
    worker_name = str(cfg.get("worker_name") or socket.gethostname())
    poll = max(1, int(cfg.get("poll_seconds", 2)))
    ffmpeg = find_ffmpeg(cfg)
    devices = detect_devices(ffmpeg)
    last_device_scan = time.time()
    psutil.cpu_percent(interval=None)

    print("=" * 58)
    print("VIC Worker v0.3")
    print("Name:", worker_name)
    print("Dashboard:", dashboard)
    print("FFmpeg:", ffmpeg or "NOT FOUND")
    print("Recordings:", recordings_root(cfg))
    print("=" * 58)

    while True:
        monitor_jobs(cfg)
        if time.time() - last_device_scan >= 30:
            devices = detect_devices(ffmpeg)
            last_device_scan = time.time()
        disk = psutil.disk_usage(str(recordings_root(cfg)))
        heartbeat = {
            "id": worker_id,
            "name": worker_name,
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "cpu": round(psutil.cpu_percent(interval=None), 1),
            "memory": round(psutil.virtual_memory().percent, 1),
            "disk_free_gb": round(disk.free / (1024 ** 3), 1),
            "ffmpeg": ffmpeg or "",
            "devices": devices,
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
