from __future__ import annotations

import base64
import hashlib
import http.client
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
import webbrowser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import psutil

from loopback_broker import SharedLoopbackBroker

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from common.discovery import discover_dashboards, probe_dashboard

CONFIG_FILE = BASE / "config" / "worker.json"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
WORKER_VERSION = "0.6.0"
ACTIVE: dict[str, dict[str, Any]] = {}
ACTIVE_LOCK = threading.RLock()

# Transfer defaults are intentionally balanced for a normal gigabit LAN.
# Worker Setup can choose 1-12 simultaneous transfers per worker.
TRANSFER_PARALLEL_LIMIT = 4
TRANSFER_SLOTS = threading.BoundedSemaphore(TRANSFER_PARALLEL_LIMIT)
TRANSFER_CHUNK_SIZE = 4 * 1024 * 1024
TRANSFER_PROGRESS_INTERVAL = 0.25
TRANSFER_PROGRESS_PENDING: dict[str, dict[str, Any]] = {}
TRANSFER_PROGRESS_LOCK = threading.RLock()

LOOPBACK_BROKER = SharedLoopbackBroker()
ENCODER_PROBE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
FFMPEG_SCAN_CACHE: dict[str, Any] = {
    "expires": 0.0,
    "path": None,
    "selection_details": "Not scanned yet",
    "candidates": [],
}
AUTO_ENCODER_LABEL = "CPU x264"
AUTO_ENCODER_DETAILS = "GPU encoder detection has not run yet."
FFMPEG_SELECTION_DETAILS = "FFmpeg selection has not run yet."
GPU_DEVICES: list[str] = []


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def load_config() -> dict[str, Any]:
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    machine = socket.gethostname()
    changed = False
    # When a used VIC folder is copied to a different PC, do not clone the
    # original worker identity. The new machine automatically receives its own.
    if str(cfg.get("worker_machine", "")) != machine:
        cfg["worker_id"] = uuid.uuid4().hex
        cfg["worker_machine"] = machine
        changed = True
    elif not cfg.get("worker_id"):
        cfg["worker_id"] = uuid.uuid4().hex
        changed = True
    cfg.setdefault("auto_discover", True)
    cfg.setdefault("dashboard_port", 8765)
    cfg.setdefault("discovery_port", 8766)
    cfg.setdefault("ffmpeg_selection_mode", "auto_compatible")
    cfg.setdefault("ffmpeg_path", "")
    cfg.setdefault("ffmpeg_last_selected_path", "")
    cfg.setdefault("ffmpeg_last_selected_version", "")
    if "transfer_parallel_limit" not in cfg:
        cfg["transfer_parallel_limit"] = 4
        changed = True
    try:
        cfg["transfer_parallel_limit"] = max(
            1,
            min(12, int(cfg.get("transfer_parallel_limit", 4))),
        )
    except (TypeError, ValueError):
        cfg["transfer_parallel_limit"] = 4
        changed = True
    if changed:
        save_config(cfg)
    return cfg


def resolve_dashboard(
    cfg: dict[str, Any],
    force_discovery: bool = False,
) -> str:
    configured = str(cfg.get("dashboard_url", "")).strip().rstrip("/")
    if configured and not force_discovery and probe_dashboard(configured, 1.0):
        return configured
    if not bool(cfg.get("auto_discover", True)):
        return configured

    print("Searching the local network for the VIC main Dashboard...")
    found = discover_dashboards(
        dashboard_port=int(cfg.get("dashboard_port", 8765)),
        discovery_port=int(cfg.get("discovery_port", 8766)),
        include_scan=True,
    )
    if found:
        url = str(found[0]["url"]).rstrip("/")
        cfg["dashboard_url"] = url
        cfg["dashboard_last_discovered"] = time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        save_config(cfg)
        print("Found and bonded to VIC Dashboard:", url)
        return url
    print("No VIC Dashboard was found on the local network.")
    return configured


def post_json(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-VIC-Token": token},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_transfer_progress(
    cfg: dict[str, Any],
    transfer_id: str,
    **fields: Any,
) -> None:
    """Queue the newest progress without pausing the file-transfer loop."""
    payload = {
        "worker_id": str(cfg.get("worker_id", "")),
        **fields,
    }
    with TRANSFER_PROGRESS_LOCK:
        existing = TRANSFER_PROGRESS_PENDING.get(transfer_id, {})
        existing.update(payload)
        TRANSFER_PROGRESS_PENDING[transfer_id] = existing


def transfer_progress_sender(cfg: dict[str, Any]) -> None:
    """Send coalesced progress in the background at a fast, low-overhead rate."""
    while True:
        with TRANSFER_PROGRESS_LOCK:
            pending = list(TRANSFER_PROGRESS_PENDING.items())
            TRANSFER_PROGRESS_PENDING.clear()

        for transfer_id, payload in pending:
            try:
                post_json(
                    cfg["dashboard_url"].rstrip("/")
                    + f"/api/transfers/{transfer_id}/progress",
                    cfg["cluster_token"],
                    payload,
                )
            except Exception:
                # Keep only the newest update if the Dashboard is temporarily
                # unreachable; this avoids an unlimited progress backlog.
                with TRANSFER_PROGRESS_LOCK:
                    current = TRANSFER_PROGRESS_PENDING.get(transfer_id, {})
                    merged = dict(payload)
                    merged.update(current)
                    TRANSFER_PROGRESS_PENDING[transfer_id] = merged

        time.sleep(0.12)


def stream_file_to_dashboard(
    cfg: dict[str, Any],
    transfer_id: str,
    source: Path,
    size: int,
    sha256: str,
    stop_event: threading.Event,
    job_id: str,
) -> dict[str, Any]:
    parsed = urlparse(
        cfg["dashboard_url"].rstrip("/")
        + f"/api/transfers/{transfer_id}/upload"
    )
    connection_class = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_class(parsed.hostname, parsed.port, timeout=60)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    connection.putrequest("POST", path)
    connection.putheader("Content-Type", "application/octet-stream")
    connection.putheader("Content-Length", str(size))
    connection.putheader("X-VIC-Token", str(cfg["cluster_token"]))
    connection.putheader("X-VIC-Worker-ID", str(cfg["worker_id"]))
    connection.putheader("X-VIC-Size", str(size))
    connection.putheader("X-VIC-SHA256", sha256)
    connection.endheaders()

    sent = 0
    last_report = 0.0
    with source.open("rb") as handle:
        while True:
            if stop_event.is_set():
                connection.close()
                raise RuntimeError("Transfer stopped by user")
            chunk = handle.read(TRANSFER_CHUNK_SIZE)
            if not chunk:
                break
            connection.send(chunk)
            sent += len(chunk)
            now = time.time()
            if now - last_report >= TRANSFER_PROGRESS_INTERVAL:
                percent = int((sent / max(size, 1)) * 48)
                message = f"Uploading to main relay: {sent / (1024**2):.1f} / {size / (1024**2):.1f} MB"
                update_job(cfg, job_id, state="running", message=message, output=str(source))
                post_transfer_progress(
                    cfg,
                    transfer_id,
                    state="uploading",
                    message=message,
                    progress_percent=percent,
                    bytes_done=sent,
                    total_bytes=size,
                )
                last_report = now

    response = connection.getresponse()
    body = response.read().decode("utf-8", errors="replace")
    connection.close()
    if response.status >= 400:
        raise RuntimeError(f"Dashboard upload failed ({response.status}): {body}")
    return json.loads(body or "{}")


def safe_transfer_relative(value: str) -> Path:
    raw = Path(value)
    parts = [
        safe_name(part)
        for part in raw.parts
        if part not in {"", ".", "..", raw.anchor}
    ]
    return Path(*parts) if parts else Path("recording.bin")


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}_transferred_{stamp}{path.suffix}")


def ffmpeg_candidate_paths(cfg: dict[str, Any]) -> list[tuple[Path, str, bool]]:
    candidates: list[tuple[Path, str, bool]] = []

    configured = str(cfg.get("ffmpeg_path", "")).strip()
    if configured:
        candidates.append((Path(configured), "Pinned/manual path", True))

    remembered = str(cfg.get("ffmpeg_last_selected_path", "")).strip()
    if remembered:
        candidates.append((Path(remembered), "Last compatible selection", False))

    compatible_root = BASE / "tools" / "ffmpeg_compatible"
    if compatible_root.exists():
        try:
            compatible_paths = sorted(
                compatible_root.rglob("ffmpeg.exe"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            compatible_paths = list(compatible_root.rglob("ffmpeg.exe"))
        for path in compatible_paths:
            candidates.append((path, "VIC compatible FFmpeg folder", False))

    candidates.append(
        (
            BASE / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            "Bundled inside VIC",
            False,
        )
    )

    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if packages.exists():
            try:
                winget_paths = sorted(
                    packages.rglob("ffmpeg.exe"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
            except OSError:
                winget_paths = list(packages.rglob("ffmpeg.exe"))
            for path in winget_paths:
                candidates.append((path, "WinGet package", False))

    normal = shutil.which("ffmpeg")
    if normal:
        candidates.append((Path(normal), "Windows PATH", False))

    result: list[tuple[Path, str, bool]] = []
    seen: set[str] = set()
    for path, origin, explicit in candidates:
        try:
            resolved = path.resolve()
            key = str(resolved).casefold()
            if key in seen or not resolved.is_file():
                continue
            seen.add(key)
            result.append((resolved, origin, explicit))
        except OSError:
            continue
    return result


def command_text(command: list[str]) -> str:
    try:
        return subprocess.list2cmdline(command)
    except Exception:
        return " ".join(command)


def run_text_command(
    command: list[str],
    timeout: float = 15.0,
) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except Exception as exc:
        return -1, "", str(exc)


def ffmpeg_encoder_listing(ffmpeg: str) -> tuple[set[str], str]:
    code, stdout, stderr = run_text_command(
        [ffmpeg, "-hide_banner", "-encoders"],
        timeout=15,
    )
    combined = "\n".join(part for part in (stdout, stderr) if part)
    if code != 0:
        return set(), combined.strip() or "Unable to list encoders"
    names: set[str] = set()
    for line in combined.splitlines():
        match = re.match(r"^\s*[A-Z.]{6}\s+(\S+)", line)
        if match:
            names.add(match.group(1))
    for encoder in ("h264_nvenc", "h264_amf", "h264_qsv"):
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(encoder)}(?![A-Za-z0-9_])", combined):
            names.add(encoder)
    return names, ""


def encoder_probe_command(ffmpeg: str, encoder: str) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=30",
        "-frames:v",
        "30",
        "-an",
        "-vf",
        "format=nv12",
        "-c:v",
        encoder,
    ]
    if encoder == "h264_nvenc":
        command += ["-preset", "p4", "-cq", "23", "-b:v", "0"]
    elif encoder == "h264_amf":
        command += ["-quality", "speed", "-rc", "cqp", "-qp_i", "23", "-qp_p", "23"]
    elif encoder == "h264_qsv":
        command += ["-preset", "veryfast", "-global_quality", "23"]
    command += ["-f", "null", "-"]
    return command


def encoder_probe_details(ffmpeg: str, encoder: str) -> dict[str, Any]:
    key = (str(Path(ffmpeg)), encoder)
    cached = ENCODER_PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    listed_names, listing_error = ffmpeg_encoder_listing(ffmpeg)
    listed = encoder in listed_names
    # Always run the real encoding test. It is authoritative.
    command = encoder_probe_command(ffmpeg, encoder)
    code, stdout, stderr = run_text_command(command, timeout=25)
    combined = "\n".join(part for part in (stderr, stdout) if part).strip()
    if code == 0:
        available = True
        reason = "Runtime encoding test passed." if listed else "Runtime encoding test passed even though listing parsing was inconclusive."
    else:
        available = False
        reason = combined or f"Runtime test exited with code {code}."
        reason = " ".join(reason.split())[:1200]
        if not listed:
            prefix = f"{encoder} was not found in the parsed encoder list. "
            if listing_error:
                prefix += f"Encoder-list error: {listing_error}. "
            reason = prefix + reason
    detail = {"available": available, "listed": listed, "reason": reason, "command": command_text(command)}
    ENCODER_PROBE_CACHE[key] = detail
    return detail


def ffmpeg_version_line(ffmpeg: str) -> str:
    code, stdout, stderr = run_text_command([ffmpeg, "-version"], timeout=10)
    text = stdout or stderr
    first = text.splitlines()[0].strip() if text.splitlines() else "Unknown FFmpeg version"
    return first if code == 0 else f"FFmpeg version check failed: {first}"



def ffmpeg_version_key(version_line: str) -> tuple[int, int, int, int]:
    match = re.search(
        r"ffmpeg version\s+(?:n)?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?",
        str(version_line or ""),
        re.IGNORECASE,
    )
    if not match:
        return (0, 0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def preferred_encoder_names(gpu_devices: list[str]) -> list[str]:
    joined = " ".join(gpu_devices).casefold()
    order: list[str] = []
    if "nvidia" in joined:
        order.append("nvenc")
    if "amd" in joined or "radeon" in joined:
        order.append("amf")
    if "intel" in joined:
        order.append("qsv")
    for item in ("nvenc", "amf", "qsv"):
        if item not in order:
            order.append(item)
    return order


def choose_ffmpeg_candidate(
    candidates: list[dict[str, Any]],
    mode: str,
    pinned_path: str,
    gpu_devices: list[str],
) -> tuple[dict[str, Any], str]:
    if not candidates:
        raise ValueError("No FFmpeg candidates were supplied")

    normalized_mode = str(mode or "auto_compatible").strip().lower()
    pinned_key = str(Path(pinned_path).resolve()).casefold() if pinned_path else ""

    if normalized_mode == "pinned" and pinned_key:
        for candidate in candidates:
            try:
                key = str(Path(str(candidate.get("path", ""))).resolve()).casefold()
            except OSError:
                key = str(candidate.get("path", "")).casefold()
            if key == pinned_key:
                return candidate, "the worker is pinned to this FFmpeg path"

    if normalized_mode == "newest":
        selected = max(
            candidates,
            key=lambda item: (
                tuple(item.get("version_key", (0, 0, 0, 0))),
                int(bool(item.get("working"))),
            ),
        )
        return selected, "Newest installed mode was selected"

    encoder_order = preferred_encoder_names(gpu_devices)
    for encoder in encoder_order:
        compatible = [
            item for item in candidates
            if encoder in list(item.get("working", []))
        ]
        if compatible:
            selected = max(
                compatible,
                key=lambda item: (
                    tuple(item.get("version_key", (0, 0, 0, 0))),
                    int(item.get("origin") == "VIC compatible FFmpeg folder"),
                ),
            )
            return selected, (
                f"it is the newest tested FFmpeg that works with {encoder} "
                "and this PC's current driver"
            )

    selected = max(
        candidates,
        key=lambda item: tuple(item.get("version_key", (0, 0, 0, 0))),
    )
    return selected, (
        "no installed FFmpeg passed a hardware runtime test, so the newest "
        "available FFmpeg was selected with CPU fallback"
    )



def inspect_ffmpeg_candidate(
    path: Path,
    origin: str,
    explicit: bool,
) -> dict[str, Any]:
    ffmpeg = str(path)
    probes = {
        "nvenc": encoder_probe_details(ffmpeg, "h264_nvenc"),
        "amf": encoder_probe_details(ffmpeg, "h264_amf"),
        "qsv": encoder_probe_details(ffmpeg, "h264_qsv"),
    }
    working = [name for name, detail in probes.items() if detail.get("available")]
    listed = [name for name, detail in probes.items() if detail.get("listed")]
    score = 10000 if explicit else 0
    score += 3000 if "nvenc" in working else 0
    score += 2000 if "amf" in working else 0
    score += 1000 if "qsv" in working else 0
    score += 200 if working else 0
    score += 20 * len(listed)
    if origin == "WinGet package":
        score += 30
    elif origin == "Bundled inside VIC":
        score += 20
    version = ffmpeg_version_line(ffmpeg)
    return {
        "path": ffmpeg,
        "origin": origin,
        "explicit": explicit,
        "version": version,
        "version_key": ffmpeg_version_key(version),
        "working": working,
        "listed": listed,
        "probes": probes,
        "score": score,
    }


def detect_gpu_devices() -> list[str]:
    devices: list[str] = []
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        code, stdout, _stderr = run_text_command(
            [
                nvidia_smi,
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            ],
            timeout=10,
        )
        if code == 0:
            for line in stdout.splitlines():
                line = line.strip()
                if line:
                    devices.append("NVIDIA " + line)
    if os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' | driver ' + $_.DriverVersion }",
        ]
        code, stdout, _stderr = run_text_command(command, timeout=15)
        if code == 0:
            for line in stdout.splitlines():
                line = line.strip()
                if line and line.casefold() not in {item.casefold() for item in devices}:
                    devices.append(line)
    return devices


def find_ffmpeg(
    cfg: dict[str, Any],
    force_scan: bool = False,
) -> str | None:
    global FFMPEG_SELECTION_DETAILS
    now = time.time()
    mode = str(cfg.get("ffmpeg_selection_mode", "auto_compatible"))
    if (
        not force_scan
        and FFMPEG_SCAN_CACHE.get("path")
        and now < float(FFMPEG_SCAN_CACHE.get("expires", 0))
        and FFMPEG_SCAN_CACHE.get("mode") == mode
    ):
        FFMPEG_SELECTION_DETAILS = str(
            FFMPEG_SCAN_CACHE.get("selection_details", "")
        )
        return str(FFMPEG_SCAN_CACHE["path"])

    candidates = [
        inspect_ffmpeg_candidate(path, origin, explicit)
        for path, origin, explicit in ffmpeg_candidate_paths(cfg)
    ]
    if not candidates:
        FFMPEG_SCAN_CACHE.update(
            {
                "expires": now + 60,
                "path": None,
                "mode": mode,
                "selection_details": "No ffmpeg.exe was found.",
                "candidates": [],
            }
        )
        FFMPEG_SELECTION_DETAILS = "No ffmpeg.exe was found."
        return None

    selected, reason = choose_ffmpeg_candidate(
        candidates,
        mode,
        str(cfg.get("ffmpeg_path", "")),
        GPU_DEVICES,
    )
    working = list(selected.get("working", []))
    details = (
        f'Selected {selected["path"]} ({selected["origin"]}) because {reason}. '
        f'Working hardware encoders: {", ".join(working) if working else "none"}. '
        f'Checked {len(candidates)} FFmpeg installation(s).'
    )

    selected_path = str(selected["path"])
    selected_version = str(selected.get("version", ""))
    if (
        cfg.get("ffmpeg_last_selected_path") != selected_path
        or cfg.get("ffmpeg_last_selected_version") != selected_version
    ):
        cfg["ffmpeg_last_selected_path"] = selected_path
        cfg["ffmpeg_last_selected_version"] = selected_version
        save_config(cfg)

    FFMPEG_SCAN_CACHE.update(
        {
            "expires": now + 300,
            "path": selected_path,
            "mode": mode,
            "selection_details": details,
            "candidates": candidates,
        }
    )
    FFMPEG_SELECTION_DETAILS = details
    return selected_path


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
                if not path.is_file() or path.name == ".gitkeep" or ".vic_recycle_bin" in path.parts:
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



def screen_refresh_rate(source: dict[str, Any]) -> int:
    options = source.get("options", {})
    if options.get("target") == "monitor":
        screen = resolve_screen(str(options.get("screen_id", "")))
        if screen and int(screen.get("frequency", 0) or 0) > 1:
            return int(screen["frequency"])
    rates = [
        int(item.get("frequency", 0) or 0)
        for item in enumerate_screens()
        if int(item.get("frequency", 0) or 0) > 1
    ]
    return max(rates) if rates else 60


def requested_capture_fps(source: dict[str, Any]) -> int | None:
    kind = source.get("type")
    options = source.get("options", {})
    mode = str(options.get("fps_mode", "")).lower()
    old_fps = int(options.get("fps", 0) or 0)
    if kind == "screen":
        if not mode:
            return old_fps or 30
        refresh = screen_refresh_rate(source)
        if mode == "auto":
            return min(refresh, 60)
        if mode == "full":
            return refresh
        if mode in {"30", "60"}:
            return int(mode)
        return max(1, min(240, old_fps or 60))
    if kind == "camera":
        if not mode:
            return old_fps or 30
        if mode == "native":
            return None
        if mode in {"30", "60"}:
            return int(mode)
        return max(1, min(240, old_fps or 60))
    return None


def screen_audio_devices(source: dict[str, Any]) -> list[dict[str, Any]]:
    options = source.get("options", {})
    stored = options.get("audio_devices", [])
    result: list[dict[str, Any]] = []
    if isinstance(stored, list):
        for item in stored:
            if isinstance(item, dict) and str(item.get("kind", "")) in {"input", "speaker"}:
                result.append(dict(item))
    if result:
        return result
    mode = str(options.get("audio_mode", "input" if options.get("audio_device") else "none"))
    if mode == "input" and options.get("audio_device"):
        return [{
            "kind": "input",
            "device": str(options.get("audio_device", "")),
            "name": str(options.get("audio_device", "")),
            "label": str(options.get("audio_device", "")),
        }]
    if mode == "speaker" and options.get("speaker_id"):
        return [{
            "kind": "speaker",
            "id": str(options.get("speaker_id", "")),
            "name": str(options.get("speaker_name", "") or options.get("speaker_id", "")),
            "label": str(options.get("speaker_name", "") or options.get("speaker_id", "")),
            "samplerate": int(options.get("samplerate", 48000) or 48000),
        }]
    return []


def screen_input_audio_devices(source: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in screen_audio_devices(source) if item.get("kind") == "input"]


def screen_speaker_audio_devices(source: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in screen_audio_devices(source) if item.get("kind") == "speaker"]


def screen_has_speaker_audio(source: dict[str, Any]) -> bool:
    return bool(screen_speaker_audio_devices(source))


def encoder_is_available(ffmpeg: str, encoder: str) -> bool:
    return bool(encoder_probe_details(ffmpeg, encoder).get("available"))


def short_probe_reason(detail: dict[str, Any]) -> str:
    reason = str(detail.get("reason", "Unavailable"))
    return " ".join(reason.split())[:240]


def select_video_encoder(
    ffmpeg: str,
    preference: str,
) -> tuple[list[str], str]:
    global AUTO_ENCODER_DETAILS
    preference = str(preference or "auto").lower()
    profiles = {
        "nvenc": (
            "h264_nvenc",
            ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20", "-b:v", "0"],
            "NVIDIA NVENC",
        ),
        "amf": (
            "h264_amf",
            ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "20", "-qp_p", "20"],
            "AMD AMF",
        ),
        "qsv": (
            "h264_qsv",
            ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "20"],
            "Intel Quick Sync",
        ),
    }
    order = [preference] if preference in profiles else ["nvenc", "amf", "qsv"]
    failure_lines: list[str] = []
    if preference != "cpu":
        for name in order:
            encoder, args, label = profiles[name]
            detail = encoder_probe_details(ffmpeg, encoder)
            if detail.get("available"):
                if preference == "auto":
                    AUTO_ENCODER_DETAILS = (
                        f"Automatic selected {label}. "
                        f"{detail.get('reason', 'Runtime test passed.')}"
                    )
                return args + ["-pix_fmt", "yuv420p"], label
            failure_lines.append(f"{label}: {short_probe_reason(detail)}")

    fallback = "CPU x264"
    if preference in profiles:
        fallback += f" — requested {profiles[preference][2]} was unavailable"
    if preference == "auto":
        AUTO_ENCODER_DETAILS = (
            "Automatic fell back to CPU x264. "
            + " | ".join(failure_lines)
        )
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"], fallback


def health_defaults(
    source: dict[str, Any],
    mode: str,
    encoder: str,
) -> dict[str, Any]:
    requested = requested_capture_fps(source)
    return {
        "requested_fps": requested if requested is not None else "Native",
        "actual_fps": None,
        "dropped_frames": 0,
        "duplicated_frames": 0,
        "bitrate_mbps": None,
        "file_size_bytes": 0,
        "duration_seconds": 0,
        "disk_per_hour_gb": None,
        "encoder": encoder,
        "speed": "",
        "frame_count": 0,
        "health_updated_ts": time.time(),
        "is_recording": mode == "record",
    }



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
        fps = requested_capture_fps(source) or 60
        args = ["-f", "gdigrab", "-framerate", str(fps)]
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
        for audio in screen_input_audio_devices(source):
            device_name = str(audio.get("device", audio.get("name", ""))).strip()
            if device_name:
                args += ["-f", "dshow", "-i", f"audio={device_name}"]
        return args

    if kind == "camera":
        args = ["-f", "dshow"]
        fps = requested_capture_fps(source)
        if fps is not None:
            args += ["-framerate", str(fps)]
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


def output_args(
    source: dict[str, Any],
    output: Path,
    ffmpeg: str,
) -> tuple[list[str], str]:
    kind = source.get("type")
    options = source.get("options", {})
    if kind == "audio_device":
        return (["-map", "0:a:0?", "-c:a", "flac", "-f", "matroska", str(output.with_suffix(".mka"))], "FLAC audio")
    if kind in {"screen", "camera"}:
        mapping = ["-map", "0:v:0"]
        audio_metadata: list[str] = []
        audio_count = 0
        if kind == "screen":
            for input_index, audio in enumerate(screen_input_audio_devices(source), start=1):
                mapping += ["-map", f"{input_index}:a:0?"]
                title = str(audio.get("label", audio.get("name", audio.get("device", f"Audio {input_index}"))))
                audio_metadata += [f"-metadata:s:a:{audio_count}", f"title={title}"]
                audio_count += 1
        elif options.get("audio_device"):
            mapping += ["-map", "0:a:0?"]
            audio_metadata += ["-metadata:s:a:0", f"title={options.get('audio_device', 'Camera audio')}"]
            audio_count = 1

        encoder_args, encoder_label = select_video_encoder(
            ffmpeg,
            str(options.get("encoder_preference", "auto")),
        )
        audio_args = (
            ["-c:a", "aac", "-b:a", "192k"] + audio_metadata
            if audio_count
            else ["-an"]
        )
        return (
            mapping + encoder_args + audio_args + [
                "-f", "matroska", str(output),
            ],
            encoder_label,
        )
    return (["-map", "0", "-c", "copy", "-f", "matroska", str(output)], "Stream copy")


def source_has_video(source: dict[str, Any]) -> bool:
    return source.get("type") in {"media_file", "screen", "camera", "rtsp", "network"}


def source_audio_map(source: dict[str, Any]) -> str | None:
    kind = source.get("type")
    options = source.get("options", {})
    if kind == "audio_device":
        return "0:a:0"
    if kind == "screen" and screen_input_audio_devices(source):
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
    preview_mode = job.get("mode") == "preview"

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
            if not test_mode and not preview_mode:
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
    """Capture screen + zero/many mic tracks + zero/many speaker sidecar WAVs."""
    if not ffmpeg:
        update_job(cfg, job["id"], state="failed", message="FFmpeg was not found on this worker.")
        return
    source = job["source"]
    options = source.get("options", {})
    mode = str(job.get("mode", "record"))
    test_mode = mode == "test"
    preview_mode = mode == "preview"
    speaker_devices = screen_speaker_audio_devices(source)
    input_devices = screen_input_audio_devices(source)

    subscriptions: list[dict[str, Any]] = []
    try:
        import numpy as np
        screen_input = source_input_args(source, test=test_mode)
        for index, audio in enumerate(speaker_devices, start=1):
            speaker_id = str(audio.get("id", audio.get("name", "")))
            speaker_name = str(audio.get("name", audio.get("label", speaker_id)) or speaker_id or f"Speaker {index}")
            subscription = LOOPBACK_BROKER.subscribe(
                subscriber_id=f"{job['id']}-speaker-{index}",
                speaker_id=speaker_id,
                speaker_name=speaker_name,
                samplerate=int(audio.get("samplerate", options.get("samplerate", 48000)) or 48000),
            )
            subscriptions.append({
                "descriptor": audio,
                "subscription": subscription,
                "name": speaker_name,
                "level_db": -70.0,
            })
    except Exception as exc:
        for item in subscriptions:
            try:
                item["subscription"].close()
            except Exception:
                pass
        update_job(cfg, job["id"], state="failed", message=f"Screen multi-audio Preview failed: {exc}")
        return

    folder = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    video_output = folder / f"{stamp}_screen.mkv"
    speaker_outputs: list[Path] = []
    for index, item in enumerate(subscriptions, start=1):
        label = safe_name(str(item["name"]))
        speaker_outputs.append(folder / f"{stamp}_loopback_{index:02d}_{label}.wav")
    preview_path = LOG_DIR / f"live_{job['id']}.jpg"
    progress = ["-stats_period", "0.5", "-progress", "pipe:2", "-nostats"]
    command = [ffmpeg, "-hide_banner", "-loglevel", "info", "-y"] + progress + screen_input
    encoder_label = "Preview only — not saving"
    output_paths: list[Path] = []
    first_input_map = "1:a:0" if input_devices else ""

    if test_mode:
        command += ["-t", "3", "-map", "0:v:0"]
        for input_index in range(1, len(input_devices) + 1):
            command += ["-map", f"{input_index}:a:0?"]
        command += ["-f", "null", "-"]
        output_text = f"3-second screen + {len(input_devices)} input(s) + {len(subscriptions)} loopback(s) test"
    elif preview_mode:
        command += ["-map", "0:v:0", "-vf", "fps=2,scale=640:-2", "-q:v", "7", "-update", "1", str(preview_path)]
        if first_input_map:
            command += ["-map", first_input_map, "-filter:a", "ebur128=framelog=verbose", "-f", "null", "-"]
        output_text = f"Preview only — {len(input_devices) + len(subscriptions)} audio device(s), nothing saved"
    else:
        encoded_args, encoder_label = output_args(source, video_output, ffmpeg)
        command += encoded_args
        command += ["-map", "0:v:0", "-vf", "fps=1,scale=640:-2", "-q:v", "7", "-update", "1", str(preview_path)]
        if first_input_map:
            command += ["-map", first_input_map, "-filter:a", "ebur128=framelog=verbose", "-f", "null", "-"]
        output_paths = [video_output, *speaker_outputs]
        output_text = " | ".join(str(path) for path in output_paths)

    log_path = LOG_DIR / f"job_{job['id']}.log"
    log_handle = log_path.open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n{time.ctime()}\n{subprocess.list2cmdline(command)}\n")
    log_handle.flush()
    try:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        for item in subscriptions:
            item["subscription"].close()
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
        "source_id": job.get("source_id", ""),
        "job_snapshot": job,
        "preview_path": "" if test_mode else str(preview_path),
        "preview_last_sent": 0.0,
        "audio_level_db": -70.0,
        "audio_updated_ts": 0.0,
        "mode": mode,
        "subscriptions": subscriptions,
        "start_ts": time.time(),
        "output_paths": [str(path) for path in output_paths],
        **health_defaults(source, mode, encoder_label),
    }
    if process.stderr is None:
        for item in subscriptions:
            item["subscription"].close()
        process.terminate()
        log_handle.close()
        update_job(cfg, job["id"], state="failed", message="FFmpeg did not provide a health stream.")
        return
    stderr_thread = threading.Thread(
        target=stderr_reader,
        args=(local, process.stderr, log_handle),
        daemon=True,
        name=f"VIC-ScreenHealth-{job['id'][:8]}",
    )
    local["stderr_thread"] = stderr_thread
    stderr_thread.start()

    def audio_loop() -> None:
        import queue
        import wave

        started = time.time()
        wav_files: list[Any] = []
        try:
            if mode == "record":
                for item, path in zip(subscriptions, speaker_outputs):
                    subscription = item["subscription"]
                    wav = wave.open(str(path), "wb")
                    wav.setnchannels(subscription.channels)
                    wav.setsampwidth(2)
                    wav.setframerate(subscription.samplerate)
                    wav_files.append(wav)

            update_job(
                cfg,
                job["id"],
                state="running",
                message=(
                    f"Preview active with {len(input_devices) + len(subscriptions)} audio device(s); nothing is being saved"
                    if preview_mode
                    else f"Recording screen with {len(input_devices)} MKV audio track(s) and {len(subscriptions)} loopback WAV(s)"
                    if mode == "record"
                    else f"Testing screen with {len(input_devices) + len(subscriptions)} audio device(s)"
                ),
                output=output_text,
                audio_level_db=-70.0,
                preview_available=not test_mode,
                requested_fps=local.get("requested_fps"),
                encoder=local.get("encoder"),
            )

            while not stop_event.is_set():
                speaker_levels: list[float] = []
                for index, item in enumerate(subscriptions):
                    subscription = item["subscription"]
                    try:
                        packet = subscription.read(timeout=0.08)
                    except queue.Empty:
                        packet = None
                    if packet is None:
                        continue
                    item["level_db"] = packet.level_db
                    speaker_levels.append(float(packet.level_db))
                    if mode == "record" and index < len(wav_files):
                        pcm = (np.clip(packet.samples, -1.0, 1.0) * 32767.0).astype("<i2")
                        wav_files[index].writeframes(pcm.tobytes())
                if speaker_levels:
                    current = float(local.get("audio_level_db", -70.0) or -70.0)
                    local["audio_level_db"] = max([current, *speaker_levels])
                    local["audio_updated_ts"] = time.time()
                if test_mode and time.time() - started >= 3.0:
                    break
                if process.poll() is not None and not test_mode:
                    raise RuntimeError(f"Screen process exited with code {process.returncode}")

            if test_mode:
                if process.poll() is None:
                    process.terminate()
                update_job(
                    cfg,
                    job["id"],
                    state="finished",
                    message="Screen multi-audio test passed",
                    output=output_text,
                    audio_level_db=local["audio_level_db"],
                )
        except Exception as exc:
            update_job(
                cfg,
                job["id"],
                state="failed",
                message=f"Screen multi-audio capture failed: {exc}",
                output=output_text,
                audio_level_db=local.get("audio_level_db"),
            )
        finally:
            for wav in wav_files:
                try:
                    wav.close()
                except Exception:
                    pass
            for item in subscriptions:
                try:
                    item["subscription"].close()
                except Exception:
                    pass
            with ACTIVE_LOCK:
                ACTIVE.pop(job["id"], None)

    thread = threading.Thread(
        target=audio_loop,
        daemon=True,
        name=f"VIC-ScreenMultiAudio-{job['id'][:8]}",
    )
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
    if job.get("mode") in {"test", "preview"}:
        update_job(cfg, job["id"], state="finished", message=f"Folder exists: {watch_path}. Continuous Preview is not required for folder sources.")
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


SUPPORTED_COOKIE_BROWSERS = {
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "opera",
    "safari",
    "vivaldi",
    "whale",
}

WEBSITE_AUTH_ERROR_MARKERS = (
    "sign in to confirm",
    "confirm your age",
    "age-restricted",
    "age restricted",
    "login required",
    "log in to",
    "private video",
    "video is private",
    "private playlist",
    "members-only",
    "members only",
    "authentication required",
    "use --cookies-from-browser",
    "use --cookies",
    "cookies are required",
    "not a bot",
)


def website_auth_required(log_path: Path) -> bool:
    try:
        with log_path.open(
            "rb",
        ) as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 200_000))
            text = handle.read().decode(
                "utf-8",
                errors="replace",
            ).lower()
    except OSError:
        return False

    return any(
        marker in text
        for marker in WEBSITE_AUTH_ERROR_MARKERS
    )


def add_website_auth(
    command: list[str],
    options: dict[str, Any],
) -> None:
    auth_mode = str(options.get("auth_mode", "none"))
    if auth_mode.startswith("browser_"):
        browser = str(
            options.get("browser_name", "edge")
        ).lower()
        if browser not in SUPPORTED_COOKIE_BROWSERS:
            browser = "edge"
        profile = str(
            options.get("browser_profile", "")
        ).strip()
        browser_spec = (
            f"{browser}:{profile}"
            if profile
            else browser
        )
        command += [
            "--cookies-from-browser",
            browser_spec,
        ]
    elif auth_mode.startswith("cookie_file_"):
        cookie_path = str(
            options.get("cookies_file", "")
        ).strip()
        if not cookie_path:
            raise ValueError(
                "This source is configured to use cookies.txt, "
                "but no cookie-file path was supplied."
            )
        command += ["--cookies", cookie_path]


def start_website_job(
    cfg: dict[str, Any],
    ffmpeg: str | None,
    job: dict[str, Any],
) -> None:
    source = job["source"]
    options = source.get("options", {})
    if job.get("mode") == "preview":
        update_job(cfg, job["id"], state="failed", message="Continuous Preview is not supported for website sources. Use Test or Start.")
        return
    url = str(options.get("url", "")).strip()
    website_mode = str(
        options.get("website_mode", "single")
    )
    auth_mode = str(options.get("auth_mode", "none"))

    folder = (
        recordings_root(cfg)
        / f"{safe_name(source['name'])}_{source['id']}"
    )
    folder.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--newline",
    ]

    if job.get("mode") == "test":
        command += [
            "--simulate",
            "--no-wait-for-video",
        ]
        if website_mode == "playlist":
            command += [
                "--yes-playlist",
                "--flat-playlist",
                "--playlist-end",
                "3",
            ]
        else:
            command.append("--no-playlist")
        command += [
            "--print",
            "title=%(title)s | status=%(live_status)s | id=%(id)s",
        ]
        output = (
            "Playlist test (first three entries)"
            if website_mode == "playlist"
            else "Website/live-event metadata test"
        )
    else:
        if website_mode == "playlist":
            template = (
                folder
                / "%(playlist_index)05d_%(title).120s_%(id)s.%(ext)s"
            )
            archive = folder / "downloaded-items.txt"
            command += [
                "--yes-playlist",
                "--ignore-errors",
                "--download-archive",
                str(archive),
                "--merge-output-format",
                "mkv",
                "-o",
                str(template),
            ]
            if options.get("playlist_reverse"):
                command.append("--playlist-reverse")
        else:
            template = (
                folder
                / "%(title).120s_%(id)s.%(ext)s"
            )
            command += [
                "--no-playlist",
                "--merge-output-format",
                "mkv",
                "-o",
                str(template),
            ]

        if ffmpeg:
            command += ["--ffmpeg-location", ffmpeg]

        if website_mode == "upcoming":
            wait_min = max(
                5,
                int(options.get("wait_min", 30)),
            )
            wait_max = max(
                wait_min,
                int(options.get("wait_max", 60)),
            )
            command += [
                "--wait-for-video",
                f"{wait_min}-{wait_max}",
                "--retries",
                "infinite",
                "--fragment-retries",
                "infinite",
            ]
            if options.get(
                "upcoming_live_from_start",
                True,
            ):
                command.append("--live-from-start")
        elif options.get("live_from_start"):
            command.append("--live-from-start")

        output = str(folder)

    fallback_command: list[str] | None = None
    fallback_message = ""

    if auth_mode in {
        "browser_always",
        "cookie_file_always",
    }:
        try:
            add_website_auth(command, options)
        except ValueError as exc:
            update_job(
                cfg,
                job["id"],
                state="failed",
                message=str(exc),
            )
            return

    elif auth_mode in {
        "browser_if_needed",
        "cookie_file_if_needed",
    }:
        fallback_command = list(command)
        try:
            add_website_auth(
                fallback_command,
                options,
            )
        except ValueError as exc:
            update_job(
                cfg,
                job["id"],
                state="failed",
                message=str(exc),
            )
            return
        fallback_message = (
            "The signed-out attempt was refused for login, "
            "age verification or private access. "
            "Retrying with this source's selected account method."
        )

    command += ["--", url]
    if fallback_command is not None:
        fallback_command += ["--", url]

    launch_process(
        cfg,
        job,
        command,
        output,
        fallback_command=fallback_command,
        fallback_on_auth_error=(
            fallback_command is not None
        ),
        fallback_message=fallback_message,
    )


def stderr_reader(local: dict[str, Any], stream, log_handle) -> None:
    meter_pattern = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")
    progress_values: dict[str, str] = {}
    try:
        for line in iter(stream.readline, ""):
            log_handle.write(line)
            log_handle.flush()
            meter = meter_pattern.search(line)
            if meter:
                try:
                    level = float(meter.group(1))
                    with ACTIVE_LOCK:
                        local["audio_level_db"] = max(-70.0, min(0.0, level))
                        local["audio_updated_ts"] = time.time()
                except ValueError:
                    pass
            stripped = line.strip()
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key not in {
                "frame", "fps", "bitrate", "total_size", "out_time_us",
                "out_time_ms", "speed", "drop_frames", "dup_frames", "progress",
            }:
                continue
            progress_values[key] = value
            if key != "progress":
                continue
            with ACTIVE_LOCK:
                try:
                    local["frame_count"] = int(progress_values.get("frame", local.get("frame_count", 0)) or 0)
                except ValueError:
                    pass
                try:
                    fps_value = progress_values.get("fps", "")
                    local["actual_fps"] = float(fps_value) if fps_value not in {"", "N/A"} else local.get("actual_fps")
                except ValueError:
                    pass
                try:
                    local["dropped_frames"] = int(progress_values.get("drop_frames", local.get("dropped_frames", 0)) or 0)
                    local["duplicated_frames"] = int(progress_values.get("dup_frames", local.get("duplicated_frames", 0)) or 0)
                except ValueError:
                    pass
                try:
                    size_value = progress_values.get("total_size", "")
                    if size_value not in {"", "N/A"}:
                        local["file_size_bytes"] = int(size_value)
                except ValueError:
                    pass
                try:
                    micros = progress_values.get("out_time_us") or progress_values.get("out_time_ms") or "0"
                    local["duration_seconds"] = max(0.0, int(micros) / 1_000_000.0)
                except ValueError:
                    pass
                bitrate_text = progress_values.get("bitrate", "")
                match = re.search(r"([0-9.]+)kbits/s", bitrate_text)
                if match:
                    local["bitrate_mbps"] = float(match.group(1)) / 1000.0
                local["speed"] = progress_values.get("speed", local.get("speed", ""))
                local["health_updated_ts"] = time.time()
            progress_values = {}
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
    progress_enabled: bool = False,
    requested_fps: int | str | None = None,
    encoder: str = "",
    output_paths: list[Path] | None = None,
    fallback_command: list[str] | None = None,
    fallback_on_auth_error: bool = False,
    fallback_message: str = "",
) -> None:
    log_path = LOG_DIR / f"job_{job['id']}.log"
    log_handle = log_path.open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n{time.ctime()}\n{subprocess.list2cmdline(command)}\n")
    log_handle.flush()
    parse_stderr = meter_enabled or progress_enabled
    try:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.PIPE if parse_stderr else log_handle,
            text=parse_stderr,
            bufsize=1 if parse_stderr else -1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        log_handle.close()
        update_job(cfg, job["id"], state="failed", message=str(exc))
        return

    health = health_defaults(job.get("source", {}), str(job.get("mode", "record")), encoder)
    if requested_fps is not None:
        health["requested_fps"] = requested_fps
    local: dict[str, Any] = {
        "kind": "process", "process": process, "log": log_handle,
        "output": output, "mode": job.get("mode", "record"),
        "source_id": job.get("source_id", ""),
        "preview_path": str(preview_path) if preview_path else "",
        "preview_last_sent": 0.0,
        "audio_level_db": -70.0 if meter_enabled else None,
        "audio_updated_ts": 0.0,
        "meter_enabled": meter_enabled,
        "log_path": str(log_path),
        "fallback_command": fallback_command,
        "fallback_on_auth_error": fallback_on_auth_error,
        "fallback_message": fallback_message,
        "fallback_used": False,
        "start_ts": time.time(),
        "output_paths": [str(path) for path in (output_paths or [])],
        **health,
    }
    with ACTIVE_LOCK:
        ACTIVE[job["id"]] = local
    if parse_stderr and process.stderr is not None:
        thread = threading.Thread(target=stderr_reader, args=(local, process.stderr, log_handle), daemon=True, name=f"VIC-Health-{job['id'][:8]}")
        local["stderr_thread"] = thread
        thread.start()
    update_job(
        cfg, job["id"], state="running",
        message=("Preview active — nothing is being saved" if job.get("mode") == "preview" else "Worker process started"),
        output=output, pid=process.pid,
        audio_level_db=local["audio_level_db"],
        preview_available=bool(preview_path),
        **{key: local.get(key) for key in (
            "requested_fps", "actual_fps", "dropped_frames", "duplicated_frames",
            "bitrate_mbps", "file_size_bytes", "duration_seconds",
            "disk_per_hour_gb", "encoder", "speed", "frame_count", "health_updated_ts",
        )},
    )


def start_ffmpeg_job(cfg: dict[str, Any], ffmpeg: str | None, job: dict[str, Any]) -> None:
    if not ffmpeg:
        update_job(cfg, job["id"], state="failed", message="FFmpeg was not found on this worker.")
        return
    source = job["source"]
    mode = str(job.get("mode", "record"))
    try:
        input_args = source_input_args(source, test=mode == "test")
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=str(exc))
        return

    if mode == "test":
        test_maps = ["-map", "0:v:0?"]
        audio_map = source_audio_map(source)
        test_maps += ["-map", audio_map] if audio_map else ["-map", "0:a:0?"]
        command = [ffmpeg, "-hide_banner", "-loglevel", "error"] + input_args + ["-t", "3"] + test_maps + ["-f", "null", "-"]
        launch_process(cfg, job, command, "3-second source test")
        return

    progress = ["-stats_period", "0.5", "-progress", "pipe:2", "-nostats"]
    preview_path: Path | None = None
    audio_map = source_audio_map(source)
    meter_enabled = bool(audio_map)
    requested = requested_capture_fps(source)

    if mode == "preview":
        command = [ffmpeg, "-hide_banner", "-loglevel", "info", "-y"] + progress + input_args
        if source_has_video(source):
            preview_path = LOG_DIR / f"live_{job['id']}.jpg"
            command += ["-map", "0:v:0", "-vf", "fps=2,scale=640:-2", "-q:v", "7", "-update", "1", str(preview_path)]
        if audio_map:
            command += ["-map", audio_map, "-filter:a", "ebur128=framelog=verbose", "-f", "null", "-"]
        elif not source_has_video(source):
            command += ["-map", "0:a:0?", "-f", "null", "-"]
        launch_process(
            cfg, job, command, "Preview only — no recording file",
            preview_path=preview_path, meter_enabled=meter_enabled,
            progress_enabled=True,
            requested_fps=requested if requested is not None else "Native",
            encoder="Preview only — not saving",
        )
        return

    folder = recordings_root(cfg) / f"{safe_name(source['name'])}_{source['id']}"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.mkv"
    encoded_args, encoder_label = output_args(source, output, ffmpeg)
    command = [ffmpeg, "-hide_banner", "-loglevel", "info", "-y"] + progress + input_args + encoded_args
    if source_has_video(source):
        preview_path = LOG_DIR / f"live_{job['id']}.jpg"
        command += ["-map", "0:v:0", "-vf", "fps=1,scale=640:-2", "-q:v", "7", "-update", "1", str(preview_path)]
    if audio_map:
        command += ["-map", audio_map, "-filter:a", "ebur128=framelog=verbose", "-f", "null", "-"]
    launch_process(
        cfg, job, command, str(output), preview_path=preview_path,
        meter_enabled=meter_enabled, progress_enabled=True,
        requested_fps=requested if requested is not None else "Native",
        encoder=encoder_label,
        output_paths=[output if source.get("type") != "audio_device" else output.with_suffix(".mka")],
    )




def recycle_bin_root(cfg: dict[str, Any]) -> Path:
    root = recordings_root(cfg) / ".vic_recycle_bin"
    root.mkdir(parents=True, exist_ok=True)
    return root


def recycled_target(cfg: dict[str, Any], raw_path: str) -> Path:
    target = Path(raw_path).expanduser().resolve()
    root = recycle_bin_root(cfg).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Refusing a recycle-bin path outside VIC recordings.") from exc
    return target


def scan_recycle_bin(cfg: dict[str, Any], limit: int = 150) -> list[dict[str, Any]]:
    root = recycle_bin_root(cfg)
    items: list[dict[str, Any]] = []
    try:
        for path in root.glob("*"):
            if not path.is_file() or path.name.endswith(".vicmeta.json"):
                continue
            meta_path = Path(str(path) + ".vicmeta.json")
            meta: dict[str, Any] = {}
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            stat = path.stat()
            items.append({
                "name": meta.get("original_name", path.name),
                "path": str(path.resolve()),
                "original_path": meta.get("original_path", ""),
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "recycled_ts": meta.get("recycled_ts", stat.st_mtime),
                "recycled": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(meta.get("recycled_ts", stat.st_mtime)))),
            })
    except OSError:
        return []
    items.sort(key=lambda item: item.get("recycled_ts", 0), reverse=True)
    return items[:limit]


def recycle_recording_file(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    raw_path = str(job.get("source", {}).get("options", {}).get("path", "")).strip()
    try:
        source, root = recording_target(cfg, raw_path)
        if not source.is_file():
            raise FileNotFoundError(f"Recording file was not found: {source}")
        recycle = recycle_bin_root(cfg)
        destination = recycle / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}__{source.name}"
        meta = {
            "original_path": str(source),
            "original_name": source.name,
            "recycled_ts": time.time(),
        }
        shutil.move(str(source), str(destination))
        Path(str(destination) + ".vicmeta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        remove_empty_recording_parents(source, root)
        update_job(cfg, job["id"], state="finished", message="Recording moved to VIC recycle bin", output=str(destination))
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Could not recycle recording: {exc}", output=raw_path)


def recycle_all_recordings(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    protected = {str(item).casefold() for item in job.get("source", {}).get("options", {}).get("protected_paths", [])}
    recycled = 0
    skipped = 0
    for root in recording_roots(cfg):
        try:
            paths = [path for path in root.rglob("*") if path.is_file() and path.name != ".gitkeep" and ".vic_recycle_bin" not in path.parts]
        except OSError:
            continue
        for path in paths:
            if str(path.resolve()).casefold() in protected:
                skipped += 1
                continue
            fake = {"id": job["id"], "source": {"options": {"path": str(path)}}}
            try:
                source, source_root = recording_target(cfg, str(path))
                recycle = recycle_bin_root(cfg)
                destination = recycle / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}__{source.name}"
                shutil.move(str(source), str(destination))
                Path(str(destination) + ".vicmeta.json").write_text(json.dumps({"original_path": str(source), "original_name": source.name, "recycled_ts": time.time()}, indent=2), encoding="utf-8")
                remove_empty_recording_parents(source, source_root)
                recycled += 1
            except Exception:
                continue
    update_job(cfg, job["id"], state="finished", message=f"Recycled {recycled} file(s); kept {skipped} protected file(s)", output=str(recycle_bin_root(cfg)))


def restore_recycled_file(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    raw_path = str(job.get("source", {}).get("options", {}).get("path", "")).strip()
    try:
        source = recycled_target(cfg, raw_path)
        if not source.is_file():
            raise FileNotFoundError("Recycled file was not found")
        meta_path = Path(str(source) + ".vicmeta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        desired = Path(str(meta.get("original_path", recordings_root(cfg) / source.name))).expanduser().resolve()
        valid, _root = recording_target(cfg, str(desired))
        valid.parent.mkdir(parents=True, exist_ok=True)
        if valid.exists():
            valid = valid.with_name(valid.stem + "_restored_" + time.strftime("%Y%m%d_%H%M%S") + valid.suffix)
        shutil.move(str(source), str(valid))
        meta_path.unlink(missing_ok=True)
        update_job(cfg, job["id"], state="finished", message="Recycled recording restored", output=str(valid))
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Could not restore recycled recording: {exc}", output=raw_path)


def delete_recycled_file(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    raw_path = str(job.get("source", {}).get("options", {}).get("path", "")).strip()
    try:
        target = recycled_target(cfg, raw_path)
        target.unlink()
        Path(str(target) + ".vicmeta.json").unlink(missing_ok=True)
        update_job(cfg, job["id"], state="finished", message="Recycled file permanently deleted", output=str(target))
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Could not permanently delete recycled file: {exc}", output=raw_path)


def empty_recycle_bin(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    root = recycle_bin_root(cfg)
    deleted = 0
    try:
        for path in root.glob("*"):
            if path.is_file():
                path.unlink()
                deleted += 1
        update_job(cfg, job["id"], state="finished", message=f"Recycle bin emptied ({deleted} file entries removed)", output=str(root))
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Could not empty recycle bin: {exc}", output=str(root))


def benchmark_recording_drive(cfg: dict[str, Any], job: dict[str, Any]) -> None:
    size_mb = max(32, min(1024, int(job.get("source", {}).get("options", {}).get("size_mb", 128) or 128)))
    target = recordings_root(cfg) / f".vic_disk_test_{uuid.uuid4().hex}.tmp"
    block = b"\0" * (4 * 1024 * 1024)
    try:
        start = time.perf_counter()
        with target.open("wb", buffering=0) as handle:
            for _ in range(max(1, size_mb // 4)):
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        write_seconds = max(0.001, time.perf_counter() - start)
        start = time.perf_counter()
        with target.open("rb", buffering=0) as handle:
            while handle.read(4 * 1024 * 1024):
                pass
        read_seconds = max(0.001, time.perf_counter() - start)
        write_rate = round(size_mb / write_seconds, 1)
        read_rate = round(size_mb / read_seconds, 1)
        update_job(cfg, job["id"], state="finished", message=f"Disk test passed: write {write_rate} MB/s, read {read_rate} MB/s", output=str(recordings_root(cfg)), benchmark_write_mbps=write_rate, benchmark_read_mbps=read_rate, benchmark_size_mb=size_mb)
    except Exception as exc:
        update_job(cfg, job["id"], state="failed", message=f"Disk benchmark failed: {exc}", output=str(target))
    finally:
        target.unlink(missing_ok=True)


def recording_roots(cfg: dict[str, Any]) -> list[Path]:
    roots = [recordings_root(cfg).resolve()]
    legacy = (BASE / "recordings").resolve()
    if legacy.exists() and legacy not in roots:
        roots.append(legacy)
    return roots


def recording_target(
    cfg: dict[str, Any],
    raw_path: str,
) -> tuple[Path, Path]:
    target = Path(raw_path).expanduser().resolve()
    for root in recording_roots(cfg):
        try:
            target.relative_to(root)
            return target, root
        except ValueError:
            continue
    raise ValueError(
        "Refusing to delete a path outside VIC recording folders."
    )


def remove_empty_recording_parents(path: Path, root: Path) -> None:
    parent = path.parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def delete_recording_file(
    cfg: dict[str, Any],
    job: dict[str, Any],
) -> None:
    raw_path = str(
        job.get("source", {})
        .get("options", {})
        .get("path", "")
    ).strip()
    try:
        target, root = recording_target(cfg, raw_path)
        if not target.is_file():
            raise FileNotFoundError(
                f"Recording file was not found: {target}"
            )
        size = target.stat().st_size
        target.unlink()
        remove_empty_recording_parents(target, root)
        update_job(
            cfg,
            job["id"],
            state="finished",
            message="Recording file permanently deleted",
            output=(
                f"{target} "
                f"({round(size / (1024 * 1024), 2)} MB)"
            ),
        )
    except Exception as exc:
        update_job(
            cfg,
            job["id"],
            state="failed",
            message=f"Could not delete recording: {exc}",
            output=raw_path,
        )


def delete_all_recording_files(
    cfg: dict[str, Any],
    job: dict[str, Any],
) -> None:
    deleted = 0
    bytes_deleted = 0
    errors: list[str] = []

    for root in recording_roots(cfg):
        if not root.exists():
            continue
        paths = sorted(
            root.rglob("*"),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for path in paths:
            try:
                if path.is_file() and path.name != ".gitkeep":
                    bytes_deleted += path.stat().st_size
                    path.unlink()
                    deleted += 1
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            except OSError as exc:
                errors.append(f"{path}: {exc}")

    message = (
        f"Permanently deleted {deleted} recording file(s), "
        f"{round(bytes_deleted / (1024 * 1024), 2)} MB."
    )
    if errors:
        message += (
            f" {len(errors)} file(s) could not be deleted."
        )

    update_job(
        cfg,
        job["id"],
        state="finished" if not errors else "failed",
        message=message,
        output="\n".join(errors[:20]),
    )




def start_control_transfer_thread(
    cfg: dict[str, Any],
    job: dict[str, Any],
    target: Any,
) -> None:
    stop_event = threading.Event()
    options = job.get("source", {}).get("options", {})
    transfer_id = str(options.get("transfer_id", ""))
    is_upload = job.get("mode") == "upload_recording_transfer"
    queue_label = (
        "upload"
        if is_upload
        else "destination download"
    )

    local: dict[str, Any] = {
        "kind": "thread",
        "stop": stop_event,
        "output": "",
        "transfer_job": True,
        "transfer_id": transfer_id,
        "transfer_stage": "waiting",
    }

    def run() -> None:
        acquired = False
        try:
            update_job(
                cfg,
                job["id"],
                state="running",
                message=(
                    f"Worker accepted transfer; waiting for its "
                    f"{queue_label} slot"
                ),
            )
            post_transfer_progress(
                cfg,
                transfer_id,
                state="worker_queue",
                message=(
                    f'{cfg.get("worker_name") or socket.gethostname()} '
                    f"accepted the job and queued the {queue_label}. "
                    f"This worker allows up to {TRANSFER_PARALLEL_LIMIT} simultaneous transfers."
                ),
                progress_percent=0 if is_upload else 50,
            )

            while not stop_event.is_set():
                acquired = TRANSFER_SLOTS.acquire(timeout=0.25)
                if acquired:
                    break

            if stop_event.is_set():
                raise RuntimeError("Transfer stopped while waiting in queue")

            local["transfer_stage"] = "active"
            update_job(
                cfg,
                job["id"],
                state="running",
                message=f"Starting transfer {queue_label}",
            )
            post_transfer_progress(
                cfg,
                transfer_id,
                state=(
                    "checking_source"
                    if is_upload
                    else "starting_download"
                ),
                message=(f"Parallel transfer slot available; starting {queue_label} "
         f"({TRANSFER_PARALLEL_LIMIT} maximum on this worker)"),
                progress_percent=1 if is_upload else 50,
            )
            target(cfg, job, stop_event)
        except Exception as exc:
            update_job(
                cfg,
                job["id"],
                state="stopped" if stop_event.is_set() else "failed",
                message=f"Transfer queue failed: {exc}",
            )
            post_transfer_progress(
                cfg,
                transfer_id,
                state="cancelled" if stop_event.is_set() else "failed",
                message=f"Transfer queue failed: {exc}",
            )
        finally:
            if acquired:
                TRANSFER_SLOTS.release()
            with ACTIVE_LOCK:
                ACTIVE.pop(job["id"], None)

    thread = threading.Thread(
        target=run,
        daemon=True,
        name=f"VIC-Transfer-{job['id'][:8]}",
    )
    local["thread"] = thread
    with ACTIVE_LOCK:
        ACTIVE[job["id"]] = local
    thread.start()


def upload_recording_transfer(
    cfg: dict[str, Any],
    job: dict[str, Any],
    stop_event: threading.Event,
) -> None:
    options = job.get("source", {}).get("options", {})
    transfer_id = str(options.get("transfer_id", ""))
    raw_path = str(options.get("path", ""))
    try:
        source, _root = recording_target(cfg, raw_path)
        if not source.is_file():
            raise FileNotFoundError(f"Recording was not found: {source}")
        first_stat = source.stat()
        size = first_stat.st_size
        update_job(
            cfg,
            job["id"],
            state="running",
            message="Checking that the recording file has finished writing",
            output=str(source),
        )
        stop_event.wait(2.0)
        if stop_event.is_set():
            raise RuntimeError("Transfer stopped by user")
        second_stat = source.stat()
        if (
            second_stat.st_size != first_stat.st_size
            or second_stat.st_mtime_ns != first_stat.st_mtime_ns
        ):
            raise RuntimeError(
                "The recording file is still changing. Stop its recording "
                "or wait for it to finish, then queue the transfer again."
            )
        size = second_stat.st_size
        hasher = hashlib.sha256()
        read_bytes = 0
        last_report = 0.0
        update_job(
            cfg,
            job["id"],
            state="running",
            message="Checking the source file before transfer",
            output=str(source),
        )
        post_transfer_progress(
            cfg,
            transfer_id,
            state="hashing_source",
            message="Checking the source file before upload",
            progress_percent=1,
            total_bytes=size,
        )
        with source.open("rb") as handle:
            while True:
                if stop_event.is_set():
                    raise RuntimeError("Transfer stopped by user")
                chunk = handle.read(TRANSFER_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
                read_bytes += len(chunk)
                now = time.time()
                if now - last_report >= 0.35:
                    percent = min(8, int((read_bytes / max(size, 1)) * 8))
                    post_transfer_progress(
                        cfg,
                        transfer_id,
                        state="hashing_source",
                        message=f"Checking source: {read_bytes / (1024**2):.1f} / {size / (1024**2):.1f} MB",
                        progress_percent=percent,
                    )
                    last_report = now
        stream_file_to_dashboard(
            cfg,
            transfer_id,
            source,
            size,
            hasher.hexdigest(),
            stop_event,
            job["id"],
        )
        update_job(
            cfg,
            job["id"],
            state="finished",
            message="Upload verified; waiting for the destination worker",
            output=str(source),
        )
    except Exception as exc:
        message = str(exc)
        update_job(
            cfg,
            job["id"],
            state="stopped" if stop_event.is_set() else "failed",
            message=f"Recording transfer upload failed: {message}",
            output=raw_path,
        )
        post_transfer_progress(
            cfg,
            transfer_id,
            state="cancelled" if stop_event.is_set() else "failed",
            message=f"Upload failed: {message}",
        )


def receive_recording_transfer(
    cfg: dict[str, Any],
    job: dict[str, Any],
    stop_event: threading.Event,
) -> None:
    options = job.get("source", {}).get("options", {})
    transfer_id = str(options.get("transfer_id", ""))
    expected_size = int(options.get("expected_size", 0) or 0)
    expected_hash = str(options.get("expected_sha256", "")).lower()
    source_name = safe_name(str(options.get("source_worker_name", "Worker")))
    relative = safe_transfer_relative(str(options.get("relative", options.get("filename", "recording.bin"))))
    destination_root = recordings_root(cfg) / f"Transferred from {source_name}"
    destination = unique_destination(destination_root / relative)
    part = destination.with_suffix(destination.suffix + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        request = Request(
            cfg["dashboard_url"].rstrip("/")
            + f"/api/transfers/{transfer_id}/download",
            headers={
                "X-VIC-Token": str(cfg["cluster_token"]),
                "X-VIC-Worker-ID": str(cfg["worker_id"]),
            },
            method="GET",
        )
        update_job(
            cfg,
            job["id"],
            state="running",
            message="Downloading and verifying the transferred recording",
            output=str(destination),
        )
        post_transfer_progress(
            cfg,
            transfer_id,
            state="downloading",
            message="Destination worker is downloading the verified staged file",
            progress_percent=50,
            total_bytes=expected_size,
        )
        hasher = hashlib.sha256()
        received = 0
        last_report = 0.0
        with urlopen(request, timeout=60) as response, part.open("wb") as handle:
            while True:
                if stop_event.is_set():
                    raise RuntimeError("Transfer stopped by user")
                chunk = response.read(TRANSFER_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                received += len(chunk)
                now = time.time()
                if now - last_report >= TRANSFER_PROGRESS_INTERVAL:
                    fraction = received / max(expected_size, 1)
                    percent = 50 + min(44, int(fraction * 44))
                    message = f"Downloading: {received / (1024**2):.1f} / {expected_size / (1024**2):.1f} MB"
                    update_job(cfg, job["id"], state="running", message=message, output=str(destination))
                    post_transfer_progress(
                        cfg,
                        transfer_id,
                        state="downloading",
                        message=message,
                        progress_percent=percent,
                        bytes_done=received,
                        total_bytes=expected_size,
                    )
                    last_report = now
        digest = hasher.hexdigest()
        if received != expected_size:
            raise ValueError(f"Expected {expected_size} bytes but received {received}.")
        if digest != expected_hash:
            raise ValueError("Destination hash does not match the source file.")
        part.replace(destination)

        response = post_json(
            cfg["dashboard_url"].rstrip("/")
            + f"/api/transfers/{transfer_id}/received",
            cfg["cluster_token"],
            {
                "worker_id": str(cfg["worker_id"]),
                "size": received,
                "sha256": digest,
                "destination_path": str(destination),
            },
        )
        if not response.get("ok"):
            raise RuntimeError(str(response))
        update_job(
            cfg,
            job["id"],
            state="finished",
            message="Transfer downloaded and verified successfully",
            output=str(destination),
        )
    except Exception as exc:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        message = str(exc)
        update_job(
            cfg,
            job["id"],
            state="stopped" if stop_event.is_set() else "failed",
            message=f"Recording transfer download failed: {message}",
            output=str(destination),
        )
        post_transfer_progress(
            cfg,
            transfer_id,
            state="cancelled" if stop_event.is_set() else "failed",
            message=f"Destination failed: {message}. The original file was kept.",
        )


def browser_executable(browser: str) -> Path | None:
    if os.name != "nt":
        return None
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
    program_files_x86 = Path(
        os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")
    )
    candidates: dict[str, list[Path]] = {
        "edge": [
            program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
            program_files / "Microsoft/Edge/Application/msedge.exe",
        ],
        "chrome": [
            local / "Google/Chrome/Application/chrome.exe",
            program_files / "Google/Chrome/Application/chrome.exe",
            program_files_x86 / "Google/Chrome/Application/chrome.exe",
        ],
        "brave": [
            local / "BraveSoftware/Brave-Browser/Application/brave.exe",
            program_files / "BraveSoftware/Brave-Browser/Application/brave.exe",
        ],
        "firefox": [
            program_files / "Mozilla Firefox/firefox.exe",
            program_files_x86 / "Mozilla Firefox/firefox.exe",
        ],
        "opera": [local / "Programs/Opera/opera.exe"],
        "vivaldi": [local / "Vivaldi/Application/vivaldi.exe"],
    }
    for candidate in candidates.get(browser, []):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            pass
    return None


def open_website_login(
    cfg: dict[str, Any],
    job: dict[str, Any],
) -> None:
    options = job.get("source", {}).get("options", {})
    browser = str(options.get("browser_name", "edge")).lower()
    profile = str(options.get("browser_profile", "")).strip()
    url = str(
        options.get(
            "url",
            "https://accounts.google.com/ServiceLogin"
            "?service=youtube&continue=https://www.youtube.com/",
        )
    )
    try:
        executable = browser_executable(browser)
        if executable:
            command = [str(executable)]
            if profile:
                if browser == "firefox":
                    command += ["-P", profile]
                elif "/" in profile or "\\" in profile:
                    command += [f"--user-data-dir={profile}"]
                else:
                    command += [f"--profile-directory={profile}"]
            command.append(url)
            subprocess.Popen(
                command,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:
            webbrowser.open(url, new=1)
        update_job(
            cfg,
            job["id"],
            state="finished",
            message=(
                "Opened the real YouTube/Google login page in "
                f"{browser}. Log in normally, then return to VIC and Test."
            ),
            output=url,
        )
    except Exception as exc:
        update_job(
            cfg,
            job["id"],
            state="failed",
            message=f"Could not open browser login window: {exc}",
            output=url,
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
    if job.get("state") == "waiting":
        return
    with ACTIVE_LOCK:
        if job["id"] in ACTIVE:
            return
    update_job(cfg, job["id"], state="starting", message="Worker accepted job")
    if job.get("mode") == "open_recordings":
        open_recordings_folder(cfg, job)
        return
    if job.get("mode") == "open_website_login":
        open_website_login(cfg, job)
        return
    if job.get("mode") == "recycle_recording":
        recycle_recording_file(cfg, job)
        return
    if job.get("mode") == "recycle_recordings_all":
        recycle_all_recordings(cfg, job)
        return
    if job.get("mode") == "restore_recycled":
        restore_recycled_file(cfg, job)
        return
    if job.get("mode") == "delete_recycled":
        delete_recycled_file(cfg, job)
        return
    if job.get("mode") == "empty_recycle_bin":
        empty_recycle_bin(cfg, job)
        return
    if job.get("mode") == "disk_benchmark":
        benchmark_recording_drive(cfg, job)
        return
    if job.get("mode") in {"delete_recording", "delete_transfer_source"}:
        delete_recording_file(cfg, job)
        return
    if job.get("mode") == "delete_recordings_all":
        delete_all_recording_files(cfg, job)
        return
    if job.get("mode") == "upload_recording_transfer":
        start_control_transfer_thread(cfg, job, upload_recording_transfer)
        return
    if job.get("mode") == "receive_recording_transfer":
        start_control_transfer_thread(cfg, job, receive_recording_transfer)
        return
    kind = job.get("source", {}).get("type")
    if kind == "folder_watch":
        start_folder_job(cfg, job)
    elif kind == "speaker_output":
        start_speaker_job(cfg, job)
    elif kind == "screen" and screen_has_speaker_audio(job.get("source", {})):
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
        health_thread = local.get("stderr_thread")
        if health_thread:
            health_thread.join(timeout=3)
        try:
            local["log"].close()
        except Exception:
            pass
        update_job(
            cfg,
            job["id"],
            state="stopped",
            message="Screen multi-audio capture stopped. The screen MKV and all companion loopback WAV files were closed safely.",
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

        fallback_command = local.get("fallback_command")
        log_path = Path(
            str(local.get("log_path", ""))
        )
        should_fallback = (
            code != 0
            and bool(fallback_command)
            and bool(local.get("fallback_on_auth_error"))
            and not bool(local.get("fallback_used"))
            and website_auth_required(log_path)
        )
        if should_fallback:
            log_handle = local["log"]
            try:
                log_handle.write(
                    "\n"
                    + time.ctime()
                    + "\nVIC automatic account fallback\n"
                    + subprocess.list2cmdline(fallback_command)
                    + "\n"
                )
                log_handle.flush()
                retry_process = subprocess.Popen(
                    fallback_command,
                    stdout=log_handle,
                    stderr=log_handle,
                    creationflags=getattr(
                        subprocess,
                        "CREATE_NO_WINDOW",
                        0,
                    ),
                )
                local["process"] = retry_process
                local["fallback_used"] = True
                update_job(
                    cfg,
                    job_id,
                    state="running",
                    message=local.get(
                        "fallback_message",
                        "Retrying with account access",
                    ),
                    output=local.get("output", ""),
                    pid=retry_process.pid,
                )
                continue
            except OSError as exc:
                try:
                    log_handle.write(
                        f"Account fallback could not start: {exc}\n"
                    )
                    log_handle.flush()
                except Exception:
                    pass

        try:
            local["log"].close()
        except Exception:
            pass
        job_snapshot = local.get("job_snapshot", {}) or {}
        source_snapshot = job_snapshot.get("source", {}) or {}
        reconnect_enabled = bool(
            code != 0
            and local.get("mode") == "record"
            and source_snapshot.get("auto_reconnect", False)
        )
        if reconnect_enabled:
            attempts = int(job_snapshot.get("reconnect_attempts", 0) or 0) + 1
            delay = max(2, min(300, int(source_snapshot.get("reconnect_delay", 5) or 5)))
            update_job(
                cfg,
                job_id,
                state="waiting_reconnect",
                message=f"Source stopped unexpectedly; reconnect attempt {attempts} in {delay} seconds",
                output=local.get("output", ""),
                reconnect_attempts=attempts,
                reconnect_after_ts=time.time() + delay,
            )
            with ACTIVE_LOCK:
                ACTIVE.pop(job_id, None)
            continue

        state = "finished" if code == 0 else "failed"
        message = (
            "Test passed"
            if local.get("mode") == "test" and code == 0
            else "Process finished"
            if code == 0
            else (
                f"Authenticated retry failed with code {code}. "
                f"See logs/job_{job_id}.log"
                if local.get("fallback_used")
                else (
                    f"Process exited with code {code}. "
                    f"See logs/job_{job_id}.log"
                )
            )
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
    now = time.time()
    for job_id, local in snapshot:
        duration = float(local.get("duration_seconds", 0) or 0)
        if duration <= 0:
            duration = max(0.0, now - float(local.get("start_ts", now)))
        file_size = int(local.get("file_size_bytes", 0) or 0)
        for text in local.get("output_paths", []) or []:
            try:
                file_size += Path(str(text)).stat().st_size
            except OSError:
                pass
        bitrate = local.get("bitrate_mbps")
        if (bitrate is None or float(bitrate or 0) <= 0) and duration > 0 and file_size > 0:
            bitrate = file_size * 8.0 / duration / 1_000_000.0
        disk_per_hour = None
        if local.get("is_recording") and bitrate is not None:
            disk_per_hour = float(bitrate) * 1_000_000.0 / 8.0 * 3600.0 / (1024 ** 3)
        item: dict[str, Any] = {
            "audio_level_db": local.get("audio_level_db"),
            "audio_updated_ts": local.get("audio_updated_ts", 0),
            "output": local.get("output", ""),
            "requested_fps": local.get("requested_fps"),
            "actual_fps": local.get("actual_fps"),
            "dropped_frames": local.get("dropped_frames", 0),
            "duplicated_frames": local.get("duplicated_frames", 0),
            "bitrate_mbps": round(float(bitrate), 3) if bitrate is not None else None,
            "file_size_bytes": file_size if local.get("is_recording") else 0,
            "duration_seconds": round(duration, 2),
            "disk_per_hour_gb": round(disk_per_hour, 3) if disk_per_hour is not None else None,
            "encoder": local.get("encoder", ""),
            "speed": local.get("speed", ""),
            "frame_count": local.get("frame_count", 0),
            "health_updated_ts": local.get("health_updated_ts", now),
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

    global TRANSFER_PARALLEL_LIMIT
    global TRANSFER_SLOTS
    TRANSFER_PARALLEL_LIMIT = max(
        1,
        min(12, int(cfg.get("transfer_parallel_limit", 4))),
    )
    TRANSFER_SLOTS = threading.BoundedSemaphore(
        TRANSFER_PARALLEL_LIMIT
    )
    threading.Thread(
        target=transfer_progress_sender,
        args=(cfg,),
        daemon=True,
        name="VIC-Transfer-Progress-Sender",
    ).start()

    dashboard = resolve_dashboard(cfg)
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
    global GPU_DEVICES
    global AUTO_ENCODER_LABEL
    global AUTO_ENCODER_DETAILS
    global FFMPEG_SELECTION_DETAILS
    GPU_DEVICES = detect_gpu_devices()
    ffmpeg = find_ffmpeg(cfg, force_scan=True)
    auto_encoder_args, auto_encoder_label = (
        select_video_encoder(ffmpeg, "auto")
        if ffmpeg
        else ([], "FFmpeg not found")
    )
    AUTO_ENCODER_LABEL = auto_encoder_label
    devices = detect_devices(ffmpeg)
    last_device_scan = time.time()
    last_recording_scan = 0.0
    last_full_status = 0.0
    recording_inventory: list[dict[str, Any]] = []
    psutil.cpu_percent(interval=None)
    connection_failures = 0
    last_discovery_attempt = 0.0

    print("=" * 64)
    print("VIC Worker v0.6.0 EXPERIMENTAL")
    print("Name:", worker_name)
    print("Dashboard:", dashboard)
    print("FFmpeg:", ffmpeg or "NOT FOUND")
    print("FFmpeg mode:", cfg.get("ffmpeg_selection_mode", "auto_compatible"))
    print("Automatic video encoder:", AUTO_ENCODER_LABEL)
    print("Encoder details:", AUTO_ENCODER_DETAILS)
    print("FFmpeg selection:", FFMPEG_SELECTION_DETAILS)
    print("Detected GPUs:", "; ".join(GPU_DEVICES) or "None reported")
    print("Recordings:", recordings_root(cfg))
    print("Parallel transfers:", TRANSFER_PARALLEL_LIMIT)
    print("Transfer chunk size:", f"{TRANSFER_CHUNK_SIZE // (1024 * 1024)} MB")
    print("=" * 64)

    while True:
        monitor_jobs(cfg)
        now = time.time()
        if now - last_device_scan >= 30:
            force_encoder_scan = now - last_device_scan >= 300
            ffmpeg = find_ffmpeg(cfg, force_scan=force_encoder_scan)
            if ffmpeg:
                _auto_args, AUTO_ENCODER_LABEL = select_video_encoder(ffmpeg, "auto")
            devices = detect_devices(ffmpeg)
            last_device_scan = now
        if now - last_recording_scan >= 5:
            recording_inventory = scan_recordings(cfg)
            last_recording_scan = now

        disk = psutil.disk_usage(str(recordings_root(cfg)))
        heartbeat = {
            "id": worker_id,
            "name": worker_name,
            "worker_version": WORKER_VERSION,
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "is_local_dashboard": local_dashboard,
            "cpu": round(psutil.cpu_percent(interval=None), 1),
            "memory": round(psutil.virtual_memory().percent, 1),
            "disk_free_gb": round(disk.free / (1024 ** 3), 1),
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
            "disk_percent": round(float(disk.percent), 1),
            "recordings_root": str(recordings_root(cfg)),
            "active_status": active_status_payload(),
            "video_encoder": AUTO_ENCODER_LABEL,
            "video_encoder_details": AUTO_ENCODER_DETAILS,
            "transfer_parallel_limit": TRANSFER_PARALLEL_LIMIT,
            "transfer_chunk_mb": TRANSFER_CHUNK_SIZE // (1024 * 1024),
            "ffmpeg_selection_details": FFMPEG_SELECTION_DETAILS,
            "ffmpeg_selection_mode": cfg.get("ffmpeg_selection_mode", "auto_compatible"),
            "ffmpeg_last_selected_path": cfg.get("ffmpeg_last_selected_path", ""),
            "ffmpeg_candidates": FFMPEG_SCAN_CACHE.get("candidates", []),
            "gpu_devices": GPU_DEVICES,
        }

        # Audio levels and job commands update rapidly. The larger device and
        # recordings inventories are only resent periodically.
        if now - last_full_status >= full_status_seconds:
            heartbeat.update(
                {
                    "ffmpeg": ffmpeg or "",
                    "devices": devices,
                    "recordings": recording_inventory,
                    "recycle_bin": scan_recycle_bin(cfg),
                }
            )
            last_full_status = now
        try:
            response = post_json(
                dashboard + "/api/worker/heartbeat",
                token,
                heartbeat,
            )
            connection_failures = 0
            remote_jobs = response.get("jobs", [])
            for job in remote_jobs:
                if job.get("desired_state") == "stopped":
                    stop_job(cfg, job)
                elif job.get("state") == "waiting_reconnect":
                    if time.time() >= float(job.get("reconnect_after_ts", time.time() + 1) or 0):
                        update_job(cfg, job["id"], state="pending", message="Reconnect delay finished; reopening source")
                        refreshed = dict(job)
                        refreshed["state"] = "pending"
                        start_job(cfg, ffmpeg, refreshed)
                elif job.get("state") in {"pending", "starting", "running"}:
                    start_job(cfg, ffmpeg, job)
        except URLError as exc:
            connection_failures += 1
            print(time.strftime("%H:%M:%S"), "Dashboard connection failed:", exc)
            if (
                bool(cfg.get("auto_discover", True))
                and connection_failures >= 3
                and now - last_discovery_attempt >= 15
            ):
                last_discovery_attempt = now
                discovered = resolve_dashboard(cfg, force_discovery=True)
                if discovered:
                    dashboard = discovered
                    local_dashboard = dashboard_is_local(dashboard)
                    print("Worker will reconnect to:", dashboard)
        except Exception as exc:
            print(time.strftime("%H:%M:%S"), "Worker error:", exc)
        time.sleep(poll)


if __name__ == "__main__":
    main()
