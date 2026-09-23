from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Iterable
from urllib.request import urlopen

import psutil

BASE = Path(__file__).resolve().parent.parent
RUNTIME = BASE / "runtime"
RUNTIME.mkdir(parents=True, exist_ok=True)

ROLES = {
    "dashboard": BASE / "dashboard" / "app.py",
    "worker": BASE / "worker" / "worker.py",
}


def pid_file(role: str) -> Path:
    return RUNTIME / f"{role}.pid.json"


def normalise(value: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(value))
    except Exception:
        return os.path.normcase(value)


def process_matches(proc: psutil.Process, role: str) -> bool:
    target = normalise(str(ROLES[role]))
    try:
        command = [normalise(str(part)) for part in proc.cmdline()]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return target in command


def role_processes(role: str) -> list[psutil.Process]:
    found: dict[int, psutil.Process] = {}
    saved = pid_file(role)
    if saved.is_file():
        try:
            pid = int(json.loads(saved.read_text(encoding="utf-8"))["pid"])
            proc = psutil.Process(pid)
            if process_matches(proc, role):
                found[pid] = proc
        except (OSError, ValueError, KeyError, json.JSONDecodeError, psutil.Error):
            pass
    for proc in psutil.process_iter(["pid"]):
        if process_matches(proc, role):
            found[proc.pid] = proc
    return list(found.values())


def save_pid(role: str, pid: int) -> None:
    pid_file(role).write_text(
        json.dumps(
            {
                "role": role,
                "pid": pid,
                "script": str(ROLES[role]),
                "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def start_role(role: str) -> bool:
    running = role_processes(role)
    if running:
        print(f"{role.title()} is already running (PID {running[0].pid}).")
        save_pid(role, running[0].pid)
        return True
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    process = subprocess.Popen(
        [sys.executable, str(ROLES[role])],
        cwd=str(BASE),
        creationflags=flags,
    )
    save_pid(role, process.pid)
    deadline = time.time() + 4
    while time.time() < deadline:
        if process.poll() is not None:
            print(f"ERROR: {role.title()} exited during startup with code {process.returncode}.")
            pid_file(role).unlink(missing_ok=True)
            return False
        time.sleep(0.2)
    print(f"{role.title()} started and stayed running (PID {process.pid}).")
    return True


def wait_for_dashboard() -> bool:
    try:
        cfg = json.loads((BASE / "config" / "dashboard.json").read_text(encoding="utf-8"))
        port = int(cfg.get("port", 8765))
    except Exception:
        port = 8765
    url = f"http://127.0.0.1:{port}/api/discovery"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.4)
    return False


def terminate_tree(processes: Iterable[psutil.Process]) -> None:
    all_processes: dict[int, psutil.Process] = {}
    for proc in processes:
        try:
            for child in proc.children(recursive=True):
                all_processes[child.pid] = child
            all_processes[proc.pid] = proc
        except psutil.Error:
            pass
    ordered = list(all_processes.values())
    for proc in ordered:
        try:
            proc.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(ordered, timeout=6)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=4)


def stop_role(role: str) -> bool:
    processes = role_processes(role)
    if not processes:
        print(f"{role.title()}: already stopped.")
        pid_file(role).unlink(missing_ok=True)
        return True
    print(
        f"Stopping {role} process tree(s): "
        + ", ".join(str(proc.pid) for proc in processes)
    )
    terminate_tree(processes)
    remaining = role_processes(role)
    if remaining:
        print(
            f"ERROR: {role.title()} is still running: "
            + ", ".join(str(proc.pid) for proc in remaining)
        )
        return False
    pid_file(role).unlink(missing_ok=True)
    print(f"{role.title()}: STOPPED AND VERIFIED.")
    return True


def status() -> bool:
    any_running = False
    for role in ("dashboard", "worker"):
        processes = role_processes(role)
        if processes:
            any_running = True
            print(
                f"{role.title()}: RUNNING — PID(s) "
                + ", ".join(str(proc.pid) for proc in processes)
            )
        else:
            print(f"{role.title()}: STOPPED")
    return any_running


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "start-dashboard":
        return 0 if start_role("dashboard") else 1
    if command == "start-worker":
        return 0 if start_role("worker") else 1
    if command == "start-all":
        if not start_role("dashboard"):
            return 1
        if wait_for_dashboard():
            print("Dashboard HTTP check: READY.")
        else:
            print("WARNING: Dashboard process is running but its HTTP page did not answer yet.")
        if not start_role("worker"):
            return 1
        try:
            cfg = json.loads((BASE / "config" / "dashboard.json").read_text(encoding="utf-8"))
            port = int(cfg.get("port", 8765))
        except Exception:
            port = 8765
        webbrowser.open(f"http://127.0.0.1:{port}")
        print("VIC start verification complete.")
        return 0
    if command == "stop-worker":
        return 0 if stop_role("worker") else 1
    if command == "stop-dashboard":
        return 0 if stop_role("dashboard") else 1
    if command == "stop-all":
        worker_ok = stop_role("worker")
        dashboard_ok = stop_role("dashboard")
        print("\nFinal VIC process check:")
        still_running = status()
        if worker_ok and dashboard_ok and not still_running:
            print("\nSUCCESS: Everything started from this VIC folder is stopped.")
            return 0
        print("\nERROR: One or more VIC processes could not be verified as stopped.")
        return 1
    if command == "status":
        status()
        return 0
    print("Unknown command:", command)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
