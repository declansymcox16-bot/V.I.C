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

from loopback_broker import SharedLoopbackBroker

BASE = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE / "config" / "worker.json"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
ACTIVE: dict[str, dict[str, Any]] = {}
ACTIVE_LOCK = threading.RLock()
LOOPBACK_BROKER = SharedLoopbackBroker()


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


def enable_physical_dpi_awareness() -> None:
    """Ask Windows for physical pixels instead of DPI-scaled logical pixels."""
    if os.name != "nt":
        return

    try:
        import ctypes

        user32 = ctypes.windll.user32

        # Windows 10+: Per-monitor DPI aware v2.
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except Exception:
            pass

        # Windows 8.1+: PROCESS_PER_MONITOR_DPI_AWARE.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass

        # Older Windows fallback.
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


def enumerate_screens() -> list[dict[str, Any]]:
    screens: list[dict[str, Any]] = []
    if os.name != "nt":
        return screens

    enable_physical_dpi_awareness()

    try:
        import ctypes
        from ctypes import wintypes

        CCHDEVICENAME = 32
        CCHFORMNAME = 32
        ENUM_CURRENT_SETTINGS = 0xFFFFFFFF

        class POINTL(ctypes.Structure):
            _fields_ = [
                ("x", wintypes.LONG),
                ("y", wintypes.LONG),
            ]

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
                ("szDevice", wintypes.WCHAR * CCHDEVICENAME),
            ]

        class PRINT_FIELDS(ctypes.Structure):
            _fields_ = [
                ("dmOrientation", ctypes.c_short),
                ("dmPaperSize", ctypes.c_short),
                ("dmPaperLength", ctypes.c_short),
                ("dmPaperWidth", ctypes.c_short),
                ("dmScale", ctypes.c_short),
                ("dmCopies", ctypes.c_short),
                ("dmDefaultSource", ctypes.c_short),
                ("dmPrintQuality", ctypes.c_short),
            ]

        class DISPLAY_FIELDS(ctypes.Structure):
            _fields_ = [
                ("dmPosition", POINTL),
                ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD),
            ]

        class DEVMODE_UNION(ctypes.Union):
            _fields_ = [
                ("print_fields", PRINT_FIELDS),
                ("display_fields", DISPLAY_FIELDS),
            ]

        class FLAGS_UNION(ctypes.Union):
            _fields_ = [
                ("dmDisplayFlags", wintypes.DWORD),
                ("dmNup", wintypes.DWORD),
            ]

        class DEVMODEW(ctypes.Structure):
            _anonymous_ = ("mode_union", "flags_union")
            _fields_ = [
                ("dmDeviceName", wintypes.WCHAR * CCHDEVICENAME),
                ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("mode_union", DEVMODE_UNION),
                ("dmColor", ctypes.c_short),
                ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short),
                ("dmFormName", wintypes.WCHAR * CCHFORMNAME),
                ("dmLogPixels", wintypes.WORD),
                ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD),
                ("flags_union", FLAGS_UNION),
                ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD),
                ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD),
                ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD),
                ("dmPanningHeight", wintypes.DWORD),
            ]

        monitor_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(RECT),
            wintypes.LPARAM,
        )

        user32 = ctypes.windll.user32
        enum_settings = user32.EnumDisplaySettingsW
        enum_settings.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(DEVMODEW),
        ]
        enum_settings.restype = wintypes.BOOL

        def callback(hmonitor, _hdc, _rect, _data):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)

            if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                return True

            rect = info.rcMonitor
            index = len(screens) + 1
            device = str(info.szDevice)
            primary = bool(info.dwFlags & 1)

            # DPI-aware monitor rectangle fallback.
            x = int(rect.left)
            y = int(rect.top)
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            frequency = 0
            bits_per_pixel = 0

            # EnumDisplaySettings returns the active physical signal mode,
            # even when Windows scaling makes the logical desktop smaller.
            mode = DEVMODEW()
            mode.dmSize = ctypes.sizeof(DEVMODEW)
            if device and enum_settings(device, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)):
                if int(mode.dmPelsWidth) > 0 and int(mode.dmPelsHeight) > 0:
                    width = int(mode.dmPelsWidth)
                    height = int(mode.dmPelsHeight)
                x = int(mode.display_fields.dmPosition.x)
                y = int(mode.display_fields.dmPosition.y)
                frequency = int(mode.dmDisplayFrequency)
                bits_per_pixel = int(mode.dmBitsPerPel)

            resolution = f"{width}x{height}"
            refresh_text = f", {frequency} Hz" if frequency > 1 else ""
            label = (
                f"Display {index}"
                f"{' (Primary)' if primary else ''}"
                f" — {resolution}{refresh_text} at ({x},{y})"
            )

            screens.append(
                {
                    "id": device or f"monitor-{index}",
                    "name": f"Display {index}" + (" (Primary)" if primary else ""),
                    "device": device,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "frequency": frequency,
                    "bits_per_pixel": bits_per_pixel,
                    "physical_resolution": resolution,
                    "label": label,
                }
            )
            return True

        user32.EnumDisplayMonitors(0, 0, monitor_proc(callback), 0)
    except Exception as exc:
        print("Physical monitor enumeration warning:", exc)

    if not screens:
        try:
            import ctypes

            enable_physical_dpi_awareness()
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
                    "frequency": 0,
                    "bits_per_pixel": 0,
                    "physical_resolution": f"{width}x{height}",
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
        audio_mode = str(options.get("audio_mode", "input" if options.get("audio_device") else "none"))
        if audio_mode == "input" and options.get("audio_device"):
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
    audio_mode = str(options.get("audio_mode", "input" if options.get("audio_device") else "none"))
    if kind == "screen" and audio_mode == "input" and options.get("audio_device"):
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
    requested_rate = int(options.get("samplerate", 48000))
    test_mode = job.get("mode") == "test"

    try:
        import numpy as np
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Speaker capture packages are missing: {exc}")
        return

    try:
        subscription = LOOPBACK_BROKER.subscribe(
            subscriber_id=job["id"],
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            samplerate=requested_rate,
        )
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Shared speaker loopback failed: {exc}")
        return

    stop_event = threading.Event()
    folder = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.wav"

    local: dict[str, Any] = {
        "kind": "thread",
        "stop": stop_event,
        "output": "3-second shared speaker test" if test_mode else str(output),
        "audio_level_db": -70.0,
        "audio_updated_ts": 0.0,
        "subscription": subscription,
    }

    def loop() -> None:
        import queue
        import wave

        started = time.time()
        wav = None
        try:
            if not test_mode:
                wav = wave.open(str(output), "wb")
                wav.setnchannels(subscription.channels)
                wav.setsampwidth(2)
                wav.setframerate(subscription.samplerate)

            update_job(
                cfg,
                job["id"],
                state="running",
                message=f"Piggybacking on shared speaker audio: {subscription.device_name}",
                output=local["output"],
                audio_level_db=-70.0,
            )

            while not stop_event.is_set():
                try:
                    packet = subscription.read(timeout=1.0)
                except queue.Empty:
                    if test_mode and time.time() - started >= 5.0:
                        raise RuntimeError("No audio packets arrived from the shared loopback.")
                    continue

                local["audio_level_db"] = packet.level_db
                local["audio_updated_ts"] = packet.timestamp
                if wav is not None:
                    pcm = (np.clip(packet.samples, -1.0, 1.0) * 32767.0).astype("<i2")
                    wav.writeframes(pcm.tobytes())
                if test_mode and time.time() - started >= 3.0:
                    break

            if test_mode:
                update_job(
                    cfg,
                    job["id"],
                    state="finished",
                    message=f"Shared speaker test passed. Latest level {local['audio_level_db']} dB.",
                    output=local["output"],
                    audio_level_db=local["audio_level_db"],
                )
            else:
                update_job(
                    cfg,
                    job["id"],
                    state="stopped",
                    message="Shared speaker recording stopped",
                    output=str(output),
                    audio_level_db=local["audio_level_db"],
                )
        except Exception as exc:
            update_job(
                cfg,
                job["id"],
                state="failed",
                message=f"Shared speaker loopback failed: {exc}",
                output=str(output),
                audio_level_db=local.get("audio_level_db"),
            )
        finally:
            if wav is not None:
                wav.close()
            subscription.close()
            with ACTIVE_LOCK:
                ACTIVE.pop(job["id"], None)

    thread = threading.Thread(target=loop, daemon=True, name=f"VIC-Speaker-{job['id'][:8]}")
    local["thread"] = thread
    with ACTIVE_LOCK:
        ACTIVE[job["id"]] = local
    thread.start()



def start_screen_speaker_job(cfg: dict[str, Any], ffmpeg: str | None, job: dict[str, Any]) -> None:
    """Record screen video while subscribing to VIC's one shared speaker-loopback stream."""
    if not ffmpeg:
        update_job(cfg, job["id"], state="failed", message="FFmpeg was not found on this worker.")
        return

    source = job["source"]
    options = source.get("options", {})
    speaker_id = str(options.get("speaker_id", ""))
    speaker_name = str(options.get("speaker_name", "") or speaker_id or "Speaker output")
    requested_rate = int(options.get("samplerate", 48000))
    test_mode = job.get("mode") == "test"

    try:
        import numpy as np
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Speaker loopback packages are missing: {exc}")
        return

    try:
        screen_input = source_input_args(source, test=test_mode)
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=str(exc))
        return

    try:
        subscription = LOOPBACK_BROKER.subscribe(
            subscriber_id=job["id"],
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            samplerate=requested_rate,
        )
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Shared screen-audio loopback failed: {exc}")
        return

    folder = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    video_output = folder / f"{stamp}_screen.mkv"
    audio_output = folder / f"{stamp}_desktop_audio.wav"
    preview_path = LOG_DIR / f"live_{job['id']}.jpg"

    command = [ffmpeg, "-hide_banner", "-loglevel", "info", "-y"] + screen_input
    if test_mode:
        command += ["-t", "3", "-map", "0:v:0", "-f", "null", "-"]
        output_text = "3-second screen + shared speaker test"
    else:
        command += [
            "-map", "0:v:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-an",
            "-f", "matroska",
            str(video_output),
            "-map", "0:v:0",
            "-vf", "fps=1,scale=640:-2",
            "-q:v", "7",
            "-update", "1",
            str(preview_path),
        ]
        output_text = f"{video_output} | {audio_output}"

    log_path = LOG_DIR / f"job_{job['id']}.log"
    log_handle = log_path.open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n{time.ctime()}\n{subprocess.list2cmdline(command)}\n")
    log_handle.flush()

    try:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        subscription.close()
        log_handle.close()
        update_job(cfg, job["id"], state="failed", message=str(exc))
        return

    stop_event = threading.Event()
    local: dict[str, Any] = {
        "kind": "screen_speaker",
        "process": process,
        "thread": None,
        "stop": stop_event,
        "log": log_handle,
        "output": output_text,
        "preview_path": "" if test_mode else str(preview_path),
        "preview_last_sent": 0.0,
        "audio_level_db": -70.0,
        "audio_updated_ts": 0.0,
        "mode": job.get("mode", "record"),
        "subscription": subscription,
    }

    def audio_loop() -> None:
        import queue
        import wave

        started = time.time()
        wav = None
        unexpected_error = ""
        try:
            if not test_mode:
                wav = wave.open(str(audio_output), "wb")
                wav.setnchannels(subscription.channels)
                wav.setsampwidth(2)
                wav.setframerate(subscription.samplerate)

            update_job(
                cfg,
                job["id"],
                state="running",
                message=(
                    f"Testing screen while piggybacking on shared speaker audio: {subscription.device_name}"
                    if test_mode
                    else f"Recording screen while piggybacking on shared speaker audio: {subscription.device_name}."
                ),
                output=output_text,
                audio_level_db=-70.0,
                preview_available=not test_mode,
            )

            while not stop_event.is_set():
                try:
                    packet = subscription.read(timeout=1.0)
                except queue.Empty:
                    packet = None

                if packet is not None:
                    local["audio_level_db"] = packet.level_db
                    local["audio_updated_ts"] = packet.timestamp
                    if wav is not None:
                        pcm = (np.clip(packet.samples, -1.0, 1.0) * 32767.0).astype("<i2")
                        wav.writeframes(pcm.tobytes())

                if test_mode and time.time() - started >= 3.0:
                    break

                code = process.poll()
                if code is not None and not test_mode:
                    unexpected_error = f"Screen recording process exited with code {code}."
                    break

            if test_mode:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                update_job(
                    cfg,
                    job["id"],
                    state="finished",
                    message=f"Screen and shared speaker test passed. Latest level {local['audio_level_db']} dB.",
                    output=output_text,
                    audio_level_db=local["audio_level_db"],
                )
                with ACTIVE_LOCK:
                    ACTIVE.pop(job["id"], None)
            elif unexpected_error:
                update_job(
                    cfg,
                    job["id"],
                    state="failed",
                    message=unexpected_error,
                    output=output_text,
                    audio_level_db=local["audio_level_db"],
                )
                with ACTIVE_LOCK:
                    ACTIVE.pop(job["id"], None)
        except Exception as exc:
            update_job(
                cfg,
                job["id"],
                state="failed",
                message=f"Shared screen-audio capture failed: {exc}",
                output=output_text,
                audio_level_db=local.get("audio_level_db"),
            )
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception:
                pass
            with ACTIVE_LOCK:
                ACTIVE.pop(job["id"], None)
        finally:
            if wav is not None:
                wav.close()
            subscription.close()

    thread = threading.Thread(target=audio_loop, daemon=True, name=f"VIC-ScreenSpeaker-{job['id'][:8]}")
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
            "-vf", "fps=1,scale=640:-2",
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
    elif kind == "screen" and str(job.get("source", {}).get("options", {}).get("audio_mode", "")) == "speaker":
        start_screen_speaker_job(cfg, ffmpeg, job)
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
    elif local["kind"] == "screen_speaker":
        local["stop"].set()
        process = local["process"]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        local["thread"].join(timeout=5)
        try:
            local["log"].close()
        except Exception:
            pass
        update_job(
            cfg,
            job["id"],
            state="stopped",
            message="Screen and speaker loopback stopped. The screen MKV and companion desktop-audio WAV were saved together.",
            output=local.get("output", ""),
            audio_level_db=local.get("audio_level_db"),
        )
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
    poll = max(0.2, float(cfg.get("poll_seconds", 0.25)))
    full_status_seconds = max(2.0, float(cfg.get("full_status_seconds", 10)))
    ffmpeg = find_ffmpeg(cfg)
    devices = detect_devices(ffmpeg)
    last_device_scan = time.time()
    last_recording_scan = 0.0
    last_full_status = 0.0
    recording_inventory: list[dict[str, Any]] = []
    psutil.cpu_percent(interval=None)

    print("=" * 64)
    print("VIC Worker v0.3.6")
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
            "active_status": active_status_payload(),
        }

        # Audio levels and job commands update rapidly. The larger device and
        # recordings inventories are only resent periodically.
        if now - last_full_status >= full_status_seconds:
            heartbeat.update(
                {
                    "ffmpeg": ffmpeg or "",
                    "devices": devices,
                    "recordings": recording_inventory,
                }
            )
            last_full_status = now
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
