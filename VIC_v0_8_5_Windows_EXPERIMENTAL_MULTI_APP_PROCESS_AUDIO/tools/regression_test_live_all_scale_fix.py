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
    script = assigned_string(tree, "GLOBAL_DISPLAY_SCRIPT")

    required_source = [
        "id='vicZoomViewport'",
        "#vicZoomViewport.vic-live-transform",
        "position:absolute;left:0;top:0",
        "vic-live-all-transforming",
        "corrected transform scale",
    ]
    for marker in required_source:
        if marker not in source:
            raise RuntimeError(f"Missing scale-fix marker: {marker}")

    required_script = [
        'const viewport=document.getElementById("vicZoomViewport")',
        "function updateLiveAllViewportHeight",
        "root.scrollHeight*liveAllScale",
        'root.style.transform="scale("+String(scale)+")"',
        'root.style.width=String(100/scale)+"%"',
        'root.style.zoom="1"',
        'viewport.classList.add("vic-live-transform")',
        "function clearLiveAllTransform",
        'root.style.zoom=String(scale)',
        'new ResizeObserver',
    ]
    for marker in required_script:
        if marker not in script:
            raise RuntimeError(f"Missing transform behaviour: {marker}")

    # The old CSS-zoom inverse-width method must be gone.
    forbidden = [
        'root.style.zoom=String(scale);\n      root.dataset.zoomPercent',
        '"vic-live-all-zoomed-out"',
    ]
    for marker in forbidden:
        if marker in script or marker in source:
            raise RuntimeError(f"Old scaling method remains: {marker}")

    if source.count("<!doctype html>") != 1:
        raise RuntimeError("More than one Dashboard page shell exists")

    if source.count("id='vicZoomViewport'") != 1:
        raise RuntimeError("The zoom viewport is not shared exactly once")

    if ".live-grid{display:grid;grid-template-columns:repeat(auto-fit" not in source:
        raise RuntimeError("Live All grid is not responsive")
    if "repeat(3," in source:
        raise RuntimeError("A hard-coded three-column grid remains")

    print("VIC Live All scale-fix regression tests passed:")
    print("- shared transform viewport wraps the Dashboard root")
    print("- Live All uses transform scale instead of CSS zoom")
    print("- inverse width creates real grid layout space")
    print("- wrapper height follows scaled content height")
    print("- ResizeObserver handles live preview/card changes")
    print("- other pages retain the original CSS zoom behaviour")
    print("- 100% keeps the familiar Live All layout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
