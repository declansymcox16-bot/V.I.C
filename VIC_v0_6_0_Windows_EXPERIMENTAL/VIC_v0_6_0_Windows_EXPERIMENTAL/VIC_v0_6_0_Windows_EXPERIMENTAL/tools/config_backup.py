from __future__ import annotations

import argparse
import json
import shutil
import time
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config"
BACKUPS = BASE / "config_backups"
BACKUPS.mkdir(parents=True, exist_ok=True)


def create_backup(quiet: bool = False) -> Path:
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    target = BACKUPS / f"VIC_Config_{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in CONFIG.glob("*.json"):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            archive.write(path, Path("config") / path.name)
    backups = sorted(BACKUPS.glob("VIC_Config_*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in backups[20:]:
        old.unlink(missing_ok=True)
    if not quiet:
        print("Created:", target)
    return target


def restore_latest() -> Path:
    backups = sorted(BACKUPS.glob("VIC_Config_*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not backups:
        raise RuntimeError("No configuration backups exist.")
    source = backups[0]
    safety = create_backup(quiet=True)
    with zipfile.ZipFile(source, "r") as archive:
        for member in archive.namelist():
            if not member.startswith("config/") or not member.endswith(".json"):
                continue
            destination = BASE / member
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    print("Restored:", source)
    print("Pre-restore safety backup:", safety)
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "restore-latest"])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if args.action == "create":
        create_backup(args.quiet)
    else:
        restore_latest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
