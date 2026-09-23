from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from jinja2 import Environment, StrictUndefined

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"

def load_dashboard():
    env = Environment(undefined=StrictUndefined, autoescape=False)
    env.globals["get_flashed_messages"] = (
        lambda with_categories=False: []
    )

    fake_request = SimpleNamespace(
        headers={},
        method="GET",
        path="/",
        form={},
        files={},
        args={},
        get_json=lambda force=False: {},
    )

    class FakeFlask:
        def __init__(self, *args, **kwargs):
            self.secret_key = ""
        def route_decorator(self, *args, **kwargs):
            def decorate(function):
                return function
            return decorate
        get = route_decorator
        post = route_decorator
        errorhandler = route_decorator
        def run(self, *args, **kwargs):
            return None

    fake_flask = ModuleType("flask")
    fake_flask.Flask = FakeFlask
    fake_flask.flash = lambda *args, **kwargs: None
    fake_flask.jsonify = lambda value=None, **kwargs: (
        value if value is not None else kwargs
    )
    fake_flask.redirect = lambda target, *args, **kwargs: target
    fake_flask.render_template_string = (
        lambda template, **context:
        env.from_string(template).render(**context)
    )
    fake_flask.request = fake_request
    fake_flask.send_file = lambda *args, **kwargs: {
        "args": args,
        "kwargs": kwargs,
    }
    fake_flask.send_from_directory = lambda *args, **kwargs: None
    fake_flask.url_for = lambda endpoint, **values: "/" + endpoint

    previous = sys.modules.get("flask")
    sys.modules["flask"] = fake_flask
    try:
        name = "vic_source_form_regression_dashboard"
        spec = importlib.util.spec_from_file_location(name, APP_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load dashboard/app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("flask", None)
        else:
            sys.modules["flask"] = previous

    return module

def main() -> int:
    source = APP_PATH.read_text(encoding="utf-8")
    ast.parse(source)

    forbidden = [
        "audio_refresh_seconds|tojson",
        "audio_smoothing|tojson",
    ]
    form_script_section = source[
        source.index("def source_form_script"):
        source.index('@app.get("/add/<source_type>")')
    ]
    for marker in forbidden:
        if marker in form_script_section:
            raise RuntimeError(
                "Source form still depends on missing Jinja value: "
                + marker
            )

    required = [
        "setup_audio_seconds_json",
        "setup_audio_smoothing_json",
        '@app.get("/sources/export-all")',
        "Export All Sources ZIP",
        "Export Source",
    ]
    for marker in required:
        if marker not in source:
            raise RuntimeError(f"Missing repair marker: {marker}")

    dashboard = load_dashboard()
    dashboard.workers = lambda: [
        {
            "id": "test-worker",
            "online": True,
            "display_name": "Test Worker",
            "host": "TEST-PC",
        }
    ]

    add_html = dashboard.add_form("screen")
    if not isinstance(add_html, str):
        raise RuntimeError("Add Source did not render HTML")
    if "<h2>Add Desktop, monitor or application window</h2>" not in add_html:
        raise RuntimeError("Screen Add Source page did not render")
    if "const setupAudioSeconds=" not in add_html:
        raise RuntimeError("Audio refresh value was not embedded")
    if "Undefined" in add_html:
        raise RuntimeError("Add Source rendered an Undefined value")

    example = {
        "id": "source-test-1",
        "name": "Example Screen",
        "type": "screen",
        "type_label": "Screen or desktop",
        "worker_id": "test-worker",
        "options": {
            "target": "desktop",
            "fps_mode": "auto",
            "fps": 60,
            "audio_devices": [],
        },
        "enabled": True,
        "archived": False,
        "favourite": False,
        "notes": "",
        "after_recording": "keep",
        "auto_reconnect": False,
        "reconnect_delay": 5,
    }
    dashboard.source_by_id = (
        lambda source_id: example if source_id == example["id"] else None
    )
    dashboard.active_job_for_source = lambda source_id: None

    edit_html = dashboard.edit_source_form(example["id"])
    if not isinstance(edit_html, str):
        raise RuntimeError("Edit Source did not render HTML")
    if "Edit Example Screen" not in edit_html:
        raise RuntimeError("Edit Source page did not render")
    if "Undefined" in edit_html:
        raise RuntimeError("Edit Source rendered an Undefined value")

    dashboard.sources = lambda: [example]
    export_result = dashboard.export_all_sources()
    if not isinstance(export_result, dict):
        raise RuntimeError("Export All route did not call send_file")
    file_obj = export_result["args"][0]
    file_obj.seek(0)
    import zipfile
    with zipfile.ZipFile(file_obj, "r") as archive:
        names = set(archive.namelist())
        if "manifest.json" not in names:
            raise RuntimeError("Export ZIP has no manifest")
        if not any(name.endswith(".vicsource.json") for name in names):
            raise RuntimeError("Export ZIP has no source file")
        manifest = json.loads(
            archive.read("manifest.json").decode("utf-8")
        )
        if manifest.get("source_count") != 1:
            raise RuntimeError("Export ZIP source count is wrong")

    print("VIC source-form/export regression tests passed:")
    print("- Add Screen page rendered with StrictUndefined")
    print("- Edit Screen page rendered with StrictUndefined")
    print("- no missing Jinja live-setting variables")
    print("- Export Source labels are visible")
    print("- Export All Sources ZIP contains manifest and source")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
