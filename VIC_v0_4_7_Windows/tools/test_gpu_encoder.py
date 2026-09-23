from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config" / "worker.json"


def run(command, timeout=25):
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


def paths():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    result = []
    configured = str(cfg.get("ffmpeg_path", "")).strip()
    if configured:
        result.append((Path(configured), "Configured in worker.json"))
    result.append((BASE / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe", "Bundled"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        package_root = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if package_root.exists():
            for path in package_root.rglob("ffmpeg.exe"):
                result.append((path, "WinGet"))
    normal = shutil.which("ffmpeg")
    if normal:
        result.append((Path(normal), "Windows PATH"))
    seen = set()
    for path, origin in result:
        try:
            resolved = path.resolve()
            key = str(resolved).casefold()
            if key not in seen and resolved.is_file():
                seen.add(key)
                yield resolved, origin
        except OSError:
            pass


def probe(ffmpeg: Path, encoder: str):
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-frames:v", "30", "-an", "-vf", "format=nv12",
        "-c:v", encoder,
    ]
    if encoder == "h264_nvenc":
        command += ["-preset", "p4", "-cq", "23", "-b:v", "0"]
    elif encoder == "h264_amf":
        command += ["-quality", "speed", "-rc", "cqp", "-qp_i", "23", "-qp_p", "23"]
    elif encoder == "h264_qsv":
        command += ["-preset", "veryfast", "-global_quality", "23"]
    command += ["-f", "null", "-"]
    code, stdout, stderr = run(command)
    return code == 0, command, (stderr or stdout).strip()


def main():
    print("=" * 78)
    print("VIC GPU ENCODER DIAGNOSTIC")
    print("=" * 78)
    candidates = list(paths())
    if not candidates:
        print("No ffmpeg.exe was found. Run INSTALL_FFMPEG.bat.")
        return 1
    for index, (ffmpeg, origin) in enumerate(candidates, start=1):
        print(f"\n[{index}] {origin}")
        print(ffmpeg)
        code, stdout, stderr = run([str(ffmpeg), "-version"], timeout=10)
        first = (stdout or stderr).splitlines()
        print(first[0] if first else "Version unavailable")
        code, encoder_stdout, encoder_stderr = run([str(ffmpeg), "-hide_banner", "-encoders"], timeout=15)
        encoder_text = "\n".join(part for part in (encoder_stdout, encoder_stderr) if part)
        for encoder, label in [
            ("h264_nvenc", "NVIDIA NVENC"),
            ("h264_amf", "AMD AMF"),
            ("h264_qsv", "Intel Quick Sync"),
        ]:
            listed = bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(encoder)}(?![A-Za-z0-9_])", encoder_text))
            print(f"\n  {label} ({encoder})")
            print("  Included in FFmpeg listing:", "YES" if listed else "NOT DETECTED")
            ok, command, error = probe(ffmpeg, encoder)
            print("  Runtime test:", "PASSED" if ok else "FAILED")
            if ok and not listed:
                print("  Note: runtime encoding succeeded; the encoder is usable.")
            if not ok:
                print("  FFmpeg error:")
                print("  " + (error or "No error text returned").replace("\n", "\n  "))
        print("\n" + "-" * 78)
    print("\nOn an NVIDIA RTX graphics card, at least one FFmpeg installation should show:")
    print("NVIDIA NVENC Runtime test: PASSED")
    print("If it says Cannot load nvcuda.dll or no capable devices, update the NVIDIA driver.")
    print("The runtime test is authoritative and prints the exact FFmpeg error when it fails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
