from pathlib import Path
import json, os, shutil

BASE = Path(__file__).resolve().parent.parent
cfg = BASE / 'config' / 'worker.json'
try:
    settings = json.loads(cfg.read_text(encoding='utf-8'))
except Exception:
    settings = {}

candidates = []
configured = str(settings.get('ffmpeg_path', '')).strip()
if configured:
    candidates.append(Path(configured))
candidates.append(BASE / 'tools' / 'ffmpeg' / 'bin' / 'ffmpeg.exe')
normal = shutil.which('ffmpeg')
if normal:
    candidates.append(Path(normal))
local = os.environ.get('LOCALAPPDATA')
if local:
    packages = Path(local) / 'Microsoft' / 'WinGet' / 'Packages'
    if packages.exists():
        candidates.extend(packages.rglob('ffmpeg.exe'))
for candidate in candidates:
    try:
        if candidate.is_file():
            print('OK - VIC found FFmpeg:')
            print(candidate.resolve())
            raise SystemExit(0)
    except OSError:
        pass
print('NOT FOUND - Run INSTALL_FFMPEG.bat')
raise SystemExit(1)
