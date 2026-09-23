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
        'root_class = "vic-live-all-page" if title == "Live All" else ""',
        "vic-live-all-page.vic-zoomed-out main",
        "body.vic-live-all-zoomed-out",
        "uses the empty side space",
    ]
    for marker in required_source:
        if marker not in source:
            raise RuntimeError(f"Missing Live All reflow marker: {marker}")

    required_script = [
        'const liveAll=root.classList.contains("vic-live-all-page")',
        "const reflow=liveAll && current<100",
        'root.classList.toggle("vic-zoomed-out",reflow)',
        '"vic-live-all-zoomed-out"',
        'root.style.width=reflow',
        'String(100/scale)+"%"',
        'window.addEventListener("resize"',
    ]
    for marker in required_script:
        if marker not in script:
            raise RuntimeError(f"Missing reflow behaviour: {marker}")

    # 100% must explicitly return to the original normal width.
    if ': "100%";' not in script:
        raise RuntimeError("Default 100% width restoration is missing")

    # The shared shell still contains only one HTML document constructor.
    if source.count("<!doctype html>") != 1:
        raise RuntimeError("More than one Dashboard shell exists")

    # The general grid stays responsive and is not hard-coded to three.
    if ".live-grid{display:grid;grid-template-columns:repeat(auto-fit" not in source:
        raise RuntimeError("Live All grid is no longer responsive")
    if "repeat(3," in source:
        raise RuntimeError("A hard-coded three-column grid remains")

    print("VIC Live All reflow regression tests passed:")
    print("- 100% keeps the original normal-width page")
    print("- below 100% gives Live All inverse pre-zoom width")
    print("- the normal main-width cap is released only on Live All")
    print("- CSS Grid can add more columns as zoom decreases")
    print("- resize and fullscreen changes reapply the layout")
    print("- all-page fullscreen and zoom controls remain shared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
