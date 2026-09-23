from pathlib import Path
import json
import os
import shutil

BASE = Path(__file__).resolve().parent.parent
settings_file = BASE / "config" / "settings.json"

try:
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
except Exception:
    settings = {}

candidates = []
configured = str(settings.get("ffmpeg_path", "")).strip()
if configured:
    candidates.append(Path(configured))

candidates.append(BASE / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")

normal = shutil.which("ffmpeg")
if normal:
    candidates.append(Path(normal))

local_appdata = os.environ.get("LOCALAPPDATA")
if local_appdata:
    winget = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
    if winget.exists():
        candidates.extend(winget.rglob("ffmpeg.exe"))

for candidate in candidates:
    try:
        if candidate.is_file():
            print("OK - VIC found FFmpeg:")
            print(candidate.resolve())
            raise SystemExit(0)
    except OSError:
        pass

print("NOT FOUND - Run INSTALL_FFMPEG.bat")
raise SystemExit(1)
