from __future__ import annotations

import argparse
import importlib
import json
import socket
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config" / "worker.json"

PACKAGES = [
    ("psutil", "psutil"),
    ("yt_dlp", "yt-dlp"),
    ("soundcard", "SoundCard"),
    ("numpy", "NumPy"),
    ("PIL", "Pillow"),
]


def normalise_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if text and "://" not in text:
        text = "http://" + text
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    print("=" * 64)
    print("VIC SECOND-PC WORKER SETUP TEST")
    print("=" * 64)

    missing: list[str] = []
    for module_name, display_name in PACKAGES:
        try:
            importlib.import_module(module_name)
            print(f"PACKAGE OK: {display_name}")
        except Exception as exc:
            missing.append(display_name)
            print(f"PACKAGE MISSING/BROKEN: {display_name} — {exc}")

    if missing:
        print()
        print("Run REPAIR_AND_SETUP_WORKER.bat.")
        return 1

    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"CONFIG FAILED: {exc}")
        return 1

    url = normalise_url(cfg.get("dashboard_url", ""))
    token = str(cfg.get("cluster_token", "")).strip()
    print(f"Dashboard address: {url or 'EMPTY'}")
    print(f"Worker name: {cfg.get('worker_name') or socket.gethostname()}")

    if not url:
        print("ADDRESS FAILED: Dashboard address is empty.")
        return 1
    if not token:
        print("TOKEN FAILED: cluster_token is empty.")
        print("Import the old worker.json or use Main PC > Portable Tools > Prepare Worker Copy.")
        return 1

    try:
        with urlopen(url + "/api/discovery", timeout=3.0) as response:
            discovery = json.loads(response.read().decode("utf-8"))
        print(
            "DASHBOARD FOUND:",
            discovery.get("hostname", "VIC"),
            discovery.get("version", ""),
        )
    except Exception as exc:
        print(f"ADDRESS/FIREWALL FAILED: {exc}")
        print("Check START_VIC.bat on the Main PC and Windows Private-network firewall access.")
        return 1

    request = Request(
        url + "/api/worker/check-token",
        headers={
            "User-Agent": "VIC-Worker-Setup-Test/1",
            "X-VIC-Token": token,
        },
    )
    try:
        with urlopen(request, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            print("TOKEN FAILED: Dashboard did not accept the token.")
            return 1
    except HTTPError as exc:
        if exc.code == 403:
            print("TOKEN FAILED: The second PC's cluster token does not match the Main PC.")
            print("Use the old working config\\worker.json, or download Prepare Worker Copy from the Main PC.")
        else:
            print(f"DASHBOARD AUTH TEST FAILED: HTTP {exc.code}")
        return 1
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        print(f"DASHBOARD AUTH TEST FAILED: {exc}")
        return 1

    print("TOKEN OK: Main Dashboard accepted this worker configuration.")
    print()
    print("ALL SECOND-PC WORKER SETUP TESTS PASSED")
    if not args.quick:
        input("Press Enter to close...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
