from __future__ import annotations

import base64
import io
import hashlib
import html
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import datetime
import uuid
import webbrowser
import zipfile
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from typing import Any

from PIL import Image

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    send_from_directory,
    url_for,
)

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from common.discovery import (
    DISCOVERY_PRODUCT,
    start_discovery_responder,
)

CONFIG = BASE / "config"
HELP = BASE / "help"
PREVIEW_DIR = BASE / "dashboard_previews"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
SOURCES_FILE = CONFIG / "sources.json"
JOBS_FILE = CONFIG / "jobs.json"
WORKERS_FILE = CONFIG / "workers.json"
TRANSFERS_FILE = CONFIG / "transfers.json"
SETTINGS_FILE = CONFIG / "dashboard.json"
TRANSFER_STAGE_DIR = BASE / "transfer_staging"
TRANSFER_STAGE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_BACKUP_DIR = BASE / "config_backups"
CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR = BASE / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
RECORDING_FLAGS_FILE = CONFIG / "recording_flags.json"
STORAGES_FILE = CONFIG / "storage_locations.json"
DASHBOARD_ERROR_LOG = BASE / "logs" / "dashboard_error.log"
LOCK = threading.RLock()

app = Flask(__name__)
app.secret_key = "vic-v0.8.7-embedded-audio-modes-local-dashboard"

DASHBOARD_VERSION = "0.8.7"
MIN_TRANSFER_WORKER_VERSION = (0, 4, 3)
TRANSFER_CHUNK_SIZE = 4 * 1024 * 1024
TRANSFER_STATUS_REFRESH_MS = 250


def load_json(path: Path, default: Any) -> Any:
    with LOCK:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default


def backup_durable_json(path: Path) -> None:
    if path not in {SOURCES_FILE, SETTINGS_FILE, RECORDING_FLAGS_FILE, STORAGES_FILE}:
        return
    if not path.is_file():
        return
    target_dir = CONFIG_BACKUP_DIR / path.stem
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = target_dir / f"{path.stem}_{stamp}{path.suffix}"
    try:
        shutil.copy2(path, target)
        backups = sorted(
            target_dir.glob(f"{path.stem}_*{path.suffix}"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in backups[25:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


DURABLE_CONFIG_FILES = {
    SOURCES_FILE,
    SETTINGS_FILE,
    RECORDING_FLAGS_FILE,
    STORAGES_FILE,
}


def save_json(path: Path, data: Any) -> None:
    """Atomically save JSON without breaking worker heartbeats on Windows."""
    with LOCK:
        encoded = json.dumps(data, indent=2)
        json.loads(encoded)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Only long-lived user configuration receives rotating backups.
        # workers/jobs/transfers are volatile and update very frequently.
        if path in DURABLE_CONFIG_FILES:
            backup_durable_json(path)

        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            # Keep a writable descriptor open while flushing and syncing.
            # v0.6.0 reopened this file read-only before fsync, which fails
            # with OSError 9 on Windows/Python 3.14.
            with tmp.open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(encoded)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    # Some filesystems do not support fsync. The atomic
                    # replacement below still prevents partial JSON files.
                    pass

            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


WEBSITE_PAGE_HOST_SUFFIXES = (
    "twitch.tv",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "dailymotion.com",
    "dai.ly",
    "kick.com",
    "rumble.com",
    "facebook.com",
    "fb.watch",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
)
DIRECT_MEDIA_URL_SUFFIXES = (
    ".m3u8",
    ".mpd",
    ".ts",
    ".m2ts",
    ".mp4",
    ".mkv",
    ".webm",
    ".flv",
    ".mov",
    ".aac",
    ".m4a",
    ".mp3",
    ".wav",
    ".ogg",
    ".opus",
)


def source_url_value(item: dict[str, Any]) -> str:
    options = item.get("options", {})
    if not isinstance(options, dict):
        options = {}
    return str(options.get("url", item.get("url", ""))).strip()


def url_is_supported_website_page(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    path = parsed.path.casefold()
    if any(path.endswith(suffix) for suffix in DIRECT_MEDIA_URL_SUFFIXES):
        return False
    host = (parsed.hostname or "").casefold().strip(".")
    return any(
        host == suffix or host.endswith("." + suffix)
        for suffix in WEBSITE_PAGE_HOST_SUFFIXES
    )


def youtube_url_contains_playlist(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold().strip(".")
    youtube_host = (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
    )
    if not youtube_host:
        return False
    if parsed.path.rstrip("/").casefold().endswith("/playlist"):
        return True
    values = parse_qs(parsed.query, keep_blank_values=False)
    return bool(values.get("list"))


def resolved_website_mode(
    url: str,
    requested: str,
    mode_version: int = 0,
) -> str:
    mode = str(requested or "auto").strip().casefold()
    if mode not in {"auto", "single", "playlist", "upcoming"}:
        mode = "auto"
    if mode in {"playlist", "upcoming"}:
        return mode
    if mode == "single" and int(mode_version or 0) >= 2:
        return "single"
    if youtube_url_contains_playlist(url):
        return "playlist"
    return "single"


def repair_source_website_routing(
    item: dict[str, Any],
) -> dict[str, Any]:
    result = dict(item)
    options = result.get("options", {})
    if not isinstance(options, dict):
        options = {}
    else:
        options = dict(options)
    result["options"] = options

    url = str(options.get("url", result.get("url", ""))).strip()
    kind = str(result.get("type", "")).strip().casefold()
    label = str(result.get("type_label", "")).strip().casefold()

    page_url = url_is_supported_website_page(url)
    website_label = label in {
        "website video or livestream",
        "youtube / website video, playlist or scheduled live event",
    }
    if page_url and (kind == "network" or website_label):
        result["type"] = "website"
        result["type_label"] = "Website video or livestream"
        result["routing_repaired"] = True
        kind = "website"

    if kind == "website":
        mode_version = int(options.get("website_mode_version", 0) or 0)
        options["website_mode"] = resolved_website_mode(
            url,
            str(options.get("website_mode", "auto")),
            mode_version,
        )
        options.setdefault("website_mode_version", 2)

    return result


def source_with_defaults(item: dict[str, Any]) -> dict[str, Any]:
    result = repair_source_website_routing(item)
    result.setdefault("enabled", True)
    result.setdefault("archived", False)
    result.setdefault("favourite", False)
    result.setdefault("notes", "")
    result.setdefault("after_recording", "keep")
    result.setdefault("auto_reconnect", False)
    result.setdefault("retry_start_failures", False)
    result.setdefault("reconnect_delay", 5)
    result.setdefault("auto_start_poll_seconds", 10)
    result.setdefault("auto_start_armed", False)
    result.setdefault("storage_id", "")
    result.setdefault("preview_display_width", 0)
    result.setdefault("preview_display_jpeg_quality", 0)
    return result


def recording_flags() -> dict[str, Any]:
    data = load_json(RECORDING_FLAGS_FILE, {"protected": {}})
    if not isinstance(data, dict):
        data = {"protected": {}}
    if not isinstance(data.get("protected"), dict):
        data["protected"] = {}
    return data


def recording_flag_key(worker_id: str, path: str) -> str:
    return worker_id + "|" + str(path).casefold()


def recording_is_protected(worker_id: str, path: str) -> bool:
    return bool(
        recording_flags().get("protected", {}).get(
            recording_flag_key(worker_id, path)
        )
    )


REFRESH_PRESETS = {
    "instant": 0.0,
    "fast": 0.2,
    "mid": 0.5,
    "slow": 1.0,
}

PREVIEW_QUALITY_PRESETS = {
    "lowest": {"width": 160, "jpeg_quality": 18, "label": "Lowest — 160 px"},
    "low": {"width": 320, "jpeg_quality": 12, "label": "Low — 320 px"},
    "balanced": {"width": 640, "jpeg_quality": 7, "label": "Balanced — 640 px"},
    "hd": {"width": 1280, "jpeg_quality": 5, "label": "HD 720p — 1280 px"},
    "full_hd": {"width": 1920, "jpeg_quality": 4, "label": "Full HD 1080p — 1920 px"},
    "qhd": {"width": 2560, "jpeg_quality": 4, "label": "QHD 1440p — 2560 px"},
    "uhd_4k": {"width": 3840, "jpeg_quality": 3, "label": "4K UHD — 3840 px"},
}

AUDIO_METER_MOTION_MODES = {"raw", "light", "medium", "heavy"}


def clamp_refresh_seconds(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(10.0, number)), 3)


def settings() -> dict[str, Any]:
    defaults = {
        "port": 8765,
        "cluster_token": "",
        "worker_offline_seconds": 12,
        "open_browser_on_start": True,
        "audio_meter_refresh_preset": "fast",
        "audio_meter_refresh_seconds": 0.2,
        "audio_meter_smoothing": True,
        "audio_meter_motion": "light",
        "preview_refresh_preset": "mid",
        "preview_refresh_seconds": 0.5,
        "preview_quality": "balanced",
        "preview_width": 640,
        "preview_jpeg_quality": 7,
    }
    loaded = load_json(SETTINGS_FILE, {})
    if not isinstance(loaded, dict):
        loaded = {}
    result = dict(defaults)
    result.update(loaded)
    result["audio_meter_refresh_seconds"] = clamp_refresh_seconds(
        result.get("audio_meter_refresh_seconds"), 0.2
    )
    result["preview_refresh_seconds"] = clamp_refresh_seconds(
        result.get("preview_refresh_seconds"), 0.5
    )
    try:
        result["preview_width"] = max(
            160,
            min(4096, int(result.get("preview_width", 640))),
        )
    except (TypeError, ValueError):
        result["preview_width"] = 640
    try:
        result["preview_jpeg_quality"] = max(
            1,
            min(31, int(result.get("preview_jpeg_quality", 7))),
        )
    except (TypeError, ValueError):
        result["preview_jpeg_quality"] = 7
    legacy_smoothing = bool(result.get("audio_meter_smoothing", True))
    motion = str(result.get("audio_meter_motion", "")).strip().lower()
    if motion not in AUDIO_METER_MOTION_MODES:
        motion = "light" if legacy_smoothing else "raw"
    result["audio_meter_motion"] = motion
    # Retain the old field so copied configs and older workers remain compatible.
    result["audio_meter_smoothing"] = motion != "raw"
    return result


def runtime_live_settings() -> dict[str, Any]:
    cfg = settings()
    preview_seconds = clamp_refresh_seconds(
        cfg.get("preview_refresh_seconds"), 0.5
    )
    # 0.00 means the fastest safe live mode. FFmpeg preview generation is
    # capped at 10 FPS to avoid overwhelming a worker or LAN.
    preview_fps = 10.0 if preview_seconds <= 0 else min(
        10.0,
        max(0.1, 1.0 / preview_seconds),
    )
    return {
        "audio_meter_refresh_seconds": clamp_refresh_seconds(
            cfg.get("audio_meter_refresh_seconds"), 0.2
        ),
        "audio_meter_smoothing": str(
            cfg.get("audio_meter_motion", "light")
        ) != "raw",
        "audio_meter_motion": str(
            cfg.get("audio_meter_motion", "light")
        ),
        "preview_refresh_seconds": preview_seconds,
        "preview_fps": round(preview_fps, 3),
        "preview_width": int(cfg.get("preview_width", 640)),
        "preview_jpeg_quality": int(
            cfg.get("preview_jpeg_quality", 7)
        ),
        "preview_quality": str(
            cfg.get("preview_quality", "balanced")
        ),
    }


def sources() -> list[dict[str, Any]]:
    return [
        source_with_defaults(item)
        for item in load_json(SOURCES_FILE, [])
        if isinstance(item, dict)
    ]


def storage_locations() -> list[dict[str, Any]]:
    values = load_json(STORAGES_FILE, [])
    if not isinstance(values, list):
        values = []
    result: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault("id", uuid.uuid4().hex[:12])
        item.setdefault("name", "Storage")
        item.setdefault("worker_id", "")
        item.setdefault("path", "")
        item.setdefault("notes", "")
        item.setdefault("enabled", True)
        result.append(item)
    return result


def storage_by_id(storage_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in storage_locations()
            if str(item.get("id", "")) == str(storage_id)
        ),
        None,
    )


def storage_paths_for_worker(worker_id: str) -> list[str]:
    paths: list[str] = []
    for item in storage_locations():
        if not item.get("enabled", True):
            continue
        if str(item.get("worker_id", "")) != str(worker_id):
            continue
        value = str(item.get("path", "")).strip()
        if value and value not in paths:
            paths.append(value)
    return paths


def jobs() -> list[dict[str, Any]]:
    return load_json(JOBS_FILE, [])


def transfers() -> list[dict[str, Any]]:
    return load_json(TRANSFERS_FILE, [])


def transfer_by_id(transfer_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in transfers() if item.get("id") == transfer_id),
        None,
    )


def update_transfer(transfer_id: str, **fields: Any) -> dict[str, Any] | None:
    data = transfers()
    updated: dict[str, Any] | None = None
    for item in data:
        if item.get("id") == transfer_id:
            item.update(fields)
            item["updated_ts"] = time.time()
            item["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            updated = item
            break
    if updated is not None:
        save_json(TRANSFERS_FILE, data)
    return updated



def version_tuple(value: Any) -> tuple[int, int, int]:
    numbers = [
        int(part)
        for part in re.findall(r"\d+", str(value or ""))
    ]
    numbers = (numbers + [0, 0, 0])[:3]
    return tuple(numbers)  # type: ignore[return-value]


def transfer_worker_ready(
    worker: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not worker:
        return False, "worker entry is missing"
    if not worker.get("online"):
        return False, (
            f'{worker.get("display_name", worker.get("name", "Worker"))} '
            "is offline"
        )
    current = str(worker.get("worker_version", "")).strip()
    if version_tuple(current) < MIN_TRANSFER_WORKER_VERSION:
        shown = current or "unknown/older"
        return False, (
            f'{worker.get("display_name", worker.get("name", "Worker"))} '
            f"is running VIC {shown}. Stop it and run "
            "START_WORKER.bat from the v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO folder."
        )
    return True, ""


def transfer_age_seconds(item: dict[str, Any]) -> float:
    return max(
        0.0,
        time.time() - float(item.get("updated_ts", item.get("created_ts", 0)) or 0),
    )


def refresh_waiting_transfer_messages() -> None:
    data = transfers()
    changed = False
    for item in data:
        if item.get("state") != "queued_upload":
            continue
        if transfer_age_seconds(item) < 8:
            continue
        source = worker_by_id(str(item.get("source_worker_id", "")))
        ready, reason = transfer_worker_ready(source)
        if not ready:
            message = "Waiting for source worker: " + reason
        else:
            message = (
                "The source worker is online with a compatible VIC worker, but has not "
                "accepted this upload yet. Use Retry now below; if it remains "
                "queued, restart START_WORKER.bat on the source PC."
            )
        if item.get("message") != message:
            item["message"] = message
            item["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            changed = True
    if changed:
        save_json(TRANSFERS_FILE, data)



def workers() -> list[dict[str, Any]]:
    items = load_json(WORKERS_FILE, [])
    cutoff = float(settings().get("worker_offline_seconds", 12))
    now = time.time()
    for item in items:
        item["online"] = now - float(item.get("last_seen_ts", 0)) <= cutoff
        item["display_name"] = (
            f'Local PC (this computer) — {item.get("host", item.get("name", ""))}'
            if item.get("is_local_dashboard")
            else item.get("name", item.get("id", "Worker"))
        )
    items.sort(key=lambda item: (not bool(item.get("is_local_dashboard")), not bool(item.get("online")), str(item.get("display_name", ""))))
    return items


def token_ok() -> bool:
    expected = str(settings().get("cluster_token", ""))
    supplied = request.headers.get("X-VIC-Token", "")
    return bool(expected) and supplied == expected


def source_by_id(source_id: str) -> dict[str, Any] | None:
    return next((item for item in sources() if item.get("id") == source_id), None)


def worker_by_id(worker_id: str) -> dict[str, Any] | None:
    return next((item for item in workers() if item.get("id") == worker_id), None)


def main_worker() -> dict[str, Any] | None:
    return next(
        (item for item in workers() if item.get("is_local_dashboard")),
        None,
    )


def job_by_id(job_id: str) -> dict[str, Any] | None:
    return next((item for item in jobs() if item.get("id") == job_id), None)



TERMINAL_STATES = {"finished", "failed", "stopped"}


def job_is_active(item: dict[str, Any]) -> bool:
    return item.get("state") not in TERMINAL_STATES


def delete_preview(job_id: str) -> None:
    try:
        (PREVIEW_DIR / f"{job_id}.jpg").unlink(missing_ok=True)
    except OSError:
        pass


def remove_job_history(job_ids: set[str]) -> int:
    """Remove inactive dashboard history and cached preview images."""
    if not job_ids:
        return 0

    data = jobs()
    removable = {
        str(item.get("id", ""))
        for item in data
        if str(item.get("id", "")) in job_ids
        and not job_is_active(item)
    }
    if not removable:
        return 0

    save_json(
        JOBS_FILE,
        [
            item
            for item in data
            if str(item.get("id", "")) not in removable
        ],
    )
    for job_id in removable:
        delete_preview(job_id)
    return len(removable)


def configured_source_ids() -> set[str]:
    return {
        str(item.get("id", ""))
        for item in sources()
        if item.get("id")
    }



def active_job_for_source(source_id: str) -> dict[str, Any] | None:
    candidates = [item for item in jobs() if item.get("source_id") == source_id]
    candidates.sort(key=lambda item: item.get("created_ts", 0), reverse=True)
    return next(
        (
            item
            for item in candidates
            if job_is_active(item)
        ),
        None,
    )


def latest_job_for_source(source_id: str) -> dict[str, Any] | None:
    candidates = [item for item in jobs() if item.get("source_id") == source_id]
    candidates.sort(key=lambda item: item.get("created_ts", 0), reverse=True)
    return candidates[0] if candidates else None


def choose_worker(worker_id: str) -> dict[str, Any] | None:
    online = [item for item in workers() if item.get("online")]
    if worker_id and worker_id != "auto":
        return next((item for item in online if item.get("id") == worker_id), None)
    if not online:
        return None
    return sorted(
        online,
        key=lambda item: (
            not bool(item.get("is_local_dashboard")),
            float(item.get("cpu", 100)),
            -float(item.get("disk_free_gb", 0)),
        ),
    )[0]



def action_redirect(default: str = "/"):
    """Return control actions to a safe local page requested by the form."""
    target = str(request.form.get("return_to", "")).strip()
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(default)



def masked_summary(source: dict[str, Any]) -> str:
    summary = str(source.get("summary", ""))
    if source.get("type") in {"rtsp", "network", "website"}:
        if "@" in summary and "://" in summary:
            scheme, rest = summary.split("://", 1)
            rest = rest.split("@", 1)[-1]
            return f"{scheme}://***:***@{rest}"
        return summary[:100]
    return summary


def audio_percent(level: Any) -> int:
    if level is None:
        return 0
    try:
        value = float(level)
    except (TypeError, ValueError):
        return 0
    return int(max(0, min(100, ((value + 60.0) / 60.0) * 100.0)))



def parse_audio_choice(value: str) -> dict[str, Any] | None:
    """Decode one screen audio selector value into a saved descriptor."""
    from urllib.parse import unquote

    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("input:"):
        device = unquote(text[len("input:"):]).strip()
        if not device:
            return None
        return {
            "kind": "input",
            "device": device,
            "name": device,
            "label": device,
        }
    if text.startswith("speaker:"):
        payload = text[len("speaker:"):]
        encoded_id, separator, encoded_name = payload.partition("|")
        speaker_id = unquote(encoded_id).strip()
        speaker_name = unquote(encoded_name).strip() if separator else speaker_id
        if not speaker_id:
            return None
        return {
            "kind": "speaker",
            "id": speaker_id,
            "name": speaker_name or speaker_id,
            "label": speaker_name or speaker_id,
            "samplerate": 48000,
        }
    return None


def audio_choice_value(item: dict[str, Any]) -> str:
    from urllib.parse import quote

    if str(item.get("kind", "")) == "input":
        return "input:" + quote(str(item.get("device", item.get("name", ""))), safe="")
    if str(item.get("kind", "")) == "speaker":
        speaker_id = quote(str(item.get("id", item.get("name", ""))), safe="")
        speaker_name = quote(str(item.get("name", item.get("label", ""))), safe="")
        return f"speaker:{speaker_id}|{speaker_name}"
    return ""


def normalized_screen_audio_devices(options: dict[str, Any]) -> list[dict[str, Any]]:
    stored = options.get("audio_devices", [])
    result: list[dict[str, Any]] = []
    if isinstance(stored, list):
        for item in stored:
            if isinstance(item, dict) and str(item.get("kind", "")) in {"input", "speaker"}:
                result.append(dict(item))
    if result:
        return result

    # Backwards compatibility with the original single-audio fields.
    mode = str(options.get("audio_mode", ""))
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


STYLE = """
<style>
:root{--bg:#0d1015;--panel:#171b22;--panel2:#202630;--line:#343c49;--text:#f3f6f9;--muted:#aeb7c3;--accent:#69adff;--good:#79e18b;--bad:#ff7f7f;--warn:#ffd06c}
*{box-sizing:border-box}body{font-family:Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--text);margin:0}
header{display:flex;align-items:center;gap:20px;padding:18px 28px;background:#151920;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}header h1{font-size:22px;margin:0}
nav{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}a,button{color:var(--text)}nav a,.btn,button{background:var(--panel2);border:1px solid #4a5666;border-radius:8px;padding:9px 13px;text-decoration:none;cursor:pointer}
nav a:hover,.btn:hover,button:hover{border-color:var(--accent)}main{max-width:1380px;margin:auto;padding:25px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:14px}.choice{display:block;background:var(--panel2);border:1px solid #3a4452;border-radius:11px;padding:18px;text-decoration:none;min-height:130px}.choice:hover{border-color:var(--accent)}
.choice strong{display:block;font-size:17px;margin-bottom:7px}.muted{color:var(--muted)}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{width:100%;border-collapse:collapse}th,td{padding:11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}input,select,textarea{width:100%;background:#0f1319;color:var(--text);border:1px solid #4a5666;border-radius:7px;padding:10px;margin:5px 0 14px}
label{font-weight:600}.inline{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.inline>*{margin-top:0}.flash{padding:12px 15px;border:1px solid #475773;background:#222936;border-radius:9px;margin-bottom:15px;white-space:pre-wrap}
.help-tip{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:#36455c;font-size:12px;cursor:help;position:relative;margin-left:5px}.help-tip:hover:after{content:attr(data-tip);position:absolute;z-index:20;left:22px;top:-8px;width:320px;background:#050608;border:1px solid #5b687b;border-radius:8px;padding:10px;font-weight:400}
.tag{display:inline-block;border:1px solid #4c596a;border-radius:999px;padding:3px 8px;font-size:12px;color:var(--muted)}code{word-break:break-all}.small{font-size:13px}
.meter{height:18px;background:#080a0d;border:1px solid #3c4654;border-radius:10px;overflow:hidden}.meter span{display:block;height:100%;width:0;background:linear-gradient(90deg,#4fd06b,#e3d64b,#ef5e5e);transition:width .25s}
.preview{display:block;width:100%;max-height:65vh;object-fit:contain;background:#06080b;border:1px solid #3b4654;border-radius:10px}.worker-local{border-color:#4d8acb}.recording-row td{font-size:14px}
.dashboard-meter{height:28px;min-width:190px;border-radius:14px;box-shadow:inset 0 0 0 1px #202836}
.dashboard-meter span{transition:width .12s linear}
.audio-readout{font-size:22px;font-weight:700;line-height:1.1;margin-bottom:7px}
.audio-caption{font-size:12px;color:var(--muted);margin-top:6px}
.live-tabs{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:16px}
.live-tabs a{background:var(--panel2);border:1px solid #4a5666;border-radius:8px;padding:10px 14px;text-decoration:none}
.live-tabs a.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.live-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}
.live-card{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px 15px 58px;min-width:0;overflow:hidden;transition:border-color .2s,box-shadow .2s,transform .2s}
.live-card:hover{transform:translateY(-1px)}
.live-card.is-recording{border-color:#3fbd5d;box-shadow:0 0 0 1px rgba(79,208,107,.35),0 0 24px rgba(79,208,107,.12)}
.live-card.is-testing,.live-card.is-pending{border-color:#d8b94f;box-shadow:0 0 0 1px rgba(255,208,108,.25)}
.live-card.is-failed{border-color:#d85c5c;box-shadow:0 0 0 1px rgba(255,127,127,.25)}
.live-card h3{margin:0 0 4px}.live-card .meter{height:24px;margin-top:8px}
.live-preview{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#05070a;border:1px solid #394554;border-radius:9px;margin:12px 0}
.live-placeholder{display:flex;align-items:center;justify-content:center;width:100%;aspect-ratio:16/9;background:#090c11;border:1px dashed #445063;border-radius:9px;color:var(--muted);text-align:center;padding:20px;margin:12px 0}
.live-status-row{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.state-pill{display:inline-block;border:1px solid #4a5666;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:700}
.live-global-controls,.live-card-controls{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.live-global-controls form,.live-card-controls form{display:inline;margin:0}
.control-button{padding:8px 11px;font-size:13px;font-weight:700}
.control-test{border-color:#c8aa43;background:#292512}
.control-start{border-color:#3ea75a;background:#122719}
.control-stop{border-color:#bd5151;background:#2a1518}
.control-open{padding:8px 11px;font-size:13px}
.live-card-controls{margin-top:13px;padding-top:12px;border-top:1px solid var(--line)}
.activity-indicator{position:absolute;right:13px;bottom:13px;display:flex;gap:7px;align-items:center;background:#0b0e13e8;border:1px solid #3d4653;border-radius:999px;padding:6px 10px;font-size:11px;font-weight:800;letter-spacing:.06em;box-shadow:0 5px 18px rgba(0,0,0,.3)}
.activity-dot{width:13px;height:13px;border-radius:50%;background:#66707e;box-shadow:0 0 0 3px rgba(102,112,126,.12)}
.activity-dot.recording{background:#52df71;box-shadow:0 0 0 4px rgba(82,223,113,.14),0 0 14px rgba(82,223,113,.9);animation:vicPulse 1.15s infinite}
.activity-dot.testing,.activity-dot.pending{background:#ffd15f;box-shadow:0 0 0 4px rgba(255,209,95,.13),0 0 12px rgba(255,209,95,.7);animation:vicPulse 1.35s infinite}
.activity-dot.failed{background:#ff6969;box-shadow:0 0 0 4px rgba(255,105,105,.13),0 0 12px rgba(255,105,105,.75)}
.activity-dot.inactive{background:#687383}
.activity-dot.previewing{background:#66b7ff;box-shadow:0 0 0 4px rgba(102,183,255,.14),0 0 14px rgba(102,183,255,.85);animation:vicPulse 1.2s infinite}
.live-card.is-previewing{border-color:#559edb;box-shadow:0 0 0 1px rgba(85,158,219,.25),0 0 25px rgba(85,158,219,.13)}
.health-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
.health-card{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:15px}
.health-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:12px}
.health-metric{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:9px}
.health-metric b{display:block;font-size:17px;margin-top:3px}
.health-mini{font-size:12px;line-height:1.45;margin-top:8px;padding:8px;background:var(--panel2);border-radius:8px}
.preview-button{border-color:#559edb;background:#102538}
.meter span{transition-property:width;transition-timing-function:linear}
.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}
.settings-note{padding:10px;border:1px solid var(--line);border-radius:9px;background:var(--panel2)}
.source-worker-bulk{display:grid;grid-template-columns:minmax(180px,1fr) minmax(240px,2fr) auto;gap:10px;align-items:end}
.source-worker-quick{display:flex;gap:6px;align-items:center;min-width:280px}
.source-worker-quick select{min-width:205px;margin:0}
.source-worker-quick button{margin:0;white-space:nowrap}
@media (max-width:900px){
  .source-worker-bulk{grid-template-columns:1fr}
  .source-worker-quick{min-width:220px;flex-wrap:wrap}
  .source-worker-quick select{min-width:170px;flex:1 1 170px}
}
#vicZoomViewport{width:100%;min-width:0}
#vicZoomRoot{zoom:1;width:100%;min-width:0;transform-origin:top left}
#vicZoomViewport.vic-live-transform{position:relative;overflow:hidden}
#vicZoomViewport.vic-live-transform #vicZoomRoot{position:absolute;left:0;top:0}
#vicZoomRoot.vic-live-all-page.vic-zoomed-out main{max-width:none;width:100%;padding-left:18px;padding-right:18px}
#vicZoomRoot.vic-live-all-page.vic-zoomed-out .live-grid{grid-template-columns:repeat(auto-fit,minmax(360px,1fr))}
body{padding-bottom:70px}
body.vic-live-all-transforming{overflow-x:hidden}
.vic-display-controls{position:fixed;right:14px;bottom:12px;z-index:1000;display:flex;align-items:center;gap:7px;flex-wrap:wrap;max-width:calc(100vw - 28px);padding:8px 10px;background:rgba(11,14,19,.96);border:1px solid #4b5869;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.45);backdrop-filter:blur(8px)}
.vic-display-controls button{margin:0;padding:7px 10px;white-space:nowrap}
.vic-display-controls input[type=range]{width:160px;min-width:110px;margin:0;padding:0;background:transparent;border:0}
.vic-zoom-value{display:inline-block;min-width:48px;text-align:center;font-weight:700}
.vic-settings-zoom-row{display:grid;grid-template-columns:auto minmax(120px,1fr) auto;gap:10px;align-items:center;margin:10px 0}
.vic-settings-zoom-row input[type=range]{margin:0}
@media (max-width:760px){
  .vic-display-controls{left:8px;right:8px;bottom:8px;justify-content:center}
  .vic-display-controls input[type=range]{flex:1 1 120px}
  body{padding-bottom:118px}
}
@keyframes vicPulse{0%,100%{transform:scale(.9);opacity:.75}50%{transform:scale(1.15);opacity:1}}
</style>
"""


GLOBAL_DISPLAY_CONTROLS = """
<div id='vicDisplayControls' class='vic-display-controls' aria-label='Dashboard display controls'>
<button type='button' id='vicFullscreenButton' title='Open or exit browser fullscreen'>Fullscreen</button>
<button type='button' id='vicZoomOutButton' title='Zoom out'>−</button>
<input id='vicZoomSlider' type='range' min='40' max='200' step='5' value='100' aria-label='Dashboard zoom percentage' title='Roll the mouse wheel here, or use Ctrl + mouse wheel anywhere'>
<span id='vicZoomValue' class='vic-zoom-value'>100%</span>
<button type='button' id='vicZoomInButton' title='Zoom in'>+</button>
<button type='button' id='vicZoomDefaultButton' title='Return to the original VIC size'>Default 100%</button>
</div>
"""

GLOBAL_DISPLAY_SCRIPT = r"""
<script>
(function(){
  "use strict";

  const storageKey="vicDashboardZoomPercentV1";
  const minimum=40;
  const maximum=200;
  const step=5;
  const viewport=document.getElementById("vicZoomViewport");
  const root=document.getElementById("vicZoomRoot");
  const slider=document.getElementById("vicZoomSlider");
  const valueLabel=document.getElementById("vicZoomValue");
  const zoomOut=document.getElementById("vicZoomOutButton");
  const zoomIn=document.getElementById("vicZoomInButton");
  const zoomDefault=document.getElementById("vicZoomDefaultButton");
  const fullscreenButton=document.getElementById("vicFullscreenButton");

  const settingsSlider=document.getElementById("vicSettingsZoomSlider");
  const settingsValue=document.getElementById("vicSettingsZoomValue");
  const settingsOut=document.getElementById("vicSettingsZoomOut");
  const settingsIn=document.getElementById("vicSettingsZoomIn");
  const settingsDefault=document.getElementById("vicSettingsZoomDefault");
  const settingsFullscreen=document.getElementById("vicSettingsFullscreen");

  let current=100;

  function clamp(value){
    const number=Number(value);
    if(!Number.isFinite(number))return 100;
    return Math.max(minimum,Math.min(maximum,Math.round(number/step)*step));
  }

  function readSaved(){
    try{
      return clamp(window.localStorage.getItem(storageKey) || 100);
    }catch(error){
      return 100;
    }
  }

  function save(value){
    try{
      window.localStorage.setItem(storageKey,String(value));
    }catch(error){
      // Zoom still works for this page when browser storage is unavailable.
    }
  }

  function updateControls(){
    const text=String(current)+"%";
    if(slider)slider.value=String(current);
    if(valueLabel)valueLabel.textContent=text;
    if(settingsSlider)settingsSlider.value=String(current);
    if(settingsValue)settingsValue.textContent=text;
  }

  let liveAllScale=1;
  let heightFrame=0;

  function updateLiveAllViewportHeight(){
    if(!viewport || !root)return;
    if(!viewport.classList.contains("vic-live-transform")){
      viewport.style.height="";
      return;
    }
    if(heightFrame)window.cancelAnimationFrame(heightFrame);
    heightFrame=window.requestAnimationFrame(function(){
      const scaledHeight=Math.ceil(root.scrollHeight*liveAllScale);
      viewport.style.height=String(Math.max(1,scaledHeight))+"px";
      heightFrame=0;
    });
  }

  function clearLiveAllTransform(){
    if(!viewport || !root)return;
    viewport.classList.remove("vic-live-transform");
    document.body.classList.remove("vic-live-all-transforming");
    viewport.style.height="";
    root.style.transform="";
    root.style.width="100%";
    root.style.zoom="1";
    root.classList.remove("vic-zoomed-out");
  }

  function applyZoom(value,shouldSave){
    current=clamp(value);
    if(root){
      const scale=current/100;
      const liveAll=root.classList.contains("vic-live-all-page");

      root.dataset.zoomPercent=String(current);

      if(liveAll){
        // Transform scaling is deterministic: inverse layout width creates
        // real space for extra columns, while the visual result still fills
        // the browser width. The wrapper receives the true scaled height.
        liveAllScale=scale;
        root.style.zoom="1";
        root.style.transform="scale("+String(scale)+")";
        root.style.width=String(100/scale)+"%";
        root.classList.toggle("vic-zoomed-out",current<100);
        if(viewport)viewport.classList.add("vic-live-transform");
        document.body.classList.add("vic-live-all-transforming");
        updateLiveAllViewportHeight();
      }else{
        clearLiveAllTransform();
        root.style.zoom=String(scale);
      }
    }
    updateControls();
    if(shouldSave!==false)save(current);
  }

  function changeZoom(amount){
    applyZoom(current+amount,true);
  }

  async function toggleFullscreen(){
    try{
      if(!document.fullscreenElement){
        await document.documentElement.requestFullscreen();
      }else{
        await document.exitFullscreen();
      }
    }catch(error){
      window.alert("Fullscreen could not be opened by this browser. Press F11 as an alternative.");
    }
  }

  function updateFullscreenButtons(){
    const active=Boolean(document.fullscreenElement);
    const label=active ? "Exit Fullscreen" : "Fullscreen";
    if(fullscreenButton)fullscreenButton.textContent=label;
    if(settingsFullscreen)settingsFullscreen.textContent=label;
  }

  if(slider){
    slider.addEventListener("input",function(){
      applyZoom(slider.value,true);
    });
    slider.addEventListener("wheel",function(event){
      event.preventDefault();
      changeZoom(event.deltaY<0 ? step : -step);
    },{passive:false});
  }
  if(zoomOut)zoomOut.addEventListener("click",function(){changeZoom(-step);});
  if(zoomIn)zoomIn.addEventListener("click",function(){changeZoom(step);});
  if(zoomDefault)zoomDefault.addEventListener("click",function(){applyZoom(100,true);});
  if(fullscreenButton)fullscreenButton.addEventListener("click",toggleFullscreen);

  if(settingsSlider){
    settingsSlider.addEventListener("input",function(){
      applyZoom(settingsSlider.value,true);
    });
    settingsSlider.addEventListener("wheel",function(event){
      event.preventDefault();
      changeZoom(event.deltaY<0 ? step : -step);
    },{passive:false});
  }
  if(settingsOut)settingsOut.addEventListener("click",function(){changeZoom(-step);});
  if(settingsIn)settingsIn.addEventListener("click",function(){changeZoom(step);});
  if(settingsDefault)settingsDefault.addEventListener("click",function(){applyZoom(100,true);});
  if(settingsFullscreen)settingsFullscreen.addEventListener("click",toggleFullscreen);

  document.addEventListener("wheel",function(event){
    if(!event.ctrlKey)return;
    event.preventDefault();
    changeZoom(event.deltaY<0 ? step : -step);
  },{passive:false});

  document.addEventListener("keydown",function(event){
    if(!event.ctrlKey)return;
    if(event.key==="0"){
      event.preventDefault();
      applyZoom(100,true);
    }else if(event.key==="+" || event.key==="="){
      event.preventDefault();
      changeZoom(step);
    }else if(event.key==="-"){
      event.preventDefault();
      changeZoom(-step);
    }
  });

  document.addEventListener("fullscreenchange",function(){
    updateFullscreenButtons();
    applyZoom(current,false);
    updateLiveAllViewportHeight();
  });
  window.addEventListener("resize",function(){
    applyZoom(current,false);
    updateLiveAllViewportHeight();
  });
  window.addEventListener("storage",function(event){
    if(event.key===storageKey && event.newValue!==null){
      applyZoom(event.newValue,false);
    }
  });

  window.vicSetDashboardZoom=function(value){
    applyZoom(value,true);
  };
  window.vicResetDashboardZoom=function(){
    applyZoom(100,true);
  };
  window.vicToggleFullscreen=toggleFullscreen;

  if(typeof ResizeObserver!=="undefined" && root){
    const liveResizeObserver=new ResizeObserver(function(){
      updateLiveAllViewportHeight();
    });
    liveResizeObserver.observe(root);
  }

  applyZoom(readSaved(),false);
  updateFullscreenButtons();
  updateLiveAllViewportHeight();
})();
</script>
"""


def page(title: str, body: str, script: str = "") -> str:
    root_class = "vic-live-all-page" if title == "Live All" else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)} — VIC</title>{STYLE}</head><body>
<div id='vicZoomViewport'><div id='vicZoomRoot' class='{root_class}'><header><h1>VIC — Video Ingest Cluster v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO</h1><nav><a href='/'>Dashboard</a><a href='/sources/manage'>Source Library</a><a href='/add'>+ Add source</a><a href='/mass-capture'>Capture Everything</a><a href='/workers'>Workers</a><a href='/live'>Live</a><a href='/live/all'>Live All</a><a href='/health'>Health</a><a href='/recordings'>Recordings</a><a href='/storage'>Storage</a><a href='/settings'>Settings</a><a href='/tools'>Portable Tools</a><a href='/jobs'>Jobs</a><a href='/help'>Help</a></nav></header><main>
{{% with messages=get_flashed_messages(with_categories=true) %}}{{% for category,message in messages %}}<div class='flash {{{{category}}}}'>{{{{message}}}}</div>{{% endfor %}}{{% endwith %}}{body}</main></div></div>{GLOBAL_DISPLAY_CONTROLS}{script}{GLOBAL_DISPLAY_SCRIPT}</body></html>"""


@app.errorhandler(500)
def dashboard_internal_error(error):
    DASHBOARD_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    original = getattr(error, "original_exception", None)
    details = original or error
    try:
        with DASHBOARD_ERROR_LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n" + "=" * 72 + "\n"
                + time.strftime("%Y-%m-%d %H:%M:%S")
                + f"\nRequest: {request.method} {request.path}\n"
                + "".join(
                    traceback.format_exception(
                        type(details), details, details.__traceback__
                    )
                )
            )
    except OSError:
        pass
    body = """<div class='card'><h2 class='bad'>VIC Dashboard error</h2><p>VIC saved the exact technical error here:</p><p><code>{{log_path}}</code></p><p class='muted'>Running jobs are not stopped by this page error.</p><a class='btn' href='/'>Return to Dashboard</a></div>"""
    return (
        render_template_string(
            page("Dashboard error", body),
            log_path=str(DASHBOARD_ERROR_LOG),
        ),
        500,
    )


SOURCE_TYPES = [
    ("media_file", "🎞️", "Video or audio file", "A file path on the selected worker or a shared network path."),
    ("screen", "🖥️", "Desktop, monitor or application window", "Capture the selected PC's whole desktop, one monitor, a region or one window."),
    ("multi_window", "🪟", "Multiple application windows", "Choose several open applications at once. VIC creates one independent source per window with optional process-only application audio."),
    ("camera", "🎥️", "Camera / capture card / OBS", "DirectShow webcam, USB camera, HDMI capture card or OBS Virtual Camera."),
    ("audio_device", "🎙️", "Microphone or audio input", "Record a DirectShow microphone or audio input independently."),
    ("speaker_output", "🔊", "Speaker or headphone output", "Record audio played through one Windows output using WASAPI loopback."),
    ("rtsp", "📹", "RTSP / IP camera", "An IP camera or RTSP-producing application."),
    ("network", "📡", "Direct network stream", "HLS/HTTP, SRT, UDP, RTP or another direct FFmpeg-readable stream."),
    ("website", "🌐", "Website video or livestream", "YouTube and other supported pages resolved by yt-dlp."),
    ("folder_watch", "📁", "Watched media folder", "Copy new media files from a folder on the selected worker."),
]

SOURCE_TYPE_KEYS = {item[0] for item in SOURCE_TYPES}
SOURCE_TYPE_LABELS = {item[0]: item[2] for item in SOURCE_TYPES}

# Names used by older builds, user-edited JSON files, and common wording.
SOURCE_TYPE_ALIASES = {
    "media_file": "media_file",
    "media": "media_file",
    "file": "media_file",
    "video_file": "media_file",
    "audio_file": "media_file",
    "local_file": "media_file",

    "screen": "screen",
    "desktop": "screen",
    "display": "screen",
    "monitor": "screen",
    "window": "screen",
    "screen_capture": "screen",
    "desktop_capture": "screen",

    "camera": "camera",
    "webcam": "camera",
    "capture_card": "camera",
    "capturecard": "camera",
    "obs": "camera",
    "obs_camera": "camera",
    "video_device": "camera",

    "audio_device": "audio_device",
    "microphone": "audio_device",
    "mic": "audio_device",
    "audio_input": "audio_device",
    "input_audio": "audio_device",

    "speaker_output": "speaker_output",
    "speaker": "speaker_output",
    "speakers": "speaker_output",
    "headphones": "speaker_output",
    "loopback": "speaker_output",
    "desktop_audio": "speaker_output",
    "system_audio": "speaker_output",
    "wasapi_loopback": "speaker_output",

    "rtsp": "rtsp",
    "ip_camera": "rtsp",
    "ipcam": "rtsp",
    "rtsp_camera": "rtsp",

    "network": "network",
    "network_stream": "network",
    "stream": "network",
    "hls": "network",
    "srt": "network",
    "udp": "network",
    "rtp": "network",
    "http_stream": "network",

    "website": "website",
    "web": "website",
    "youtube": "website",
    "livestream": "website",
    "website_video": "website",
    "web_video": "website",

    "folder_watch": "folder_watch",
    "folder": "folder_watch",
    "watch_folder": "folder_watch",
    "watched_folder": "folder_watch",
}

SOURCE_TYPE_LABEL_ALIASES = {
    "video or audio file": "media_file",
    "desktop, monitor or application window": "screen",
    "screen or desktop": "screen",
    "camera / capture card / obs": "camera",
    "camera or capture card": "camera",
    "microphone or audio input": "audio_device",
    "speaker or headphone output": "speaker_output",
    "rtsp / ip camera": "rtsp",
    "direct network stream": "network",
    "website video or livestream": "website",
    "watched media folder": "folder_watch",
}


class SourceImportError(ValueError):
    pass


def normalise_import_type(candidate: dict[str, Any]) -> str:
    raw_value = (
        candidate.get("type")
        or candidate.get("source_type")
        or candidate.get("kind")
        or ""
    )
    normalised = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(raw_value).strip().casefold(),
    ).strip("_")

    label = str(candidate.get("type_label", "")).strip().casefold()
    candidate_url = source_url_value(candidate)
    if (
        normalised == "network"
        and (
            url_is_supported_website_page(candidate_url)
            or label == "website video or livestream"
        )
    ):
        return "website"

    if normalised in SOURCE_TYPE_KEYS:
        return normalised
    if normalised in SOURCE_TYPE_ALIASES:
        return SOURCE_TYPE_ALIASES[normalised]

    if label in SOURCE_TYPE_LABEL_ALIASES:
        return SOURCE_TYPE_LABEL_ALIASES[label]

    if not normalised and not label:
        raise SourceImportError(
            "This JSON does not contain a source type. "
            "When using Export All, select the downloaded ZIP itself or "
            "one of its .vicsource.json files, not manifest.json."
        )

    shown = str(raw_value or candidate.get("type_label", "")).strip()
    raise SourceImportError(
        f'Unsupported source type "{shown}". '
        "The importer supports current VIC source types and common older "
        "names such as desktop, webcam, microphone, loopback and YouTube."
    )


def candidates_from_json(
    raw: Any,
    *,
    origin: str,
) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("source"), dict):
            candidates = [raw["source"]]
        elif isinstance(raw.get("sources"), list):
            possible = raw["sources"]
            # Export-All manifest entries describe files but do not contain
            # the complete options required to recreate a source.
            if raw.get("vic_source_library_format") and all(
                isinstance(item, dict)
                and "file" in item
                and "options" not in item
                for item in possible
            ):
                raise SourceImportError(
                    "You selected manifest.json by itself. "
                    "Select the original VIC Source Library ZIP directly, "
                    "or select one of the .vicsource.json files inside it."
                )
            candidates = possible
        else:
            candidates = [raw]
    else:
        raise SourceImportError(
            f"{origin} does not contain a JSON source object or source list."
        )

    result = [item for item in candidates if isinstance(item, dict)]
    if not result:
        raise SourceImportError(
            f"{origin} does not contain any usable source objects."
        )
    if len(result) > 500:
        raise SourceImportError(
            "This file contains more than 500 sources. "
            "Split it into smaller imports first."
        )
    return result


def decode_json_source_file(
    payload: bytes,
    *,
    origin: str,
) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceImportError(
            f"{origin} is not a UTF-8 JSON file."
        ) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceImportError(
            f"{origin} is not valid JSON: line {exc.lineno}, "
            f"column {exc.colno}."
        ) from exc
    return candidates_from_json(raw, origin=origin)


def decode_source_import(
    filename: str,
    payload: bytes,
) -> tuple[list[dict[str, Any]], str]:
    if not payload:
        raise SourceImportError("The selected file is empty.")

    safe_name = Path(filename or "source-import").name
    looks_like_zip = (
        safe_name.casefold().endswith(".zip")
        or payload.startswith(b"PK\x03\x04")
        or payload.startswith(b"PK\x05\x06")
        or payload.startswith(b"PK\x07\x08")
    )

    if not looks_like_zip:
        return (
            decode_json_source_file(payload, origin=safe_name),
            "JSON source file",
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    except zipfile.BadZipFile as exc:
        raise SourceImportError(
            f"{safe_name} looks like a ZIP but Windows/Python could not "
            "open it as a valid ZIP file."
        ) from exc

    with archive:
        file_infos = [
            info
            for info in archive.infolist()
            if not info.is_dir()
        ]
        total_uncompressed = sum(info.file_size for info in file_infos)
        if total_uncompressed > 50 * 1024 * 1024:
            raise SourceImportError(
                "The selected ZIP expands beyond 50 MB. "
                "Only source/config exports should be imported here."
            )

        names = [info.filename for info in file_infos]
        portable_names = [
            name for name in names
            if name.casefold().endswith(".vicsource.json")
        ]

        # Prefer the individual portable source files in Export All ZIPs.
        # This avoids importing the same source twice when a backup also
        # happens to contain config/sources.json.
        selected_names: list[str]
        format_label: str
        if portable_names:
            selected_names = portable_names
            format_label = "VIC Source Library ZIP"
        else:
            source_list_names = [
                name for name in names
                if Path(name).name.casefold() == "sources.json"
            ]
            if source_list_names:
                selected_names = source_list_names
                format_label = "VIC configuration/backup ZIP"
            else:
                json_names = [
                    name for name in names
                    if name.casefold().endswith(".json")
                    and Path(name).name.casefold() != "manifest.json"
                ]
                selected_names = json_names
                format_label = "JSON ZIP"

        if not selected_names:
            if any(
                Path(name).name.casefold() == "manifest.json"
                for name in names
            ):
                raise SourceImportError(
                    "This ZIP contains manifest.json but no complete portable "
                    "source files. Re-export the Source Library from VIC."
                )
            raise SourceImportError(
                "No .vicsource.json or sources.json file was found in "
                "the selected ZIP."
            )

        collected: list[dict[str, Any]] = []
        errors: list[str] = []
        for name in selected_names:
            try:
                data = archive.read(name)
                collected.extend(
                    decode_json_source_file(data, origin=name)
                )
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        if not collected:
            detail = errors[0] if errors else "No source data was found."
            raise SourceImportError(
                "No source could be read from the ZIP. " + detail
            )

        if len(collected) > 500:
            raise SourceImportError(
                "This ZIP contains more than 500 sources. "
                "Split it into smaller imports first."
            )

        return collected, format_label


def prepare_imported_source(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    imported = json.loads(json.dumps(candidate))
    source_type = normalise_import_type(imported)
    imported["type"] = source_type
    imported["type_label"] = SOURCE_TYPE_LABELS[source_type]

    if not isinstance(imported.get("options"), dict):
        imported["options"] = {}

    for runtime_key in (
        "job",
        "worker_name",
        "display_summary",
        "auth_label",
        "active",
        "online",
    ):
        imported.pop(runtime_key, None)

    imported = source_with_defaults(imported)
    imported["id"] = uuid.uuid4().hex[:12]

    original_name = str(
        imported.get("name")
        or SOURCE_TYPE_LABELS[source_type]
        or "Imported Source"
    ).strip()
    imported["name"] = original_name + " (Imported)"
    imported["enabled"] = False
    imported["archived"] = False
    imported["favourite"] = False
    imported["auto_start_armed"] = False
    imported["imported"] = True
    imported["imported_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    imported["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return imported


@app.get("/")
def dashboard():
    srcs = [item for item in sources() if not item.get("archived")]
    srcs.sort(key=lambda item: (not bool(item.get("favourite")), not bool(item.get("enabled")), str(item.get("name", "")).casefold()))
    worker_map = {item["id"]: item for item in workers()}
    for source in srcs:
        job = latest_job_for_source(source["id"])
        source["job"] = job
        source["display_summary"] = masked_summary(source)
        auth_mode = str(
            source.get("options", {}).get("auth_mode", "none")
        )
        source["auth_label"] = {
            "browser_if_needed": "Account only if needed — browser",
            "browser_always": "Account always — browser",
            "cookie_file_if_needed": "Account only if needed — cookies.txt",
            "cookie_file_always": "Account always — cookies.txt",
        }.get(auth_mode, "")
        assigned = source.get("worker_id", "auto")
        source["worker_name"] = (
            "Automatic"
            if assigned == "auto"
            else worker_map.get(assigned, {}).get("display_name", "Unavailable worker")
        )

    all_jobs = jobs()
    body = """
<div class='grid'>
<div class='card'><strong>Online workers</strong><p class='{{"good" if online else "bad"}}'>{{online}} online</p></div>
<div class='card'><strong>Sources</strong><p>{{srcs|length}} configured</p></div>
<div class='card'><strong>Active jobs</strong><p>{{active}} running or pending</p></div>
<div class='card'><strong>Recordings reported</strong><p>{{recording_count}} files</p></div>
</div>
<div class='card'><div class='inline' style='justify-content:space-between'><div><h2 style='margin:0'>Media sources</h2><p class='muted'>Commands run on Automatic, Local PC, or the worker you selected.</p></div>
<div class='inline'>
<form method='post' action='/sources/test-all'><button title='Send a short test job for every configured source'>Test All</button></form><form method='post' action='/sources/preview-all'><button class='preview-button' title='Open continuous monitoring without saving recordings'>Preview All — Unlimited</button></form>
<form method='post' action='/sources/start-all' onsubmit='return confirm("Start every configured source?")'><button title='Start every source that is not already active'>Start All</button></form>
<form method='post' action='/sources/stop-all'><button title='Stop every active recording and test job'>Stop All</button></form>
<form method='post' action='/jobs/clear-inactive' onsubmit='return confirm("Clear finished, stopped and failed job history and cached previews? Saved recording files will be kept.")'><input type='hidden' name='return_to' value='/'><button class='control-test'>Clear old history</button></form>
<a class='btn' href='/sources/manage'>Manage / Archived</a><a class='btn' href='/sources/import'>Import Source / ZIP</a><form method='post' action='/sources/delete-all' onsubmit='return confirm("Delete ALL configured sources and their inactive VIC history? Saved recording files will be kept. Active sources must be stopped first.")'><input type='hidden' name='return_to' value='/'><button class='control-stop'>Delete All Sources</button></form>
<a class='btn' href='/add'>+ Add source</a>
</div></div>
<table><tr><th>Source</th><th>Worker</th><th>Latest job</th><th style='min-width:230px'>Audio level</th><th>Actions</th></tr>
{% for source in srcs %}<tr><td><strong>{{"★ " if source.favourite else ""}}{{source.name}}</strong> {% if not source.enabled %}<span class='tag bad'>DISABLED</span>{% endif %}<br><span class='tag'>{{source.type_label}}</span>{% if source.auth_label %} <span class='tag'>🔐 {{source.auth_label}}</span>{% endif %}<br><span class='small muted'>{{source.display_summary}}</span>{% if source.notes %}<br><span class='small'>📝 {{source.notes}}</span>{% endif %}{% if source.after_recording != "keep" %}<br><span class='tag'>After recording: {{source.after_recording.replace("_", " ")}}</span>{% endif %}</td><td>{{source.worker_name}}</td>
<td>{% if source.job %}<span id='dashboard-state-{{source.job.id}}' class='{{"good" if source.job.state=="running" else "bad" if source.job.state=="failed" else "muted"}}'>{{source.job.state|upper}}</span><br><span id='dashboard-message-{{source.job.id}}' class='small muted'>{{source.job.message}}</span>{% else %}<span class='muted'>Never run</span>{% endif %}</td>
<td>{% if source.job %}
<div id='dashboard-audio-text-{{source.job.id}}' class='audio-readout'>{% if source.job.audio_level_db is not none %}{{"%.1f"|format(source.job.audio_level_db)}} dB{% else %}No meter{% endif %}</div>
<div class='meter dashboard-meter'><span id='dashboard-audio-meter-{{source.job.id}}' style='width:{{audio_percent(source.job.audio_level_db)}}%'></span></div>
<div class='audio-caption'>Updates several times per second while the worker is online.</div>
{% else %}<span class='muted'>No job data</span>{% endif %}</td>
<td><form style='display:inline' method='post' action='/sources/{{source.id}}/test'><button>Test</button></form>
{% if source.type in preview_types %}<form style='display:inline' method='post' action='/sources/{{source.id}}/preview'><button class='preview-button'>Preview Unlimited</button></form>{% endif %}
<form style='display:inline' method='post' action='/sources/{{source.id}}/start'><button>Start</button></form>
{% if source.type in auto_start_types %}<form style='display:inline' method='post' action='/sources/{{source.id}}/auto-start'><button class='{{"control-stop" if source.auto_start_armed else "control-test"}}'>{{"Disarm Auto Start" if source.auto_start_armed else "Arm Auto Start"}}</button></form>{% endif %}
<form style='display:inline' method='post' action='/sources/{{source.id}}/stop'><button>Stop</button></form>
{% if source.job %}<a class='btn' target='_blank' href='/live/{{source.job.id}}'>Live view</a>{% endif %}
<a class='btn' href='/sources/{{source.id}}/edit'>Edit</a><form style='display:inline' method='post' action='/sources/{{source.id}}/toggle-enabled'><button>{{'Disable' if source.enabled else 'Enable'}}</button></form><form style='display:inline' method='post' action='/sources/{{source.id}}/toggle-favourite'><button>{{'Unfavourite' if source.favourite else 'Favourite'}}</button></form><form style='display:inline' method='post' action='/sources/{{source.id}}/duplicate'><button>Duplicate</button></form><a class='btn' href='/sources/{{source.id}}/export'>Export Source</a><form style='display:inline' method='post' action='/sources/{{source.id}}/archive' onsubmit='return confirm("Archive this source? Its settings are kept and it can be restored later.")'><button>Archive</button></form>
<form style='display:inline' method='post' action='/sources/{{source.id}}/clear-history' onsubmit='return confirm("Clear this source's inactive Live cards, jobs and cached previews? Saved recording files will be kept.")'><input type='hidden' name='return_to' value='/'><button class='control-test'>Clear history</button></form>
<form style='display:inline' method='post' action='/sources/{{source.id}}/delete' onsubmit='return confirm("Delete this source and remove its inactive Live cards, jobs and cached previews? Saved recording files will be kept.")'><input type='hidden' name='return_to' value='/'><button class='control-stop'>Delete source</button></form></td></tr>
{% else %}<tr><td colspan='5' class='muted'>No sources yet. Start the local worker, then add a source.</td></tr>{% endfor %}</table></div>
<div class='card'><h2>Local PC</h2><p>When <strong>START_VIC.bat</strong> is running, the worker list should contain <strong>Local PC (this computer)</strong>. Select it for this computer's screens, microphones, speakers and cameras.</p><p><a class='btn' href='/mass-capture'>Capture every selected local device as its own source</a></p></div>
"""
    worker_items = workers()
    active = sum(
        1
        for item in all_jobs
        if job_is_active(item)
    )
    recording_count = sum(len(item.get("recordings", [])) for item in worker_items)
    dashboard_script = """<script>
const dashboardJobIds={{job_ids|tojson}};
const dashboardAudioSeconds={{audio_refresh_seconds|tojson}};
const dashboardAudioMs=dashboardAudioSeconds<=0?50:Math.max(50,Math.round(dashboardAudioSeconds*1000));
const dashboardMotion={{audio_motion|tojson}};
function vicMeterMotion(meter,percent,mode){
  if(!meter)return;
  const target=Math.max(0,Math.min(100,Number(percent)||0));
  const previous=Number(meter.dataset.level||meter.style.width.replace('%','')||0);
  const rising=target>=previous;
  const timings={raw:[0,0],light:[20,90],medium:[45,180],heavy:[90,420]};
  const pair=timings[mode]||timings.light;
  const duration=rising?pair[0]:pair[1];
  meter.style.transitionProperty='width';
  meter.style.transitionTimingFunction=rising?'linear':'ease-out';
  meter.style.transitionDuration=duration+'ms';
  meter.dataset.level=String(target);
  meter.style.width=target+'%';
}
function dashboardSetMeter(meter,percent){vicMeterMotion(meter,percent,dashboardMotion);}
async function refreshDashboardMeters(){
  try{
    const response=await fetch('/api/live-status',{cache:'no-store'});
    const payload=await response.json();
    const statusMap=new Map((payload.jobs||[]).map(item=>[item.id,item]));
    dashboardJobIds.forEach(jobId=>{
      const item=statusMap.get(jobId);
      if(!item)return;
      const meter=document.getElementById('dashboard-audio-meter-'+jobId);
      const text=document.getElementById('dashboard-audio-text-'+jobId);
      const state=document.getElementById('dashboard-state-'+jobId);
      const message=document.getElementById('dashboard-message-'+jobId);
      dashboardSetMeter(meter,item.audio_percent||0);
      if(text)text.textContent=item.audio_level_db===null?'No meter':item.audio_level_db.toFixed(1)+' dB';
      if(state){
        state.textContent=(item.state||'unknown').toUpperCase();
        state.className=item.state==='running'?'good':item.state==='failed'?'bad':'muted';
      }
      if(message)message.textContent=item.message||'';
    });
  }catch(error){
    console.debug('Dashboard meter refresh failed',error);
  }
}
async function dashboardMeterLoop(){await refreshDashboardMeters();setTimeout(dashboardMeterLoop,dashboardAudioMs);}
dashboardMeterLoop();
</script>"""
    return render_template_string(
        page("Dashboard", body, dashboard_script),
        srcs=srcs,
        online=sum(1 for item in worker_items if item.get("online")),
        active=active,
        recording_count=recording_count,
        job_ids=[
            source["job"]["id"]
            for source in srcs
            if source.get("job")
        ],
        audio_percent=audio_percent,
        preview_types=PREVIEW_SOURCE_TYPES,
        auto_start_types=AUTO_START_SOURCE_TYPES,
        audio_refresh_seconds=runtime_live_settings()["audio_meter_refresh_seconds"],
        audio_smoothing=runtime_live_settings()["audio_meter_smoothing"],
        audio_motion=runtime_live_settings()["audio_meter_motion"],
    )


@app.get("/add")
def add_choice():
    body = """<div class='card'><h2>What would you like to ingest?</h2><p class='muted'>Choose a type. You can assign it to Automatic, Local PC, or another connected worker.</p><div class='grid'>{% for key,icon,name,description in types %}<a class='choice' href='/add/{{key}}'><strong>{{icon}} {{name}}</strong><span class='muted'>{{description}}</span></a>{% endfor %}</div></div>"""
    return render_template_string(page("Add source", body), types=SOURCE_TYPES, audio_refresh_seconds=runtime_live_settings()["audio_meter_refresh_seconds"], audio_smoothing=runtime_live_settings()["audio_meter_smoothing"])


def source_form(source_type: str) -> tuple[str, str, str]:
    common_device_help = "Choose a worker first, then press Load worker devices."
    single_audio_meter = """
<div id='singleAudioMeterBox' class='card' data-row-id='single-audio-meter' data-audio-meter-row='1' style='margin:10px 0;padding:12px'>
  <div class='inline'>
    <button type='button' class='control-test' onclick='startSingleAudioMeter()'>Start live meter</button>
    <button type='button' onclick='stopSingleAudioMeter()'>Stop meter</button>
  </div>
  <div class='audio-readout' id='single-audio-meter-text'>Choose a device, then start the live meter.</div>
  <div class='meter dashboard-meter'><span id='single-audio-meter-meter' style='width:0%'></span></div>
  <div class='small muted' id='single-audio-meter-status'>Setup monitor is stopped. Nothing is being saved.</div>
</div>
"""
    if source_type == "media_file":
        return (
            "Video or audio file",
            "The path must exist on the selected worker.",
            """<label>File path <span class='help-tip' data-tip='For another PC, use a path on that worker or a shared network path.'>?</span></label><input name='path' required placeholder='C:\\Videos\\clip.mp4'><label><input style='width:auto' type='checkbox' name='realtime' checked> Read at normal speed</label><br><label><input style='width:auto' type='checkbox' name='loop'> Loop until stopped</label>""",
        )
    if source_type == "multi_window":
        return (
            "Multiple application windows",
            "Select several open application windows on one worker. VIC creates one normal independent source for each selection.",
            """<div class='settings-note'>
<strong>1. Choose the worker above.</strong><br>
<strong>2. Press Load Application Windows.</strong><br>
<strong>3. Tick every window you want and save once.</strong>
</div>
<div class='inline' style='margin-top:10px'><button type='button' class='control-test' onclick='loadMultiApplicationWindows()'>Load Application Windows</button><button type='button' onclick='selectAllMultiApplicationWindows(true)'>Select all shown</button><button type='button' onclick='selectAllMultiApplicationWindows(false)'>Clear selection</button></div>
<label>Search open applications</label><input id='multiWindowFilter' placeholder='Type part of an application or title' oninput='renderMultiApplicationWindows()'>
<div id='multiWindowList' class='settings-note' style='max-height:430px;overflow:auto'><span class='muted'>Press Load Application Windows.</span></div>
<div id='multiWindowStatus' class='small muted'>No application windows loaded yet.</div>
<fieldset style='margin:14px 0'>
<legend>Video and application audio</legend>
<label class='check'><input type='checkbox' name='application_audio' checked> Capture only each selected application's audio process tree</label>
<label>Audio file arrangement</label>
<select name='audio_storage_mode'>
  <option value='embed_only' selected>Embed audio in the video only</option>
  <option value='separate_only'>Keep video and separate audio files only</option>
  <option value='embed_and_separate'>Embed audio in the video and also keep separate files</option>
</select>
<p class='small muted'><strong>Embed and also keep separate files</strong> creates a normal video containing audio and keeps the application-audio WAV beside it for editing or backup. This does not use speaker/headphone loopback and does not record unrelated applications. Chrome windows/tabs can share one Chrome process tree.</p>
<label>Frame rate</label><select id='fpsMode' name='fps_mode' onchange='fpsModeChanged()'><option value='auto' selected>Auto — match display refresh, capped at 60 FPS</option><option value='30'>30 FPS</option><option value='60'>60 FPS</option><option value='full'>Full display refresh rate</option><option value='custom'>Custom</option></select><div id='customFpsBox' style='display:none'><label>Custom FPS</label><input type='number' name='fps' value='60' min='1' max='240'></div>
<label>Recording encoder</label><select name='encoder_preference'><option value='auto' selected>Automatic — use a working GPU encoder when available</option><option value='nvenc'>NVIDIA NVENC</option><option value='amf'>AMD AMF</option><option value='qsv'>Intel Quick Sync</option><option value='cpu'>CPU — x264</option></select>
</fieldset>""",
        )
    if source_type == "screen":
        return (
            "Desktop, monitor or application window",
            "Captures the selected worker's Windows display.",
            f"""<label>Capture target</label><select name='target' id='target' onchange='targetChanged()'><option value='desktop'>Entire desktop / all monitors</option><option value='monitor'>One specific monitor</option><option value='window'>Application window</option></select>
<div id='monitorBox' style='display:none'><label>Monitor <span class='help-tip' data-tip='{common_device_help}'>?</span></label><select id='screenSelect' name='screen_id'><option value=''>Load worker devices first</option></select></div>
<div id='windowBox' style='display:none'>
<label>Application window</label>
<div class='inline'><input id='windowTitle' name='window_title' placeholder='Choose from the open applications or type a title'><button type='button' onclick='loadApplicationWindows()'>Load Application Windows</button></div>
<input type='hidden' id='windowPid' name='window_pid' value='0'><input type='hidden' id='windowProcess' name='window_process'>
<label class='check'><input type='checkbox' name='application_audio'> Capture only this application's process audio</label>
<p class='small muted'>Choose the window from the loaded list to bind its process ID. This uses Windows application/process audio, not speaker loopback. Updated Windows 10 build 19041–20347 is attempted in compatibility mode; build 20348+ is the officially documented range. Chrome tabs/windows may share one Chrome process tree.</p>
<div id='windowPickerBox' class='settings-note' style='display:none;margin-top:8px'>
<label>Search open applications</label><input id='windowFilter' placeholder='Type part of an application or title' oninput='filterApplicationWindows()'>
<label>Open windows on selected worker</label><select id='windowPicker' size='10' style='min-height:240px' ondblclick='chooseApplicationWindow()'></select>
<div class='inline'><button type='button' class='control-start' onclick='chooseApplicationWindow()'>Use selected window</button><button type='button' onclick='loadApplicationWindows()'>Refresh list</button></div>
<div id='windowPickerStatus' class='small muted'>Press Load Application Windows to view currently open applications.</div>
</div>
<p class='small muted'>VIC inserts the exact current title for you. If the application changes its title later, reload and choose it again.</p>
</div>
<label>Frame rate</label><select id='fpsMode' name='fps_mode' onchange='fpsModeChanged()'><option value='auto' selected>Auto — match display refresh, capped at 60 FPS</option><option value='30'>30 FPS</option><option value='60'>60 FPS</option><option value='full'>Full display refresh rate</option><option value='custom'>Custom</option></select><div id='customFpsBox' style='display:none'><label>Custom FPS</label><input type='number' name='fps' value='60' min='1' max='240'></div>
<label>Recording encoder</label><select name='encoder_preference'><option value='auto' selected>Automatic — use a working GPU encoder when available</option><option value='nvenc'>NVIDIA NVENC</option><option value='amf'>AMD AMF</option><option value='qsv'>Intel Quick Sync</option><option value='cpu'>CPU — x264</option></select>
<label>Optional desktop/loopback audio devices <span class='help-tip' data-tip='{common_device_help} Add one or more microphones, capture-card audio inputs, speakers, HDMI outputs or headphones. Each selected device has its own live level meter.'>?</span></label>
<div id='screenAudioRows'></div>
<div class='inline' style='margin-top:8px'><button type='button' onclick='addScreenAudioRow()'>+ Add another audio device</button><button type='button' onclick='loadDevices()'>Load worker devices</button><button type='button' onclick='stopAllAudioMeters()'>Stop setup meters</button></div>
<label>Application / loopback audio file arrangement</label>
<select name='audio_storage_mode'>
  <option value='separate_only' selected>Keep video and separate audio files only — current loopback behaviour</option>
  <option value='embed_only'>Embed audio in the video only</option>
  <option value='embed_and_separate'>Embed audio in the video and also keep separate files</option>
</select>
<p class='muted small'>Microphone/input and camera audio already records as named tracks inside the MKV. This setting controls application-process audio and speaker/HDMI/headphone loopback audio. In <strong>Embed and also keep separate files</strong>, the final MKV contains the audio and the original WAV files remain beside it. The setup meters monitor only; they do not save audio.</p>
<div id='deviceSummary' class='muted small'></div>
<details><summary>Advanced custom region</summary><p class='muted small'>Used with Entire desktop. A selected monitor automatically uses its own coordinates.</p><div class='grid'><div><label>X offset</label><input type='number' name='offset_x' value='0'></div><div><label>Y offset</label><input type='number' name='offset_y' value='0'></div><div><label>Width</label><input type='number' name='width' value='0' min='0'></div><div><label>Height</label><input type='number' name='height' value='0' min='0'></div></div></details>""",
        )
    if source_type == "camera":
        return (
            "Camera / capture card / OBS",
            "Uses a DirectShow video device on the selected worker.",
            f"""<label>Video device <span class='help-tip' data-tip='{common_device_help}'>?</span></label><div class='inline'><input name='video_device' list='videoDevices' required><button type='button' onclick='loadDevices()'>Load worker devices</button></div><datalist id='videoDevices'></datalist><label>Optional audio device</label><div class='inline'><input id='singleAudioInput' name='audio_device' list='audioDevices' onchange='startSingleAudioMeter()'><button type='button' onclick='loadDevices()'>Load worker devices</button></div><datalist id='audioDevices'></datalist>{single_audio_meter}<div id='deviceSummary' class='muted small'></div><label>Resolution</label><input name='resolution' placeholder='1920x1080'><label>Frame rate</label><select id='fpsMode' name='fps_mode' onchange='fpsModeChanged()'><option value='60' selected>60 FPS</option><option value='30'>30 FPS</option><option value='native'>Device native/default</option><option value='custom'>Custom</option></select><div id='customFpsBox' style='display:none'><label>Custom FPS</label><input type='number' name='fps' value='60' min='1' max='240'></div>
<label>Recording encoder</label><select name='encoder_preference'><option value='auto' selected>Automatic — use a working GPU encoder when available</option><option value='nvenc'>NVIDIA NVENC</option><option value='amf'>AMD AMF</option><option value='qsv'>Intel Quick Sync</option><option value='cpu'>CPU — x264</option></select>""",
        )
    if source_type == "audio_device":
        return (
            "Microphone or audio device",
            "Records a DirectShow audio input on the selected worker.",
            f"""<label>Audio device <span class='help-tip' data-tip='{common_device_help}'>?</span></label><div class='inline'><input id='singleAudioInput' name='audio_device' list='audioDevices' required onchange='startSingleAudioMeter()'><button type='button' onclick='loadDevices()'>Load worker devices</button></div><datalist id='audioDevices'></datalist>{single_audio_meter}<div id='deviceSummary' class='muted small'></div>""",
        )
    if source_type == "speaker_output":
        return (
            "Speaker or headphone output",
            "Records sound played through one Windows output using WASAPI loopback.",
            f"""<label>Speaker/output device <span class='help-tip' data-tip='{common_device_help} This is different from a microphone.'>?</span></label><div class='inline'><select id='speakerSelect' name='speaker_id' required><option value=''>Load worker devices first</option></select><button type='button' onclick='loadDevices()'>Load worker devices</button></div><input type='hidden' id='speakerName' name='speaker_name'>{single_audio_meter}<div id='deviceSummary' class='muted small'></div><p class='muted small'>This captures whatever Windows plays through the selected speakers, headphones, HDMI TV or USB headset. It creates a separate WAV recording.</p>""",
        )
    if source_type == "rtsp":
        return (
            "RTSP / IP camera",
            "The worker connects to the RTSP address.",
            """<label>RTSP URL</label><input name='url' required placeholder='rtsp://user:password@192.168.1.50:554/stream'><label>Transport</label><select name='transport'><option value='tcp'>TCP — reliable</option><option value='udp'>UDP — lower delay</option></select>""",
        )
    if source_type == "network":
        return (
            "Direct network stream",
            "For direct HLS, SRT, UDP, RTP or HTTP media addresses.",
            """<label>Stream address</label><input name='url' required placeholder='https://example/stream.m3u8'>""",
        )
    if source_type == "website":
        return (
            "YouTube / website video, playlist or scheduled live event",
            "Uses yt-dlp on the selected worker. Supports one video/live page, a playlist, or an upcoming scheduled livestream.",
            """<label>Website URL <span class='help-tip' data-tip='Use a normal YouTube or other supported webpage URL. Only record media you are entitled to access.'>?</span></label>
<input id='websiteUrl' name='url' required placeholder='https://www.twitch.tv/channel or YouTube video/playlist URL' oninput='websiteUrlChanged()'>
<label>Website mode</label>
<select id='websiteMode' name='website_mode' onchange='websiteModeChanged()'>
<option value='auto'>Auto Detect — playlist URLs record every item</option>
<option value='single'>Single item only — ignore any playlist in the URL</option>
<option value='playlist'>Entire playlist — save every available item separately</option>
<option value='upcoming'>Upcoming scheduled live event — wait until it starts</option>
</select>
<div id='websiteModeHint' class='settings-note small'>Paste a Twitch, YouTube or supported webpage. VIC will choose yt-dlp automatically.</div>
<div id='singleLiveOptions'>
<label><input style='width:auto' type='checkbox' name='live_from_start'> For supported livestreams, request from the beginning</label>
</div>
<div id='playlistOptions' class='card' style='display:none;margin-top:12px'>
<strong>Playlist behaviour</strong>
<p class='muted small'>Each video is saved separately. VIC keeps a download archive inside this source folder, so pressing Start again later skips items already recorded and collects new playlist entries.</p>
<label><input style='width:auto' type='checkbox' name='playlist_reverse'> Record oldest playlist items first</label>
</div>
<div id='upcomingOptions' class='card' style='display:none;margin-top:12px'>
<strong>Upcoming live-event waiting</strong>
<p class='muted small'>The worker stays ready and checks again until the scheduled event begins. Stop the source to cancel waiting.</p>
<div class='grid'>
<div><label>Minimum check interval (seconds)</label><input type='number' name='wait_min' value='30' min='5' max='3600'></div>
<div><label>Maximum check interval (seconds)</label><input type='number' name='wait_max' value='60' min='5' max='3600'></div>
</div>
<label><input style='width:auto' type='checkbox' name='upcoming_live_from_start' checked> Record from the beginning when supported</label>
</div>
<div class='card' style='margin-top:14px'>
<strong>Optional account/login use</strong>
<p class='muted small'>VIC never asks for or stores your password. Login cookies are used only for this one source, and only according to the option selected below.</p>
<label>Account behaviour</label>
<select id='authMode' name='auth_mode' onchange='authModeChanged()'>
<option value='none'>No account — stay signed out</option>
<option value='browser_if_needed'>Automatic — try signed out first, then use browser login only for a login/age/private-content error</option>
<option value='browser_always'>Always use browser login for this source</option>
<option value='cookie_file_if_needed'>Automatic — try signed out first, then use a cookies.txt file only if needed</option>
<option value='cookie_file_always'>Always use a cookies.txt file for this source</option>
</select>
<div id='browserAuthOptions' style='display:none'>
<label>Logged-in browser on the selected worker PC</label>
<select id='browserName' name='browser_name'>
<option value='edge'>Microsoft Edge</option>
<option value='chrome'>Google Chrome</option>
<option value='firefox'>Mozilla Firefox</option>
<option value='brave'>Brave</option>
<option value='chromium'>Chromium</option>
<option value='opera'>Opera</option>
<option value='vivaldi'>Vivaldi</option>
<option value='whale'>Whale</option>
</select>
<label>Optional browser profile name or path</label>
<input id='browserProfile' name='browser_profile' placeholder='Example: Default or Profile 1'>
<div class='inline'><button type='button' onclick='openWebsiteLogin()'>Open YouTube login window on selected worker</button><span id='loginOpenStatus' class='small muted'></span></div>
<p class='muted small'>This opens the real YouTube/Google sign-in page in the selected browser on the worker PC. Log in there normally, return to VIC, select an account mode, then press Test. VIC never receives your password.</p>
</div>
<div id='cookieFileAuthOptions' style='display:none'>
<label>cookies.txt path on the selected worker PC</label>
<input name='cookies_file' placeholder='C:\\VIC Private\\youtube-cookies.txt'>
<p class='muted small'>This must be a Netscape/Mozilla-format cookies file stored on the worker PC. VIC stores only this path in the source settings, not the cookie contents.</p>
</div>
<p class='warn small'><strong>Account caution:</strong> use account access only for content you are entitled to view and record. Heavy automated use can cause a service to restrict the account.</p>
</div>
<p class='warn small'>Website services can change or require sign-in. Only capture media you own or have permission to record.</p>""",
        )
    if source_type == "folder_watch":
        return (
            "Watched media folder",
            "Copies new media appearing in a folder on the selected worker.",
            """<label>Folder path</label><input name='path' required placeholder='D:\\Incoming Media'>""",
        )
    raise KeyError(source_type)


def source_form_script(
    existing_source: dict[str, Any] | None = None,
) -> str:
    # Source settings may contain Windows device IDs, paths, URLs, quotes,
    # braces or text resembling Jinja. Raw JSON can therefore break the Edit
    # template. Base64 carries ASCII JSON safely through Python, Jinja, HTML
    # and JavaScript, then the browser decodes it.
    existing_json = json.dumps(
        existing_source or {},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    existing_payload = base64.b64encode(
        existing_json.encode("ascii")
    ).decode("ascii")

    # Embed concrete live-settings values directly into the returned script.
    # v0.6.2 left these as Jinja variables. Add/Edit callers that did not pass
    # them produced an Undefined value and crashed the entire form at |tojson.
    live_cfg = runtime_live_settings()
    setup_audio_seconds_json = json.dumps(
        float(live_cfg["audio_meter_refresh_seconds"])
    )
    setup_audio_motion_json = json.dumps(
        str(live_cfg.get("audio_meter_motion", "light"))
    )
    return f"""<script>
const existingSource=JSON.parse(atob('{existing_payload}'));

let loadedAudioInputs=[];
let loadedSpeakers=[];
let loadedApplicationWindows=[];
let nextAudioRowId=1;
const audioMeterJobs=new Map();

function audioChoiceLabel(value){{
  const text=String(value||'');
  if(text.startsWith('input:')){{
    try{{return decodeURIComponent(text.slice(6));}}catch(_e){{return text.slice(6);}}
  }}
  if(text.startsWith('speaker:')){{
    const body=text.slice(8);
    const parts=body.split('|');
    try{{return decodeURIComponent(parts[1]||parts[0]||'Speaker output');}}catch(_e){{return parts[1]||parts[0]||'Speaker output';}}
  }}
  return 'Choose an audio device';
}}

function fillAudioChoiceSelect(select,currentValue='',currentLabel=''){{
  if(!select)return;
  const wanted=String(currentValue||select.value||'');
  select.innerHTML='';
  select.appendChild(new Option('Choose an audio device',''));
  if(loadedAudioInputs.length){{
    const group=document.createElement('optgroup');
    group.label='Microphones / audio inputs — separate MKV tracks';
    loadedAudioInputs.forEach(item=>group.appendChild(
      new Option(item,'input:'+encodeURIComponent(item))
    ));
    select.appendChild(group);
  }}
  if(loadedSpeakers.length){{
    const group=document.createElement('optgroup');
    group.label='Speakers / HDMI / headphones — companion WAV files';
    loadedSpeakers.forEach(item=>group.appendChild(
      new Option(
        item.label||item.name,
        'speaker:'+encodeURIComponent(item.id||item.name)+'|'+encodeURIComponent(item.name||item.label||item.id)
      )
    ));
    select.appendChild(group);
  }}
  ensureSelectValue(select,wanted,currentLabel||audioChoiceLabel(wanted));
}}

function addScreenAudioRow(value='',label=''){{
  const container=document.getElementById('screenAudioRows');
  if(!container)return null;
  const rowId='screen-audio-row-'+(nextAudioRowId++);
  const row=document.createElement('div');
  row.className='card';
  row.style.margin='8px 0';
  row.style.padding='12px';
  row.dataset.rowId=rowId;
  row.dataset.audioMeterRow='1';
  row.innerHTML=`<div class="inline"><select class="screen-audio-select" name="audio_choices" style="min-width:360px"></select><button type="button" class="control-stop">Remove</button></div><div class="audio-readout" id="${{rowId}}-text">Choose a device to view its live level.</div><div class="meter dashboard-meter"><span id="${{rowId}}-meter" style="width:0%"></span></div><div class="small muted" id="${{rowId}}-status">Setup monitor is stopped.</div>`;
  container.appendChild(row);
  const select=row.querySelector('select');
  fillAudioChoiceSelect(select,value,label);
  select.addEventListener('change',()=>startAudioMeterForRow(row));
  row.querySelector('button').addEventListener('click',async()=>{{
    await stopAudioMeterForRow(row);
    row.remove();
    if(!container.querySelector('.screen-audio-select'))addScreenAudioRow();
  }});
  if(value)setTimeout(()=>startAudioMeterForRow(row),150);
  return row;
}}

async function selectedWorkerId(){{
  let id=document.getElementById('workerSelect')?.value||'auto';
  if(id==='auto'){{
    const response=await fetch('/api/automatic-worker',{{cache:'no-store'}});
    const automatic=await response.json();
    if(!automatic.id)throw new Error('No online worker is available.');
    id=automatic.id;
  }}
  return id;
}}

async function stopAudioMeterById(rowId){{
  if(!rowId)return;
  const jobId=audioMeterJobs.get(rowId);
  audioMeterJobs.delete(rowId);
  if(jobId){{
    try{{await fetch('/api/audio-meters/stop',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{job_id:jobId}})}});}}catch(_e){{}}
  }}
  const meter=document.getElementById(rowId+'-meter');
  const text=document.getElementById(rowId+'-text');
  const status=document.getElementById(rowId+'-status');
  if(meter){{meter.style.width='0%';meter.dataset.level='0';}}
  if(text)text.textContent='Meter stopped.';
  if(status){{status.textContent='Setup monitor is stopped. Nothing is being saved.';status.className='small muted';}}
}}

async function startAudioMeterJob(rowId,choice,label){{
  await stopAudioMeterById(rowId);
  const text=document.getElementById(rowId+'-text');
  const status=document.getElementById(rowId+'-status');
  if(!choice){{
    if(text)text.textContent='Choose a device to view its live level.';
    return;
  }}
  if(status){{status.textContent='Opening live audio monitor…';status.className='small muted';}}
  try{{
    const workerId=await selectedWorkerId();
    const response=await fetch('/api/audio-meters/start',{{
      method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{worker_id:workerId,audio_choice:choice,label:label||audioChoiceLabel(choice)}})
    }});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'Could not open audio meter.');
    audioMeterJobs.set(rowId,result.job_id);
    if(status){{status.textContent='Live monitor on '+(result.worker_name||'worker')+'. Nothing is being saved.';status.className='small good';}}
  }}catch(error){{
    if(status){{status.textContent=String(error);status.className='small bad';}}
  }}
}}

async function stopAudioMeterForRow(row){{
  if(row)await stopAudioMeterById(row.dataset.rowId);
}}

async function startAudioMeterForRow(row){{
  if(!row)return;
  const select=row.querySelector('select');
  const choice=select?.value||'';
  const label=select?.options[select.selectedIndex]?.text||audioChoiceLabel(choice);
  await startAudioMeterJob(row.dataset.rowId,choice,label);
}}

function singleAudioChoice(){{
  const speaker=document.getElementById('speakerSelect');
  if(speaker){{
    const selected=loadedSpeakers.find(item=>String(item.id||item.name)===String(speaker.value));
    if(!speaker.value)return {{choice:'',label:''}};
    const name=selected?.name||document.getElementById('speakerName')?.value||speaker.options[speaker.selectedIndex]?.text||speaker.value;
    return {{
      choice:'speaker:'+encodeURIComponent(speaker.value)+'|'+encodeURIComponent(name),
      label:selected?.label||name
    }};
  }}
  const input=document.getElementById('singleAudioInput');
  const name=String(input?.value||'').trim();
  return {{
    choice:name?'input:'+encodeURIComponent(name):'',
    label:name
  }};
}}

async function startSingleAudioMeter(){{
  const selected=singleAudioChoice();
  await startAudioMeterJob('single-audio-meter',selected.choice,selected.label);
}}

async function stopSingleAudioMeter(){{
  await stopAudioMeterById('single-audio-meter');
}}

async function stopAllAudioMeters(){{
  const jobIds=[...audioMeterJobs.values()];
  audioMeterJobs.clear();
  if(jobIds.length){{
    try{{await fetch('/api/audio-meters/stop-all',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{job_ids:jobIds}})}});}}catch(_e){{}}
  }}
  document.querySelectorAll('[data-audio-meter-row][data-row-id]').forEach(row=>{{
    const meter=document.getElementById(row.dataset.rowId+'-meter');
    const text=document.getElementById(row.dataset.rowId+'-text');
    const status=document.getElementById(row.dataset.rowId+'-status');
    if(meter){{meter.style.width='0%';meter.dataset.level='0';}}
    if(text)text.textContent='Meter stopped.';
    if(status){{status.textContent='Setup monitor is stopped. Nothing is being saved.';status.className='small muted';}}
  }});
}}

function vicMeterMotion(meter,percent,mode){{
  if(!meter)return;
  const target=Math.max(0,Math.min(100,Number(percent)||0));
  const previous=Number(meter.dataset.level||meter.style.width.replace('%','')||0);
  const rising=target>=previous;
  const timings={{raw:[0,0],light:[20,90],medium:[45,180],heavy:[90,420]}};
  const pair=timings[mode]||timings.light;
  const duration=rising?pair[0]:pair[1];
  meter.style.transitionProperty='width';
  meter.style.transitionTimingFunction=rising?'linear':'ease-out';
  meter.style.transitionDuration=duration+'ms';
  meter.dataset.level=String(target);
  meter.style.width=target+'%';
}}
async function refreshAudioMeters(){{
  const ids=[...audioMeterJobs.values()];
  if(!ids.length)return;
  try{{
    const response=await fetch('/api/audio-meters/status?ids='+encodeURIComponent(ids.join(',')),{{cache:'no-store'}});
    const result=await response.json();
    const byId=new Map((result.jobs||[]).map(item=>[item.id,item]));
    audioMeterJobs.forEach((jobId,rowId)=>{{
      const item=byId.get(jobId);
      if(!item)return;
      const meter=document.getElementById(rowId+'-meter');
      const text=document.getElementById(rowId+'-text');
      const status=document.getElementById(rowId+'-status');
      if(meter){{vicMeterMotion(meter,item.audio_percent||0,setupAudioMotion);}}
      if(text)text.textContent=item.audio_level_db===null?'Waiting for sound…':Number(item.audio_level_db).toFixed(1)+' dB';
      if(status)status.textContent=(item.state||'unknown').toUpperCase()+' · '+(item.message||'');
      if(['finished','failed','stopped'].includes(item.state))audioMeterJobs.delete(rowId);
    }});
  }}catch(error){{console.debug('Audio meter refresh failed',error);}}
}}
const setupAudioSeconds={setup_audio_seconds_json};
const setupAudioRefreshMs=setupAudioSeconds<=0?50:Math.max(50,Math.round(setupAudioSeconds*1000));
const setupAudioMotion={setup_audio_motion_json};
async function setupAudioMeterLoop(){{await refreshAudioMeters();setTimeout(setupAudioMeterLoop,setupAudioRefreshMs);}}
setupAudioMeterLoop();
window.addEventListener('beforeunload',()=>{{
  const ids=[...audioMeterJobs.values()];
  if(ids.length&&navigator.sendBeacon){{
    navigator.sendBeacon('/api/audio-meters/stop-all',new Blob([JSON.stringify({{job_ids:ids}})],{{type:'application/json'}}));
  }}
}});

function applicationWindowLabel(item){{
  const title=String(item?.title||'').trim();
  const process=String(item?.process||'').trim();
  const pid=Number(item?.pid||0);
  return title+(process?' — '+process:'')+(pid?' (PID '+pid+')':'');
}}

function filterApplicationWindows(){{
  const picker=document.getElementById('windowPicker');
  if(!picker)return;
  const query=String(document.getElementById('windowFilter')?.value||'').trim().toLowerCase();
  const current=String(document.getElementById('windowTitle')?.value||'');
  picker.innerHTML='';
  loadedApplicationWindows
    .filter(item=>!query||applicationWindowLabel(item).toLowerCase().includes(query))
    .forEach(item=>{{
      const option=new Option(applicationWindowLabel(item),String(item.title||''));
      option.dataset.pid=String(Number(item.pid||0));
      option.dataset.process=String(item.process||'');
      picker.appendChild(option);
    }});
  ensureSelectValue(picker,current,current);
  const status=document.getElementById('windowPickerStatus');
  if(status)status.textContent=picker.options.length+' open application window(s) shown.';
}}

function chooseApplicationWindow(){{
  const picker=document.getElementById('windowPicker');
  const title=document.getElementById('windowTitle');
  if(!picker||!title||!picker.value)return;
  title.value=picker.value;
  const chosen=picker.options[picker.selectedIndex];
  const pid=document.getElementById('windowPid');
  const process=document.getElementById('windowProcess');
  if(pid)pid.value=String(chosen?.dataset?.pid||0);
  if(process)process.value=String(chosen?.dataset?.process||'');
  const status=document.getElementById('windowPickerStatus');
  if(status){{status.textContent='Selected exact title and process: '+picker.value+' · PID '+String(chosen?.dataset?.pid||'unknown');status.className='small good';}}
}}

async function loadApplicationWindows(){{
  const status=document.getElementById('windowPickerStatus');
  const panel=document.getElementById('windowPickerBox');
  if(panel)panel.style.display='block';
  if(status){{status.textContent='Loading open application windows from the selected worker…';status.className='small muted';}}
  try{{
    const id=await selectedWorkerId();
    const response=await fetch('/api/workers/'+encodeURIComponent(id)+'/inventory?windows='+Date.now(),{{cache:'no-store'}});
    const devices=await response.json();
    if(!response.ok||devices.error)throw new Error(devices.error||'Could not load application windows.');
    loadedApplicationWindows=Array.isArray(devices.windows)?devices.windows:[];
    filterApplicationWindows();
    if(status&&!loadedApplicationWindows.length){{
      status.textContent='No visible titled application windows were reported. Open the application, wait two seconds, then press Refresh list.';
      status.className='small bad';
    }}
  }}catch(error){{
    if(status){{status.textContent=String(error);status.className='small bad';}}
  }}
}}


function selectedMultiApplicationKeys(){{
  return new Set(
    [...document.querySelectorAll('input[name="multi_window_json"]:checked')]
      .map(item=>String(item.dataset.key||item.value||''))
  );
}}

function updateMultiApplicationCount(){{
  const status=document.getElementById('multiWindowStatus');
  if(!status)return;
  const selected=document.querySelectorAll('input[name="multi_window_json"]:checked').length;
  const shown=document.querySelectorAll('#multiWindowList input[name="multi_window_json"]').length;
  status.textContent=selected+' selected · '+shown+' shown · '+loadedApplicationWindows.length+' loaded';
  status.className='small '+(selected?'good':'muted');
}}

function renderMultiApplicationWindows(){{
  const box=document.getElementById('multiWindowList');
  if(!box)return;
  const selected=selectedMultiApplicationKeys();
  const query=String(document.getElementById('multiWindowFilter')?.value||'').trim().toLowerCase();
  box.innerHTML='';
  const shown=loadedApplicationWindows.filter(item=>!query||applicationWindowLabel(item).toLowerCase().includes(query));
  shown.forEach((item,index)=>{{
    const key=String(item.pid||0)+'|'+String(item.title||'');
    const label=document.createElement('label');
    label.className='check settings-note';
    label.style.display='block';
    label.style.margin='5px 0';
    const input=document.createElement('input');
    input.type='checkbox';
    input.name='multi_window_json';
    input.dataset.key=key;
    input.value=JSON.stringify({{title:String(item.title||''),pid:Number(item.pid||0),process:String(item.process||'')}});
    input.checked=selected.has(key);
    input.addEventListener('change',updateMultiApplicationCount);
    label.appendChild(input);
    label.appendChild(document.createTextNode(' '+applicationWindowLabel(item)));
    box.appendChild(label);
  }});
  if(!shown.length)box.innerHTML='<span class="muted">No matching open windows.</span>';
  updateMultiApplicationCount();
}}

function selectAllMultiApplicationWindows(select){{
  document.querySelectorAll('#multiWindowList input[name="multi_window_json"]').forEach(item=>item.checked=Boolean(select));
  updateMultiApplicationCount();
}}

async function loadMultiApplicationWindows(){{
  const status=document.getElementById('multiWindowStatus');
  if(status){{status.textContent='Loading open windows from the selected worker…';status.className='small muted';}}
  try{{
    const id=await selectedWorkerId();
    const response=await fetch('/api/workers/'+encodeURIComponent(id)+'/inventory?windows='+Date.now(),{{cache:'no-store'}});
    const devices=await response.json();
    if(!response.ok||devices.error)throw new Error(devices.error||'Could not load application windows.');
    loadedApplicationWindows=Array.isArray(devices.windows)?devices.windows:[];
    renderMultiApplicationWindows();
    if(status&&!devices.application_audio_supported){{
      status.textContent='Windows were loaded, but process-only application audio is unavailable on this worker: '+String(devices.application_audio_status||'unknown reason');
      status.className='small bad';
    }}else if(status&&devices.application_audio_compatibility_mode){{
      status.textContent=String(devices.application_audio_status||'Windows 10 compatibility mode enabled.');
      status.className='small muted';
    }}
  }}catch(error){{
    if(status){{status.textContent=String(error);status.className='small bad';}}
  }}
}}

function targetChanged(){{
  const target=document.getElementById('target');
  const win=document.getElementById('windowBox');
  const mon=document.getElementById('monitorBox');
  if(!target)return;
  if(win)win.style.display=target.value==='window'?'block':'none';
  if(mon)mon.style.display=target.value==='monitor'?'block':'none';
}}

function workerChanged(){{
  stopAllAudioMeters();
  loadedAudioInputs=[];
  loadedSpeakers=[];
  loadedApplicationWindows=[];
  const windowPicker=document.getElementById('windowPicker');
  if(windowPicker)windowPicker.innerHTML='';
  const windowPanel=document.getElementById('windowPickerBox');
  if(windowPanel)windowPanel.style.display='none';
  const multiWindowList=document.getElementById('multiWindowList');
  if(multiWindowList)multiWindowList.innerHTML='<span class="muted">Worker changed. Press Load Application Windows again.</span>';
  updateMultiApplicationCount();
  document.querySelectorAll('.screen-audio-select').forEach(select=>fillAudioChoiceSelect(select,select.value,audioChoiceLabel(select.value)));
  const summary=document.getElementById('deviceSummary');
  if(summary)summary.textContent='Worker changed. Press Load worker devices again.';
}}

function websiteUrlLooksLikePlaylist(value){{
  try{{
    const parsed=new URL(String(value||'').trim());
    const host=parsed.hostname.toLowerCase();
    const youtube=host==='youtu.be'||host==='youtube.com'||host.endsWith('.youtube.com');
    return youtube&&(parsed.pathname.replace(/\/$/,'').endsWith('/playlist')||Boolean(parsed.searchParams.get('list')));
  }}catch(error){{
    return false;
  }}
}}

function websiteModeChanged(){{
  const mode=document.getElementById('websiteMode');
  if(!mode)return;
  const url=document.getElementById('websiteUrl')?.value||'';
  const detectedPlaylist=websiteUrlLooksLikePlaylist(url);
  const effective=mode.value==='auto'?(detectedPlaylist?'playlist':'single'):mode.value;
  const single=document.getElementById('singleLiveOptions');
  const playlist=document.getElementById('playlistOptions');
  const upcoming=document.getElementById('upcomingOptions');
  const hint=document.getElementById('websiteModeHint');
  if(single)single.style.display=effective==='single'?'block':'none';
  if(playlist)playlist.style.display=effective==='playlist'?'block':'none';
  if(upcoming)upcoming.style.display=effective==='upcoming'?'block':'none';
  if(hint){{
    if(mode.value==='auto'&&detectedPlaylist){{
      hint.textContent='Playlist detected: VIC will record every available playlist item using yt-dlp.';
    }}else if(mode.value==='auto'){{
      hint.textContent='Single webpage detected: VIC will use yt-dlp for one video or livestream.';
    }}else if(mode.value==='single'&&detectedPlaylist){{
      hint.textContent='Single item only is selected, so VIC will ignore the rest of this playlist.';
    }}else if(mode.value==='playlist'){{
      hint.textContent='Entire playlist selected: every available item will be saved separately.';
    }}else{{
      hint.textContent='VIC will wait for this scheduled livestream to begin.';
    }}
  }}
}}

function websiteUrlChanged(){{
  websiteModeChanged();
}}

function fpsModeChanged(){{
  const mode=document.getElementById('fpsMode');
  const box=document.getElementById('customFpsBox');
  if(mode&&box)box.style.display=mode.value==='custom'?'block':'none';
}}

function authModeChanged(){{
  const mode=document.getElementById('authMode');
  if(!mode)return;
  const browser=document.getElementById('browserAuthOptions');
  const cookieFile=document.getElementById('cookieFileAuthOptions');
  const usesBrowser=mode.value.startsWith('browser_');
  const usesFile=mode.value.startsWith('cookie_file_');
  if(browser)browser.style.display=usesBrowser?'block':'none';
  if(cookieFile)cookieFile.style.display=usesFile?'block':'none';
}}

function ensureSelectValue(element,value,label){{
  if(!element||value===undefined||value===null)return;
  const text=String(value);
  if(text && ![...element.options].some(option=>option.value===text)){{
    element.appendChild(new Option(label||text,text));
  }}
  element.value=text;
}}

function setNamedField(name,value){{
  const elements=[...document.querySelectorAll('[name="'+name+'"]')];
  elements.forEach(element=>{{
    if(element.type==='checkbox'){{
      element.checked=Boolean(value);
    }}else if(element.tagName==='SELECT'){{
      ensureSelectValue(element,value,String(value));
    }}else if(value!==undefined&&value!==null){{
      element.value=String(value);
    }}
  }});
}}

function applyExistingSource(){{
  if(!existingSource||!existingSource.id)return;
  setNamedField('name',existingSource.name||'');
  const worker=document.getElementById('workerSelect');
  ensureSelectValue(
    worker,
    existingSource.worker_id||'auto',
    (existingSource.worker_id||'auto')+' — currently assigned'
  );

  const options=existingSource.options||{{}};
  Object.entries(options).forEach(([key,value])=>setNamedField(key,value));
  if((existingSource.type==='screen'||existingSource.type==='camera')&&!options.fps_mode&&options.fps){{setNamedField('fps_mode','custom');setNamedField('fps',options.fps);}}

  if(existingSource.type==='screen'){{
    const devices=Array.isArray(options.audio_devices)?options.audio_devices:[];
    if(devices.length){{
      devices.forEach(item=>{{
        if(item.kind==='input'){{
          const name=item.device||item.name||item.label||'';
          addScreenAudioRow('input:'+encodeURIComponent(name),item.label||name);
        }}else if(item.kind==='speaker'){{
          const id=item.id||item.name||'';
          const name=item.name||item.label||id;
          addScreenAudioRow('speaker:'+encodeURIComponent(id)+'|'+encodeURIComponent(name),item.label||name);
        }}
      }});
    }}else if(options.audio_mode==='input'&&options.audio_device){{
      addScreenAudioRow('input:'+encodeURIComponent(options.audio_device),options.audio_device);
    }}else if(options.audio_mode==='speaker'&&options.speaker_id){{
      addScreenAudioRow('speaker:'+encodeURIComponent(options.speaker_id)+'|'+encodeURIComponent(options.speaker_name||options.speaker_id),options.speaker_name||options.speaker_id);
    }}else{{
      addScreenAudioRow();
    }}
  }}

  targetChanged();
  websiteModeChanged();
  authModeChanged();
  fpsModeChanged();
}}

async function openWebsiteLogin(){{
  const status=document.getElementById('loginOpenStatus');
  try{{
    let workerId=document.getElementById('workerSelect')?.value||'auto';
    if(workerId==='auto'){{
      const automaticResponse=await fetch('/api/automatic-worker',{{cache:'no-store'}});
      const automatic=await automaticResponse.json();
      if(!automatic.id)throw new Error('No online worker is available.');
      workerId=automatic.id;
    }}
    const browser=document.getElementById('browserName')?.value||'edge';
    const profile=document.getElementById('browserProfile')?.value||'';
    if(status)status.textContent='Sending login-window command...';
    const response=await fetch(
      '/api/workers/'+encodeURIComponent(workerId)+'/open-login',
      {{
        method:'POST',
        headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{browser_name:browser,browser_profile:profile}})
      }}
    );
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'Could not open login window.');
    if(status){{status.textContent='Login window requested on '+(result.worker_name||'worker')+'.';status.className='small good';}}
  }}catch(error){{
    if(status){{status.textContent=String(error);status.className='small bad';}}
  }}
}}

async function loadDevices(){{
  let id=document.getElementById('workerSelect').value;
  if(id==='auto'){{
    const automaticResponse=await fetch('/api/automatic-worker');
    const automatic=await automaticResponse.json();
    if(!automatic.id){{
      alert('No online worker. Start VIC and wait for Local PC to appear.');
      return;
    }}
    id=automatic.id;
  }}

  const current={{
    screen:document.getElementById('screenSelect')?.value||'',
    speaker:document.getElementById('speakerSelect')?.value||'',
    audio:document.getElementById('singleAudioInput')?.value||'',
    window:document.getElementById('windowTitle')?.value||''
  }};

  const response=await fetch('/api/workers/'+id+'/inventory');
  const devices=await response.json();
  if(devices.error){{
    alert(devices.error);
    return;
  }}

  const inputs=(devices.audio_inputs||devices.audio||[]);
  const speakers=(devices.speakers||[]);
  const videoList=document.getElementById('videoDevices');
  const audioList=document.getElementById('audioDevices');
  const screenSelect=document.getElementById('screenSelect');
  const speakerSelect=document.getElementById('speakerSelect');
  const singleAudioInput=document.getElementById('singleAudioInput');
  const screenAudioRows=[...document.querySelectorAll('.screen-audio-select')];
  loadedApplicationWindows=Array.isArray(devices.windows)?devices.windows:[];
  if(document.getElementById('windowPicker'))filterApplicationWindows();

  if(videoList){{
    videoList.innerHTML='';
    (devices.video||[]).forEach(item=>videoList.appendChild(new Option(item,item)));
  }}
  if(audioList){{
    audioList.innerHTML='';
    inputs.forEach(item=>audioList.appendChild(new Option(item,item)));
  }}
  if(singleAudioInput)singleAudioInput.value=current.audio;

  loadedAudioInputs=inputs;
  loadedSpeakers=speakers;
  screenAudioRows.forEach(select=>{{
    const wanted=select.value;
    const label=select.options[select.selectedIndex]?.text||audioChoiceLabel(wanted);
    fillAudioChoiceSelect(select,wanted,label);
    const row=select.closest('[data-row-id]');
    if(wanted&&row)startAudioMeterForRow(row);
  }});

  if(screenSelect){{
    screenSelect.innerHTML='';
    (devices.screens||[]).forEach(item=>screenSelect.appendChild(
      new Option(item.label||item.name,item.id)
    ));
    if(!(devices.screens||[]).length){{
      screenSelect.appendChild(new Option('No monitors reported',''));
    }}
    ensureSelectValue(screenSelect,current.screen,current.screen);
  }}

  if(speakerSelect){{
    speakerSelect.innerHTML='';
    speakers.forEach(item=>speakerSelect.appendChild(
      new Option(item.label||item.name,item.id)
    ));
    if(!speakers.length){{
      speakerSelect.appendChild(new Option('No speaker outputs reported',''));
    }}
    ensureSelectValue(speakerSelect,current.speaker,current.speaker);
    speakerSelect.onchange=()=>{{
      const chosen=speakers.find(item=>item.id===speakerSelect.value);
      const hidden=document.getElementById('speakerName');
      if(hidden)hidden.value=chosen?chosen.name:'';
      startSingleAudioMeter();
    }};
    speakerSelect.dispatchEvent(new Event('change'));
  }}

  const summary=document.getElementById('deviceSummary');
  if(summary){{
    summary.textContent=
      'Loaded '+(devices.screens||[]).length+' monitor(s), '+
      (devices.video||[]).length+' video device(s), '+
      inputs.length+' microphone/input device(s), '+
      speakers.length+' speaker/output device(s), and '+
      loadedApplicationWindows.length+' open application window(s).';
  }}
  if(singleAudioInput&&singleAudioInput.value)setTimeout(startSingleAudioMeter,100);
}}

document.addEventListener('DOMContentLoaded',()=>{{
  applyExistingSource();
  if(document.getElementById('screenAudioRows')&&!document.querySelector('.screen-audio-select'))addScreenAudioRow();
  const singleInput=document.getElementById('singleAudioInput');
  if(singleInput)singleInput.addEventListener('change',startSingleAudioMeter);
  targetChanged();
  websiteModeChanged();
  authModeChanged();
  fpsModeChanged();
}});
</script>"""


@app.get("/add/<source_type>")
def add_form(source_type: str):
    try:
        name, description, fields = source_form(source_type)
    except KeyError:
        return "Unknown source type", 404

    online_workers = [
        item for item in workers()
        if item.get("online")
    ]
    body = """<div class='card'><h2>Add {{type_name}}</h2><p class='muted'>{{description}}</p><form method='post' action='/add/{{source_type}}'><label>Source name</label><input name='name' required placeholder='Friendly source name'><label>Worker <span class='help-tip' data-tip='Local PC means the computer running this dashboard. Automatic usually prefers the local PC, then chooses by CPU and disk.'>?</span></label><select id='workerSelect' name='worker_id' onchange='workerChanged()'><option value='auto'>Automatic</option>{% for worker in workers %}<option value='{{worker.id}}'>{{worker.display_name}} — {{worker.host}}</option>{% endfor %}</select>{% if not workers %}<p class='bad'>No online workers. START_VIC.bat should start Local PC automatically.</p>{% endif %}<fieldset style='margin:14px 0'><legend>Source management</legend><label class='check'><input type='checkbox' name='enabled' {% if not source_item or source_item.enabled %}checked{% endif %}> Enabled — allow Test, Preview and Start</label><label class='check'><input type='checkbox' name='auto_reconnect' {% if source_item and source_item.auto_reconnect %}checked{% endif %}> Automatically reconnect after an unexpected source failure</label><label class='check'><input type='checkbox' name='retry_start_failures' {% if source_item and source_item.retry_start_failures %}checked{% endif %}> Automatically retry when a manual Start fails before recording begins</label><label>Retry / reconnect delay (seconds)</label><input type='number' name='reconnect_delay' min='2' max='300' value='{{source_item.reconnect_delay if source_item else 5}}'><label>Arm Auto Start availability check interval (seconds)</label><input type='number' name='auto_start_poll_seconds' min='2' max='300' value='{{source_item.auto_start_poll_seconds if source_item else 10}}'><p class='small muted'>For RTSP, direct network streams and single/upcoming website streams, Arm Auto Start checks availability without creating an empty recording. It starts recording only after real media is detected.</p><label>Recording storage</label><select name='storage_id'><option value=''>Worker default recording folder</option>{% for storage in storage_locations %}<option value='{{storage.id}}' {{"selected" if source_item and source_item.storage_id==storage.id else ""}}>{{storage.name}} — {{storage.path}} — {{storage.worker_name}}</option>{% endfor %}</select><p class='small muted'>A saved storage location also chooses the worker that owns that path. Manage locations in Storage.</p><label>After a successful recording finishes</label><select name='after_recording'><option value='keep' {{"selected" if not source_item or source_item.after_recording=="keep" else ""}}>Keep on recording worker</option><option value='copy_main' {{"selected" if source_item and source_item.after_recording=="copy_main" else ""}}>Copy recording to Main PC</option><option value='move_main' {{"selected" if source_item and source_item.after_recording=="move_main" else ""}}>Move recording to Main PC after verification</option></select><label>Notes</label><textarea name='notes' rows='3' placeholder='Device instructions, cable, audio delay, or anything useful'>{{source_item.notes if source_item else ""}}</textarea></fieldset>{{fields|safe}}<div class='inline'><button>Save source</button><a class='btn' href='/add'>Back</a></div></form></div>"""
    return render_template_string(
        page(
            "Add source",
            body,
            source_form_script(),
        ),
        type_name=name,
        description=description,
        source_type=source_type,
        fields=fields,
        workers=online_workers,
        source_item={},
        storage_locations=[{**item, "worker_name": (worker_by_id(str(item.get("worker_id", ""))) or {}).get("display_name", item.get("worker_id", "Unknown worker"))} for item in storage_locations() if item.get("enabled", True)],
    )


def parse_source_form_options(
    source_type: str,
    form: Any,
) -> tuple[dict[str, Any], str]:
    if source_type == "media_file":
        options = {
            "path": form.get("path", "").strip(),
            "realtime": "realtime" in form,
            "loop": "loop" in form,
        }
        return options, options["path"]

    if source_type == "multi_window":
        selections: list[dict[str, Any]] = []
        seen_windows: set[tuple[int, str]] = set()
        for raw in form.getlist("multi_window_json"):
            try:
                item = json.loads(str(raw))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            process = str(item.get("process", "")).strip()
            try:
                pid = max(0, int(item.get("pid", 0) or 0))
            except (TypeError, ValueError):
                pid = 0
            if not title:
                continue
            identity = (pid, title.casefold())
            if identity in seen_windows:
                continue
            seen_windows.add(identity)
            selections.append({
                "title": title,
                "pid": pid,
                "process": process,
            })
        if not selections:
            raise ValueError(
                "Load Application Windows and tick at least one window."
            )
        options = {
            "selected_windows": selections,
            "fps_mode": form.get("fps_mode", "auto").strip(),
            "fps": max(1, min(240, int(form.get("fps", 60) or 60))),
            "encoder_preference": form.get("encoder_preference", "auto").strip(),
            "application_audio": "application_audio" in form,
            "audio_storage_mode": (
                form.get("audio_storage_mode", "embed_only").strip()
                if form.get("audio_storage_mode", "embed_only").strip()
                in {"embed_only", "separate_only", "embed_and_separate"}
                else "embed_only"
            ),
        }
        return options, f"{len(selections)} application window(s)"

    if source_type == "screen":
        raw_audio_choices = form.getlist("audio_choices")
        audio_devices: list[dict[str, Any]] = []
        seen_audio: set[tuple[str, str]] = set()
        for raw_choice in raw_audio_choices:
            parsed = parse_audio_choice(str(raw_choice))
            if not parsed:
                continue
            identity = (
                str(parsed.get("kind", "")),
                str(parsed.get("device", parsed.get("id", ""))).casefold(),
            )
            if identity in seen_audio:
                continue
            seen_audio.add(identity)
            audio_devices.append(parsed)

        first = audio_devices[0] if audio_devices else {}
        audio_mode = (
            "none"
            if not audio_devices
            else str(first.get("kind", ""))
            if len(audio_devices) == 1
            else "multiple"
        )
        audio_device = (
            str(first.get("device", ""))
            if first.get("kind") == "input"
            else ""
        )
        speaker_id = (
            str(first.get("id", ""))
            if first.get("kind") == "speaker"
            else ""
        )
        speaker_name = (
            str(first.get("name", ""))
            if first.get("kind") == "speaker"
            else ""
        )

        options = {
            "target": form.get("target", "desktop"),
            "screen_id": form.get("screen_id", "").strip(),
            "window_title": form.get(
                "window_title",
                "",
            ).strip(),
            "window_pid": max(0, int(form.get("window_pid", 0) or 0)),
            "window_process": form.get("window_process", "").strip(),
            "application_audio": "application_audio" in form,
            "application_audio_mode": "process_tree",
            "fps_mode": form.get("fps_mode", "auto").strip(),
            "fps": max(1, min(240, int(form.get("fps", 60) or 60))),
            "encoder_preference": form.get("encoder_preference", "auto").strip(),
            "audio_devices": audio_devices,
            "audio_mode": audio_mode,
            "audio_device": audio_device,
            "speaker_id": speaker_id,
            "speaker_name": speaker_name,
            "samplerate": 48000,
            "audio_storage_mode": (
                form.get("audio_storage_mode", "separate_only").strip()
                if form.get("audio_storage_mode", "separate_only").strip()
                in {"embed_only", "separate_only", "embed_and_separate"}
                else "separate_only"
            ),
            "offset_x": int(form.get("offset_x", 0) or 0),
            "offset_y": int(form.get("offset_y", 0) or 0),
            "width": int(form.get("width", 0) or 0),
            "height": int(form.get("height", 0) or 0),
        }
        if (
            options["target"] == "monitor"
            and not options["screen_id"]
        ):
            raise ValueError(
                "Choose a monitor after loading worker devices."
            )
        summary = (
            options["window_title"]
            if options["target"] == "window"
            else options["screen_id"]
            if options["target"] == "monitor"
            else "Entire desktop"
        )
        if audio_devices:
            summary += f" + {len(audio_devices)} audio device(s)"
        return options, summary

    if source_type == "camera":
        options = {
            "video_device": form.get(
                "video_device",
                "",
            ).strip(),
            "audio_device": form.get(
                "audio_device",
                "",
            ).strip(),
            "resolution": form.get(
                "resolution",
                "",
            ).strip(),
            "fps_mode": form.get("fps_mode", "60").strip(),
            "fps": max(1, min(240, int(form.get("fps", 60) or 60))),
            "encoder_preference": form.get("encoder_preference", "auto").strip(),
        }
        return options, options["video_device"]

    if source_type == "audio_device":
        options = {
            "audio_device": form.get(
                "audio_device",
                "",
            ).strip()
        }
        return options, options["audio_device"]

    if source_type == "speaker_output":
        options = {
            "speaker_id": form.get(
                "speaker_id",
                "",
            ).strip(),
            "speaker_name": form.get(
                "speaker_name",
                "",
            ).strip(),
            "samplerate": 48000,
        }
        if not options["speaker_id"]:
            raise ValueError(
                "Load worker devices and choose a speaker/output device."
            )
        return (
            options,
            options["speaker_name"] or options["speaker_id"],
        )

    if source_type == "rtsp":
        options = {
            "url": form.get("url", "").strip(),
            "transport": form.get("transport", "tcp"),
        }
        return options, options["url"]

    if source_type == "network":
        options = {"url": form.get("url", "").strip()}
        return options, options["url"]

    if source_type == "website":
        website_mode = form.get(
            "website_mode",
            "single",
        ).strip()
        if website_mode not in {
            "auto",
            "single",
            "playlist",
            "upcoming",
        }:
            website_mode = "auto"

        wait_min = max(
            5,
            min(
                3600,
                int(form.get("wait_min", 30) or 30),
            ),
        )
        wait_max = max(
            wait_min,
            min(
                3600,
                int(form.get("wait_max", 60) or 60),
            ),
        )

        auth_mode = form.get(
            "auth_mode",
            "none",
        ).strip()
        allowed_auth_modes = {
            "none",
            "browser_if_needed",
            "browser_always",
            "cookie_file_if_needed",
            "cookie_file_always",
        }
        if auth_mode not in allowed_auth_modes:
            auth_mode = "none"

        allowed_browsers = {
            "brave",
            "chrome",
            "chromium",
            "edge",
            "firefox",
            "opera",
            "vivaldi",
            "whale",
        }
        browser_name = form.get(
            "browser_name",
            "edge",
        ).strip().lower()
        if browser_name not in allowed_browsers:
            browser_name = "edge"

        cookies_file = form.get(
            "cookies_file",
            "",
        ).strip()
        if (
            auth_mode.startswith("cookie_file_")
            and not cookies_file
        ):
            raise ValueError(
                "Enter the cookies.txt path on the selected worker PC."
            )

        options = {
            "url": form.get("url", "").strip(),
            "website_mode": website_mode,
            "website_mode_version": 2,
            "live_from_start": "live_from_start" in form,
            "playlist_reverse": "playlist_reverse" in form,
            "wait_min": wait_min,
            "wait_max": wait_max,
            "upcoming_live_from_start": (
                "upcoming_live_from_start" in form
            ),
            "auth_mode": auth_mode,
            "browser_name": browser_name,
            "browser_profile": form.get(
                "browser_profile",
                "",
            ).strip(),
            "cookies_file": cookies_file,
        }
        effective_mode = resolved_website_mode(
            options["url"],
            website_mode,
            2,
        )
        mode_label = {
            "single": "Single video/live",
            "playlist": "Entire playlist",
            "upcoming": "Upcoming live event",
        }[effective_mode]
        return options, f"{mode_label}: {options['url']}"

    if source_type == "folder_watch":
        options = {
            "path": form.get("path", "").strip()
        }
        return options, options["path"]

    raise ValueError("Unsupported source type.")


@app.post("/add/<source_type>")
def save_source(source_type: str):
    try:
        type_label, _, _ = source_form(source_type)
    except KeyError:
        return "Unknown source type", 404

    name = request.form.get("name", "").strip()
    worker_id = (
        request.form.get("worker_id", "auto").strip()
        or "auto"
    )
    if not name:
        flash("A source name is required.", "bad")
        return redirect(
            url_for("add_form", source_type=source_type)
        )

    try:
        options, summary = parse_source_form_options(
            source_type,
            request.form,
        )
    except ValueError as exc:
        flash(str(exc) or "One of the settings is invalid.", "bad")
        return redirect(
            url_for("add_form", source_type=source_type)
        )

    data = sources()

    if source_type == "multi_window":
        selected_windows = options.get("selected_windows", [])
        created_names: list[str] = []
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for index, selected in enumerate(selected_windows, start=1):
            title = str(selected.get("title", "")).strip()
            process = str(selected.get("process", "")).strip()
            pid = max(0, int(selected.get("pid", 0) or 0))
            short_title = title[:110]
            source_name = (
                f"{name} — {short_title}"
                if len(selected_windows) > 1
                else name
            )
            window_options = {
                "target": "window",
                "screen_id": "",
                "window_title": title,
                "window_pid": pid,
                "window_process": process,
                "application_audio": bool(options.get("application_audio", True)),
                "application_audio_mode": "process_tree",
                "fps_mode": str(options.get("fps_mode", "auto")),
                "fps": int(options.get("fps", 60) or 60),
                "encoder_preference": str(options.get("encoder_preference", "auto")),
                "audio_devices": [],
                "audio_mode": "none",
                "audio_device": "",
                "speaker_id": "",
                "speaker_name": "",
                "samplerate": 44100,
                "audio_storage_mode": str(
                    options.get("audio_storage_mode", "embed_only")
                ),
                "offset_x": 0,
                "offset_y": 0,
                "width": 0,
                "height": 0,
            }
            data.append({
                "id": uuid.uuid4().hex[:12],
                "name": source_name,
                "type": "screen",
                "type_label": SOURCE_TYPE_LABELS["screen"],
                "worker_id": worker_id,
                "options": window_options,
                "summary": title + (
                    " + process-only application audio"
                    if window_options["application_audio"]
                    else ""
                ),
                "enabled": "enabled" in request.form,
                "archived": False,
                "favourite": False,
                "notes": request.form.get("notes", "").strip(),
                "after_recording": (
                    request.form.get("after_recording", "keep")
                    if request.form.get("after_recording", "keep")
                    in {"keep", "copy_main", "move_main"}
                    else "keep"
                ),
                "auto_reconnect": "auto_reconnect" in request.form,
                "retry_start_failures": "retry_start_failures" in request.form,
                "reconnect_delay": max(2, min(300, int(request.form.get("reconnect_delay", 5) or 5))),
                "auto_start_poll_seconds": max(2, min(300, int(request.form.get("auto_start_poll_seconds", 10) or 10))),
                "auto_start_armed": False,
                "storage_id": request.form.get("storage_id", "").strip(),
                "preview_display_width": 0,
                "preview_display_jpeg_quality": 0,
                "created": now,
                "created_from_multi_window": True,
            })
            created_names.append(source_name)
        save_json(SOURCES_FILE, data)
        mode_label = {
            "embed_only": "embedded in each video",
            "separate_only": "kept as separate WAV files",
            "embed_and_separate": "embedded in each video and also kept as separate WAV files",
        }.get(str(options.get("audio_storage_mode", "embed_only")), "embedded in each video")
        audio_note = (
            f" Each source uses process-only application audio, {mode_label}."
            if options.get("application_audio", True)
            else ""
        )
        flash(
            f"Created {len(created_names)} independent application-window source(s)."
            + audio_note
            + " Test them before Start.",
            "good",
        )
        return redirect("/sources/manage")

    data.append(
        {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "type": source_type,
            "type_label": type_label,
            "worker_id": worker_id,
            "options": options,
            "summary": summary,
            "enabled": "enabled" in request.form,
            "archived": False,
            "favourite": False,
            "notes": request.form.get("notes", "").strip(),
            "after_recording": (request.form.get("after_recording", "keep") if request.form.get("after_recording", "keep") in {"keep", "copy_main", "move_main"} else "keep"),
            "auto_reconnect": "auto_reconnect" in request.form,
            "retry_start_failures": "retry_start_failures" in request.form,
            "reconnect_delay": max(2, min(300, int(request.form.get("reconnect_delay", 5) or 5))),
            "auto_start_poll_seconds": max(2, min(300, int(request.form.get("auto_start_poll_seconds", 10) or 10))),
            "auto_start_armed": False,
            "storage_id": request.form.get("storage_id", "").strip(),
            "preview_display_width": 0,
            "preview_display_jpeg_quality": 0,
            "created": time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
    )
    save_json(SOURCES_FILE, data)
    flash(
        f'Source "{name}" added. Click Test before Start.',
        "good",
    )
    return redirect("/")


@app.get("/sources/<source_id>/edit")
def edit_source_form(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404

    source_type = str(source.get("type", ""))
    try:
        type_name, description, fields = source_form(
            source_type
        )
    except KeyError:
        return "Unknown source type", 404

    worker_items = workers()
    body = """<div class='card'><h2>Edit {{source_item.name}}</h2><p class='muted'><strong>Type:</strong> {{type_name}}. The source type stays fixed; its name, worker and settings can be changed.</p>{% if active %}<p class='bad'><strong>This source is active.</strong> Stop it before saving changes.</p>{% endif %}<form method='post' action='/sources/{{source_item.id}}/edit'><label>Source name</label><input name='name' required placeholder='Friendly source name'><label>Worker <span class='help-tip' data-tip='For browser-cookie login, the selected worker must have that browser installed and logged in.'>?</span></label><select id='workerSelect' name='worker_id' onchange='workerChanged()'><option value='auto'>Automatic</option>{% for worker in workers %}<option value='{{worker.id}}'>{{worker.display_name}} — {{worker.host}} — {{"ONLINE" if worker.online else "OFFLINE"}}</option>{% endfor %}</select><fieldset style='margin:14px 0'><legend>Source management</legend><label class='check'><input type='checkbox' name='enabled' {% if not source_item or source_item.enabled %}checked{% endif %}> Enabled — allow Test, Preview and Start</label><label class='check'><input type='checkbox' name='auto_reconnect' {% if source_item and source_item.auto_reconnect %}checked{% endif %}> Automatically reconnect after an unexpected source failure</label><label class='check'><input type='checkbox' name='retry_start_failures' {% if source_item and source_item.retry_start_failures %}checked{% endif %}> Automatically retry when a manual Start fails before recording begins</label><label>Retry / reconnect delay (seconds)</label><input type='number' name='reconnect_delay' min='2' max='300' value='{{source_item.reconnect_delay if source_item else 5}}'><label>Arm Auto Start availability check interval (seconds)</label><input type='number' name='auto_start_poll_seconds' min='2' max='300' value='{{source_item.auto_start_poll_seconds if source_item else 10}}'><p class='small muted'>For RTSP, direct network streams and single/upcoming website streams, Arm Auto Start checks availability without creating an empty recording. It starts recording only after real media is detected.</p><label>Recording storage</label><select name='storage_id'><option value=''>Worker default recording folder</option>{% for storage in storage_locations %}<option value='{{storage.id}}' {{"selected" if source_item and source_item.storage_id==storage.id else ""}}>{{storage.name}} — {{storage.path}} — {{storage.worker_name}}</option>{% endfor %}</select><p class='small muted'>A saved storage location also chooses the worker that owns that path. Manage locations in Storage.</p><label>After a successful recording finishes</label><select name='after_recording'><option value='keep' {{"selected" if not source_item or source_item.after_recording=="keep" else ""}}>Keep on recording worker</option><option value='copy_main' {{"selected" if source_item and source_item.after_recording=="copy_main" else ""}}>Copy recording to Main PC</option><option value='move_main' {{"selected" if source_item and source_item.after_recording=="move_main" else ""}}>Move recording to Main PC after verification</option></select><label>Notes</label><textarea name='notes' rows='3' placeholder='Device instructions, cable, audio delay, or anything useful'>{{source_item.notes if source_item else ""}}</textarea></fieldset>{{fields|safe}}<div class='inline'><button {{"disabled" if active else ""}}>Save changes</button><a class='btn' href='/'>Cancel</a></div></form></div>"""
    return render_template_string(
        page(
            "Edit source",
            body,
            source_form_script(
                {
                    "id": str(source.get("id", "")),
                    "name": str(source.get("name", "")),
                    "type": str(source.get("type", "")),
                    "worker_id": str(source.get("worker_id", "auto")),
                    "options": (
                        source.get("options", {})
                        if isinstance(source.get("options", {}), dict)
                        else {}
                    ),
                    "enabled": bool(source.get("enabled", True)),
                    "archived": bool(source.get("archived", False)),
                    "favourite": bool(source.get("favourite", False)),
                    "notes": str(source.get("notes", "")),
                    "after_recording": str(source.get("after_recording", "keep")),
                    "auto_reconnect": bool(source.get("auto_reconnect", False)),
                    "retry_start_failures": bool(source.get("retry_start_failures", False)),
                    "reconnect_delay": int(source.get("reconnect_delay", 5) or 5),
                    "auto_start_poll_seconds": int(source.get("auto_start_poll_seconds", 10) or 10),
                    "auto_start_armed": bool(source.get("auto_start_armed", False)),
                    "storage_id": str(source.get("storage_id", "")),
                }
            ),
        ),
        source_item=source,
        type_name=type_name,
        description=description,
        fields=fields,
        workers=worker_items,
        active=bool(active_job_for_source(source_id)),
        storage_locations=[{**item, "worker_name": (worker_by_id(str(item.get("worker_id", ""))) or {}).get("display_name", item.get("worker_id", "Unknown worker"))} for item in storage_locations() if item.get("enabled", True)],
    )


@app.post("/sources/<source_id>/edit")
def edit_source(source_id: str):
    existing = source_by_id(source_id)
    if not existing:
        flash("That source no longer exists.", "bad")
        return redirect("/")

    if active_job_for_source(source_id):
        flash(
            "Stop this source before editing it.",
            "bad",
        )
        return redirect(
            url_for(
                "edit_source_form",
                source_id=source_id,
            )
        )

    source_type = str(existing.get("type", ""))
    try:
        type_label, _, _ = source_form(source_type)
    except KeyError:
        flash("That source type is no longer supported.", "bad")
        return redirect("/")

    name = request.form.get("name", "").strip()
    if not name:
        flash("A source name is required.", "bad")
        return redirect(
            url_for(
                "edit_source_form",
                source_id=source_id,
            )
        )

    worker_id = (
        request.form.get("worker_id", "auto").strip()
        or "auto"
    )
    try:
        options, summary = parse_source_form_options(
            source_type,
            request.form,
        )
    except ValueError as exc:
        flash(str(exc) or "One of the settings is invalid.", "bad")
        return redirect(
            url_for(
                "edit_source_form",
                source_id=source_id,
            )
        )

    updated = dict(existing)
    updated.update(
        {
            "name": name,
            "type_label": type_label,
            "worker_id": worker_id,
            "options": options,
            "summary": summary,
            "enabled": "enabled" in request.form,
            "notes": request.form.get("notes", "").strip(),
            "after_recording": (request.form.get("after_recording", "keep") if request.form.get("after_recording", "keep") in {"keep", "copy_main", "move_main"} else "keep"),
            "auto_reconnect": "auto_reconnect" in request.form,
            "retry_start_failures": "retry_start_failures" in request.form,
            "reconnect_delay": max(2, min(300, int(request.form.get("reconnect_delay", 5) or 5))),
            "auto_start_poll_seconds": max(2, min(300, int(request.form.get("auto_start_poll_seconds", 10) or 10))),
            "storage_id": request.form.get("storage_id", "").strip(),
            "modified": time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
    )

    data = [
        updated if item.get("id") == source_id else item
        for item in sources()
    ]
    save_json(SOURCES_FILE, data)
    flash(
        f'Source "{name}" updated. Run Test again before Start.',
        "good",
    )
    return redirect("/")



@app.get("/sources/manage")
def manage_sources_page():
    items = sources()
    items.sort(
        key=lambda item: (
            not bool(item.get("favourite")),
            bool(item.get("archived")),
            not bool(item.get("enabled", True)),
            str(item.get("name", "")).casefold(),
        )
    )
    available_workers = workers()
    known_worker_ids = {
        str(item.get("id", ""))
        for item in available_workers
    }

    for item in items:
        worker_id = str(item.get("worker_id", "auto") or "auto")
        if worker_id == "auto":
            item["worker_display"] = "Automatic"
            item["worker_missing"] = False
        else:
            assigned = next(
                (
                    worker
                    for worker in available_workers
                    if str(worker.get("id", "")) == worker_id
                ),
                None,
            )
            if assigned:
                item["worker_display"] = str(
                    assigned.get("display_name")
                    or assigned.get("name")
                    or worker_id
                )
                item["worker_missing"] = False
            else:
                item["worker_display"] = worker_id
                item["worker_missing"] = (
                    worker_id not in known_worker_ids
                )

    body = """
<div class='card'>
  <div class='live-status-row'>
    <div>
      <h2 style='margin:0'>Source Library</h2>
      <p class='muted'>Disable sources temporarily, archive them without deleting settings, duplicate them, or export/import portable source files.</p>
      <p class='small'><strong>After importing:</strong> use the worker assignment section below. Imported sources stay disabled until you test and enable them.</p>
    </div>
    <div class='inline'>
      <a class='btn' href='/add'>+ Add source</a>
      <form method='post' action='/sources/enable-all' onsubmit='return confirm("Enable every non-archived source? Archived sources will remain archived.")'>
        <button class='control-start'>Enable All</button>
      </form>
      <form method='post' action='/sources/disable-all' onsubmit='return confirm("Disable all sources? Any source currently previewing, testing or recording will be left enabled until it is stopped.")'>
        <button class='control-stop'>Disable All</button>
      </form>
      <a class='btn' href='/sources/import'>Import Source / ZIP</a>
      <a class='btn' href='/sources/export-all'>Export All Sources ZIP</a>
    </div>
  </div>
</div>

<div class='card'>
  <h3>Set worker after import</h3>
  <p class='muted'>Choose which sources to change and the worker PC that should capture them. This does not enable or start any source.</p>
  <form method='post' action='/sources/set-worker-bulk'
        class='source-worker-bulk'
        onsubmit='return confirm("Apply this worker to every source in the selected group? Active sources will be skipped.")'>
    <label>
      Sources to change
      <select name='scope'>
        <option value='imported' selected>Imported sources — recommended</option>
        <option value='disabled'>All disabled, non-archived sources</option>
        <option value='automatic'>Sources using Automatic or a missing worker</option>
        <option value='all_non_archived'>Every non-archived source</option>
      </select>
    </label>
    <label>
      Set worker
      <select name='worker_id' required>
        <option value='auto'>Automatic worker selection</option>
        {% for worker in workers %}
        <option value='{{worker.id}}'>{{worker.display_name}} — {{"ONLINE" if worker.online else "OFFLINE"}}</option>
        {% endfor %}
      </select>
    </label>
    <button class='control-test'>Set Worker for Selected Group</button>
  </form>
  <p class='small muted'>A source currently testing, previewing or recording is skipped. If its saved recording storage belongs to a different worker, VIC clears that incompatible storage selection and reports it.</p>
</div>

<table>
  <tr>
    <th>Source</th>
    <th>Status</th>
    <th>Worker</th>
    <th>After recording</th>
    <th>Notes</th>
    <th>Actions</th>
  </tr>
  {% for source in sources %}
  <tr>
    <td>
      <strong>{{"★ " if source.favourite else ""}}{{source.name}}</strong><br>
      <span class='tag'>{{source.type_label}}</span>
      {% if source.imported or source.name.endswith("(Imported)") %}
      <span class='tag'>IMPORTED</span>
      {% endif %}
      {% if source.routing_repaired %}
      <span class='tag'>YT-DLP ROUTING REPAIRED</span>
      {% endif %}
    </td>
    <td>
      {% if source.archived %}
      <span class='tag'>ARCHIVED</span>
      {% elif source.enabled %}
      <span class='good'>ENABLED</span>
      {% else %}
      <span class='bad'>DISABLED</span>
      {% endif %}
    </td>
    <td>
      <form method='post'
            action='/sources/{{source.id}}/set-worker'
            class='source-worker-quick'>
        <select name='worker_id' aria-label='Worker for {{source.name}}'>
          <option value='auto' {{"selected" if source.worker_id in ["", "auto"] else ""}}>Automatic</option>
          {% for worker in workers %}
          <option value='{{worker.id}}' {{"selected" if source.worker_id==worker.id else ""}}>{{worker.display_name}} — {{"ONLINE" if worker.online else "OFFLINE"}}</option>
          {% endfor %}
          {% if source.worker_missing %}
          <option value='{{source.worker_id}}' selected>Missing saved worker — {{source.worker_id}}</option>
          {% endif %}
        </select>
        <button>Set</button>
      </form>
      <span class='small {{"bad" if source.worker_missing else "muted"}}'>
        Current: {{source.worker_display}}
      </span>
    </td>
    <td>{{source.after_recording.replace("_", " ")}}</td>
    <td>{{source.notes or "—"}}</td>
    <td>
      <div class='inline'>
        <a class='btn' href='/sources/{{source.id}}/edit'>Edit</a>
        <form method='post' action='/sources/{{source.id}}/toggle-enabled'>
          <button>{{"Disable" if source.enabled else "Enable"}}</button>
        </form>
        <form method='post' action='/sources/{{source.id}}/toggle-favourite'>
          <button>{{"Unfavourite" if source.favourite else "Favourite"}}</button>
        </form>
        <form method='post' action='/sources/{{source.id}}/duplicate'>
          <button>Duplicate</button>
        </form>
        <a class='btn' href='/sources/{{source.id}}/export'>Export Source</a>
        {% if source.archived %}
        <form method='post' action='/sources/{{source.id}}/restore'>
          <button class='control-start'>Restore</button>
        </form>
        {% else %}
        <form method='post' action='/sources/{{source.id}}/archive'>
          <button>Archive</button>
        </form>
        {% endif %}
      </div>
    </td>
  </tr>
  {% else %}
  <tr><td colspan='6' class='muted'>No sources have been configured.</td></tr>
  {% endfor %}
</table>
"""
    return render_template_string(
        page("Source Library", body),
        sources=items,
        workers=available_workers,
    )


def update_source_fields(source_id: str, **fields: Any) -> dict[str, Any] | None:
    data = sources()
    updated = None
    for item in data:
        if item.get("id") == source_id:
            item.update(fields)
            item["modified"] = time.strftime("%Y-%m-%d %H:%M:%S")
            updated = item
            break
    if updated:
        save_json(SOURCES_FILE, data)
    return updated


def source_looks_imported(item: dict[str, Any]) -> bool:
    return bool(
        item.get("imported")
        or item.get("imported_at")
        or str(item.get("name", "")).strip().endswith("(Imported)")
    )


def source_matches_worker_scope(
    item: dict[str, Any],
    scope: str,
) -> bool:
    if scope == "imported":
        return source_looks_imported(item)
    if scope == "disabled":
        return (
            not bool(item.get("archived", False))
            and not bool(item.get("enabled", True))
        )
    if scope == "automatic":
        worker_id = str(item.get("worker_id", "auto") or "auto")
        return (
            worker_id == "auto"
            or worker_by_id(worker_id) is None
        )
    if scope == "all_non_archived":
        return not bool(item.get("archived", False))
    return False


def apply_worker_assignment_to_source(
    item: dict[str, Any],
    worker_id: str,
) -> tuple[bool, bool]:
    """Set one source's worker and clear incompatible saved storage.

    Returns:
        changed, storage_cleared
    """
    target = str(worker_id or "auto")
    changed = str(item.get("worker_id", "auto") or "auto") != target
    storage_cleared = False

    storage_id = str(item.get("storage_id", "")).strip()
    if storage_id:
        storage = storage_by_id(storage_id)
        storage_worker = (
            str(storage.get("worker_id", ""))
            if storage
            else ""
        )
        if target == "auto" or storage_worker != target:
            item["storage_id"] = ""
            storage_cleared = True
            changed = True

    if changed:
        item["worker_id"] = target
        item["modified"] = time.strftime("%Y-%m-%d %H:%M:%S")

    return changed, storage_cleared


def assign_worker_to_source_group(
    worker_id: str,
    *,
    scope: str = "",
    source_id: str = "",
) -> tuple[int, int, int, int]:
    """Assign a worker without changing enabled/archive state.

    Returns:
        changed, skipped_active, storage_cleared, matched
    """
    data = sources()
    changed = 0
    skipped_active = 0
    storage_cleared = 0
    matched = 0

    for item in data:
        item_id = str(item.get("id", ""))
        selected = (
            item_id == source_id
            if source_id
            else source_matches_worker_scope(item, scope)
        )
        if not selected:
            continue

        matched += 1
        if item_id and active_job_for_source(item_id):
            skipped_active += 1
            continue

        item_changed, cleared = apply_worker_assignment_to_source(
            item,
            worker_id,
        )
        if item_changed:
            changed += 1
        if cleared:
            storage_cleared += 1

    if changed:
        save_json(SOURCES_FILE, data)

    return changed, skipped_active, storage_cleared, matched


def requested_worker_assignment() -> tuple[str | None, str]:
    worker_id = str(
        request.form.get("worker_id", "auto")
        or "auto"
    ).strip()
    if worker_id == "auto":
        return "auto", "Automatic"

    worker = worker_by_id(worker_id)
    if not worker:
        return None, ""

    label = str(
        worker.get("display_name")
        or worker.get("name")
        or worker_id
    )
    return worker_id, label


@app.post("/sources/set-worker-bulk")
def set_worker_for_source_group():
    worker_id, worker_label = requested_worker_assignment()
    if worker_id is None:
        flash(
            "That worker is no longer registered. Start it or choose "
            "another worker.",
            "bad",
        )
        return action_redirect("/sources/manage")

    scope = str(request.form.get("scope", "imported")).strip()
    allowed_scopes = {
        "imported",
        "disabled",
        "automatic",
        "all_non_archived",
    }
    if scope not in allowed_scopes:
        flash("Choose a valid source group.", "bad")
        return action_redirect("/sources/manage")

    changed, active, cleared, matched = (
        assign_worker_to_source_group(
            worker_id,
            scope=scope,
        )
    )

    if not matched:
        flash(
            "No sources matched that group. Imported sources are detected "
            "by their import marker or the (Imported) name suffix.",
            "bad",
        )
        return action_redirect("/sources/manage")

    parts = [
        f'Set worker to "{worker_label}" for {changed} source(s).'
    ]
    if active:
        parts.append(
            f"{active} active source(s) were skipped. Stop them and "
            "apply the worker again."
        )
    if cleared:
        parts.append(
            f"Cleared incompatible recording storage from {cleared} "
            "source(s)."
        )
    if not changed and not active:
        parts.append("Every matched source already used that worker.")

    flash(" ".join(parts), "bad" if active else "good")
    return action_redirect("/sources/manage")


@app.post("/sources/<source_id>/set-worker")
def set_worker_for_one_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        flash("Source not found.", "bad")
        return action_redirect("/sources/manage")

    worker_id, worker_label = requested_worker_assignment()
    if worker_id is None:
        flash(
            "That worker is no longer registered. Start it or choose "
            "another worker.",
            "bad",
        )
        return action_redirect("/sources/manage")

    changed, active, cleared, _matched = (
        assign_worker_to_source_group(
            worker_id,
            source_id=source_id,
        )
    )
    if active:
        flash(
            f'Source "{source.get("name", "Source")}" is active. Stop it '
            "before changing its worker.",
            "bad",
        )
        return action_redirect("/sources/manage")

    message = (
        f'Source "{source.get("name", "Source")}" now uses '
        f'"{worker_label}".'
        if changed
        else (
            f'Source "{source.get("name", "Source")}" already uses '
            f'"{worker_label}".'
        )
    )
    if cleared:
        message += (
            " Its saved recording storage belonged to another worker "
            "and was cleared."
        )

    flash(message, "good")
    return action_redirect("/sources/manage")


def set_all_sources_enabled(
    enabled: bool,
) -> tuple[int, int, int]:
    """Bulk-change source enabled state without disrupting active jobs.

    Returns:
        changed_count, skipped_active_count, skipped_archived_count
    """
    data = sources()
    changed = 0
    skipped_active = 0
    skipped_archived = 0
    modified = time.strftime("%Y-%m-%d %H:%M:%S")

    for item in data:
        source_id = str(item.get("id", ""))

        # Enabling must never silently restore archived sources.
        if enabled and bool(item.get("archived", False)):
            skipped_archived += 1
            continue

        # Match the existing individual Disable safety behaviour.
        if (
            not enabled
            and bool(item.get("enabled", True))
            and source_id
            and active_job_for_source(source_id)
        ):
            skipped_active += 1
            continue

        current = bool(item.get("enabled", True))
        state_changed = current != enabled
        arm_changed = bool(item.get("auto_start_armed", False)) and not enabled
        if state_changed or arm_changed:
            item["enabled"] = enabled
            if not enabled:
                item["auto_start_armed"] = False
            item["modified"] = modified
            changed += 1

    if changed:
        save_json(SOURCES_FILE, data)

    return changed, skipped_active, skipped_archived


@app.post("/sources/enable-all")
def enable_all_sources():
    changed, _active, archived = set_all_sources_enabled(True)

    parts = [f"Enabled {changed} source(s)."]
    if archived:
        parts.append(
            f"{archived} archived source(s) remained archived."
        )
    if not changed and not archived:
        parts.append("Every source was already enabled.")

    flash(" ".join(parts), "good")
    return action_redirect("/sources/manage")


@app.post("/sources/disable-all")
def disable_all_sources():
    changed, active, _archived = set_all_sources_enabled(False)

    parts = [f"Disabled {changed} source(s)."]
    category = "good"
    if active:
        parts.append(
            f"{active} active source(s) were safely left enabled. "
            "Stop them, then press Disable All again."
        )
        category = "bad"
    elif not changed:
        parts.append("Every source was already disabled.")

    flash(" ".join(parts), category)
    return action_redirect("/sources/manage")


@app.post("/sources/<source_id>/toggle-enabled")
def toggle_source_enabled(source_id: str):
    source = source_by_id(source_id)
    if not source:
        flash("Source not found.", "bad")
        return action_redirect("/sources/manage")
    if source.get("enabled", True) and active_job_for_source(source_id):
        flash("Stop this source before disabling it.", "bad")
        return action_redirect("/sources/manage")
    next_enabled = not bool(source.get("enabled", True))
    updated = update_source_fields(
        source_id,
        enabled=next_enabled,
        auto_start_armed=(
            bool(source.get("auto_start_armed", False))
            if next_enabled
            else False
        ),
    )
    flash(
        f'Source "{source.get("name", "Source")}" is now '
        + ("enabled." if updated and updated.get("enabled") else "disabled."),
        "good",
    )
    return action_redirect("/sources/manage")


@app.post("/sources/<source_id>/toggle-favourite")
def toggle_source_favourite(source_id: str):
    source = source_by_id(source_id)
    if not source:
        flash("Source not found.", "bad")
        return action_redirect("/sources/manage")
    update_source_fields(
        source_id,
        favourite=not bool(source.get("favourite", False)),
    )
    flash("Favourite setting updated.", "good")
    return action_redirect("/sources/manage")


@app.post("/sources/<source_id>/archive")
def archive_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        flash("Source not found.", "bad")
        return action_redirect("/sources/manage")
    if active_job_for_source(source_id):
        flash("Stop this source before archiving it.", "bad")
        return action_redirect("/sources/manage")
    update_source_fields(source_id, archived=True, enabled=False, auto_start_armed=False)
    flash(f'Source "{source.get("name", "Source")}" archived. Its settings were kept.', "good")
    return action_redirect("/sources/manage")


@app.post("/sources/<source_id>/restore")
def restore_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        flash("Source not found.", "bad")
        return action_redirect("/sources/manage")
    update_source_fields(source_id, archived=False, enabled=True)
    flash(f'Source "{source.get("name", "Source")}" restored and enabled.', "good")
    return action_redirect("/sources/manage")


@app.post("/sources/<source_id>/duplicate")
def duplicate_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        flash("Source not found.", "bad")
        return action_redirect("/sources/manage")
    copy = json.loads(json.dumps(source))
    copy["id"] = uuid.uuid4().hex[:12]
    copy["name"] = str(source.get("name", "Source")) + " (Copy)"
    copy["archived"] = False
    copy["enabled"] = False
    copy["auto_start_armed"] = False
    copy["favourite"] = False
    copy["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data = sources()
    data.append(copy)
    save_json(SOURCES_FILE, data)
    flash("Source duplicated as disabled. Edit and test it before enabling.", "good")
    return action_redirect("/sources/manage")


@app.get("/sources/<source_id>/export")
def export_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    export = {
        "vic_source_format": 1,
        "exported_by": "VIC v0.7.0",
        "source": source,
    }
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(source.get("name", "source"))).strip('._') or "source"
    payload = io.BytesIO(json.dumps(export, indent=2).encode("utf-8"))
    return send_file(
        payload,
        as_attachment=True,
        download_name=safe + ".vicsource.json",
        mimetype="application/json",
    )


@app.get("/sources/export-all")
def export_all_sources():
    items = sources()
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = io.BytesIO()

    used_names: set[str] = set()
    manifest_sources: list[dict[str, Any]] = []

    with zipfile.ZipFile(
        payload,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for index, source in enumerate(items, start=1):
            source_name = str(source.get("name", "source"))
            safe = re.sub(
                r"[^A-Za-z0-9_.-]+",
                "_",
                source_name,
            ).strip("._") or f"source_{index}"

            candidate = safe + ".vicsource.json"
            if candidate.casefold() in used_names:
                candidate = (
                    safe
                    + "_"
                    + str(source.get("id", index))[:8]
                    + ".vicsource.json"
                )
            used_names.add(candidate.casefold())

            exported = {
                "vic_source_format": 1,
                "exported_by": "VIC v0.7.0",
                "exported_at": generated_at,
                "source": source,
            }
            archive.writestr(
                candidate,
                json.dumps(exported, indent=2),
            )
            manifest_sources.append(
                {
                    "name": source_name,
                    "id": source.get("id", ""),
                    "type": source.get("type", ""),
                    "file": candidate,
                }
            )

        manifest = {
            "vic_source_library_format": 1,
            "exported_by": "VIC v0.7.0",
            "exported_at": generated_at,
            "source_count": len(items),
            "sources": manifest_sources,
        }
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2),
        )
        archive.writestr(
            "README.txt",
            (
                "VIC Source Library export\n"
                "=========================\n\n"
                "Import this whole ZIP directly by opening Source Library and pressing "
                "Import Source / ZIP. You can also import one individual "
                ".vicsource.json file from inside it.\n"
                "Imported sources receive new IDs and start disabled so their "
                "worker and device settings can be checked safely.\n"
            ),
        )

    payload.seek(0)
    filename = (
        "VIC_Source_Library_"
        + time.strftime("%Y-%m-%d_%H-%M-%S")
        + ".zip"
    )
    return send_file(
        payload,
        as_attachment=True,
        download_name=filename,
        mimetype="application/zip",
    )


@app.get("/sources/import")
def import_source_form():
    body = """
<div class='card'>
<h2>Import portable source or Source Library ZIP</h2>
<p class='muted'>Choose any of these:</p>
<ul>
<li>one <code>.vicsource.json</code> exported by VIC</li>
<li>the complete <code>VIC_Source_Library_....zip</code> downloaded by Export All Sources ZIP</li>
<li>an older raw source JSON or <code>sources.json</code> list</li>
<li>a VIC config/backup ZIP containing <code>config/sources.json</code></li>
</ul>
<p class='small'>Do not extract and select <code>manifest.json</code> by itself. Select the original ZIP directly. Imported sources receive new IDs and start disabled so you can safely check their worker and device choices.</p>
<form method='post' enctype='multipart/form-data'>
<input type='file' name='source_file' accept='.zip,.json,.vicsource.json,application/zip,application/json' required>
<div class='inline'>
<button>Import source file or ZIP</button>
<a class='btn' href='/sources/manage'>Cancel</a>
</div>
</form>
</div>
"""
    return render_template_string(page("Import Source / ZIP", body))


@app.post("/sources/import")
def import_source_file():
    upload = request.files.get("source_file")
    if not upload:
        flash("Choose a source JSON file or VIC Source Library ZIP.", "bad")
        return redirect("/sources/import")

    filename = str(getattr(upload, "filename", "") or "source-import")
    try:
        payload = upload.read()
        candidates, format_label = decode_source_import(
            filename,
            payload,
        )

        imported_items: list[dict[str, Any]] = []
        skipped: list[str] = []
        for index, candidate in enumerate(candidates, start=1):
            try:
                imported_items.append(
                    prepare_imported_source(candidate)
                )
            except Exception as exc:
                candidate_name = str(
                    candidate.get("name")
                    or candidate.get("id")
                    or f"item {index}"
                )
                skipped.append(f"{candidate_name}: {exc}")

        if not imported_items:
            if skipped:
                raise SourceImportError(skipped[0])
            raise SourceImportError(
                "No usable sources were found in the selected file."
            )

        data = sources()
        data.extend(imported_items)
        save_json(SOURCES_FILE, data)
    except Exception as exc:
        flash(f"Could not import that source: {exc}", "bad")
        return redirect("/sources/import")

    message = (
        f"Imported {len(imported_items)} source(s) from {format_label}. "
        "They have new IDs and are disabled. In Source Library use "
        "Set Worker for Imported Sources, then Test and enable them."
    )
    if skipped:
        message += (
            f" Skipped {len(skipped)} invalid item(s). First issue: "
            + skipped[0]
        )
    flash(message, "good")
    return redirect("/sources/manage")


PREVIEW_SOURCE_TYPES = {
    "media_file",
    "screen",
    "camera",
    "audio_device",
    "speaker_output",
    "rtsp",
    "network",
}

HEALTH_FIELDS = {
    "requested_fps",
    "actual_fps",
    "dropped_frames",
    "duplicated_frames",
    "bitrate_mbps",
    "file_size_bytes",
    "duration_seconds",
    "disk_per_hour_gb",
    "disk_write_bytes_per_second",
    "encoder",
    "speed",
    "frame_count",
    "health_updated_ts",
}


AUTO_START_SOURCE_TYPES = {"rtsp", "network", "website"}


def source_supports_preview(source: dict[str, Any]) -> bool:
    return str(source.get("type", "")) in PREVIEW_SOURCE_TYPES


def source_supports_auto_start(source: dict[str, Any]) -> bool:
    kind = str(source.get("type", ""))
    if kind not in AUTO_START_SOURCE_TYPES:
        return False
    if kind == "website":
        return str(source.get("options", {}).get("website_mode", "single")) != "playlist"
    return True


def mark_job_stopping(item: dict[str, Any], message: str) -> None:
    item["desired_state"] = "stopped"
    item["state"] = "stopping"
    item["message"] = message
    item["updated_ts"] = time.time()


def release_waiting_jobs(data: list[dict[str, Any]]) -> bool:
    changed = False
    by_id = {str(item.get("id", "")): item for item in data}
    for item in data:
        if item.get("state") != "waiting":
            continue
        dependency = by_id.get(str(item.get("wait_for_job_id", "")))
        if dependency and job_is_active(dependency):
            continue
        item["state"] = "pending"
        item["message"] = "Previous Preview stopped; waiting for worker to start recording"
        item["updated_ts"] = time.time()
        changed = True
    return changed


def create_job(
    source: dict[str, Any],
    mode: str,
    *,
    allow_active: bool = False,
    wait_for_job_id: str = "",
    initial_state: str = "",
    initial_message: str = "",
    armed_auto_start: bool = False,
) -> tuple[bool, str]:
    source = source_with_defaults(source)
    if source.get("archived"):
        return False, "This source is archived. Restore it in Source Library first."
    if not source.get("enabled", True):
        return False, "This source is disabled. Enable it before using it."
    storage = storage_by_id(str(source.get("storage_id", "")))
    if storage:
        if not storage.get("enabled", True):
            return False, "The selected storage location is disabled."
        selected = worker_by_id(str(storage.get("worker_id", "")))
        if not selected or not selected.get("online"):
            return False, "The worker that owns the selected storage location is offline."
        source["recording_storage_path"] = str(storage.get("path", "")).strip()
        source["recording_storage_name"] = str(storage.get("name", "Storage"))
        source["worker_id"] = str(storage.get("worker_id", ""))
    else:
        selected = choose_worker(source.get("worker_id", "auto"))
        if not selected:
            return False, "No suitable online worker is available. Start VIC and wait for Local PC to appear online."
    current = active_job_for_source(source["id"])
    if current and not allow_active:
        return False, f'An active job already exists: {current.get("state", "unknown")}'
    now = time.time()
    item = {
        "id": uuid.uuid4().hex,
        "source_id": source["id"],
        "source_name": source["name"],
        "worker_id": selected["id"],
        "worker_name": selected.get("display_name", selected.get("name", "Worker")),
        "source": source,
        "mode": mode,
        "preview_unlimited": mode == "preview",
        "desired_state": "running",
        "state": initial_state or ("waiting" if wait_for_job_id else "pending"),
        "message": initial_message or (
            "Waiting for Preview to stop before recording"
            if wait_for_job_id
            else "Waiting for worker"
        ),
        "wait_for_job_id": wait_for_job_id,
        "armed_auto_start": bool(armed_auto_start),
        "availability_attempts": 0,
        "ever_running": False,
        "output": "",
        "audio_level_db": None,
        "preview_available": False,
        "requested_fps": None,
        "actual_fps": None,
        "dropped_frames": 0,
        "duplicated_frames": 0,
        "bitrate_mbps": None,
        "file_size_bytes": 0,
        "duration_seconds": 0,
        "disk_per_hour_gb": None,
        "disk_write_bytes_per_second": 0.0,
        "encoder": "",
        "speed": "",
        "frame_count": 0,
        "created_ts": now,
        "updated_ts": now,
    }
    data = jobs()
    data.append(item)
    save_json(JOBS_FILE, data)
    return True, f'{mode.title()} job sent to {item["worker_name"]}.'


def queue_all_sources(mode: str) -> tuple[int, list[str]]:
    configured = [item for item in sources() if item.get("enabled", True) and not item.get("archived")]
    queued = 0
    failures: list[str] = []

    for source in configured:
        ok, message = create_job(source, mode)
        if ok:
            queued += 1
        else:
            failures.append(f'{source.get("name", "Source")}: {message}')

    return queued, failures


@app.post("/sources/test-all")
def test_all_sources():
    configured = [item for item in sources() if item.get("enabled", True) and not item.get("archived")]
    if not configured:
        flash("There are no enabled sources to test.", "bad")
        return action_redirect("/")

    queued, failures = queue_all_sources("test")
    message = f"Queued tests for {queued} of {len(configured)} source(s)."
    if failures:
        message += "\nSkipped or failed:\n" + "\n".join(failures[:20])
    flash(message, "good" if queued else "bad")
    return action_redirect("/")


@app.post("/sources/preview-all")
def preview_all_sources():
    configured = [item for item in sources() if item.get("enabled", True) and not item.get("archived") and source_supports_preview(item)]
    if not configured:
        flash("There are no preview-capable sources configured.", "bad")
        return action_redirect("/")
    queued = 0
    failures: list[str] = []
    for source in configured:
        ok, message = create_job(source, "preview")
        if ok:
            queued += 1
        else:
            failures.append(f'{source.get("name", "Source")}: {message}')
    message = f"Queued continuous Previews for {queued} source(s). Nothing is saved until Start is pressed."
    if failures:
        message += "\nSkipped:\n" + "\n".join(failures[:20])
    flash(message, "good" if queued else "bad")
    return action_redirect("/")


@app.post("/sources/start-all")
def start_all_sources():
    configured = [item for item in sources() if item.get("enabled", True) and not item.get("archived")]
    if not configured:
        flash("There are no enabled sources to start.", "bad")
        return action_redirect("/")

    queued = 0
    failures: list[str] = []
    data = jobs()
    data_changed = False
    for source in configured:
        current = active_job_for_source(source["id"])
        if current and current.get("mode") == "preview":
            for item in data:
                if item.get("id") == current.get("id"):
                    mark_job_stopping(item, "Switching Preview to recording")
                    data_changed = True
            ok, message = create_job(
                source,
                "record",
                allow_active=True,
                wait_for_job_id=str(current.get("id", "")),
            )
        else:
            ok, message = create_job(source, "record")
        if ok:
            queued += 1
        else:
            failures.append(f'{source.get("name", "Source")}: {message}')
    if data_changed:
        current_jobs = jobs()
        stopping = {str(item.get("id")): item for item in data}
        for item in current_jobs:
            replacement = stopping.get(str(item.get("id")))
            if replacement:
                item.update(replacement)
        save_json(JOBS_FILE, current_jobs)
    message = f"Queued recordings for {queued} of {len(configured)} source(s). Active Previews will stop and change to recording automatically."
    if failures:
        message += "\nSkipped or failed:\n" + "\n".join(failures[:20])
    flash(message, "good" if queued else "bad")
    return action_redirect("/")


@app.post("/sources/stop-all")
def stop_all_sources():
    data = jobs()
    stopped = 0
    now = time.time()

    for item in data:
        if job_is_active(item):
            item["desired_state"] = "stopped"
            item["state"] = "stopping"
            item["message"] = "Stop All requested"
            item["updated_ts"] = now
            stopped += 1

    if stopped:
        save_json(JOBS_FILE, data)
        flash(f"Stop command sent for {stopped} active job(s).", "good")
    else:
        flash("There are no active jobs to stop.", "bad")
    return action_redirect("/")


def set_source_auto_start_armed(
    source_id: str,
    armed: bool,
) -> dict[str, Any] | None:
    return update_source_fields(
        source_id,
        auto_start_armed=bool(armed),
    )


def queue_armed_auto_start(
    source: dict[str, Any],
) -> tuple[bool, str]:
    source = source_with_defaults(source)
    if not source_supports_auto_start(source):
        return False, (
            "Arm Auto Start is available for RTSP, direct network streams "
            "and single/upcoming website streams."
        )
    current = active_job_for_source(str(source.get("id", "")))
    if current:
        if current.get("armed_auto_start"):
            return True, "Auto Start is already armed for this source."
        return False, "Stop the current Test, Preview or recording before arming Auto Start."

    poll_seconds = max(
        2,
        min(300, int(source.get("auto_start_poll_seconds", 10) or 10)),
    )
    return create_job(
        source,
        "wait_and_record",
        initial_state="waiting_available",
        initial_message=(
            "AUTO START ARMED — waiting for real media. "
            f"Checking every {poll_seconds} seconds."
        ),
        armed_auto_start=True,
    )


def ensure_armed_auto_start_jobs() -> int:
    """Queue armed sources when a suitable worker becomes available."""
    queued = 0
    now = time.time()
    for source in sources():
        source = source_with_defaults(source)
        if not source.get("auto_start_armed"):
            continue
        if source.get("archived") or not source.get("enabled", True):
            continue
        if not source_supports_auto_start(source):
            continue
        if active_job_for_source(str(source.get("id", ""))):
            continue
        latest = latest_job_for_source(str(source.get("id", "")))
        retry_after = max(
            5,
            min(300, int(source.get("auto_start_poll_seconds", 10) or 10)),
        )
        if (
            latest
            and latest.get("armed_auto_start")
            and now - float(latest.get("updated_ts", 0) or 0) < retry_after
        ):
            continue
        ok, _message = queue_armed_auto_start(source)
        if ok:
            queued += 1
    return queued


@app.post("/sources/<source_id>/auto-start")
def toggle_source_auto_start(source_id: str):
    source = source_by_id(source_id)
    if not source:
        flash("Source not found.", "bad")
        return action_redirect("/live/all")
    source = source_with_defaults(source)

    if source.get("auto_start_armed"):
        set_source_auto_start_armed(source_id, False)
        data = jobs()
        stopped = 0
        for item in data:
            if (
                item.get("source_id") == source_id
                and item.get("armed_auto_start")
                and job_is_active(item)
            ):
                mark_job_stopping(item, "Auto Start disarmed by user")
                stopped += 1
        if stopped:
            save_json(JOBS_FILE, data)
        flash(
            f'Auto Start disarmed for "{source.get("name", "Source")}".',
            "good",
        )
        return action_redirect("/live/all")

    if source.get("archived"):
        flash("Restore this source before arming Auto Start.", "bad")
        return action_redirect("/live/all")
    if not source.get("enabled", True):
        flash("Enable this source before arming Auto Start.", "bad")
        return action_redirect("/live/all")
    if not source_supports_auto_start(source):
        flash(
            "Arm Auto Start supports RTSP, direct network streams and "
            "single/upcoming website streams.",
            "bad",
        )
        return action_redirect("/live/all")

    set_source_auto_start_armed(source_id, True)
    refreshed = source_by_id(source_id) or source
    ok, message = queue_armed_auto_start(refreshed)
    if ok:
        message = (
            f'Auto Start armed for "{source.get("name", "Source")}". '
            "VIC will wait without creating an empty recording and start "
            "as soon as real media is detected."
        )
        category = "good"
    elif "No suitable online worker" in message:
        message = (
            f'Auto Start armed for "{source.get("name", "Source")}". '
            "It is waiting for a worker to come online, then availability "
            "checks will begin automatically."
        )
        category = "good"
    else:
        set_source_auto_start_armed(source_id, False)
        category = "bad"
    flash(message, category)
    return action_redirect("/live/all")


@app.post("/sources/<source_id>/preview")
def preview_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    if not source_supports_preview(source):
        flash("Continuous Preview is not available for this source type. Use Test instead.", "bad")
        return action_redirect("/")
    ok, message = create_job(source, "preview")
    if ok:
        message = "Preview started with no time limit. It saves no recording file and stays active until Stop or Start Recording is pressed."
    flash(message, "good" if ok else "bad")
    return action_redirect("/")


@app.post("/sources/<source_id>/test")
def test_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    ok, message = create_job(source, "test")
    flash(message, "good" if ok else "bad")
    return action_redirect("/")


@app.post("/sources/<source_id>/start")
def start_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    current = active_job_for_source(source_id)
    if current and current.get("mode") == "preview":
        data = jobs()
        for item in data:
            if item.get("id") == current.get("id"):
                mark_job_stopping(item, "Start pressed — changing Preview into recording")
        save_json(JOBS_FILE, data)
        ok, message = create_job(
            source,
            "record",
            allow_active=True,
            wait_for_job_id=str(current.get("id", "")),
        )
        if ok:
            message = "Preview is stopping. Recording will start automatically as soon as the capture device is released."
    else:
        ok, message = create_job(source, "record")
    flash(message, "good" if ok else "bad")
    return action_redirect("/")


@app.post("/sources/<source_id>/rollover-storage")
def rollover_source_storage(source_id: str):
    source = source_by_id(source_id)
    target_id = request.form.get("storage_id", "").strip()
    target = storage_by_id(target_id)
    current = active_job_for_source(source_id)
    if not source or not current or current.get("mode") != "record":
        flash("An active recording is required before rolling to another storage location.", "bad")
        return action_redirect("/live/all")
    if not target or not target.get("enabled", True):
        flash("Choose an enabled storage location.", "bad")
        return action_redirect("/live/all")
    if str(target.get("worker_id", "")) != str(current.get("worker_id", "")):
        flash("For a live rollover, the target storage must be attached to the same worker that is capturing this source.", "bad")
        return action_redirect("/live/all")

    update_source_fields(source_id, storage_id=target_id)
    refreshed = source_by_id(source_id) or source
    data = jobs()
    for item in data:
        if item.get("id") == current.get("id"):
            item["finish_capture"] = str(source.get("type", "")) == "website"
            item["graceful_finish"] = True
            item["rollover_storage_id"] = target_id
            item["rollover_move_closed"] = True
            item["rollover_move_queued"] = False
            mark_job_stopping(item, f'Rolling recording to {target.get("name", "storage")}')
    save_json(JOBS_FILE, data)
    ok, message = create_job(
        refreshed,
        "record",
        allow_active=True,
        wait_for_job_id=str(current.get("id", "")),
    )
    if ok:
        message = (
            f'Closing the current segment safely, then recording will continue on "{target.get("name", "Storage")}". '
            "The closed segment will be verified and moved to free the original drive. The source website/OBS broadcast is not stopped."
        )
    flash(message, "good" if ok else "bad")
    return action_redirect("/live/all")


@app.post("/sources/<source_id>/finish-capture")
def finish_website_capture(source_id: str):
    source = source_by_id(source_id)
    if not source or str(source.get("type", "")) != "website":
        flash("Finish Capture is only available for website recordings.", "bad")
        return action_redirect("/live/all")
    data = jobs()
    candidates = [
        item
        for item in data
        if item.get("source_id") == source_id
        and item.get("mode") == "record"
        and job_is_active(item)
    ]
    if not candidates:
        flash("No active website recording was found.", "bad")
        return action_redirect("/live/all")
    for item in candidates:
        item["finish_capture"] = True
        mark_job_stopping(
            item,
            "Finish Capture requested — closing VIC recording without stopping the website broadcast",
        )
    save_json(JOBS_FILE, data)
    flash(
        "Finish Capture sent. VIC will close/remux the current .part file; the website or OBS broadcast remains live.",
        "good",
    )
    return action_redirect("/live/all")


@app.post("/sources/<source_id>/stop")
def stop_source(source_id: str):
    source = source_by_id(source_id)
    if source and source.get("auto_start_armed"):
        set_source_auto_start_armed(source_id, False)
    data = jobs()
    candidates = [
        item for item in data
        if item.get("source_id") == source_id and job_is_active(item)
    ]
    if not candidates:
        flash("No active job for this source.", "bad")
        return action_redirect("/")
    for item in candidates:
        mark_job_stopping(item, "Stop requested")
    save_json(JOBS_FILE, data)
    flash(f"Stop command sent for {len(candidates)} active job(s) on this source.", "good")
    return action_redirect("/")


@app.post("/sources/<source_id>/clear-history")
def clear_source_history(source_id: str):
    source = source_by_id(source_id)
    if not source:
        flash("That source no longer exists.", "bad")
        return action_redirect("/")

    related_ids = {
        str(item.get("id", ""))
        for item in jobs()
        if item.get("source_id") == source_id
    }
    removed = remove_job_history(related_ids)
    active_kept = 1 if active_job_for_source(source_id) else 0
    message = (
        f'Cleared {removed} inactive history item(s) for '
        f'"{source.get("name", "Source")}".'
    )
    if active_kept:
        message += " The active job was kept."
    message += " Saved recording files were kept."
    flash(message, "good")
    return action_redirect("/")


@app.post("/sources/<source_id>/delete")
def delete_source(source_id: str):
    if active_job_for_source(source_id):
        flash("Stop the active job before deleting this source.", "bad")
        return action_redirect("/")

    existing = source_by_id(source_id)
    if not existing:
        flash("That source has already been deleted.", "bad")
        return action_redirect("/")

    save_json(
        SOURCES_FILE,
        [
            item
            for item in sources()
            if item.get("id") != source_id
        ],
    )
    related_ids = {
        str(item.get("id", ""))
        for item in jobs()
        if item.get("source_id") == source_id
    }
    removed = remove_job_history(related_ids)
    flash(
        f'Source "{existing.get("name", "Source")}" deleted. '
        f"Removed {removed} inactive Live/job item(s). "
        "Saved recording files were kept.",
        "good",
    )
    return action_redirect("/")


@app.post("/sources/delete-all")
def delete_all_sources():
    active = [item for item in jobs() if job_is_active(item)]
    if active:
        flash(
            f"Stop all active jobs first. {len(active)} active job(s) remain.",
            "bad",
        )
        return action_redirect("/")

    source_count = len(sources())
    all_job_ids = {
        str(item.get("id", ""))
        for item in jobs()
        if item.get("id")
    }
    history_count = remove_job_history(all_job_ids)
    save_json(SOURCES_FILE, [])
    flash(
        f"Deleted {source_count} source(s) and "
        f"{history_count} history item(s). "
        "Saved recording files were kept.",
        "good",
    )
    return action_redirect("/")


@app.post("/jobs/<job_id>/delete")
def delete_job_history(job_id: str):
    item = job_by_id(job_id)
    if not item:
        flash("That history item has already been removed.", "bad")
        return action_redirect("/jobs")
    if job_is_active(item):
        flash("Stop this job before deleting its history.", "bad")
        return action_redirect("/jobs")

    removed = remove_job_history({job_id})
    flash(
        "History item and cached preview deleted. "
        "Saved recording files were kept."
        if removed
        else "Nothing was removed.",
        "good" if removed else "bad",
    )
    return action_redirect("/jobs")


@app.post("/jobs/clear-inactive")
def clear_inactive_jobs():
    ids = {
        str(item.get("id", ""))
        for item in jobs()
        if not job_is_active(item)
    }
    removed = remove_job_history(ids)
    flash(
        f"Cleared {removed} finished, stopped or failed history item(s). "
        "Saved recording files were kept.",
        "good",
    )
    return action_redirect("/jobs")


@app.post("/jobs/clear-orphaned")
def clear_orphaned_jobs():
    valid_sources = configured_source_ids()
    ids = {
        str(item.get("id", ""))
        for item in jobs()
        if not job_is_active(item)
        and str(item.get("source_id", "")) not in valid_sources
    }
    removed = remove_job_history(ids)
    flash(
        f"Cleared {removed} old item(s) belonging to deleted sources.",
        "good",
    )
    return action_redirect("/live/all")


@app.post("/jobs/delete-all")
def delete_all_job_history():
    active = [item for item in jobs() if job_is_active(item)]
    if active:
        flash(
            f"Stop all active jobs first. {len(active)} active job(s) remain.",
            "bad",
        )
        return action_redirect("/jobs")

    ids = {
        str(item.get("id", ""))
        for item in jobs()
        if item.get("id")
    }
    removed = remove_job_history(ids)
    flash(
        f"Deleted all {removed} dashboard job/history item(s). "
        "Saved recording files were kept.",
        "good",
    )
    return action_redirect("/jobs")


@app.get("/mass-capture")
def mass_capture_page():
    online_workers = [item for item in workers() if item.get("online")]
    body = """<div class='card'><h2>Capture Everything</h2><p class='muted'>Load one worker's inventory, review the checklist, then create one independent source for every checked device.</p>
<form method='post' action='/mass-capture' id='massForm'><label>Worker</label><select id='massWorker' name='worker_id'><option value=''>Choose a worker</option>{% for worker in workers %}<option value='{{worker.id}}'>{{worker.display_name}} — {{worker.host}}</option>{% endfor %}</select>
<div class='inline'><button type='button' onclick='loadMassDevices()'>Load all devices</button><button type='button' onclick='setAll(true)'>Select all</button><button type='button' onclick='setAll(false)'>Clear all</button></div>
<p id='massSummary' class='muted'>Nothing loaded yet.</p><div id='massDevices'></div>
<div class='card'><h3>What should VIC do?</h3><label><input style='width:auto' type='radio' name='action' value='create' checked> Create selected sources only</label><br><label><input style='width:auto' type='radio' name='action' value='start'> Create and immediately start selected sources</label><p class='warn'>Starting everything can use substantial CPU, USB bandwidth and disk space.</p><button>Create selected sources</button></div></form></div>"""
    script = """<script>
function esc(s){return String(s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#039;'}[c]));}
function section(title,kind,items,labelFn){if(!items.length)return `<div class='card'><h3>${title}</h3><p class='muted'>None reported.</p></div>`;return `<div class='card'><h3>${title}</h3>`+items.map((x,i)=>{const id=typeof x==='string'?x:x.id;const label=labelFn(x);const value=kind+':'+btoa(unescape(encodeURIComponent(JSON.stringify({id:id,name:typeof x==='string'?x:(x.name||label),label:label}))));return `<label style='display:block;margin:8px 0'><input class='massCheck' style='width:auto' type='checkbox' name='device' value='${value}' checked> ${esc(label)}</label>`;}).join('')+'</div>';}
async function loadMassDevices(){const id=document.getElementById('massWorker').value;if(!id){alert('Choose a worker first.');return;}const r=await fetch('/api/workers/'+id+'/inventory');const d=await r.json();if(d.error){alert(d.error);return;}let html='';html+=section('Monitors','screen',d.screens||[],x=>x.label||x.name);html+=section('Microphones / audio inputs','mic',d.audio_inputs||d.audio||[],x=>x);html+=section('Speakers / headphones','speaker',d.speakers||[],x=>x.label||x.name);html+=section('Cameras / capture devices','camera',d.video||[],x=>x);document.getElementById('massDevices').innerHTML=html;document.querySelectorAll('.massCheck').forEach(x=>x.addEventListener('change',updateEstimate));updateEstimate();}
function updateEstimate(){const selected=[...document.querySelectorAll('.massCheck:checked')];const counts={screen:0,mic:0,speaker:0,camera:0};selected.forEach(x=>{const k=x.value.split(':',1)[0];if(k in counts)counts[k]++;});const disk=(counts.screen*4+counts.camera*3+counts.mic*.7+counts.speaker*.7).toFixed(1);const cpu=counts.screen*15+counts.camera*10+counts.mic+counts.speaker;document.getElementById('massSummary').textContent='Selected '+selected.length+' source(s): '+counts.screen+' screen(s), '+counts.mic+' microphone(s), '+counts.speaker+' speaker output(s), '+counts.camera+' camera(s). Rough upper estimate: '+cpu+'% CPU load score and '+disk+' GB per hour. Actual use depends on resolution, frame rate and activity.';}
function setAll(value){document.querySelectorAll('.massCheck').forEach(x=>x.checked=value);updateEstimate();}
</script>"""
    return render_template_string(page("Capture Everything", body, script), workers=online_workers)


@app.post("/mass-capture")
def mass_capture_create():
    worker_id = request.form.get("worker_id", "").strip()
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("Choose an online worker and load its devices first.", "bad")
        return redirect("/mass-capture")
    raw_devices = request.form.getlist("device")
    if not raw_devices:
        flash("No devices were selected.", "bad")
        return redirect("/mass-capture")
    created: list[dict[str, Any]] = []
    data = sources()
    for raw in raw_devices:
        try:
            kind, encoded = raw.split(":", 1)
            payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
            device_id = str(payload.get("id", ""))
            device_name = str(payload.get("name", "") or payload.get("label", "") or device_id)
            label = str(payload.get("label", "") or device_name)
        except Exception:
            continue
        if kind == "screen":
            source_type = "screen"
            type_label = "Desktop, monitor or application window"
            options = {"target": "monitor", "screen_id": device_id, "window_title": "", "fps": 30, "audio_device": "", "offset_x": 0, "offset_y": 0, "width": 0, "height": 0}
            name = label
            summary = label
        elif kind == "mic":
            source_type = "audio_device"
            type_label = "Microphone or audio input"
            options = {"audio_device": device_name}
            name = device_name
            summary = device_name
        elif kind == "speaker":
            source_type = "speaker_output"
            type_label = "Speaker or headphone output"
            options = {"speaker_id": device_id, "speaker_name": device_name, "samplerate": 48000}
            name = device_name + " output"
            summary = label
        elif kind == "camera":
            source_type = "camera"
            type_label = "Camera / capture card / OBS"
            options = {"video_device": device_name, "audio_device": "", "resolution": "", "fps": 30}
            name = device_name
            summary = device_name
        else:
            continue
        source = {"id": uuid.uuid4().hex[:12], "name": name, "type": source_type, "type_label": type_label, "worker_id": worker_id, "options": options, "summary": summary, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        data.append(source)
        created.append(source)
    save_json(SOURCES_FILE, data)
    started = 0
    errors: list[str] = []
    if request.form.get("action") == "start":
        for source in created:
            ok, message = create_job(source, "record")
            if ok:
                started += 1
            else:
                errors.append(f'{source["name"]}: {message}')
    message = f"Created {len(created)} separate source(s)."
    if request.form.get("action") == "start":
        message += f" Started {started}."
    if errors:
        message += "\n" + "\n".join(errors[:10])
    flash(message, "good" if created else "bad")
    return redirect("/")


@app.get("/workers")
def workers_page():
    body = """<div class='card'><div class='live-status-row'><div><h2 style='margin:0'>Worker PCs</h2><p class='muted'>Local PC means the computer running the dashboard. Click any worker to view its monitors and devices.</p></div><form method='post' action='/workers/clear-offline' onsubmit='return confirm("Forget all offline worker entries? A worker will appear again automatically when START_WORKER.bat reconnects.")'><button class='control-stop'>Clear offline workers</button></form></div>
<div class='grid'>{% for worker in workers %}<div class='choice {{"worker-local" if worker.is_local_dashboard else ""}}'><a style='display:block;text-decoration:none' href='/workers/{{worker.id}}'><strong>{{worker.display_name}}</strong><span class='{{"good" if worker.online else "bad"}}'>{{"ONLINE" if worker.online else "OFFLINE"}}</span><p class='muted'>{{worker.host}}<br>VIC {{worker.worker_version or 'older/unknown'}}<br>CPU {{worker.cpu}}% · Memory {{worker.memory}}%<br>{{worker.disk_free_gb}} GB free<br>{{worker.recordings|length}} recording file(s)</p></a>{% if not worker.online %}<form method='post' action='/workers/{{worker.id}}/forget' onsubmit='return confirm("Forget this offline worker entry?")'><button class='control-stop'>Forget worker</button></form>{% endif %}</div>{% else %}<div class='card muted'>No workers have registered. Run START_VIC.bat.</div>{% endfor %}</div></div>"""
    return render_template_string(
        page("Workers", body),
        workers=workers(),
    )


@app.post("/workers/clear-offline")
def clear_offline_workers():
    data = workers()
    kept = [item for item in data if item.get("online")]
    removed = len(data) - len(kept)
    save_json(WORKERS_FILE, kept)
    flash(
        f"Forgot {removed} offline worker entr{'y' if removed == 1 else 'ies'}.",
        "good",
    )
    return redirect("/workers")


@app.post("/workers/<worker_id>/forget")
def forget_worker(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker:
        flash("That worker entry has already been removed.", "bad")
        return redirect("/workers")
    if worker.get("online"):
        flash("Stop that worker before forgetting it.", "bad")
        return redirect("/workers")
    save_json(
        WORKERS_FILE,
        [
            item
            for item in workers()
            if item.get("id") != worker_id
        ],
    )
    flash("Offline worker entry forgotten.", "good")
    return redirect("/workers")


@app.get("/workers/<worker_id>")
def worker_detail(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker:
        return "Worker not found", 404
    devices = worker.get("devices", {})
    body = """<div class='card'><h2>{{worker.display_name}}</h2><p class='{{"good" if worker.online else "bad"}}'>{{"ONLINE" if worker.online else "OFFLINE"}}</p><p><strong>Host:</strong> {{worker.host}}<br><strong>Platform:</strong> {{worker.platform}}<br><strong>VIC worker version:</strong> {{worker.worker_version or 'older/unknown'}}<br><strong>Automatic video encoder:</strong> <span class='{{"good" if "CPU" not in (worker.video_encoder or "") else "bad"}}'>{{worker.video_encoder or 'Not reported'}}</span><br><strong>CPU:</strong> {{worker.cpu}}%<br><strong>Memory:</strong> {{worker.memory}}%<br><strong>Free disk:</strong> {{worker.disk_free_gb}} GB<br><strong>Recordings:</strong> <code>{{worker.recordings_root or "Not reported"}}</code><br><strong>FFmpeg selection mode:</strong> {{worker.ffmpeg_selection_mode or "auto_compatible"}}<br><strong>Selected FFmpeg:</strong> <code>{{worker.ffmpeg or "Not found"}}</code></p><form method='post' action='/workers/{{worker.id}}/open-recordings'><button>Open recording folder on this PC</button></form></div>
<div class='grid'><div class='card'><h3>GPU encoder diagnosis</h3><p><strong>Automatic result:</strong><br>{{worker.video_encoder_details or "Not reported yet"}}</p><p><strong>Why this FFmpeg was selected:</strong><br>{{worker.ffmpeg_selection_details or "Not reported yet"}}</p><p><strong>Detected graphics devices:</strong></p><ul>{% for item in worker.gpu_devices or [] %}<li>{{item}}</li>{% else %}<li class='muted'>No GPU name was reported.</li>{% endfor %}</ul><p class='muted small'>Run <code>TEST_GPU_ENCODER.bat</code> on this worker for the complete FFmpeg error text.</p></div>
<div class='card'><h3>FFmpeg installations checked</h3><ul>{% for candidate in worker.ffmpeg_candidates or [] %}<li><code>{{candidate.path}}</code><br><span class='small'>{{candidate.origin}} · {{candidate.version}}<br>Working: {{candidate.working|join(", ") if candidate.working else "none"}} · Listed: {{candidate.listed|join(", ") if candidate.listed else "none"}}</span></li>{% else %}<li class='muted'>No candidate report yet. Restart the worker.</li>{% endfor %}</ul></div>
<div class='card'><h3>Video devices</h3><ul>{% for item in devices.video or [] %}<li>{{item}}</li>{% else %}<li class='muted'>None reported</li>{% endfor %}</ul></div><div class='card'><h3>Microphones / audio inputs</h3><ul>{% for item in devices.audio_inputs or devices.audio or [] %}<li>{{item}}</li>{% else %}<li class='muted'>None reported</li>{% endfor %}</ul></div><div class='card'><h3>Speakers / audio outputs</h3><ul>{% for item in devices.speakers or [] %}<li>{{item.label or item.name}}</li>{% else %}<li class='muted'>None reported</li>{% endfor %}</ul></div><div class='card'><h3>Monitors</h3><ul>{% for item in devices.screens or [] %}<li>{{item.label}}</li>{% else %}<li class='muted'>No monitor details</li>{% endfor %}</ul></div><div class='card'><h3>Open application windows</h3><ul>{% for item in devices.windows or [] %}<li><strong>{{item.title}}</strong>{% if item.process %} — {{item.process}}{% endif %}{% if item.pid %} (PID {{item.pid}}){% endif %}</li>{% else %}<li class='muted'>No visible titled windows reported.</li>{% endfor %}</ul></div><div class='card'><h3>Detected storage drives</h3>{% for drive in worker.drives or [] %}<div class='settings-note'><strong>{{drive.name or drive.path}}</strong><br><code>{{drive.path}}</code><br>{{drive.free_gb}} GB free / {{drive.total_gb}} GB · {{drive.percent}}% used<form method='post' action='/workers/{{worker.id}}/benchmark-path'><input type='hidden' name='path' value='{{drive.path}}'><button {{"disabled" if not worker.online else ""}}>Test read/write speed</button></form></div>{% else %}<p class='muted'>No drive inventory reported.</p>{% endfor %}<a class='btn' href='/storage'>Manage saved storage locations</a></div></div>"""
    return render_template_string(page("Worker details", body), worker=worker, devices=devices)


def create_worker_control_job(
    worker: dict[str, Any],
    mode: str,
    source_name: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    job = {
        "id": uuid.uuid4().hex,
        "source_id": f"system-{mode}-{uuid.uuid4().hex[:8]}",
        "source_name": source_name,
        "worker_id": worker["id"],
        "worker_name": worker.get(
            "display_name",
            worker.get("name", "Worker"),
        ),
        "source": {
            "id": "system",
            "name": "System",
            "type": "control",
            "options": options or {},
        },
        "mode": mode,
        "desired_state": "running",
        "state": "pending",
        "message": "Waiting for worker",
        "output": "",
        "created_ts": now,
        "updated_ts": now,
    }
    data = jobs()
    data.append(job)
    save_json(JOBS_FILE, data)
    return job




@app.post("/api/workers/<worker_id>/open-login")
def open_worker_login(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        return jsonify({"error": "That worker is offline."}), 404
    payload = request.get_json(silent=True) or {}
    browser = str(payload.get("browser_name", "edge")).strip().lower()
    profile = str(payload.get("browser_profile", "")).strip()
    allowed = {
        "brave", "chrome", "chromium", "edge", "firefox",
        "opera", "vivaldi", "whale",
    }
    if browser not in allowed:
        browser = "edge"
    job = create_worker_control_job(
        worker,
        "open_website_login",
        "Open YouTube login window",
        {
            "browser_name": browser,
            "browser_profile": profile,
            "url": (
                "https://accounts.google.com/ServiceLogin"
                "?service=youtube&continue=https://www.youtube.com/"
            ),
        },
    )
    return jsonify(
        {
            "ok": True,
            "job_id": job["id"],
            "worker_name": job["worker_name"],
        }
    )


@app.post("/workers/<worker_id>/open-recordings")
def open_recordings(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("That worker is not online.", "bad")
        return redirect("/recordings")
    job = create_worker_control_job(
        worker,
        "open_recordings",
        "Open recordings folder",
    )
    flash(f'Open-folder command sent to {job["worker_name"]}.', "good")
    return redirect("/recordings")


@app.post("/recordings/protect")
def protect_recording():
    worker_id = str(request.form.get("worker_id", "")).strip()
    path = str(request.form.get("path", "")).strip()
    if not worker_id or not path:
        flash("Missing recording information.", "bad")
        return redirect("/recordings")
    data = recording_flags()
    data["protected"][recording_flag_key(worker_id, path)] = {
        "worker_id": worker_id,
        "path": path,
        "protected_ts": time.time(),
    }
    save_json(RECORDING_FLAGS_FILE, data)
    flash("Recording protected from recycling and move deletion.", "good")
    return redirect("/recordings")


@app.post("/recordings/unprotect")
def unprotect_recording():
    worker_id = str(request.form.get("worker_id", "")).strip()
    path = str(request.form.get("path", "")).strip()
    data = recording_flags()
    data["protected"].pop(recording_flag_key(worker_id, path), None)
    save_json(RECORDING_FLAGS_FILE, data)
    flash("Recording protection removed.", "good")
    return redirect("/recordings")


@app.post("/workers/<worker_id>/restore-recycled")
def restore_recycled(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("That worker is offline.", "bad")
        return redirect("/recordings")
    path = str(request.form.get("path", "")).strip()
    create_worker_control_job(worker, "restore_recycled", f"Restore recycled recording: {Path(path).name}", {"path": path})
    flash("Restore command sent to worker.", "good")
    return redirect("/recordings")


@app.post("/workers/<worker_id>/delete-recycled")
def delete_recycled(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("That worker is offline.", "bad")
        return redirect("/recordings")
    path = str(request.form.get("path", "")).strip()
    create_worker_control_job(worker, "delete_recycled", f"Permanently delete recycled file: {Path(path).name}", {"path": path})
    flash("Permanent recycle-bin delete command sent.", "good")
    return redirect("/recordings")


@app.post("/workers/<worker_id>/empty-recycle-bin")
def empty_recycle_bin(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("That worker is offline.", "bad")
        return redirect("/recordings")
    create_worker_control_job(worker, "empty_recycle_bin", "Empty VIC recycle bin")
    flash("Empty recycle-bin command sent.", "good")
    return redirect("/recordings")


@app.post("/workers/<worker_id>/delete-recording")
def delete_recording(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("That worker is offline, so the file was not deleted.", "bad")
        return redirect("/recordings")

    path = str(request.form.get("path", "")).strip()
    if not path:
        flash("No recording path was supplied.", "bad")
        return redirect("/recordings")

    if recording_is_protected(worker_id, path):
        flash("That recording is protected. Remove protection before recycling it.", "bad")
        return redirect("/recordings")
    job = create_worker_control_job(
        worker,
        "recycle_recording",
        f"Recycle recording: {Path(path).name}",
        {"path": path},
    )
    flash(
        f'Recycle command sent to {job["worker_name"]}. The file can be restored later.',
        "good",
    )
    return redirect("/recordings")


@app.post("/workers/<worker_id>/delete-recordings-all")
def delete_worker_recordings(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("That worker is offline, so no files were deleted.", "bad")
        return redirect("/recordings")

    protected_paths = [
        item.get("path", "")
        for item in recording_flags().get("protected", {}).values()
        if item.get("worker_id") == worker_id
    ]
    job = create_worker_control_job(
        worker,
        "recycle_recordings_all",
        "Recycle all unprotected recordings",
        {"protected_paths": protected_paths},
    )
    flash(
        f'Recycle-all command sent to {job["worker_name"]}. Protected files are kept.',
        "good",
    )
    return redirect("/recordings")


@app.post("/recordings/delete-all")
def delete_all_recordings():
    online = [item for item in workers() if item.get("online")]
    if not online:
        flash("No workers are online, so no recording files were deleted.", "bad")
        return redirect("/recordings")

    for worker in online:
        protected_paths = [
            item.get("path", "")
            for item in recording_flags().get("protected", {}).values()
            if item.get("worker_id") == worker.get("id")
        ]
        create_worker_control_job(
            worker,
            "recycle_recordings_all",
            "Recycle all unprotected recordings",
            {"protected_paths": protected_paths},
        )
    flash(
        f"Recycle-all command sent to "
        f"{len(online)} online worker(s). Protected files are kept.",
        "good",
    )
    return redirect("/recordings")


TRANSFER_TERMINAL_STATES = {
    "completed",
    "failed",
    "source_delete_failed",
    "cancelled",
}


def transfer_already_queued(
    source_worker_id: str,
    source_path: str,
) -> bool:
    normalized = str(source_path).casefold()
    return any(
        str(item.get("source_worker_id", "")) == source_worker_id
        and str(item.get("source_path", "")).casefold() == normalized
        and str(item.get("state", "")) not in TRANSFER_TERMINAL_STATES
        for item in transfers()
    )


def queue_recording_transfer(
    source_worker: dict[str, Any],
    target_worker: dict[str, Any],
    inventory_item: dict[str, Any],
    operation: str,
    *,
    batch_id: str = "",
    batch_label: str = "",
    batch_index: int = 1,
    batch_total: int = 1,
) -> tuple[dict[str, Any] | None, str]:
    source_worker_id = str(source_worker.get("id", ""))
    target_worker_id = str(target_worker.get("id", ""))
    source_path = str(inventory_item.get("path", "")).strip()

    source_ready, source_reason = transfer_worker_ready(source_worker)
    if not source_ready:
        return None, source_reason
    target_ready, target_reason = transfer_worker_ready(target_worker)
    if not target_ready:
        return None, target_reason

    if not source_path:
        return None, "missing path"
    if operation == "move" and recording_is_protected(source_worker_id, source_path):
        return None, "recording is protected; use Copy or remove protection first"
    if source_worker_id == target_worker_id:
        return None, "source and destination are the same"
    if transfer_already_queued(source_worker_id, source_path):
        return None, "already queued"

    operation = operation if operation in {"copy", "move"} else "move"
    transfer_id = uuid.uuid4().hex
    now = time.time()
    filename = str(
        inventory_item.get("name", Path(source_path).name)
    )
    relative = str(
        inventory_item.get("relative", filename)
    )

    record = {
        "id": transfer_id,
        "operation": operation,
        "state": "queued_upload",
        "progress_percent": 0,
        "message": (
            "Queued behind any earlier transfer on the source worker"
        ),
        "source_worker_id": source_worker_id,
        "source_worker_name": source_worker.get(
            "display_name",
            source_worker.get("name", "Source worker"),
        ),
        "target_worker_id": target_worker_id,
        "target_worker_name": target_worker.get(
            "display_name",
            target_worker.get("name", "Destination worker"),
        ),
        "source_path": source_path,
        "relative": relative,
        "filename": filename,
        "reported_size_mb": inventory_item.get("size_mb", 0),
        "batch_id": batch_id,
        "batch_label": batch_label,
        "batch_index": batch_index,
        "batch_total": batch_total,
        "created_ts": now,
        "updated_ts": now,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    data = transfers()
    data.append(record)
    save_json(TRANSFERS_FILE, data)

    job = create_worker_control_job(
        source_worker,
        "upload_recording_transfer",
        (
            f'{operation.title()} recording to '
            f'{record["target_worker_name"]}: {filename}'
        ),
        {
            "transfer_id": transfer_id,
            "path": source_path,
            "relative": relative,
            "filename": filename,
            "operation": operation,
            "batch_id": batch_id,
            "batch_index": batch_index,
            "batch_total": batch_total,
        },
    )
    update_transfer(
        transfer_id,
        source_job_id=job["id"],
    )
    return record, ""


def find_inventory_items(
    source_worker: dict[str, Any],
    requested_paths: list[str],
) -> list[dict[str, Any]]:
    inventory = {
        str(item.get("path", "")): item
        for item in source_worker.get("recordings", [])
        if item.get("path")
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in requested_paths:
        text = str(path).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        item = inventory.get(text)
        if item:
            result.append(item)
    return result


def queue_transfer_batch(
    source_worker: dict[str, Any],
    target_worker: dict[str, Any],
    items: list[dict[str, Any]],
    operation: str,
    batch_label: str,
) -> tuple[int, int, list[str]]:
    batch_id = uuid.uuid4().hex
    queued = 0
    skipped = 0
    reasons: list[str] = []
    total = len(items)

    for index, item in enumerate(items, start=1):
        record, reason = queue_recording_transfer(
            source_worker,
            target_worker,
            item,
            operation,
            batch_id=batch_id,
            batch_label=batch_label,
            batch_index=index,
            batch_total=total,
        )
        if record:
            queued += 1
        else:
            skipped += 1
            if reason and reason not in reasons:
                reasons.append(reason)

    return queued, skipped, reasons


@app.post("/recordings/transfer")
def create_recording_transfer():
    source_worker_id = str(
        request.form.get("source_worker_id", "")
    ).strip()
    target_worker_id = str(
        request.form.get("target_worker_id", "")
    ).strip()
    source_path = str(
        request.form.get("path", "")
    ).strip()
    operation = str(
        request.form.get("operation", "move")
    ).strip().lower()
    if operation not in {"copy", "move"}:
        operation = "move"

    source_worker = worker_by_id(source_worker_id)
    target_worker = worker_by_id(target_worker_id)
    if not source_worker or not source_worker.get("online"):
        flash("The computer holding that file is offline.", "bad")
        return redirect("/recordings")
    if not target_worker or not target_worker.get("online"):
        flash("The destination worker is offline.", "bad")
        return redirect("/recordings")
    if source_worker_id == target_worker_id:
        flash("Choose a different destination computer.", "bad")
        return redirect("/recordings")

    items = find_inventory_items(
        source_worker,
        [source_path],
    )
    if not items:
        flash(
            "That file is no longer in the worker's recording list. "
            "Wait a few seconds and refresh.",
            "bad",
        )
        return redirect("/recordings")

    record, reason = queue_recording_transfer(
        source_worker,
        target_worker,
        items[0],
        operation,
    )
    if not record:
        flash(
            "That transfer was not queued: "
            + (reason or "unknown reason"),
            "bad",
        )
        return redirect("/recordings")

    flash(
        f'{operation.title()} queued: {record["filename"]} → '
        f'{record["target_worker_name"]}. The original is kept '
        "until the destination verifies the complete file.",
        "good",
    )
    return redirect("/recordings")


@app.post("/recordings/transfer-batch")
def create_recording_transfer_batch():
    source_worker_id = str(
        request.form.get("source_worker_id", "")
    ).strip()
    action = str(
        request.form.get("action", "move_selected")
    ).strip().lower()

    source_worker = worker_by_id(source_worker_id)
    if not source_worker or not source_worker.get("online"):
        flash("The source worker is offline.", "bad")
        return redirect("/recordings")

    operation = "copy" if action.startswith("copy_") else "move"
    use_all = action in {
        "copy_all",
        "move_all",
        "move_all_main",
    }

    target_worker_id = str(
        request.form.get("target_worker_id", "")
    ).strip()
    if action == "move_all_main":
        local = main_worker()
        target_worker_id = (
            str(local.get("id", ""))
            if local
            else ""
        )

    target_worker = worker_by_id(target_worker_id)
    if not target_worker or not target_worker.get("online"):
        flash("The destination worker is offline.", "bad")
        return redirect("/recordings")
    if source_worker_id == target_worker_id:
        flash("Choose a different destination computer.", "bad")
        return redirect("/recordings")

    if use_all:
        items = list(source_worker.get("recordings", []))
        selection_name = "all recordings"
    else:
        selected_paths = request.form.getlist("paths")
        items = find_inventory_items(
            source_worker,
            selected_paths,
        )
        selection_name = "selected recordings"

    if not items:
        flash(
            "No recording files were selected or available.",
            "bad",
        )
        return redirect("/recordings")

    batch_label = (
        f'{operation.title()} {selection_name} from '
        f'{source_worker.get("display_name", "worker")} to '
        f'{target_worker.get("display_name", "worker")}'
    )
    queued, skipped, reasons = queue_transfer_batch(
        source_worker,
        target_worker,
        items,
        operation,
        batch_label,
    )

    message = (
        f"Mass {operation} queued for {queued} file(s) from "
        f'{source_worker.get("display_name", "worker")} to '
        f'{target_worker.get("display_name", "worker")}. '
        "That worker processes several transfers at the same time when its limit allows."
    )
    if skipped:
        message += f" Skipped {skipped} file(s)."
    if reasons:
        message += " Reason(s): " + ", ".join(reasons[:3]) + "."
    flash(message, "good" if queued else "bad")
    return redirect("/recordings")


@app.post("/recordings/move-all-remote-to-main")
def move_all_remote_recordings_to_main():
    local = main_worker()
    if not local or not local.get("online"):
        flash("The Main PC worker is not online.", "bad")
        return redirect("/recordings")

    total_queued = 0
    total_skipped = 0
    source_count = 0

    for source_worker in workers():
        if (
            not source_worker.get("online")
            or source_worker.get("id") == local.get("id")
        ):
            continue
        items = list(source_worker.get("recordings", []))
        if not items:
            continue
        source_count += 1
        queued, skipped, _reasons = queue_transfer_batch(
            source_worker,
            local,
            items,
            "move",
            (
                "Move every remote recording to Main PC "
                f'from {source_worker.get("display_name", "worker")}'
            ),
        )
        total_queued += queued
        total_skipped += skipped

    if not total_queued:
        flash(
            "No remote recording files were available to move. "
            f"Skipped {total_skipped} already queued file(s).",
            "bad",
        )
        return redirect("/recordings")

    flash(
        f"Mass move queued for {total_queued} file(s) from "
        f"{source_count} remote worker(s) to the Main PC. "
        "Each source worker processes several transfers at the same time when its limit allows. "
        f"Skipped {total_skipped} duplicate/active queue item(s).",
        "good",
    )
    return redirect("/recordings")


def stop_transfer_jobs(transfer: dict[str, Any]) -> None:
    job_ids = {
        str(transfer.get("source_job_id", "")),
        str(transfer.get("target_job_id", "")),
        str(transfer.get("delete_job_id", "")),
    }
    job_ids.discard("")
    if not job_ids:
        return
    data = jobs()
    changed = False
    for item in data:
        if str(item.get("id", "")) in job_ids and job_is_active(item):
            item["desired_state"] = "stopped"
            item["state"] = "stopping"
            item["message"] = "Transfer cancelled or retried"
            item["updated_ts"] = time.time()
            changed = True
    if changed:
        save_json(JOBS_FILE, data)


RETRYABLE_TRANSFER_STATES = {
    "failed",
    "source_delete_failed",
}

ACTIVE_TRANSFER_STATES = {
    "worker_queue",
    "checking_source",
    "hashing_source",
    "uploading",
    "queued_download",
    "starting_download",
    "downloading",
    "deleting_source",
}


def transfer_is_stuck(
    transfer: dict[str, Any],
    *,
    stale_seconds: float = 30.0,
) -> bool:
    state = str(transfer.get("state", ""))
    if state in RETRYABLE_TRANSFER_STATES:
        return True
    if state == "queued_upload":
        return transfer_age_seconds(transfer) >= 10.0
    if state in ACTIVE_TRANSFER_STATES:
        return transfer_age_seconds(transfer) >= stale_seconds
    return False


def retry_transfer_record(
    transfer: dict[str, Any],
) -> tuple[bool, str]:
    transfer_id = str(transfer.get("id", "")).strip()
    if not transfer_id:
        return False, "missing transfer ID"

    source_worker = worker_by_id(
        str(transfer.get("source_worker_id", ""))
    )
    target_worker = worker_by_id(
        str(transfer.get("target_worker_id", ""))
    )
    source_ready, source_reason = transfer_worker_ready(source_worker)
    target_ready, target_reason = transfer_worker_ready(target_worker)
    if not source_ready or not target_ready:
        return False, (
            source_reason
            if not source_ready
            else target_reason
        )

    source_path = str(transfer.get("source_path", "")).strip()
    inventory = find_inventory_items(
        source_worker,
        [source_path],
    )
    if not inventory:
        return False, (
            "source file is not currently reported by its worker"
        )

    stop_transfer_jobs(transfer)
    try:
        (TRANSFER_STAGE_DIR / f"{transfer_id}.bin").unlink(
            missing_ok=True
        )
        (TRANSFER_STAGE_DIR / f"{transfer_id}.part").unlink(
            missing_ok=True
        )
    except OSError:
        pass

    job = create_worker_control_job(
        source_worker,
        "upload_recording_transfer",
        (
            f'Retry {str(transfer.get("operation", "move")).title()} '
            f'to {target_worker.get("display_name", "worker")}: '
            f'{transfer.get("filename", "recording")}'
        ),
        {
            "transfer_id": transfer_id,
            "path": source_path,
            "relative": transfer.get(
                "relative",
                transfer.get("filename", "recording.bin"),
            ),
            "filename": transfer.get(
                "filename",
                "recording.bin",
            ),
            "operation": transfer.get("operation", "move"),
            "batch_id": transfer.get("batch_id", ""),
            "batch_index": transfer.get("batch_index", 1),
            "batch_total": transfer.get("batch_total", 1),
        },
    )
    update_transfer(
        transfer_id,
        state="queued_upload",
        message=(
            "Retry queued. Waiting for the compatible source worker "
            "to accept an available parallel upload slot."
        ),
        progress_percent=0,
        source_job_id=job["id"],
        target_job_id="",
        delete_job_id="",
        destination_path="",
        bytes_done=0,
        bytes_per_second=0,
        eta_seconds=None,
        rate_sample_bytes=0,
        rate_sample_ts=time.time(),
    )
    return True, ""


@app.post("/transfers/<transfer_id>/retry")
def retry_transfer(transfer_id: str):
    transfer = transfer_by_id(transfer_id)
    if not transfer:
        flash("That transfer no longer exists.", "bad")
        return redirect("/recordings")

    retried, reason = retry_transfer_record(transfer)
    if not retried:
        flash(
            "Transfer could not be retried: " + reason + ".",
            "bad",
        )
        return redirect("/recordings")

    flash(
        "Transfer retry queued. The source worker should accept it "
        "into an available parallel slot within a few seconds.",
        "good",
    )
    return redirect("/recordings")


@app.post("/transfers/retry-all")
def retry_all_transfers():
    data = sorted(
        transfers(),
        key=lambda item: item.get("created_ts", 0),
    )
    retried = 0
    skipped_active = 0
    skipped_finished = 0
    failed_reasons: dict[str, int] = {}

    for transfer in data:
        state = str(transfer.get("state", ""))
        if state in {"completed", "cancelled"}:
            skipped_finished += 1
            continue
        if not transfer_is_stuck(transfer):
            skipped_active += 1
            continue

        ok, reason = retry_transfer_record(transfer)
        if ok:
            retried += 1
        else:
            failed_reasons[reason] = (
                failed_reasons.get(reason, 0) + 1
            )

    message = (
        f"Retry All queued {retried} failed or stuck transfer(s). "
        f"Left {skipped_active} healthy active transfer(s) running and "
        f"ignored {skipped_finished} completed/cancelled item(s)."
    )
    if failed_reasons:
        reason_text = "; ".join(
            f"{count}× {reason}"
            for reason, count in list(failed_reasons.items())[:4]
        )
        message += " Could not retry: " + reason_text + "."

    flash(message, "good" if retried else "bad")
    return redirect("/recordings")


@app.post("/transfers/<transfer_id>/cancel")
def cancel_transfer(transfer_id: str):
    transfer = transfer_by_id(transfer_id)
    if not transfer:
        flash("That transfer no longer exists.", "bad")
        return redirect("/recordings")

    stop_transfer_jobs(transfer)
    try:
        (TRANSFER_STAGE_DIR / f"{transfer_id}.bin").unlink(missing_ok=True)
        (TRANSFER_STAGE_DIR / f"{transfer_id}.part").unlink(missing_ok=True)
    except OSError:
        pass
    update_transfer(
        transfer_id,
        state="cancelled",
        message=(
            "Transfer cancelled. The original recording was kept. "
            "A completed destination copy, if one already existed, was not deleted."
        ),
    )
    flash("Transfer cancelled; the original recording was kept.", "good")
    return redirect("/recordings")


@app.post("/transfers/clear-finished")
def clear_finished_transfers():
    terminal = {"completed", "failed", "source_delete_failed", "cancelled"}
    data = transfers()
    removed = [item for item in data if item.get("state") in terminal]
    kept = [item for item in data if item.get("state") not in terminal]
    for item in removed:
        try:
            (TRANSFER_STAGE_DIR / f'{item.get("id", "")}.bin').unlink(missing_ok=True)
            (TRANSFER_STAGE_DIR / f'{item.get("id", "")}.part').unlink(missing_ok=True)
        except OSError:
            pass
    save_json(TRANSFERS_FILE, kept)
    flash(f"Cleared {len(removed)} finished/failed transfer item(s).", "good")
    return redirect("/recordings")


@app.post("/api/transfers/<transfer_id>/progress")
def transfer_progress(transfer_id: str):
    if not token_ok():
        return jsonify({"error": "Invalid cluster token"}), 403
    transfer = transfer_by_id(transfer_id)
    if not transfer:
        return jsonify({"error": "Transfer not found"}), 404
    payload = request.get_json(silent=True) or {}
    worker_id = str(payload.get("worker_id", ""))
    if worker_id not in {
        str(transfer.get("source_worker_id", "")),
        str(transfer.get("target_worker_id", "")),
    }:
        return jsonify({"error": "Worker is not part of this transfer"}), 403

    allowed = {"state", "message", "progress_percent", "bytes_done", "total_bytes"}
    fields = {key: payload[key] for key in allowed if key in payload}
    now = time.time()
    if "bytes_done" in fields:
        current_bytes = int(fields.get("bytes_done", 0) or 0)
        previous_bytes = int(transfer.get("rate_sample_bytes", transfer.get("bytes_done", 0)) or 0)
        previous_ts = float(transfer.get("rate_sample_ts", transfer.get("updated_ts", now)) or now)
        elapsed = max(0.001, now - previous_ts)
        if current_bytes >= previous_bytes and elapsed >= 0.15:
            instant = (current_bytes - previous_bytes) / elapsed
            previous_rate = float(transfer.get("bytes_per_second", 0) or 0)
            smoothed = instant if previous_rate <= 0 else previous_rate * 0.55 + instant * 0.45
            fields["bytes_per_second"] = round(smoothed, 2)
            total = int(fields.get("total_bytes", transfer.get("total_bytes", 0)) or 0)
            remaining = max(0, total - current_bytes)
            fields["eta_seconds"] = round(remaining / smoothed, 1) if smoothed > 1 and total else None
            fields["rate_sample_bytes"] = current_bytes
            fields["rate_sample_ts"] = now
    update_transfer(transfer_id, **fields)
    return jsonify({"ok": True})


@app.get("/api/transfers/status")
def transfer_status_api():
    result = []
    for item in sorted(
        transfers(),
        key=lambda value: value.get("created_ts", 0),
        reverse=True,
    )[:250]:
        result.append({
            "id": item.get("id", ""),
            "state": item.get("state", "unknown"),
            "message": item.get("message", ""),
            "progress_percent": item.get("progress_percent", 0),
            "bytes_done": item.get("bytes_done", 0),
            "total_bytes": item.get("total_bytes", item.get("size_bytes", 0)),
            "bytes_per_second": item.get("bytes_per_second", 0),
            "eta_seconds": item.get("eta_seconds"),
            "destination_path": item.get("destination_path", ""),
            "updated_ts": item.get("updated_ts", 0),
        })
    response = jsonify({
        "transfers": result,
        "server_ts": time.time(),
        "refresh_ms": TRANSFER_STATUS_REFRESH_MS,
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.post("/api/transfers/<transfer_id>/upload")
def upload_transfer(transfer_id: str):
    if not token_ok():
        return jsonify({"error": "Invalid cluster token"}), 403
    transfer = transfer_by_id(transfer_id)
    if not transfer:
        return jsonify({"error": "Transfer not found"}), 404

    source_worker_id = str(request.headers.get("X-VIC-Worker-ID", ""))
    if source_worker_id != str(transfer.get("source_worker_id", "")):
        return jsonify({"error": "Wrong source worker"}), 403

    expected_size = int(request.headers.get("X-VIC-Size", "0") or 0)
    expected_hash = str(request.headers.get("X-VIC-SHA256", "")).lower().strip()
    free = shutil.disk_usage(TRANSFER_STAGE_DIR).free
    if expected_size and free < expected_size + 256 * 1024 * 1024:
        update_transfer(
            transfer_id,
            state="failed",
            message="The main PC does not have enough temporary free space for this transfer.",
        )
        return jsonify({"error": "Not enough temporary disk space on the main PC"}), 507

    part_path = TRANSFER_STAGE_DIR / f"{transfer_id}.part"
    final_path = TRANSFER_STAGE_DIR / f"{transfer_id}.bin"
    hasher = hashlib.sha256()
    received = 0
    try:
        with part_path.open("wb") as handle:
            while True:
                chunk = request.stream.read(TRANSFER_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                received += len(chunk)
        digest = hasher.hexdigest()
        if expected_size and received != expected_size:
            raise ValueError(f"Expected {expected_size} bytes but received {received}.")
        if expected_hash and digest != expected_hash:
            raise ValueError("The staged file hash does not match the source file.")
        part_path.replace(final_path)
    except Exception as exc:
        try:
            part_path.unlink(missing_ok=True)
        except OSError:
            pass
        update_transfer(transfer_id, state="failed", message=f"Upload failed: {exc}")
        return jsonify({"error": str(exc)}), 400

    target_worker = worker_by_id(str(transfer.get("target_worker_id", "")))
    if not target_worker:
        update_transfer(
            transfer_id,
            state="failed",
            message="The destination worker entry no longer exists. The staged file was kept for inspection.",
            size_bytes=received,
            sha256=digest,
        )
        return jsonify({"error": "Destination worker no longer exists"}), 404

    target_job = create_worker_control_job(
        target_worker,
        "receive_recording_transfer",
        f'Receive recording from {transfer.get("source_worker_name", "worker")}: {transfer.get("filename", "recording")}',
        {
            "transfer_id": transfer_id,
            "relative": transfer.get("relative", transfer.get("filename", "recording.bin")),
            "filename": transfer.get("filename", "recording.bin"),
            "source_worker_name": transfer.get("source_worker_name", "Worker"),
            "expected_size": received,
            "expected_sha256": digest,
        },
    )
    update_transfer(
        transfer_id,
        state="queued_download",
        message="Upload verified on the main PC; waiting for the destination worker",
        progress_percent=50,
        size_bytes=received,
        sha256=digest,
        target_job_id=target_job["id"],
        bytes_done=0,
        total_bytes=received,
        bytes_per_second=0,
        eta_seconds=None,
        rate_sample_bytes=0,
        rate_sample_ts=time.time(),
    )
    return jsonify({"ok": True, "target_job_id": target_job["id"], "sha256": digest, "size": received})


@app.get("/api/transfers/<transfer_id>/download")
def download_transfer(transfer_id: str):
    if not token_ok():
        return jsonify({"error": "Invalid cluster token"}), 403
    transfer = transfer_by_id(transfer_id)
    if not transfer:
        return jsonify({"error": "Transfer not found"}), 404
    target_worker_id = str(request.headers.get("X-VIC-Worker-ID", ""))
    if target_worker_id != str(transfer.get("target_worker_id", "")):
        return jsonify({"error": "Wrong destination worker"}), 403
    path = TRANSFER_STAGE_DIR / f"{transfer_id}.bin"
    if not path.is_file():
        return jsonify({"error": "Staged transfer file is missing"}), 404
    return send_file(
        path,
        as_attachment=True,
        download_name=str(transfer.get("filename", "recording.bin")),
        conditional=True,
    )


@app.post("/api/transfers/<transfer_id>/received")
def transfer_received(transfer_id: str):
    if not token_ok():
        return jsonify({"error": "Invalid cluster token"}), 403
    transfer = transfer_by_id(transfer_id)
    if not transfer:
        return jsonify({"error": "Transfer not found"}), 404
    payload = request.get_json(silent=True) or {}
    worker_id = str(payload.get("worker_id", ""))
    if worker_id != str(transfer.get("target_worker_id", "")):
        return jsonify({"error": "Wrong destination worker"}), 403
    received_hash = str(payload.get("sha256", "")).lower()
    received_size = int(payload.get("size", 0) or 0)
    if received_hash != str(transfer.get("sha256", "")).lower():
        update_transfer(transfer_id, state="failed", message="Destination hash verification failed. The original file was kept.")
        return jsonify({"error": "Hash verification failed"}), 400
    if received_size != int(transfer.get("size_bytes", 0) or 0):
        update_transfer(transfer_id, state="failed", message="Destination size verification failed. The original file was kept.")
        return jsonify({"error": "Size verification failed"}), 400

    destination_path = str(payload.get("destination_path", ""))
    try:
        (TRANSFER_STAGE_DIR / f"{transfer_id}.bin").unlink(missing_ok=True)
    except OSError:
        pass

    if transfer.get("operation") == "move":
        source_worker = worker_by_id(str(transfer.get("source_worker_id", "")))
        if not source_worker:
            update_transfer(
                transfer_id,
                state="source_delete_failed",
                message="The destination verified the file, but the original worker entry is missing. The original was not deleted.",
                progress_percent=100,
                destination_path=destination_path,
            )
            return jsonify({"ok": True, "warning": "Source worker entry missing"})
        delete_job = create_worker_control_job(
            source_worker,
            "delete_transfer_source",
            f'Delete verified original after move: {transfer.get("filename", "recording")}',
            {
                "path": transfer.get("source_path", ""),
                "transfer_id": transfer_id,
            },
        )
        update_transfer(
            transfer_id,
            state="deleting_source",
            message="Destination verified the file; deleting the original from the source worker",
            progress_percent=95,
            destination_path=destination_path,
            delete_job_id=delete_job["id"],
        )
    else:
        update_transfer(
            transfer_id,
            state="completed",
            message="Copy completed and verified; the original file was kept",
            progress_percent=100,
            destination_path=destination_path,
        )
    return jsonify({"ok": True})


def latest_live_jobs() -> list[dict[str, Any]]:
    """Return the newest job for every source that still exists."""
    newest: dict[str, dict[str, Any]] = {}
    valid_sources = configured_source_ids()
    for item in sorted(
        jobs(),
        key=lambda value: value.get("created_ts", 0),
        reverse=True,
    ):
        source_id = str(item.get("source_id", item.get("id", "")))
        if source_id not in valid_sources:
            continue
        if source_id not in newest:
            newest[source_id] = item

    source_map = {
        str(item.get("id", "")): source_with_defaults(item)
        for item in sources()
    }
    # Add virtual IDLE entries for configured sources that have never had a
    # job. This is especially important for website sources: Live All must
    # show the source and its Start/Arm controls before the website goes live.
    for source_id, source in source_map.items():
        if source_id in newest:
            continue
        newest[source_id] = {
            "id": "idle-" + source_id,
            "source_id": source_id,
            "source_name": source.get("name", "Source"),
            "worker_id": source.get("worker_id", "auto"),
            "worker_name": "Not running",
            "source": source,
            "mode": "idle",
            "state": "idle",
            "message": "Ready — press Start, Preview or Arm Auto Start",
            "output": "",
            "created_ts": 0,
            "updated_ts": 0,
            "preview_available": False,
            "virtual_idle": True,
        }

    values: list[dict[str, Any]] = []
    for source_id, item in newest.items():
        current = dict(item)
        latest_source = source_map.get(source_id)
        if latest_source:
            current["source"] = latest_source
            current["source_name"] = latest_source.get(
                "name",
                current.get("source_name", "Source"),
            )
        values.append(current)
    values.sort(
        key=lambda item: (
            item.get("state") not in {"running", "pending", "starting", "stopping"},
            -float(item.get("created_ts", 0)),
        )
    )
    return values[:60]


def health_value(item: dict[str, Any], key: str, default: Any = None) -> Any:
    value = item.get(key, default)
    return default if value is None else value


@app.get("/health")
def health_page():
    data = latest_live_jobs()
    body = """<div class='card'><div class='live-tabs'><a href='/live'>Live list</a><a href='/live/all'>Live All grid</a><a class='active' href='/health'>Recording Health</a></div><div class='live-status-row'><div><h2 style='margin:0'>Recording Health</h2><p class='muted'>Detailed live encoder and recording statistics. Preview jobs show capture performance but save no media file.</p></div><a class='btn' href='/workers'>Worker hardware</a></div></div>
<div class='health-grid'>{% for job in jobs %}<div class='health-card' id='health-card-{{job.id}}'><div class='live-status-row'><div><h3 style='margin:0'>{{job.source_name}}</h3><span class='small muted'>{{job.worker_name}}</span></div><span id='health-mode-{{job.id}}' class='state-pill'>{{job.mode|upper}} · {{job.state|upper}}</span></div><div class='health-metrics'>
<div class='health-metric'><span class='small muted'>Actual / requested FPS</span><b id='health-fps-{{job.id}}'>— / —</b></div>
<div class='health-metric'><span class='small muted'>Dropped / duplicated</span><b id='health-drop-{{job.id}}'>0 / 0</b></div>
<div class='health-metric'><span class='small muted'>Current bitrate</span><b id='health-bitrate-{{job.id}}'>—</b></div>
<div class='health-metric'><span class='small muted'>Current file size</span><b id='health-size-{{job.id}}'>—</b></div>
<div class='health-metric'><span class='small muted'>Actual disk write rate</span><b id='health-write-{{job.id}}'>—</b></div>
<div class='health-metric'><span class='small muted'>Duration</span><b id='health-duration-{{job.id}}'>00:00:00</b></div>
<div class='health-metric'><span class='small muted'>Estimated storage/hour</span><b id='health-hour-{{job.id}}'>—</b></div>
<div class='health-metric'><span class='small muted'>Encoder</span><b id='health-encoder-{{job.id}}'>—</b></div>
<div class='health-metric'><span class='small muted'>Processing speed</span><b id='health-speed-{{job.id}}'>—</b></div>
</div><p id='health-message-{{job.id}}' class='small muted'>{{job.message}}</p><div class='inline'>{% if job.source.type in preview_types %}<form method='post' action='/sources/{{job.source_id}}/preview'><input type='hidden' name='return_to' value='/health'><button class='preview-button'>Preview Unlimited</button></form>{% endif %}<form method='post' action='/sources/{{job.source_id}}/start'><input type='hidden' name='return_to' value='/health'><button class='control-start'>Start recording</button></form>{% if job.source.type in auto_start_types %}<form method='post' action='/sources/{{job.source_id}}/auto-start'><input type='hidden' name='return_to' value='/health'><button class='{{"control-stop" if job.source.auto_start_armed else "control-test"}}'>{{"Disarm Auto Start" if job.source.auto_start_armed else "Arm Auto Start"}}</button></form>{% endif %}<form method='post' action='/sources/{{job.source_id}}/stop'><input type='hidden' name='return_to' value='/health'><button class='control-stop'>Stop</button></form></div></div>{% else %}<div class='card muted'>No source jobs exist yet.</div>{% endfor %}</div>"""
    script = """<script>
const healthIds={{job_ids|tojson}};
function durationText(seconds){seconds=Math.max(0,Math.floor(Number(seconds)||0));const h=String(Math.floor(seconds/3600)).padStart(2,'0');const m=String(Math.floor((seconds%3600)/60)).padStart(2,'0');const s=String(seconds%60).padStart(2,'0');return h+':'+m+':'+s;}
function sizeText(bytes){bytes=Number(bytes)||0;if(bytes<=0)return '—';if(bytes>=1073741824)return (bytes/1073741824).toFixed(2)+' GB';if(bytes>=1048576)return (bytes/1048576).toFixed(2)+' MB';if(bytes>=1024)return (bytes/1024).toFixed(1)+' KB';return Math.round(bytes)+' B';}
function writeRateText(bytesPerSecond){const value=Math.max(0,Number(bytesPerSecond)||0);const bits=value*8;let byteText;if(value>=1073741824)byteText=(value/1073741824).toFixed(2)+' GB/s';else if(value>=1048576)byteText=(value/1048576).toFixed(2)+' MB/s';else if(value>=1024)byteText=(value/1024).toFixed(1)+' KB/s';else byteText=Math.round(value)+' B/s';let bitText;if(bits>=1000000000)bitText=(bits/1000000000).toFixed(2)+' Gb/s';else if(bits>=1000000)bitText=(bits/1000000).toFixed(2)+' Mb/s';else if(bits>=1000)bitText=(bits/1000).toFixed(1)+' kb/s';else bitText=Math.round(bits)+' b/s';return bitText+' · '+byteText;}
function metricText(value,suffix,digits=1){return value===null||value===undefined||value===''?'—':Number(value).toFixed(digits)+suffix;}
async function refreshHealth(){try{const r=await fetch('/api/live-status',{cache:'no-store'});const d=await r.json();const map=new Map((d.jobs||[]).map(x=>[x.id,x]));healthIds.forEach(id=>{const x=map.get(id);if(!x)return;document.getElementById('health-mode-'+id).textContent=(x.mode||'').toUpperCase()+' · '+(x.state||'').toUpperCase();document.getElementById('health-fps-'+id).textContent=(x.actual_fps===null?'—':Number(x.actual_fps).toFixed(2))+' / '+(x.requested_fps??'Source');const drop=document.getElementById('health-drop-'+id);drop.textContent=(x.dropped_frames||0)+' / '+(x.duplicated_frames||0);drop.className=(x.dropped_frames||0)>0?'bad':'';document.getElementById('health-bitrate-'+id).textContent=metricText(x.bitrate_mbps,' Mbps');document.getElementById('health-size-'+id).textContent=sizeText(x.file_size_bytes);document.getElementById('health-write-'+id).textContent=writeRateText(x.disk_write_bytes_per_second);document.getElementById('health-duration-'+id).textContent=durationText(x.duration_seconds);document.getElementById('health-hour-'+id).textContent=metricText(x.disk_per_hour_gb,' GB/h');document.getElementById('health-encoder-'+id).textContent=x.encoder||'—';document.getElementById('health-speed-'+id).textContent=x.speed||'—';document.getElementById('health-message-'+id).textContent=x.message||'';});}catch(e){console.debug('Health refresh failed',e);}}
refreshHealth();setInterval(refreshHealth,500);
</script>"""
    return render_template_string(page("Recording Health", body, script), jobs=data, job_ids=[item["id"] for item in data], preview_types=PREVIEW_SOURCE_TYPES)


@app.get("/live")
def live_page():
    data = latest_live_jobs()
    body = """<div class='card'><div class='live-tabs'><a class='active' href='/live'>Live list</a><a href='/live/all'>Live All grid</a><a href='/health'>Recording Health</a></div><div class='live-status-row'><div><h2 style='margin:0'>Live source monitor</h2><p class='muted'>Preview runs continuously without saving. Press Start while Preview is active to change it into a recording automatically.</p></div><div class='live-global-controls'><form method='post' action='/sources/preview-all'><input type='hidden' name='return_to' value='/live'><button class='preview-button'>Preview All — Unlimited</button></form><form method='post' action='/sources/start-all'><input type='hidden' name='return_to' value='/live'><button class='control-start'>Start All</button></form><form method='post' action='/sources/stop-all'><input type='hidden' name='return_to' value='/live'><button class='control-stop'>Stop All</button></form></div></div><table><tr><th>Source</th><th>State</th><th>Audio</th><th>Recording health</th><th>Actions</th></tr>
{% for job in jobs %}<tr><td><strong>{{job.source_name}}</strong><br><span class='small muted'>{{job.worker_name}}</span></td><td><span id='live-list-state-{{job.id}}'>{{job.mode|upper}} · {{job.state|upper}}</span></td><td><div id='live-list-audio-text-{{job.id}}' class='audio-readout'>No meter</div><div class='meter dashboard-meter'><span id='live-list-audio-meter-{{job.id}}'></span></div></td><td><div id='live-list-health-{{job.id}}' class='health-mini'>FPS — · Drop 0 · — Mbps · —</div></td><td>{% if job.source.type in preview_types %}<form style='display:inline' method='post' action='/sources/{{job.source_id}}/preview'><input type='hidden' name='return_to' value='/live'><button class='preview-button'>Preview Unlimited</button></form>{% endif %}<form style='display:inline' method='post' action='/sources/{{job.source_id}}/start'><input type='hidden' name='return_to' value='/live'><button class='control-start'>Start</button></form>{% if job.source.type in auto_start_types %}<form style='display:inline' method='post' action='/sources/{{job.source_id}}/auto-start'><input type='hidden' name='return_to' value='/live'><button class='{{"control-stop" if job.source.auto_start_armed else "control-test"}}'>{{"Disarm Auto Start" if job.source.auto_start_armed else "Arm Auto Start"}}</button></form>{% endif %}<form style='display:inline' method='post' action='/sources/{{job.source_id}}/stop'><input type='hidden' name='return_to' value='/live'><button class='control-stop'>Stop</button></form><a class='btn' target='_blank' href='/live/{{job.id}}'>Open</a><a class='btn' href='/sources/{{job.source_id}}/edit'>Edit</a></td></tr>{% else %}<tr><td colspan='5' class='muted'>No jobs yet.</td></tr>{% endfor %}</table></div>"""
    live_cfg = runtime_live_settings()
    script = """<script>
const liveListIds={{job_ids|tojson}};
const audioSeconds={{audio_refresh_seconds|tojson}};
const audioMs=audioSeconds<=0?50:Math.max(50,Math.round(audioSeconds*1000));
const audioMotion={{audio_motion|tojson}};
let lastAudioAt=0;
function vicMeterMotion(meter,percent,mode){
  if(!meter)return;
  const target=Math.max(0,Math.min(100,Number(percent)||0));
  const previous=Number(meter.dataset.level||meter.style.width.replace('%','')||0);
  const rising=target>=previous;
  const timings={raw:[0,0],light:[20,90],medium:[45,180],heavy:[90,420]};
  const pair=timings[mode]||timings.light;
  const duration=rising?pair[0]:pair[1];
  meter.style.transitionProperty='width';
  meter.style.transitionTimingFunction=rising?'linear':'ease-out';
  meter.style.transitionDuration=duration+'ms';
  meter.dataset.level=String(target);
  meter.style.width=target+'%';
}
function setLiveListMeter(meter,percent){vicMeterMotion(meter,percent,audioMotion);}
async function refreshLiveList(){try{const r=await fetch('/api/live-status',{cache:'no-store'});const d=await r.json();const map=new Map((d.jobs||[]).map(x=>[x.id,x]));const now=Date.now();const updateAudio=now-lastAudioAt>=audioMs;liveListIds.forEach(id=>{const x=map.get(id);if(!x)return;const meter=document.getElementById('live-list-audio-meter-'+id);const text=document.getElementById('live-list-audio-text-'+id);if(updateAudio){setLiveListMeter(meter,x.audio_percent||0);if(text)text.textContent=x.audio_level_db===null?'No meter':x.audio_level_db.toFixed(1)+' dB';}document.getElementById('live-list-state-'+id).textContent=(x.mode||'').toUpperCase()+' · '+(x.state||'').toUpperCase();document.getElementById('live-list-health-'+id).textContent='FPS '+(x.actual_fps===null?'—':Number(x.actual_fps).toFixed(1))+'/'+(x.requested_fps??'Source')+' · Drop '+(x.dropped_frames||0)+' · '+(x.bitrate_mbps===null?'—':Number(x.bitrate_mbps).toFixed(1))+' Mbps · '+(x.encoder||'—');});if(updateAudio)lastAudioAt=now;}catch(e){console.debug(e);}}
async function liveListLoop(){await refreshLiveList();setTimeout(liveListLoop,Math.min(500,audioMs));}
liveListLoop();
</script>"""
    return render_template_string(page("Live", body, script), jobs=data, job_ids=[item["id"] for item in data], preview_types=PREVIEW_SOURCE_TYPES, auto_start_types=AUTO_START_SOURCE_TYPES, audio_refresh_seconds=live_cfg["audio_meter_refresh_seconds"], audio_smoothing=live_cfg["audio_meter_smoothing"], audio_motion=live_cfg["audio_meter_motion"])


@app.get("/live/all")
def live_all_page():
    data = latest_live_jobs()
    body = """<div class='card'><div class='live-tabs'><a href='/live'>Live list</a><a class='active' href='/live/all'>Live All grid</a><a href='/health'>Recording Health</a></div><div class='live-status-row'><div><h2 style='margin:0'>Live All</h2><p class='muted'>Preview stays active without a time limit and saves nothing. Press Start to change that source into recording.</p><p class='small muted'>At 100% this keeps the familiar layout. Below 100%, Live All uses a corrected transform scale so cards stay proportional while additional source columns fill the available screen width.</p></div><div class='live-global-controls'><form method='post' action='/sources/preview-all'><input type='hidden' name='return_to' value='/live/all'><button class='preview-button'>Preview All — Unlimited</button></form><form method='post' action='/sources/start-all'><input type='hidden' name='return_to' value='/live/all'><button class='control-start'>Start All</button></form><form method='post' action='/sources/stop-all'><input type='hidden' name='return_to' value='/live/all'><button class='control-stop'>Stop All</button></form><a class='btn' href='/health'>Detailed Health</a></div></div></div><div class='live-grid'>
{% for job in jobs %}<div class='live-card {% if job.state=="running" and job.mode=="record" %}is-recording{% elif job.state=="running" and job.mode=="preview" %}is-previewing{% elif job.state=="running" and job.mode=="test" %}is-testing{% elif job.state in ["pending","starting","stopping","waiting","waiting_available","waiting_reconnect"] %}is-pending{% elif job.state=="failed" %}is-failed{% endif %}' id='live-card-{{job.id}}'><div class='live-status-row'><div><h3>{{job.source_name}}</h3><span class='small muted'>{{job.worker_name}}</span></div><span id='all-state-{{job.id}}' class='state-pill'>{{job.state|upper}}</span></div><img id='all-preview-{{job.id}}' class='live-preview' src='/previews/{{job.id}}.jpg?w={{job.source.preview_display_width or runtime.preview_width}}&q={{job.source.preview_display_jpeg_quality or runtime.preview_jpeg_quality}}&t={{stamp}}' data-source-id='{{job.source_id}}' onerror='this.style.display="none";document.getElementById("all-placeholder-{{job.id}}").style.display="flex"'><div id='all-placeholder-{{job.id}}' class='live-placeholder' style='display:none'>Press Preview Unlimited to monitor until you stop it or start recording.</div><div id='all-audio-text-{{job.id}}' class='audio-readout'>No audio meter</div><div class='meter'><span id='all-audio-meter-{{job.id}}'></span></div><div id='all-health-{{job.id}}' class='health-mini'>FPS — · Drop 0 · — Mbps · —</div><p id='all-message-{{job.id}}' class='small muted'>{{job.message}}</p><div class='live-card-controls'>{% if job.source.type in preview_types %}<form method='post' action='/sources/{{job.source_id}}/preview'><input type='hidden' name='return_to' value='/live/all'><button class='preview-button'>Preview Unlimited</button></form>{% endif %}<form method='post' action='/sources/{{job.source_id}}/start'><input type='hidden' name='return_to' value='/live/all'><button class='control-start'>Start</button></form>{% if job.source.type in auto_start_types %}<form method='post' action='/sources/{{job.source_id}}/auto-start'><input type='hidden' name='return_to' value='/live/all'><button class='{{"control-stop" if job.source.auto_start_armed else "control-test"}}'>{{"Disarm Auto Start" if job.source.auto_start_armed else "Arm Auto Start"}}</button></form>{% endif %}{% if job.source.type=="website" and job.mode=="record" and job.state in ["running","starting","pending","stopping"] %}<form method='post' action='/sources/{{job.source_id}}/finish-capture'><input type='hidden' name='return_to' value='/live/all'><button class='control-test'>Finish Capture</button></form>{% endif %}<form method='post' action='/sources/{{job.source_id}}/stop'><input type='hidden' name='return_to' value='/live/all'><button class='control-stop'>{{"Force Stop" if job.source.type=="website" else "Stop"}}</button></form><a class='btn' target='_blank' href='/live/{{job.id}}'>Individual</a><a class='btn' href='/sources/{{job.source_id}}/edit'>Edit</a></div><div class='settings-note small'><label style='margin-top:0'>This source view quality</label><div class='inline'><select id='preview-quality-{{job.source_id}}'><option value='global' {{"selected" if not job.source.preview_display_width else ""}}>Use global setting</option><option value='lowest' {{"selected" if job.source.preview_display_width==160 else ""}}>Lowest 160p</option><option value='low' {{"selected" if job.source.preview_display_width==320 else ""}}>Low 320p</option><option value='balanced' {{"selected" if job.source.preview_display_width==640 else ""}}>Balanced 640p</option><option value='hd' {{"selected" if job.source.preview_display_width==1280 else ""}}>HD 720p</option><option value='full_hd' {{"selected" if job.source.preview_display_width==1920 else ""}}>Full HD 1080p</option><option value='qhd' {{"selected" if job.source.preview_display_width==2560 else ""}}>2K/QHD 1440p</option><option value='uhd_4k' {{"selected" if job.source.preview_display_width==3840 else ""}}>4K UHD</option></select><button type='button' onclick='applyPreviewQuality("{{job.source_id}}","{{job.id}}")'>Apply now</button></div>{% if job.mode=="record" and job.state in ["running","starting","pending"] %}<label>Roll recording to storage</label><form method='post' action='/sources/{{job.source_id}}/rollover-storage' class='inline'><select name='storage_id' required><option value=''>Choose same-worker storage…</option>{% for storage in storage_locations if storage.enabled and storage.worker_id==job.worker_id %}<option value='{{storage.id}}'>{{storage.name}} — {{storage.path}}</option>{% endfor %}</select><button class='control-test'>Roll safely</button></form>{% endif %}</div><div class='activity-indicator'><span id='all-dot-{{job.id}}' class='activity-dot inactive'></span><span id='all-activity-label-{{job.id}}'>INACTIVE</span></div></div>{% else %}<div class='card muted'>No jobs exist yet.</div>{% endfor %}</div>"""
    live_cfg = runtime_live_settings()
    script = """<script>
const liveAllIds={{job_ids|tojson}};
const audioSeconds={{audio_refresh_seconds|tojson}};
const previewSeconds={{preview_refresh_seconds|tojson}};
const audioMs=audioSeconds<=0?50:Math.max(50,Math.round(audioSeconds*1000));
const previewMs=previewSeconds<=0?100:Math.max(100,Math.round(previewSeconds*1000));
const audioMotion={{audio_motion|tojson}};
let currentLiveSettings={{runtime|tojson}};
async function applyPreviewQuality(sourceId,jobId){
  const select=document.getElementById('preview-quality-'+sourceId);
  if(!select)return;
  try{
    const response=await fetch('/api/sources/'+encodeURIComponent(sourceId)+'/preview-display',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset:select.value})});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||'Could not apply preview quality');
    const image=document.getElementById('all-preview-'+jobId);
    if(image){image.src='/previews/'+jobId+'.jpg?w='+data.width+'&q='+data.jpeg_quality+'&t='+Date.now();}
  }catch(error){window.alert(String(error));}
}
let lastPreviewRefresh=0,lastAudioRefresh=0;
function vicMeterMotion(meter,percent,mode){
  if(!meter)return;
  const target=Math.max(0,Math.min(100,Number(percent)||0));
  const previous=Number(meter.dataset.level||meter.style.width.replace('%','')||0);
  const rising=target>=previous;
  const timings={raw:[0,0],light:[20,90],medium:[45,180],heavy:[90,420]};
  const pair=timings[mode]||timings.light;
  const duration=rising?pair[0]:pair[1];
  meter.style.transitionProperty='width';
  meter.style.transitionTimingFunction=rising?'linear':'ease-out';
  meter.style.transitionDuration=duration+'ms';
  meter.dataset.level=String(target);
  meter.style.width=target+'%';
}
function setAllMeter(meter,percent){vicMeterMotion(meter,percent,audioMotion);}
async function refreshLiveAll(){try{const r=await fetch('/api/live-status',{cache:'no-store'});const d=await r.json();const map=new Map((d.jobs||[]).map(x=>[x.id,x]));const now=Date.now();const updateAudio=now-lastAudioRefresh>=audioMs;const updatePreview=now-lastPreviewRefresh>=previewMs;liveAllIds.forEach(id=>{const x=map.get(id);if(!x)return;const meter=document.getElementById('all-audio-meter-'+id);const text=document.getElementById('all-audio-text-'+id);if(updateAudio){setAllMeter(meter,x.audio_percent||0);if(text)text.textContent=x.audio_level_db===null?'No audio meter':x.audio_level_db.toFixed(1)+' dB';}document.getElementById('all-state-'+id).textContent=(x.state||'').toUpperCase();document.getElementById('all-health-'+id).textContent='FPS '+(x.actual_fps===null?'—':Number(x.actual_fps).toFixed(1))+'/'+(x.requested_fps??'Source')+' · Drop '+(x.dropped_frames||0)+' · '+(x.bitrate_mbps===null?'—':Number(x.bitrate_mbps).toFixed(1))+' Mbps · '+(x.encoder||'—');document.getElementById('all-message-'+id).textContent=x.message||'';let visual='inactive',label='INACTIVE';if(x.state==='running'&&x.mode==='record'){visual='recording';label='RECORDING';}else if(x.state==='running'&&x.mode==='preview'){visual='previewing';label='PREVIEW — UNLIMITED · NOT SAVING';}else if(x.state==='running'&&x.mode==='test'){visual='testing';label='TESTING';}else if(['pending','starting','stopping','waiting','waiting_available','waiting_reconnect'].includes(x.state)){visual='pending';label=(x.state||'').toUpperCase();}else if(x.state==='failed'){visual='failed';label='FAILED';}const card=document.getElementById('live-card-'+id);if(card){card.classList.remove('is-recording','is-previewing','is-testing','is-pending','is-failed');if(visual!=='inactive')card.classList.add('is-'+visual);}document.getElementById('all-dot-'+id).className='activity-dot '+visual;document.getElementById('all-activity-label-'+id).textContent=label;const preview=document.getElementById('all-preview-'+id);const placeholder=document.getElementById('all-placeholder-'+id);if(x.preview_available&&preview&&updatePreview){preview.style.display='block';if(placeholder)placeholder.style.display='none';const width=Number(x.preview_display_width||0)||Number((d.live_settings||{}).preview_width||640);const quality=Number(x.preview_display_jpeg_quality||0)||Number((d.live_settings||{}).preview_jpeg_quality||7);preview.src='/previews/'+id+'.jpg?w='+width+'&q='+quality+'&t='+now;}else if(!x.preview_available&&placeholder){placeholder.style.display='flex';}});if(updateAudio)lastAudioRefresh=now;if(updatePreview)lastPreviewRefresh=now;}catch(e){console.debug(e);}}
async function liveAllLoop(){await refreshLiveAll();setTimeout(liveAllLoop,Math.min(500,audioMs,previewMs));}
liveAllLoop();
</script>"""
    return render_template_string(page("Live All", body, script), jobs=data, job_ids=[item["id"] for item in data], stamp=time.time(), preview_types=PREVIEW_SOURCE_TYPES, auto_start_types=AUTO_START_SOURCE_TYPES, audio_refresh_seconds=live_cfg["audio_meter_refresh_seconds"], preview_refresh_seconds=live_cfg["preview_refresh_seconds"], audio_smoothing=live_cfg["audio_meter_smoothing"], audio_motion=live_cfg["audio_meter_motion"], runtime=live_cfg, storage_locations=storage_locations())


@app.get("/live/<job_id>")
def live_job(job_id: str):
    job = job_by_id(job_id)
    if not job:
        return "Job not found", 404
    body = """<div class='card'><div class='live-status-row'><div><h2>{{job.source_name}}</h2><p><strong>Worker:</strong> {{job.worker_name}}<br><strong>Status:</strong> <span id='state'>{{job.state}}</span><br><strong>Message:</strong> <span id='message'>{{job.message}}</span><br><strong>Output:</strong> <code id='output'>{{job.output}}</code></p></div><div class='inline'>{% if job.source.type in preview_types %}<form method='post' action='/sources/{{job.source_id}}/preview'><button class='preview-button'>Preview Unlimited</button></form>{% endif %}<form method='post' action='/sources/{{job.source_id}}/start'><button class='control-start'>Start recording</button></form>{% if job.source.type in auto_start_types %}<form method='post' action='/sources/{{job.source_id}}/auto-start'><button class='{{"control-stop" if job.source.auto_start_armed else "control-test"}}'>{{"Disarm Auto Start" if job.source.auto_start_armed else "Arm Auto Start"}}</button></form>{% endif %}<form method='post' action='/sources/{{job.source_id}}/stop'><button class='control-stop'>Stop</button></form></div></div></div><div class='card'><h3>Minimal health</h3><div id='individual-health' class='health-mini'>Waiting for statistics…</div></div><div class='card'><h3>Audio level</h3><div class='meter'><span id='meter'></span></div><p id='audioText' class='muted'>Waiting for meter data...</p></div><div class='card'><h3>Refreshed video preview</h3><img id='preview' class='preview' src='/previews/{{job.id}}.jpg?t={{stamp}}' onerror='this.style.display="none"'><p id='previewText' class='muted'>Preview stays active without a time limit and does not save a recording file.</p></div>"""
    live_cfg = runtime_live_settings()
    script = """<script>
const jobId={{job_id|tojson}};
const audioSeconds={{audio_refresh_seconds|tojson}};
const previewSeconds={{preview_refresh_seconds|tojson}};
const audioMs=audioSeconds<=0?50:Math.max(50,Math.round(audioSeconds*1000));
const previewMs=previewSeconds<=0?100:Math.max(100,Math.round(previewSeconds*1000));
const audioMotion={{audio_motion|tojson}};
function individualSizeText(bytes){const value=Math.max(0,Number(bytes)||0);if(value>=1073741824)return (value/1073741824).toFixed(2)+' GB';if(value>=1048576)return (value/1048576).toFixed(2)+' MB';if(value>=1024)return (value/1024).toFixed(1)+' KB';return value?Math.round(value)+' B':'—';}
function individualWriteText(bytesPerSecond){const value=Math.max(0,Number(bytesPerSecond)||0);return (value*8/1000000).toFixed(2)+' Mb/s · '+(value/1048576).toFixed(2)+' MB/s';}
let lastImageRefresh=0,lastAudioRefresh=0;
function vicMeterMotion(meter,percent,mode){
  if(!meter)return;
  const target=Math.max(0,Math.min(100,Number(percent)||0));
  const previous=Number(meter.dataset.level||meter.style.width.replace('%','')||0);
  const rising=target>=previous;
  const timings={raw:[0,0],light:[20,90],medium:[45,180],heavy:[90,420]};
  const pair=timings[mode]||timings.light;
  const duration=rising?pair[0]:pair[1];
  meter.style.transitionProperty='width';
  meter.style.transitionTimingFunction=rising?'linear':'ease-out';
  meter.style.transitionDuration=duration+'ms';
  meter.dataset.level=String(target);
  meter.style.width=target+'%';
}
function setIndividualMeter(percent){vicMeterMotion(document.getElementById('meter'),percent,audioMotion);}
async function refreshStatus(){try{const r=await fetch('/api/jobs/'+jobId+'/status',{cache:'no-store'});const d=await r.json();const now=Date.now();document.getElementById('state').textContent=(d.mode||'').toUpperCase()+' · '+(d.state||'').toUpperCase();document.getElementById('message').textContent=d.message||'';document.getElementById('output').textContent=d.output||'';if(now-lastAudioRefresh>=audioMs){setIndividualMeter(d.audio_percent||0);document.getElementById('audioText').textContent=d.audio_level_db===null?'No live audio meter is available.':d.audio_level_db.toFixed(1)+' dB';lastAudioRefresh=now;}document.getElementById('individual-health').textContent='FPS '+(d.actual_fps===null?'—':Number(d.actual_fps).toFixed(2))+'/'+(d.requested_fps??'Source')+' · Dropped '+(d.dropped_frames||0)+' · '+(d.bitrate_mbps===null?'—':Number(d.bitrate_mbps).toFixed(1))+' Mbps · Size '+individualSizeText(d.file_size_bytes)+' · Write '+individualWriteText(d.disk_write_bytes_per_second)+' · '+(d.encoder||'—');const img=document.getElementById('preview');if(d.preview_available&&now-lastImageRefresh>=previewMs){img.style.display='block';img.src='/previews/'+jobId+'.jpg?t='+now;lastImageRefresh=now;}}catch(e){document.getElementById('message').textContent='Dashboard status error: '+e;}}
async function individualLoop(){await refreshStatus();setTimeout(individualLoop,Math.min(500,audioMs,previewMs));}
individualLoop();
</script>"""
    return render_template_string(page("Live source", body, script), job=job, stamp=time.time(), job_id=job_id, preview_types=PREVIEW_SOURCE_TYPES, audio_refresh_seconds=live_cfg["audio_meter_refresh_seconds"], preview_refresh_seconds=live_cfg["preview_refresh_seconds"], audio_smoothing=live_cfg["audio_meter_smoothing"], audio_motion=live_cfg["audio_meter_motion"])


@app.get("/recordings")
def recordings_page():
    refresh_waiting_transfer_messages()
    worker_items = workers()
    for index, worker in enumerate(worker_items, start=1):
        worker["ui_id"] = f"worker-{index}"
        for item in worker.get("recordings", []) or []:
            item["protected"] = recording_is_protected(
                str(worker.get("id", "")),
                str(item.get("path", "")),
            )

    online_workers = [
        item for item in worker_items
        if item.get("online")
    ]
    local_worker = main_worker()
    remote_recording_count = sum(
        len(item.get("recordings", []))
        for item in worker_items
        if (
            item.get("online")
            and (
                not local_worker
                or item.get("id") != local_worker.get("id")
            )
        )
    )
    transfer_items = sorted(
        transfers(),
        key=lambda item: item.get("created_ts", 0),
        reverse=True,
    )[:200]

    body = """<div class='card'><div class='live-status-row'><div><h2 style='margin:0'>Recordings and file transfers</h2><p class='muted'>Move or copy one file, selected files, every file from one worker, or every remote recording to the Main PC. A <strong>Move</strong> deletes the original only after size and SHA-256 verification.</p></div>
<div class='live-global-controls'>
{% if main_worker and main_worker.online and remote_recording_count %}
<form method='post' action='/recordings/move-all-remote-to-main' onsubmit='return confirm("Move ALL {{remote_recording_count}} remote recording file(s) to the Main PC? Every original is deleted only after its destination copy is verified.")'><button class='control-start'>Move All Remote Files to Main PC</button></form>
{% endif %}
<form method='post' action='/recordings/delete-all' onsubmit='return confirm("Move ALL unprotected recording files from every online worker into each worker's VIC recycle bin?")'><button class='control-stop'>Recycle All Unprotected Everywhere</button></form>
</div></div></div>

{% if transfers %}<div class='card'><div class='live-status-row'><div><h3 style='margin:0'>File-transfer queue</h3><p class='muted'>Each worker can handle up to its configured parallel-transfer limit at the same time. Retry All restarts failed items and transfers whose progress has been stale for at least 30 seconds, while healthy active transfers continue uninterrupted.</p></div><div class='inline'><form method='post' action='/transfers/retry-all' onsubmit='return confirm("Retry every failed or stuck transfer? Healthy uploads and downloads will continue without restarting.")'><button class='control-start'>Retry All Failed / Stuck</button></form><form method='post' action='/transfers/clear-finished'><button>Clear finished transfers</button></form></div></div>
<table><tr><th>File / batch</th><th>From → To</th><th>Action</th><th>Status</th><th>Progress</th><th>Destination</th><th>Controls</th></tr>
{% for transfer in transfers %}<tr id='transfer-row-{{transfer.id}}'>
<td><strong>{{transfer.filename}}</strong><br><span class='small muted'>{{transfer.reported_size_mb}} MB{% if transfer.batch_id %}<br>Mass batch {{transfer.batch_index}} of {{transfer.batch_total}}{% endif %}</span></td>
<td>{{transfer.source_worker_name}}<br>→ {{transfer.target_worker_name}}</td>
<td>{{transfer.operation|upper}}</td>
<td id='transfer-status-cell-{{transfer.id}}' class='{{"good" if transfer.state=="completed" else "bad" if transfer.state in ["failed","source_delete_failed"] else "muted"}}'><strong id='transfer-state-{{transfer.id}}'>{{transfer.state.replace("_"," ")|upper}}</strong><br><span id='transfer-message-{{transfer.id}}' class='small'>{{transfer.message}}</span></td>
<td><div class='meter dashboard-meter'><span id='transfer-meter-{{transfer.id}}' style='width:{{transfer.progress_percent or 0}}%'></span></div><span id='transfer-percent-{{transfer.id}}' class='small'>{{transfer.progress_percent or 0}}%</span><br><span id='transfer-speed-{{transfer.id}}' class='small muted'>Waiting for live progress…</span></td>
<td><code id='transfer-destination-{{transfer.id}}'>{{transfer.destination_path or "—"}}</code></td>
<td><div class='inline'>
{% if transfer.state != "completed" %}
<form method='post' action='/transfers/{{transfer.id}}/retry' onsubmit='return confirm("Retry this transfer from the beginning? Any incomplete staged copy will be discarded; the original source recording is kept.")'><button class='control-start'>Retry now</button></form>
{% endif %}
{% if transfer.state not in ["completed","cancelled"] %}
<form method='post' action='/transfers/{{transfer.id}}/cancel' onsubmit='return confirm("Cancel this transfer? The original recording will be kept.")'><button class='control-stop'>Cancel</button></form>
{% endif %}
</div></td>
</tr>{% endfor %}</table></div>{% endif %}

{% for worker in workers %}
<div class='card'>
<div class='live-status-row'>
<div><h3 style='margin:0'>{{worker.display_name}}</h3><p class='muted'><code>{{worker.recordings_root or "Recording folder not reported"}}</code> · {{"ONLINE" if worker.online else "OFFLINE"}} · VIC {{worker.worker_version or "older/unknown"}} · {{worker.recordings|length}} file(s) · up to {{worker.transfer_parallel_limit or 1}} parallel · {{worker.transfer_chunk_mb or 1}} MB chunks</p></div>
<div class='inline'>
<form method='post' action='/workers/{{worker.id}}/open-recordings'><button>Open folder on worker</button></form>
<form method='post' action='/workers/{{worker.id}}/delete-recordings-all' onsubmit='return confirm("Move all unprotected recordings from {{worker.display_name}} into its VIC recycle bin?")'><button class='control-stop' {{"disabled" if not worker.online else ""}}>Recycle All Unprotected</button></form>
</div>
</div>

{% if worker.online and online_workers|length > 1 and worker.recordings %}
<form id='mass-form-{{worker.ui_id}}' method='post' action='/recordings/transfer-batch' onsubmit='return confirmMassTransfer(event,this,"{{worker.display_name}}")'>
<input type='hidden' name='source_worker_id' value='{{worker.id}}'>
<div class='card' style='margin:12px 0;background:var(--panel2)'>
<div class='live-status-row'>
<div><strong>Mass copy or move</strong><br><span class='small muted'><span id='selected-count-{{worker.ui_id}}'>0</span> selected. Up to {{worker.transfer_parallel_limit or 1}} files transfer at the same time from this worker.</span></div>
<div class='live-global-controls'>
<select name='target_worker_id' required>
{% for target in online_workers %}{% if target.id != worker.id %}
<option value='{{target.id}}'>{{target.display_name}}{{" — Main PC" if target.is_local_dashboard else ""}}</option>
{% endif %}{% endfor %}
</select>
<button name='action' value='copy_selected'>Copy Selected</button>
<button class='control-start' name='action' value='move_selected'>Move Selected</button>
<button name='action' value='copy_all'>Copy All</button>
<button class='control-start' name='action' value='move_all'>Move All</button>
{% if main_worker and main_worker.online and worker.id != main_worker.id %}
<button class='control-start' name='action' value='move_all_main'>Move All to Main PC</button>
{% endif %}
</div>
</div>
</div>
</form>
{% endif %}

<table><tr><th style='width:45px'>{% if worker.online and online_workers|length > 1 and worker.recordings %}<input type='checkbox' title='Select or clear every file on this worker' onclick='toggleWorkerFiles("mass-form-{{worker.ui_id}}",this.checked)'>{% endif %}</th><th>File</th><th>Folder</th><th>Size</th><th>Modified</th><th>Transfer</th><th>Delete</th></tr>
{% for item in worker.recordings or [] %}
<tr class='recording-row'>
<td>{% if worker.online and online_workers|length > 1 %}<input type='checkbox' name='paths' value='{{item.path}}' form='mass-form-{{worker.ui_id}}' onchange='updateSelectedCount("mass-form-{{worker.ui_id}}","selected-count-{{worker.ui_id}}")'>{% endif %}</td>
<td><strong>{{item.name}}</strong> {% if item.protected %}<span class='tag good'>PROTECTED</span>{% endif %}<br><code class='small'>{{item.path}}</code></td>
<td>{{item.relative}}</td><td>{{item.size_mb}} MB</td><td>{{item.modified}}</td>
<td>{% if worker.online and online_workers|length > 1 %}
<form method='post' action='/recordings/transfer' onsubmit='return confirm("Transfer {{item.name}}? A Move deletes the original only after verification.")'>
<input type='hidden' name='source_worker_id' value='{{worker.id}}'>
<input type='hidden' name='path' value='{{item.path}}'>
<select name='target_worker_id' required>{% for target in online_workers %}{% if target.id != worker.id %}<option value='{{target.id}}'>{{target.display_name}}{{" — Main PC" if target.is_local_dashboard else ""}}</option>{% endif %}{% endfor %}</select>
<div class='inline' style='margin-top:7px'><button name='operation' value='copy'>Copy to worker</button><button class='control-start' name='operation' value='move'>Move to worker</button></div>
</form>
{% if main_worker and worker.id != main_worker.id and main_worker.online %}
<form method='post' action='/recordings/transfer' style='margin-top:7px' onsubmit='return confirm("Move {{item.name}} to the Main PC? The original is deleted only after verification.")'>
<input type='hidden' name='source_worker_id' value='{{worker.id}}'>
<input type='hidden' name='target_worker_id' value='{{main_worker.id}}'>
<input type='hidden' name='path' value='{{item.path}}'>
<button class='control-start' name='operation' value='move'>Move to Main PC</button>
</form>{% endif %}
{% else %}<span class='small muted'>Another online worker is required.</span>{% endif %}</td>
<td><div class='inline'>{% if item.protected %}<form method='post' action='/recordings/unprotect'><input type='hidden' name='worker_id' value='{{worker.id}}'><input type='hidden' name='path' value='{{item.path}}'><button>Unprotect</button></form>{% else %}<form method='post' action='/recordings/protect'><input type='hidden' name='worker_id' value='{{worker.id}}'><input type='hidden' name='path' value='{{item.path}}'><button>Protect</button></form><form method='post' action='/workers/{{worker.id}}/delete-recording' onsubmit='return confirm("Move {{item.name}} into the VIC recycle bin? It can be restored later.")'><input type='hidden' name='path' value='{{item.path}}'><button class='control-stop' {{"disabled" if not worker.online else ""}}>Recycle</button></form>{% endif %}</div></td>
</tr>
{% else %}<tr><td colspan='7' class='muted'>No recordings reported by this worker.</td></tr>{% endfor %}
</table>
{% if worker.recycle_bin %}<details style='margin-top:14px'><summary><strong>VIC Recycle Bin — {{worker.recycle_bin|length}} file(s)</strong></summary><div class='inline' style='margin:10px 0'><form method='post' action='/workers/{{worker.id}}/empty-recycle-bin' onsubmit='return confirm("Permanently empty this worker recycle bin? This cannot be undone.")'><button class='control-stop'>Empty Recycle Bin Permanently</button></form></div><table><tr><th>Recycled file</th><th>Original location</th><th>Size</th><th>Recycled</th><th>Actions</th></tr>{% for recycled in worker.recycle_bin %}<tr><td>{{recycled.name}}</td><td><code>{{recycled.original_path or "Unknown"}}</code></td><td>{{recycled.size_mb}} MB</td><td>{{recycled.recycled}}</td><td><div class='inline'><form method='post' action='/workers/{{worker.id}}/restore-recycled'><input type='hidden' name='path' value='{{recycled.path}}'><button class='control-start'>Restore</button></form><form method='post' action='/workers/{{worker.id}}/delete-recycled' onsubmit='return confirm("Permanently delete this recycled file? This cannot be undone.")'><input type='hidden' name='path' value='{{recycled.path}}'><button class='control-stop'>Delete permanently</button></form></div></td></tr>{% endfor %}</table></details>{% endif %}
</div>
{% else %}<div class='card muted'>No worker information is available.</div>{% endfor %}"""

    script = """<script>
function massCheckboxes(formId){
  return [...document.querySelectorAll(
    'input[type="checkbox"][name="paths"][form="'+formId+'"]'
  )];
}
function toggleWorkerFiles(formId,checked){
  massCheckboxes(formId).forEach(box=>box.checked=checked);
  const countId='selected-count-'+formId.replace('mass-form-','');
  updateSelectedCount(formId,countId);
}
function updateSelectedCount(formId,countId){
  const selected=massCheckboxes(formId).filter(box=>box.checked).length;
  const output=document.getElementById(countId);
  if(output)output.textContent=selected;
}
function confirmMassTransfer(event,form,workerName){
  const action=event.submitter?.value||'move_selected';
  const selected=massCheckboxes(form.id).filter(box=>box.checked).length;
  const needsSelection=action.endsWith('_selected');
  if(needsSelection && selected===0){
    alert('Select at least one recording first.');
    return false;
  }
  const operation=action.startsWith('copy_')?'COPY':'MOVE';
  const amount=needsSelection?selected:'ALL';
  const destination=action==='move_all_main'
    ?'the Main PC'
    :(form.querySelector('[name="target_worker_id"]')?.selectedOptions[0]?.text||'the selected worker');
  const warning=operation==='MOVE'
    ?' Originals are deleted only after each destination file is verified.'
    :' Originals will be kept.';
  return confirm(
    operation+' '+amount+' recording file(s) from '+workerName+
    ' to '+destination+'?'+warning
  );
}
function transferSize(bytes){
  const value=Number(bytes||0);
  if(value>=1073741824)return (value/1073741824).toFixed(2)+' GB';
  if(value>=1048576)return (value/1048576).toFixed(1)+' MB';
  if(value>=1024)return (value/1024).toFixed(1)+' KB';
  return value+' B';
}
function transferDuration(seconds){
  if(seconds===null||seconds===undefined||!Number.isFinite(Number(seconds)))return 'estimating…';
  let value=Math.max(0,Math.round(Number(seconds)));
  const h=Math.floor(value/3600);value%=3600;
  const m=Math.floor(value/60);const s=value%60;
  return (h?h+'h ':'')+(m?m+'m ':'')+s+'s';
}
async function refreshTransferQueue(){
  try{
    const response=await fetch('/api/transfers/status',{cache:'no-store'});
    const payload=await response.json();
    (payload.transfers||[]).forEach(item=>{
      const meter=document.getElementById('transfer-meter-'+item.id);
      if(!meter)return;
      const percent=Math.max(0,Math.min(100,Number(item.progress_percent||0)));
      meter.style.width=percent+'%';
      const percentText=document.getElementById('transfer-percent-'+item.id);
      if(percentText)percentText.textContent=percent.toFixed(percent%1?1:0)+'%';
      const state=document.getElementById('transfer-state-'+item.id);
      if(state)state.textContent=String(item.state||'unknown').replaceAll('_',' ').toUpperCase();
      const message=document.getElementById('transfer-message-'+item.id);
      if(message)message.textContent=item.message||'';
      const cell=document.getElementById('transfer-status-cell-'+item.id);
      if(cell)cell.className=item.state==='completed'?'good':['failed','source_delete_failed'].includes(item.state)?'bad':'muted';
      const destination=document.getElementById('transfer-destination-'+item.id);
      if(destination)destination.textContent=item.destination_path||'—';
      const speed=document.getElementById('transfer-speed-'+item.id);
      if(speed){
        const rate=Number(item.bytes_per_second||0);
        const done=Number(item.bytes_done||0);
        const total=Number(item.total_bytes||0);
        const parts=[];
        if(total)parts.push(transferSize(done)+' / '+transferSize(total));
        if(rate>0)parts.push(transferSize(rate)+'/s');
        if(rate>0&&item.eta_seconds!==null)parts.push('about '+transferDuration(item.eta_seconds)+' remaining');
        speed.textContent=parts.join(' · ')||(item.state==='completed'?'Finished':'Waiting for byte progress…');
      }
    });
  }catch(error){console.debug('Transfer progress refresh failed',error);}
}
refreshTransferQueue();
setInterval(refreshTransferQueue,250);
</script>"""

    return render_template_string(
        page("Recordings", body, script),
        workers=worker_items,
        online_workers=online_workers,
        main_worker=local_worker,
        remote_recording_count=remote_recording_count,
        transfers=transfer_items,
    )


@app.get("/storage")
def storage_page():
    worker_items = workers()
    worker_map = {str(item.get("id", "")): item for item in worker_items}
    active = jobs()
    locations = storage_locations()
    for item in locations:
        worker = worker_map.get(str(item.get("worker_id", "")), {})
        item["worker_name"] = worker.get("display_name", item.get("worker_id", "Unknown worker"))
        item["worker_online"] = bool(worker.get("online"))
        item["latest_benchmark"] = next(
            (
                job
                for job in sorted(active, key=lambda x: x.get("updated_ts", 0), reverse=True)
                if job.get("mode") == "disk_benchmark"
                and str((job.get("source", {}).get("options", {}) or {}).get("storage_id", "")) == str(item.get("id", ""))
            ),
            None,
        )
    for worker in worker_items:
        worker["recording_size_gb"] = round(
            sum(float(item.get("size_mb", 0) or 0) for item in worker.get("recordings", []) or []) / 1024.0,
            2,
        )
        rates = [
            float(job.get("disk_per_hour_gb", 0) or 0)
            for job in active
            if job.get("worker_id") == worker.get("id")
            and job_is_active(job)
            and float(job.get("disk_per_hour_gb", 0) or 0) > 0
        ]
        total_rate = sum(rates)
        worker["estimated_hours"] = round(float(worker.get("disk_free_gb", 0) or 0) / total_rate, 1) if total_rate > 0 else None
    body = """<div class='card'><div class='live-status-row'><div><h2 style='margin:0'>Advanced Storage Manager</h2><p class='muted'>Save named paths on specific workers, benchmark them, choose them in source settings, and roll an active recording to another path safely.</p></div><a class='btn control-start' href='/storage/add'>+ Add Storage Location</a></div><div class='settings-note small'><strong>Safety:</strong> VIC never moves an open file. Roll recording closes the current segment, starts a new segment on the selected storage, then verifies and moves the closed segment.</div></div>
<div class='card'><h3>Saved storage locations</h3><table><tr><th>Name</th><th>Worker</th><th>Path</th><th>Latest speed</th><th>Actions</th></tr>{% for storage in locations %}<tr><td><strong>{{storage.name}}</strong><br><span class='{{"good" if storage.enabled else "bad"}}'>{{"ENABLED" if storage.enabled else "DISABLED"}}</span></td><td>{{storage.worker_name}}<br><span class='{{"good" if storage.worker_online else "bad"}}'>{{"ONLINE" if storage.worker_online else "OFFLINE"}}</span></td><td><code>{{storage.path}}</code><br><span class='small muted'>{{storage.notes or "—"}}</span></td><td>{% if storage.latest_benchmark %}Write {{storage.latest_benchmark.benchmark_write_mbps or "—"}} MB/s<br>Read {{storage.latest_benchmark.benchmark_read_mbps or "—"}} MB/s<br><span class='small muted'>{{storage.latest_benchmark.message}}</span>{% else %}—{% endif %}</td><td><div class='inline'><form method='post' action='/storage/{{storage.id}}/benchmark'><button {{"disabled" if not storage.worker_online else ""}}>Speed Test</button></form><form method='post' action='/storage/{{storage.id}}/open'><button {{"disabled" if not storage.worker_online else ""}}>Open</button></form><form method='post' action='/storage/{{storage.id}}/toggle'><button>{{"Disable" if storage.enabled else "Enable"}}</button></form><form method='post' action='/storage/{{storage.id}}/remove' onsubmit='return confirm("Remove this saved storage location? Existing files are not deleted.")'><button class='control-stop'>Remove</button></form></div></td></tr>{% else %}<tr><td colspan='5' class='muted'>No custom storage locations yet.</td></tr>{% endfor %}</table></div>
<div class='card'><h3>Detected drives reported by workers</h3><p class='small muted'>Use these details to choose the correct path when adding storage. Remove only removes a VIC saved location; it never ejects or formats a drive.</p><div class='grid'>{% for worker in workers %}<div class='card'><h3>{{worker.display_name}}</h3><p class='{{"good" if worker.online else "bad"}}'>{{"ONLINE" if worker.online else "OFFLINE"}}</p><p><strong>Default recording path:</strong> <code>{{worker.recordings_root or "—"}}</code><br><strong>Estimated active recording time:</strong> {{worker.estimated_hours ~ " hours" if worker.estimated_hours is not none else "No active estimate"}}</p>{% for drive in worker.drives or [] %}<div class='settings-note'><strong>{{drive.name or drive.path}}</strong> <span class='tag'>{{drive.fstype or drive.type or "drive"}}</span><br><code>{{drive.path}}</code><br>{{drive.used_gb}} GB used / {{drive.total_gb}} GB · <span class='{{"bad" if drive.free_gb < 50 else "good"}}'>{{drive.free_gb}} GB free</span> · {{drive.percent}}%<form method='post' action='/workers/{{worker.id}}/benchmark-path' class='inline'><input type='hidden' name='path' value='{{drive.path}}'><button {{"disabled" if not worker.online else ""}}>Test this drive</button></form></div>{% else %}<p class='muted'>No drive inventory reported yet. Restart/update this worker.</p>{% endfor %}</div>{% else %}<div class='card muted'>No workers reported.</div>{% endfor %}</div></div>"""
    return render_template_string(page("Storage Manager", body), workers=worker_items, locations=locations)


@app.get("/storage/add")
def add_storage_form():
    body = """<div class='card'><h2>Add Storage Location</h2><p class='muted'>Enter a path that exists on the selected worker. Windows examples: <code>D:\\VIC Recordings</code> or <code>\\\\NAS\\Recordings</code>.</p><form method='post'><label>Name</label><input name='name' required placeholder='Fast SSD, Archive HDD, NAS'><label>Worker</label><select name='worker_id' required>{% for worker in workers %}<option value='{{worker.id}}'>{{worker.display_name}} — {{"ONLINE" if worker.online else "OFFLINE"}}</option>{% endfor %}</select><label>Path/address on that worker</label><input name='path' required placeholder='D:\\VIC Recordings'><label>Notes</label><textarea name='notes' rows='3' placeholder='Drive model, cable, purpose or limits'></textarea><div class='inline'><button class='control-start'>Save Storage</button><a class='btn' href='/storage'>Cancel</a></div></form></div>"""
    return render_template_string(page("Add Storage", body), workers=workers())


@app.post("/storage/add")
def save_storage_location():
    name = request.form.get("name", "").strip()
    worker_id = request.form.get("worker_id", "").strip()
    path = request.form.get("path", "").strip()
    if not name or not worker_id or not path:
        flash("Name, worker and path are required.", "bad")
        return redirect("/storage/add")
    data = storage_locations()
    data.append({
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "worker_id": worker_id,
        "path": path,
        "notes": request.form.get("notes", "").strip(),
        "enabled": True,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_json(STORAGES_FILE, data)
    flash(f'Storage location "{name}" saved. Run Speed Test before using it for important recordings.', "good")
    return redirect("/storage")


@app.post("/storage/<storage_id>/toggle")
def toggle_storage_location(storage_id: str):
    data = storage_locations()
    changed = False
    enabled = False
    for item in data:
        if str(item.get("id", "")) == storage_id:
            enabled = not bool(item.get("enabled", True))
            item["enabled"] = enabled
            changed = True
    if changed:
        save_json(STORAGES_FILE, data)
        flash("Storage enabled." if enabled else "Storage disabled. Existing files were not changed.", "good")
    else:
        flash("Storage location not found.", "bad")
    return redirect("/storage")


@app.post("/storage/<storage_id>/remove")
def remove_storage_location(storage_id: str):
    before = storage_locations()
    after = [item for item in before if str(item.get("id", "")) != storage_id]
    if len(after) == len(before):
        flash("Storage location not found.", "bad")
        return redirect("/storage")
    save_json(STORAGES_FILE, after)
    # Sources fall back to their worker default if a saved location is removed.
    source_data = sources()
    source_changed = False
    for source in source_data:
        if str(source.get("storage_id", "")) == storage_id:
            source["storage_id"] = ""
            source_changed = True
    if source_changed:
        save_json(SOURCES_FILE, source_data)
    flash("Saved storage location removed. No recording files were deleted.", "good")
    return redirect("/storage")


def queue_storage_control(storage_id: str, mode: str, label: str) -> tuple[bool, str]:
    storage = storage_by_id(storage_id)
    if not storage:
        return False, "Storage location not found."
    worker = worker_by_id(str(storage.get("worker_id", "")))
    if not worker or not worker.get("online"):
        return False, "The storage worker is offline."
    create_worker_control_job(worker, mode, label, {
        "path": storage.get("path", ""),
        "storage_id": storage_id,
        "storage_name": storage.get("name", "Storage"),
        "size_mb": 128,
    })
    return True, "Command sent."


@app.post("/storage/<storage_id>/benchmark")
def benchmark_storage_location(storage_id: str):
    ok, message = queue_storage_control(storage_id, "disk_benchmark", "Test saved storage speed")
    flash("Storage speed test sent. Refresh this page after it finishes." if ok else message, "good" if ok else "bad")
    return redirect("/storage")


@app.post("/storage/<storage_id>/open")
def open_storage_location(storage_id: str):
    ok, message = queue_storage_control(storage_id, "open_storage_path", "Open saved storage path")
    flash("Open-folder command sent to that worker." if ok else message, "good" if ok else "bad")
    return redirect("/storage")


@app.post("/workers/<worker_id>/benchmark-path")
def benchmark_worker_path(worker_id: str):
    worker = worker_by_id(worker_id)
    path = request.form.get("path", "").strip()
    if not worker or not worker.get("online"):
        flash("That worker is offline.", "bad")
        return redirect("/storage")
    create_worker_control_job(worker, "disk_benchmark", "Test detected drive speed", {"size_mb": 128, "path": path})
    flash("Drive benchmark sent.", "good")
    return redirect("/storage")


@app.post("/workers/<worker_id>/benchmark-disk")
def benchmark_worker_disk(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("That worker is offline.", "bad")
        return redirect("/storage")
    create_worker_control_job(worker, "disk_benchmark", "Test recording-drive speed", {"size_mb": 128})
    flash("Disk benchmark sent. Refresh Storage after it finishes.", "good")
    return redirect("/storage")


def build_config_backup_zip() -> Path:
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    target = EXPORTS_DIR / f"VIC_Config_Backup_{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in CONFIG.glob("*.json"):
            archive.write(path, Path("config") / path.name)
        archive.writestr("BACKUP_INFO.txt", "VIC portable configuration backup. Recordings are not included.\n")
    return target


def build_worker_copy_zip() -> Path:
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    target = EXPORTS_DIR / f"VIC_Worker_Copy_v0.8.7_{stamp}.zip"
    excluded_roots = {"worker_recordings", "recordings", "logs", "dashboard_previews", "transfer_staging", "config_backups", "exports", "rollback"}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in BASE.rglob("*"):
            if not path.is_file() or path == target:
                continue
            relative = path.relative_to(BASE)
            if relative.parts and relative.parts[0] in excluded_roots:
                continue
            if "__pycache__" in relative.parts:
                continue
            archive.write(path, Path("VIC_v0_8_7_Worker_Copy") / relative)
        archive.writestr("VIC_v0_8_7_Worker_Copy/WORKER_COPY_README.txt", "SECOND PC SETUP\n================\n\n1. Extract this complete folder on the second PC.\n2. Run REPAIR_AND_SETUP_WORKER.bat.\n3. In the setup window press Test Address + Token.\n4. Press Save and Start Worker.\n\nThis copy already contains the Main Dashboard cluster token. No recordings or logs are included.\n")
    return target


def build_support_zip() -> Path:
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    target = EXPORTS_DIR / f"VIC_Support_{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (BASE / "logs").glob("*"):
            if path.is_file():
                archive.write(path, Path("logs") / path.name)
        safe_sources = []
        for source in sources():
            item = json.loads(json.dumps(source))
            options = item.get("options", {})
            for key in ("url", "cookies_file", "browser_profile"):
                if key in options and options[key]:
                    options[key] = "[REMOVED FOR SUPPORT ZIP]"
            safe_sources.append(item)
        archive.writestr("sanitised_sources.json", json.dumps(safe_sources, indent=2))
        archive.writestr("workers.json", json.dumps(workers(), indent=2))
        archive.writestr("recent_jobs.json", json.dumps(sorted(jobs(), key=lambda item: item.get("updated_ts", 0), reverse=True)[:100], indent=2))
        archive.writestr("recent_transfers.json", json.dumps(sorted(transfers(), key=lambda item: item.get("updated_ts", 0), reverse=True)[:100], indent=2))
    return target





def refresh_value_from_form(
    preset: str,
    custom_value: Any,
    default: float,
) -> tuple[str, float]:
    preset = str(preset or "custom")
    if preset in REFRESH_PRESETS:
        return preset, REFRESH_PRESETS[preset]
    return "custom", clamp_refresh_seconds(custom_value, default)


@app.get("/settings")
def settings_page():
    cfg = settings()
    runtime = runtime_live_settings()
    body = """<div class='card'><div class='live-status-row'><div><h2 style='margin:0'>Live display settings</h2><p class='muted'>Control how quickly audio meters and preview images update. All values stay in this portable VIC folder.</p></div><span class='tag'>Preview is unlimited until Stop or Start Recording</span></div></div>
<form method='post' action='/settings'>
<div class='settings-grid'>
<div class='card'><h3>Audio visual refresh</h3><label>Refresh preset</label><select id='audioRefreshPreset' name='audio_refresh_preset' onchange='refreshPresetChanged("audio")'><option value='instant' {{"selected" if cfg.audio_meter_refresh_preset=="instant" else ""}}>Instant — 0.00 seconds</option><option value='fast' {{"selected" if cfg.audio_meter_refresh_preset=="fast" else ""}}>Fast — 0.20 seconds</option><option value='mid' {{"selected" if cfg.audio_meter_refresh_preset=="mid" else ""}}>Mid — 0.50 seconds</option><option value='slow' {{"selected" if cfg.audio_meter_refresh_preset=="slow" else ""}}>Slow — 1.00 second</option><option value='custom' {{"selected" if cfg.audio_meter_refresh_preset=="custom" else ""}}>Custom</option></select><label>Custom refresh time (0.00–10.00 seconds)</label><input id='audioRefreshCustom' name='audio_refresh_custom' type='number' min='0' max='10' step='0.01' value='{{"%.2f"|format(cfg.audio_meter_refresh_seconds)}}'><label>Meter movement</label><select name='audio_meter_motion'><option value='raw' {{"selected" if cfg.audio_meter_motion=="raw" else ""}}>Raw — exact jumps, no animation</option><option value='light' {{"selected" if cfg.audio_meter_motion=="light" else ""}}>Light — very fast rise, quick fall (recommended)</option><option value='medium' {{"selected" if cfg.audio_meter_motion=="medium" else ""}}>Medium — smoother movement</option><option value='heavy' {{"selected" if cfg.audio_meter_motion=="heavy" else ""}}>Heavy — slowest movement</option></select><div class='settings-note small'>0.00 requests the fastest safe worker/browser update. Light follows incoming audio closely: approximately 20 ms attack and 90 ms release. Silence now returns to zero instead of holding the previous peak.</div></div>
<div class='card'><h3>Live preview update</h3><label>Preset</label><select id='previewRefreshPreset' name='preview_refresh_preset' onchange='refreshPresetChanged("preview")'><option value='instant' {{"selected" if cfg.preview_refresh_preset=="instant" else ""}}>Instant — 0.00 seconds</option><option value='fast' {{"selected" if cfg.preview_refresh_preset=="fast" else ""}}>Fast — 0.20 seconds</option><option value='mid' {{"selected" if cfg.preview_refresh_preset=="mid" else ""}}>Mid — 0.50 seconds</option><option value='slow' {{"selected" if cfg.preview_refresh_preset=="slow" else ""}}>Slow — 1.00 second</option><option value='custom' {{"selected" if cfg.preview_refresh_preset=="custom" else ""}}>Custom</option></select><label>Custom preview time (0.00–10.00 seconds)</label><input id='previewRefreshCustom' name='preview_refresh_custom' type='number' min='0' max='10' step='0.01' value='{{"%.2f"|format(cfg.preview_refresh_seconds)}}'><div class='settings-note small'>This controls Dashboard, Live All and individual live image refresh. 0.00 is capped at 10 preview frames per second to protect recording performance.</div></div>
<div class='card'><h3>Live preview quality</h3><label>Quality preset</label><select id='previewQuality' name='preview_quality' onchange='qualityChanged()'><option value='lowest' {{"selected" if cfg.preview_quality=="lowest" else ""}}>Lowest — 160 px</option><option value='low' {{"selected" if cfg.preview_quality=="low" else ""}}>Low — 320 px</option><option value='balanced' {{"selected" if cfg.preview_quality=="balanced" else ""}}>Balanced — 640 px</option><option value='hd' {{"selected" if cfg.preview_quality=="hd" else ""}}>HD 720p — 1280 px</option><option value='full_hd' {{"selected" if cfg.preview_quality=="full_hd" else ""}}>Full HD 1080p — 1920 px</option><option value='qhd' {{"selected" if cfg.preview_quality=="qhd" else ""}}>QHD 1440p — 2560 px</option><option value='uhd_4k' {{"selected" if cfg.preview_quality=="uhd_4k" else ""}}>4K UHD — 3840 px</option><option value='custom' {{"selected" if cfg.preview_quality=="custom" else ""}}>Custom — up to 4096 px</option></select><label>Custom preview width (160–4096 pixels)</label><input id='previewWidth' name='preview_width' type='number' min='160' max='4096' step='2' value='{{cfg.preview_width}}'><label>Custom JPEG quality (1 best, 31 smallest)</label><input id='previewJpegQuality' name='preview_jpeg_quality' type='number' min='1' max='31' value='{{cfg.preview_jpeg_quality}}'><div class='settings-note small'>4K preview can use substantial CPU/GPU and network bandwidth, especially with fast refresh or Live All. Changing this setting updates active Live/Live All images automatically. VIC resizes the delivered preview without stopping the recording. The FFmpeg capture-size reduction itself applies on the next capture start.</div></div>
<div class='card'><h3>Dashboard zoom and fullscreen</h3><p class='muted'>This rescales the complete VIC interface on every screen: cards, previews, audio meters, tables, forms and navigation.</p><div class='vic-settings-zoom-row'><button type='button' id='vicSettingsZoomOut'>−</button><input id='vicSettingsZoomSlider' type='range' min='40' max='200' step='5' value='100' aria-label='Dashboard zoom percentage'><button type='button' id='vicSettingsZoomIn'>+</button></div><div class='inline'><span id='vicSettingsZoomValue' class='tag'>100%</span><button type='button' id='vicSettingsZoomDefault'>Default 100%</button><button type='button' id='vicSettingsFullscreen'>Fullscreen</button></div><div class='settings-note small'>Range: 40%–200%. Use Ctrl + mouse wheel anywhere, roll the wheel over either zoom slider, or use Ctrl + / Ctrl − / Ctrl 0. Your level is remembered across every VIC page and synchronised with other browser tabs.</div></div>
</div>
<div class='card'><div class='inline'><button class='control-start'>Save live settings</button><a class='btn' href='/live/all'>Open Live All</a></div><p class='small muted'>Current worker preview generation: {{runtime.preview_fps}} FPS · {{runtime.preview_width}} px · JPEG q{{runtime.preview_jpeg_quality}}. Preview duration: unlimited.</p></div>
</form>"""
    script = """<script>
const presetValues={instant:0,fast:0.2,mid:0.5,slow:1};
function refreshPresetChanged(kind){
  const preset=document.getElementById(kind==='audio'?'audioRefreshPreset':'previewRefreshPreset');
  const custom=document.getElementById(kind==='audio'?'audioRefreshCustom':'previewRefreshCustom');
  if(preset&&custom&&Object.prototype.hasOwnProperty.call(presetValues,preset.value))custom.value=presetValues[preset.value].toFixed(2);
}
const qualityValues={lowest:[160,18],low:[320,12],balanced:[640,7],hd:[1280,5],full_hd:[1920,4],qhd:[2560,4],uhd_4k:[3840,3]};
function qualityChanged(){
  const quality=document.getElementById('previewQuality');
  if(!quality||!qualityValues[quality.value])return;
  const values=qualityValues[quality.value];
  document.getElementById('previewWidth').value=values[0];
  document.getElementById('previewJpegQuality').value=values[1];
}
</script>"""
    return render_template_string(
        page("Settings", body, script),
        cfg=cfg,
        runtime=runtime,
    )


@app.post("/settings")
def save_settings_page():
    cfg = settings()
    audio_preset, audio_seconds = refresh_value_from_form(
        request.form.get("audio_refresh_preset", "fast"),
        request.form.get("audio_refresh_custom", "0.2"),
        0.2,
    )
    preview_preset, preview_seconds = refresh_value_from_form(
        request.form.get("preview_refresh_preset", "mid"),
        request.form.get("preview_refresh_custom", "0.5"),
        0.5,
    )
    quality = str(request.form.get("preview_quality", "balanced"))
    if quality in PREVIEW_QUALITY_PRESETS:
        quality_values = PREVIEW_QUALITY_PRESETS[quality]
        preview_width = int(quality_values["width"])
        jpeg_quality = int(quality_values["jpeg_quality"])
    else:
        quality = "custom"
        try:
            preview_width = max(
                160,
                min(4096, int(request.form.get("preview_width", 640))),
            )
        except (TypeError, ValueError):
            preview_width = 640
        try:
            jpeg_quality = max(
                1,
                min(31, int(request.form.get("preview_jpeg_quality", 7))),
            )
        except (TypeError, ValueError):
            jpeg_quality = 7

    cfg.update({
        "audio_meter_refresh_preset": audio_preset,
        "audio_meter_refresh_seconds": audio_seconds,
        "audio_meter_motion": (
            str(request.form.get("audio_meter_motion", "light"))
            if str(request.form.get("audio_meter_motion", "light"))
            in AUDIO_METER_MOTION_MODES
            else "light"
        ),
        "audio_meter_smoothing": (
            str(request.form.get("audio_meter_motion", "light")) != "raw"
        ),
        "preview_refresh_preset": preview_preset,
        "preview_refresh_seconds": preview_seconds,
        "preview_quality": quality,
        "preview_width": preview_width,
        "preview_jpeg_quality": jpeg_quality,
    })
    save_json(SETTINGS_FILE, cfg)
    flash(
        "Live settings saved. Open Live pages update their delivered image quality automatically without stopping recordings. "
        "The worker FFmpeg preview generation size changes on the next capture start.",
        "good",
    )
    return redirect("/settings")


@app.get("/tools")
def portable_tools_page():
    body = """<div class='card'><h2>Portable Tools</h2><p class='muted'>Everything stays inside the VIC folder. These tools do not require a database server or permanent Windows service.</p><div class='grid'><div class='card'><h3>Configuration backup</h3><p>Download sources, worker settings and queue configuration without recordings.</p><a class='btn' href='/tools/config-backup'>Download Config Backup</a></div><div class='card'><h3>Prepare Worker Copy</h3><p>Create a clean matching worker ZIP with the cluster token and program files, but no recordings, logs or transfer staging.</p><a class='btn' href='/tools/worker-copy'>Download Worker Copy ZIP</a></div><div class='card'><h3>Support package</h3><p>Create logs and diagnostics with website URLs and cookie paths removed from the source export.</p><a class='btn' href='/tools/support-zip'>Download Support ZIP</a></div><div class='card'><h3>Rollback</h3><p>This experimental folder contains the complete original v0.5.1 ZIP in <code>rollback</code>. Run <code>ROLLBACK_TO_V0_5_1.bat</code> to extract it beside this folder.</p></div></div></div>"""
    return render_template_string(page("Portable Tools", body))


@app.get("/tools/config-backup")
def download_config_backup():
    return send_file(build_config_backup_zip(), as_attachment=True)


@app.get("/tools/worker-copy")
def download_worker_copy():
    return send_file(build_worker_copy_zip(), as_attachment=True)


@app.get("/tools/support-zip")
def download_support_zip():
    return send_file(build_support_zip(), as_attachment=True)


@app.get("/jobs")
def jobs_page():
    data = sorted(
        [item for item in jobs() if not item.get("temporary")],
        key=lambda item: item.get("created_ts", 0),
        reverse=True,
    )[:150]
    body = """<div class='card'><div class='live-status-row'><div><h2 style='margin:0'>Recent jobs</h2><p class='muted'>History deletion removes VIC entries and cached previews, not saved media files.</p></div>
<div class='live-global-controls'>
<form method='post' action='/jobs/clear-orphaned' onsubmit='return confirm("Clear inactive jobs belonging to deleted sources?")'><input type='hidden' name='return_to' value='/jobs'><button class='control-test'>Clear deleted sources</button></form>
<form method='post' action='/jobs/clear-inactive' onsubmit='return confirm("Clear all finished, stopped and failed job history? Saved recordings will be kept.")'><input type='hidden' name='return_to' value='/jobs'><button class='control-stop'>Clear inactive</button></form>
<form method='post' action='/jobs/delete-all' onsubmit='return confirm("Delete ALL VIC job history and cached previews? Active jobs must be stopped. Saved recordings will be kept.")'><input type='hidden' name='return_to' value='/jobs'><button class='control-stop'>Delete All History</button></form>
</div></div>
<table><tr><th>Source</th><th>Worker</th><th>Mode</th><th>State</th><th>Message / output</th><th>Actions</th></tr>{% for job in jobs %}<tr><td>{{job.source_name}}</td><td>{{job.worker_name}}</td><td>{{job.mode}}</td><td class='{{"good" if job.state=="running" else "bad" if job.state=="failed" else "muted"}}'>{{job.state}}</td><td>{{job.message}}{% if job.output %}<br><code>{{job.output}}</code>{% endif %}</td><td><a class='btn' target='_blank' href='/live/{{job.id}}'>View</a>{% if job.state in ["finished","failed","stopped"] %}<form style='display:inline' method='post' action='/jobs/{{job.id}}/delete' onsubmit='return confirm("Delete this job history item and cached preview? Saved recordings will be kept.")'><input type='hidden' name='return_to' value='/jobs'><button class='control-stop'>Delete</button></form>{% else %}<br><span class='small muted'>Stop before deleting</span>{% endif %}</td></tr>{% else %}<tr><td colspan='6' class='muted'>No jobs yet.</td></tr>{% endfor %}</table></div>"""
    return render_template_string(
        page("Jobs", body),
        jobs=data,
    )


@app.get("/help")
def help_page():
    body = """<div class='card'><h2>Help</h2><div class='grid'><a class='choice' href='/help-file/START_HERE.html'><strong>Start here</strong><span class='muted'>Install and run a first job.</span></a><a class='choice' href='/help-file/MASS_CAPTURE.html'><strong>Capture Everything</strong><span class='muted'>Create separate sources for all selected devices.</span></a><a class='choice' href='/help-file/WORKERS.html'><strong>Worker PCs</strong><span class='muted'>How distributed jobs work.</span></a><a class='choice' href='/help-file/LIVE_AND_RECORDINGS.html'><strong>Live and Recordings</strong><span class='muted'>Preview, audio meter and recording locations.</span></a><a class='choice' href='/help-file/WEBSITE_VIDEO.html'><strong>Website video</strong><span class='muted'>YouTube and yt-dlp support.</span></a><a class='choice' href='/help-file/FFMPEG_HELP.html'><strong>FFmpeg</strong><span class='muted'>Install the media engine.</span></a></div></div>"""
    return render_template_string(page("Help", body))


@app.get("/help-file/<path:filename>")
def help_file(filename: str):
    return send_from_directory(HELP, filename)


@app.get("/previews/<job_id>.jpg")
def preview_image(job_id: str):
    path = PREVIEW_DIR / f"{job_id}.jpg"
    if not path.is_file():
        return "Preview not available", 404
    try:
        width = max(160, min(4096, int(request.args.get("w", 0) or 0)))
    except (TypeError, ValueError):
        width = 0
    try:
        ffmpeg_q = max(1, min(31, int(request.args.get("q", 0) or 0)))
    except (TypeError, ValueError):
        ffmpeg_q = 0
    if not width and not ffmpeg_q:
        return send_from_directory(PREVIEW_DIR, f"{job_id}.jpg", max_age=0)
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            if width and image.width > width:
                height = max(1, round(image.height * width / image.width))
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            quality = max(30, min(95, 100 - (ffmpeg_q or 7) * 2))
            payload = io.BytesIO()
            image.save(payload, format="JPEG", quality=quality, optimize=True)
            payload.seek(0)
        response = send_file(payload, mimetype="image/jpeg", max_age=0)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response
    except Exception:
        return send_from_directory(PREVIEW_DIR, f"{job_id}.jpg", max_age=0)


@app.post("/api/sources/<source_id>/preview-display")
def set_source_preview_display(source_id: str):
    payload = request.get_json(silent=True) or {}
    preset = str(payload.get("preset", "global"))
    values = {
        "global": (0, 0),
        "lowest": (160, 18),
        "low": (320, 12),
        "balanced": (640, 7),
        "hd": (1280, 5),
        "full_hd": (1920, 4),
        "qhd": (2560, 4),
        "uhd_4k": (3840, 3),
    }
    if preset not in values:
        return jsonify({"error": "Unknown preview quality preset"}), 400
    width, jpeg_quality = values[preset]
    updated = update_source_fields(
        source_id,
        preview_display_width=width,
        preview_display_jpeg_quality=jpeg_quality,
    )
    if not updated:
        return jsonify({"error": "Source not found"}), 404
    data = jobs()
    changed = False
    for item in data:
        if item.get("source_id") != source_id:
            continue
        source_snapshot = dict(item.get("source", {}) or {})
        source_snapshot["preview_display_width"] = width
        source_snapshot["preview_display_jpeg_quality"] = jpeg_quality
        item["source"] = source_snapshot
        item["updated_ts"] = time.time()
        changed = True
    if changed:
        save_json(JOBS_FILE, data)
    runtime = runtime_live_settings()
    return jsonify({
        "ok": True,
        "preset": preset,
        "width": width or runtime["preview_width"],
        "jpeg_quality": jpeg_quality or runtime["preview_jpeg_quality"],
        "uses_global": width == 0,
    })



def create_temporary_audio_meter_job(
    worker: dict[str, Any],
    descriptor: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    now = time.time()
    job_id = uuid.uuid4().hex
    kind = str(descriptor.get("kind", ""))
    if kind == "speaker":
        source = {
            "id": "__audio_meter__" + job_id,
            "name": label or str(descriptor.get("name", "Speaker output")),
            "type": "speaker_output",
            "type_label": "Temporary speaker meter",
            "worker_id": worker["id"],
            "options": {
                "speaker_id": str(descriptor.get("id", "")),
                "speaker_name": str(descriptor.get("name", label)),
                "samplerate": 48000,
            },
        }
    else:
        source = {
            "id": "__audio_meter__" + job_id,
            "name": label or str(descriptor.get("device", "Audio input")),
            "type": "audio_device",
            "type_label": "Temporary input meter",
            "worker_id": worker["id"],
            "options": {
                "audio_device": str(descriptor.get("device", "")),
            },
        }
    job = {
        "id": job_id,
        "source_id": source["id"],
        "source_name": source["name"],
        "worker_id": worker["id"],
        "worker_name": worker.get("display_name", worker.get("name", "Worker")),
        "source": source,
        "mode": "preview",
        "preview_unlimited": True,
        "desired_state": "running",
        "state": "pending",
        "message": "Waiting for worker to open live setup meter",
        "output": "Setup meter only — nothing is saved",
        "audio_level_db": None,
        "preview_available": False,
        "temporary": True,
        "meter_only": True,
        "expires_ts": now + 15 * 60,
        "created_ts": now,
        "updated_ts": now,
    }
    data = jobs()
    data.append(job)
    save_json(JOBS_FILE, data)
    return job


def stop_temporary_jobs(job_ids: set[str]) -> int:
    if not job_ids:
        return 0
    data = jobs()
    changed = 0
    for item in data:
        if str(item.get("id", "")) not in job_ids:
            continue
        if not item.get("temporary"):
            continue
        if job_is_active(item):
            mark_job_stopping(item, "Setup audio meter closed")
            changed += 1
    if changed:
        save_json(JOBS_FILE, data)
    return changed


def expire_temporary_audio_meters(data: list[dict[str, Any]]) -> bool:
    now = time.time()
    changed = False
    for item in data:
        if not item.get("temporary") or not job_is_active(item):
            continue
        if now >= float(item.get("expires_ts", now + 1)):
            mark_job_stopping(item, "Setup audio meter expired after 15 minutes")
            changed = True
    return changed


@app.post("/api/audio-meters/start")
def start_audio_meter():
    payload = request.get_json(silent=True) or {}
    worker_id = str(payload.get("worker_id", "auto"))
    worker = choose_worker(worker_id)
    if not worker:
        return jsonify({"error": "No suitable online worker is available."}), 404
    descriptor = parse_audio_choice(str(payload.get("audio_choice", "")))
    if not descriptor:
        return jsonify({"error": "Choose a valid audio device first."}), 400
    job = create_temporary_audio_meter_job(
        worker,
        descriptor,
        str(payload.get("label", "Audio device")),
    )
    return jsonify({
        "ok": True,
        "job_id": job["id"],
        "worker_name": job["worker_name"],
    })


@app.post("/api/audio-meters/stop")
def stop_audio_meter():
    payload = request.get_json(silent=True) or {}
    count = stop_temporary_jobs({str(payload.get("job_id", ""))})
    return jsonify({"ok": True, "stopped": count})


@app.post("/api/audio-meters/stop-all")
def stop_all_audio_meters_api():
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("job_ids", [])
    ids = {str(item) for item in raw_ids if item}
    count = stop_temporary_jobs(ids)
    return jsonify({"ok": True, "stopped": count})


@app.get("/api/audio-meters/status")
def audio_meter_status():
    ids = {
        text
        for text in str(request.args.get("ids", "")).split(",")
        if text
    }
    result = []
    for item in jobs():
        if str(item.get("id", "")) not in ids or not item.get("temporary"):
            continue
        level = item.get("audio_level_db")
        result.append({
            "id": item.get("id", ""),
            "state": item.get("state", "unknown"),
            "message": item.get("message", ""),
            "audio_level_db": level,
            "audio_percent": audio_percent(level),
        })
    return jsonify({"jobs": result, "server_ts": time.time()})


@app.get("/api/live-status")
def live_status():
    result: list[dict[str, Any]] = []
    for item in jobs():
        job_id = str(item.get("id", ""))
        if not job_id:
            continue
        level = item.get("audio_level_db")
        result.append(
            {
                "id": job_id,
                "source_id": item.get("source_id", ""),
                "source_name": item.get("source_name", ""),
                "worker_name": item.get("worker_name", ""),
                "state": item.get("state", "unknown"),
                "mode": item.get("mode", ""),
                "message": item.get("message", ""),
                "output": item.get("output", ""),
                "audio_level_db": level,
                "audio_percent": audio_percent(level),
                "preview_available": (
                    PREVIEW_DIR / f"{job_id}.jpg"
                ).is_file(),
                "requested_fps": item.get("requested_fps"),
                "actual_fps": item.get("actual_fps"),
                "dropped_frames": item.get("dropped_frames", 0),
                "duplicated_frames": item.get("duplicated_frames", 0),
                "bitrate_mbps": item.get("bitrate_mbps"),
                "file_size_bytes": item.get("file_size_bytes", 0),
                "duration_seconds": item.get("duration_seconds", 0),
                "disk_per_hour_gb": item.get("disk_per_hour_gb"),
                "disk_write_bytes_per_second": item.get("disk_write_bytes_per_second", 0),
                "encoder": item.get("encoder", ""),
                "speed": item.get("speed", ""),
                "frame_count": item.get("frame_count", 0),
                "preview_display_width": int((item.get("source", {}) or {}).get("preview_display_width", 0) or 0),
                "preview_display_jpeg_quality": int((item.get("source", {}) or {}).get("preview_display_jpeg_quality", 0) or 0),
                "updated_ts": item.get("updated_ts", 0),
            }
        )
    return jsonify({"jobs": result, "server_ts": time.time(), "live_settings": runtime_live_settings()})


@app.get("/api/jobs/<job_id>/status")
def job_status(job_id: str):
    job = job_by_id(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    preview = PREVIEW_DIR / f"{job_id}.jpg"
    level = job.get("audio_level_db")
    return jsonify(
        {
            "id": job_id,
            "state": job.get("state", "unknown"),
            "message": job.get("message", ""),
            "output": job.get("output", ""),
            "audio_level_db": level,
            "audio_percent": audio_percent(level),
            "preview_available": preview.is_file(),
            "mode": job.get("mode", ""),
            "requested_fps": job.get("requested_fps"),
            "actual_fps": job.get("actual_fps"),
            "dropped_frames": job.get("dropped_frames", 0),
            "duplicated_frames": job.get("duplicated_frames", 0),
            "bitrate_mbps": job.get("bitrate_mbps"),
            "file_size_bytes": job.get("file_size_bytes", 0),
            "duration_seconds": job.get("duration_seconds", 0),
            "disk_per_hour_gb": job.get("disk_per_hour_gb"),
            "disk_write_bytes_per_second": job.get("disk_write_bytes_per_second", 0),
            "encoder": job.get("encoder", ""),
            "speed": job.get("speed", ""),
            "frame_count": job.get("frame_count", 0),
            "preview_display_width": int((job.get("source", {}) or {}).get("preview_display_width", 0) or 0),
            "preview_display_jpeg_quality": int((job.get("source", {}) or {}).get("preview_display_jpeg_quality", 0) or 0),
            "updated_ts": job.get("updated_ts", 0),
        }
    )


@app.get("/api/automatic-worker")
def automatic_worker():
    item = choose_worker("auto")
    return (
        jsonify({"id": item.get("id"), "name": item.get("display_name")})
        if item
        else jsonify({"id": ""})
    )


@app.get("/api/workers/<worker_id>/inventory")
def worker_inventory(worker_id: str):
    item = worker_by_id(worker_id)
    if not item or not item.get("online"):
        return jsonify({"error": "Worker is not online."}), 404
    return jsonify(item.get("devices", {"video": [], "audio": [], "audio_inputs": [], "speakers": [], "screens": [], "windows": []}))


@app.get("/api/worker/check-token")
def worker_check_token():
    if not token_ok():
        return jsonify({
            "ok": False,
            "error": "Invalid cluster token",
        }), 403
    return jsonify({
        "ok": True,
        "hostname": socket.gethostname(),
        "version": DASHBOARD_VERSION,
    })


@app.post("/api/worker/heartbeat")
def worker_heartbeat():
    if not token_ok():
        return jsonify({"error": "Invalid cluster token"}), 403
    payload = request.get_json(force=True)
    worker_id = str(payload.get("id", "")).strip()
    if not worker_id:
        return jsonify({"error": "Missing worker id"}), 400

    active_status = payload.pop("active_status", {}) or {}
    data = load_json(WORKERS_FILE, [])
    now = time.time()
    previous = next(
        (item for item in data if item.get("id") == worker_id),
        {},
    )
    incoming_devices = payload.get(
        "devices",
        previous.get("devices", {}),
    )
    if not isinstance(incoming_devices, dict):
        incoming_devices = {}
    else:
        incoming_devices = dict(incoming_devices)
    if "windows" in payload:
        incoming_devices["windows"] = payload.get("windows", []) or []

    entry = {
        "id": worker_id,
        "name": payload.get("name") or previous.get("name") or worker_id,
        "worker_version": payload.get(
            "worker_version",
            previous.get("worker_version", ""),
        ),
        "transfer_parallel_limit": payload.get(
            "transfer_parallel_limit",
            previous.get("transfer_parallel_limit", 1),
        ),
        "transfer_chunk_mb": payload.get(
            "transfer_chunk_mb",
            previous.get("transfer_chunk_mb", 1),
        ),
        "host": payload.get("host", previous.get("host", "")),
        "platform": payload.get("platform", previous.get("platform", "")),
        "is_local_dashboard": bool(
            payload.get(
                "is_local_dashboard",
                previous.get("is_local_dashboard", False),
            )
        ),
        "cpu": payload.get("cpu", previous.get("cpu", 0)),
        "memory": payload.get("memory", previous.get("memory", 0)),
        "disk_free_gb": payload.get(
            "disk_free_gb",
            previous.get("disk_free_gb", 0),
        ),
        "disk_total_gb": payload.get("disk_total_gb", previous.get("disk_total_gb", 0)),
        "disk_used_gb": payload.get("disk_used_gb", previous.get("disk_used_gb", 0)),
        "disk_percent": payload.get("disk_percent", previous.get("disk_percent", 0)),
        "recordings_root": payload.get(
            "recordings_root",
            previous.get("recordings_root", ""),
        ),
        "ffmpeg": payload.get("ffmpeg", previous.get("ffmpeg", "")),
        "video_encoder": payload.get("video_encoder", previous.get("video_encoder", "")),
        "video_encoder_details": payload.get(
            "video_encoder_details",
            previous.get("video_encoder_details", ""),
        ),
        "ffmpeg_selection_details": payload.get(
            "ffmpeg_selection_details",
            previous.get("ffmpeg_selection_details", ""),
        ),
        "ffmpeg_selection_mode": payload.get(
            "ffmpeg_selection_mode",
            previous.get("ffmpeg_selection_mode", "auto_compatible"),
        ),
        "ffmpeg_last_selected_path": payload.get(
            "ffmpeg_last_selected_path",
            previous.get("ffmpeg_last_selected_path", ""),
        ),
        "ffmpeg_candidates": payload.get(
            "ffmpeg_candidates",
            previous.get("ffmpeg_candidates", []),
        ),
        "gpu_devices": payload.get(
            "gpu_devices",
            previous.get("gpu_devices", []),
        ),
        "devices": incoming_devices,
        "recordings": payload.get(
            "recordings",
            previous.get("recordings", []),
        ),
        "recycle_bin": payload.get(
            "recycle_bin",
            previous.get("recycle_bin", []),
        ),
        "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_seen_ts": now,
    }
    replaced = False
    for index, old in enumerate(data):
        if old.get("id") == worker_id:
            data[index] = entry
            replaced = True
            break
    if not replaced:
        data.append(entry)
    save_json(WORKERS_FILE, data)

    # An armed source survives Dashboard/worker restarts. As soon as any
    # suitable worker is online, queue its lightweight availability watcher.
    ensure_armed_auto_start_jobs()

    job_data = jobs()
    jobs_changed = False
    for job_id, status in active_status.items():
        preview_b64 = status.get("preview_b64")
        if preview_b64:
            try:
                (PREVIEW_DIR / f"{job_id}.jpg").write_bytes(base64.b64decode(preview_b64))
            except (OSError, ValueError):
                pass
        for item in job_data:
            if item.get("id") == job_id:
                if "audio_level_db" in status:
                    item["audio_level_db"] = status.get("audio_level_db")
                if status.get("output"):
                    item["output"] = status.get("output")
                for key in HEALTH_FIELDS:
                    if key in status:
                        item[key] = status.get(key)
                if preview_b64 or (PREVIEW_DIR / f"{job_id}.jpg").is_file():
                    item["preview_available"] = True
                item["updated_ts"] = now
                jobs_changed = True
                break
    if jobs_changed:
        save_json(JOBS_FILE, job_data)

    current_jobs = jobs()
    current_jobs_changed = expire_temporary_audio_meters(current_jobs)
    if release_waiting_jobs(current_jobs):
        current_jobs_changed = True
    if current_jobs_changed:
        save_json(JOBS_FILE, current_jobs)
    assigned = [
        item
        for item in current_jobs
        if item.get("worker_id") == worker_id
        and job_is_active(item)
    ]
    return jsonify({"ok": True, "jobs": assigned, "runtime_settings": runtime_live_settings(), "storage_paths": storage_paths_for_worker(worker_id)})


@app.post("/api/worker/job-update")
def job_update():
    if not token_ok():
        return jsonify({"error": "Invalid cluster token"}), 403
    payload = request.get_json(force=True)
    job_id = payload.get("job_id")
    data = jobs()
    found = False
    matched_job: dict[str, Any] | None = None
    rollover_control_request: tuple[dict[str, Any], str, dict[str, Any]] | None = None
    allowed = {
        "state",
        "message",
        "output",
        "pid",
        "audio_level_db",
        "preview_available",
        "benchmark_write_mbps",
        "benchmark_read_mbps",
        "benchmark_size_mb",
        "storage_id",
        "reconnect_attempts",
        "reconnect_after_ts",
        "availability_attempts",
        "availability_last_reason",
        "mode",
        "ever_running",
        *HEALTH_FIELDS,
    }
    for item in data:
        if item.get("id") == job_id:
            for key in allowed:
                if key in payload:
                    item[key] = payload[key]
            incoming_state = str(payload.get("state", item.get("state", "")))
            if incoming_state == "running":
                item["ever_running"] = True

            if (
                incoming_state in {"stopped", "finished"}
                and item.get("rollover_move_closed")
                and not item.get("rollover_move_queued")
                and payload.get("output")
            ):
                target_storage = storage_by_id(str(item.get("rollover_storage_id", "")))
                owner = worker_by_id(str(item.get("worker_id", "")))
                if target_storage and owner:
                    rollover_control_request = (
                        owner,
                        f'Move closed segment for {item.get("source_name", "Source")}',
                        {
                            "output": payload.get("output", ""),
                            "destination_root": target_storage.get("path", ""),
                            "source_name": item.get("source_name", "Source"),
                            "source_started_ts": item.get("created_ts", 0),
                            "storage_id": target_storage.get("id", ""),
                        },
                    )
                    item["rollover_move_queued"] = True

            source_snapshot = item.get("source", {}) or {}
            should_retry_start = bool(
                incoming_state == "failed"
                and item.get("mode") == "record"
                and not item.get("ever_running", False)
                and item.get("desired_state") == "running"
                and source_snapshot.get("retry_start_failures", False)
            )
            if should_retry_start:
                attempts = int(item.get("reconnect_attempts", 0) or 0) + 1
                delay = max(
                    2,
                    min(300, int(source_snapshot.get("reconnect_delay", 5) or 5)),
                )
                reason = str(payload.get("message", "Start failed"))
                item["state"] = "waiting_reconnect"
                item["message"] = (
                    f"Start failed: {reason}. Retry attempt {attempts} "
                    f"in {delay} seconds"
                )
                item["reconnect_attempts"] = attempts
                item["reconnect_after_ts"] = time.time() + delay

            item["updated_ts"] = time.time()
            matched_job = item
            found = True
            break
    if not found:
        return jsonify({"error": "Job not found"}), 404

    if (
        matched_job
        and matched_job.get("armed_auto_start")
        and matched_job.get("state") == "running"
    ):
        set_source_auto_start_armed(
            str(matched_job.get("source_id", "")),
            False,
        )

    should_run_post_action = bool(
        matched_job
        and matched_job.get("mode") == "record"
        and matched_job.get("state") == "finished"
        and not matched_job.get("post_record_action_done")
    )
    if should_run_post_action and matched_job is not None:
        matched_job["post_record_action_done"] = True
        matched_job["post_record_action_result"] = "Checking automatic post-record action"
    save_json(JOBS_FILE, data)

    if rollover_control_request is not None:
        owner, label, options = rollover_control_request
        create_worker_control_job(
            owner,
            "move_closed_outputs",
            label,
            options,
        )

    if should_run_post_action and matched_job is not None:
        source = source_by_id(str(matched_job.get("source_id", "")))
        action = str((source or {}).get("after_recording", "keep"))
        output_path = str(matched_job.get("output", "")).strip()
        source_worker = worker_by_id(str(matched_job.get("worker_id", "")))
        destination = main_worker()
        result = "Kept on recording worker"
        if action in {"copy_main", "move_main"} and output_path and source_worker and destination:
            if source_worker.get("id") == destination.get("id"):
                result = "Already recorded on Main PC"
            else:
                operation = "copy" if action == "copy_main" else "move"
                record, reason = queue_recording_transfer(
                    source_worker,
                    destination,
                    {
                        "path": output_path,
                        "name": Path(output_path).name,
                        "relative": Path(output_path).name,
                        "size_mb": round(float(matched_job.get("file_size_bytes", 0) or 0) / (1024 * 1024), 2),
                    },
                    operation,
                    batch_label=f"Automatic post-record {operation}",
                )
                result = (
                    f"Automatic {operation} queued to Main PC"
                    if record
                    else f"Automatic {operation} could not be queued: {reason}"
                )
        job_rows = jobs()
        for row in job_rows:
            if row.get("id") == matched_job.get("id"):
                row["post_record_action_result"] = result
                break
        save_json(JOBS_FILE, job_rows)

    if matched_job and matched_job.get("mode") == "delete_transfer_source":
        transfer_id = str(
            matched_job.get("source", {})
            .get("options", {})
            .get("transfer_id", "")
        )
        state = str(matched_job.get("state", ""))
        if transfer_id and state == "finished":
            update_transfer(
                transfer_id,
                state="completed",
                message="Move completed and verified; the original file was deleted",
                progress_percent=100,
            )
        elif transfer_id and state == "failed":
            update_transfer(
                transfer_id,
                state="source_delete_failed",
                message=(
                    "The destination verified the file, but VIC could not "
                    "delete the original. Both copies were kept. "
                    + str(matched_job.get("message", ""))
                ),
                progress_percent=100,
            )
    return jsonify({"ok": True})


@app.get("/api/discovery")
def discovery_info():
    cfg = settings()
    return jsonify(
        {
            "product": DISCOVERY_PRODUCT,
            "version": "0.8.7",
            "hostname": socket.gethostname(),
            "port": int(cfg.get("port", 8765)),
        }
    )


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "version": "0.8.7",
            "workers": len(workers()),
            "jobs": len(jobs()),
        }
    )


if __name__ == "__main__":
    cfg = settings()
    port = int(cfg.get("port", 8765))
    address = f"http://127.0.0.1:{port}"
    discovery_port = int(cfg.get("discovery_port", 8766))
    start_discovery_responder(
        dashboard_port=port,
        discovery_port=discovery_port,
    )
    print("VIC dashboard v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO")
    print("Dashboard:", address)
    print("Worker auto-discovery: UDP port", discovery_port)
    print("Keep this window open.")
    if cfg.get("open_browser_on_start", True):
        threading.Timer(1.5, lambda: webbrowser.open(address)).start()
    app.run(host="0.0.0.0", port=port, threaded=True)
