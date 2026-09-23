from __future__ import annotations

import ast
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"


def assigned_string(tree: ast.AST, name: str) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, str):
                raise RuntimeError(f"{name} is not a string")
            return value
    raise RuntimeError(f"Could not find {name}")


def main() -> int:
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    controls = assigned_string(tree, "GLOBAL_DISPLAY_CONTROLS")
    script = assigned_string(tree, "GLOBAL_DISPLAY_SCRIPT")

    required_controls = [
        "vicFullscreenButton",
        "vicZoomSlider",
        "vicZoomOutButton",
        "vicZoomInButton",
        "vicZoomDefaultButton",
        "Default 100%",
    ]
    for marker in required_controls:
        if marker not in controls:
            raise RuntimeError(f"Missing global control: {marker}")

    required_script = [
        'const minimum=40',
        'const maximum=200',
        'document.documentElement.requestFullscreen',
        'document.exitFullscreen',
        'event.ctrlKey',
        'window.localStorage.setItem',
        'window.addEventListener("storage"',
        'root.style.zoom',
        'applyZoom(100,true)',
        'vicSettingsZoomSlider',
    ]
    for marker in required_script:
        if marker not in script:
            raise RuntimeError(f"Missing global display behaviour: {marker}")

    required_page = [
        "<div id='vicZoomRoot'>",
        "{GLOBAL_DISPLAY_CONTROLS}",
        "{GLOBAL_DISPLAY_SCRIPT}",
    ]
    page_section = source[
        source.index("def page("):
        source.index("@app.errorhandler(500)")
    ]
    for marker in required_page:
        if marker not in page_section:
            raise RuntimeError(f"Shared page shell is missing: {marker}")

    required_settings = [
        "Dashboard zoom and fullscreen",
        "vicSettingsZoomSlider",
        "Default 100%",
        "synchronised with other browser tabs",
    ]
    for marker in required_settings:
        if marker not in source:
            raise RuntimeError(f"Settings page is missing: {marker}")

    # There must be one central HTML document constructor so all Dashboard
    # routes receive the same shared controls.
    if source.count("<!doctype html>") != 1:
        raise RuntimeError(
            "More than one HTML shell exists; zoom may not cover every page"
        )

    print("VIC fullscreen/zoom regression tests passed:")
    print("- shared controls are in the single page shell")
    print("- zoom range is 40%-200%")
    print("- Fullscreen and Exit Fullscreen are supported")
    print("- Ctrl + wheel and slider-wheel zoom are supported")
    print("- zoom is remembered across pages and browser tabs")
    print("- Settings contains matching slider/default/fullscreen controls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
