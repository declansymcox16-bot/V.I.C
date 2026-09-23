from __future__ import annotations

import ast
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"


def load_dashboard():
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

    request = SimpleNamespace(
        headers={},
        method="GET",
        path="/",
        form={},
        files={},
        args={},
        get_json=lambda force=False: {},
    )

    fake_flask = ModuleType("flask")
    fake_flask.Flask = FakeFlask
    fake_flask.flash = lambda *args, **kwargs: None
    fake_flask.jsonify = lambda value=None, **kwargs: (
        value if value is not None else kwargs
    )
    fake_flask.redirect = lambda target, *args, **kwargs: target
    fake_flask.render_template_string = lambda template, **context: template
    fake_flask.request = request
    fake_flask.send_file = lambda *args, **kwargs: {
        "args": args,
        "kwargs": kwargs,
    }
    fake_flask.send_from_directory = lambda *args, **kwargs: None
    fake_flask.url_for = lambda endpoint, **values: "/" + endpoint

    previous = sys.modules.get("flask")
    sys.modules["flask"] = fake_flask
    try:
        name = "vic_source_import_regression_dashboard"
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


def source(name: str, source_type: str = "screen") -> dict:
    return {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": source_type,
        "type_label": "Desktop, monitor or application window",
        "worker_id": "auto",
        "options": {"target": "desktop", "fps_mode": "auto", "fps": 60},
        "enabled": True,
    }


def make_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return output.getvalue()


def main() -> int:
    source_code = APP_PATH.read_text(encoding="utf-8")
    ast.parse(source_code)

    required = [
        "def decode_source_import",
        "def candidates_from_json",
        "def normalise_import_type",
        "def prepare_imported_source",
        "Import source file or ZIP",
        "Select the original ZIP directly",
        "SOURCE_TYPE_ALIASES",
    ]
    for marker in required:
        if marker not in source_code:
            raise RuntimeError(f"Missing import-fix marker: {marker}")

    dashboard = load_dashboard()

    current = {
        "vic_source_format": 1,
        "exported_by": "VIC v0.6.5",
        "source": source("Current Screen"),
    }
    items, label = dashboard.decode_source_import(
        "Current_Screen.vicsource.json",
        json.dumps(current).encode("utf-8"),
    )
    if len(items) != 1 or label != "JSON source file":
        raise RuntimeError("Current single-source export was not detected")

    raw_items, _ = dashboard.decode_source_import(
        "raw.json",
        json.dumps(source("Raw Screen")).encode("utf-8"),
    )
    if len(raw_items) != 1:
        raise RuntimeError("Raw source JSON was not accepted")

    list_items, _ = dashboard.decode_source_import(
        "sources.json",
        json.dumps(
            [source("One"), source("Two", "camera")]
        ).encode("utf-8"),
    )
    if len(list_items) != 2:
        raise RuntimeError("sources.json list was not accepted")

    alias = source("Legacy Desktop", "desktop")
    prepared_alias = dashboard.prepare_imported_source(alias)
    if prepared_alias["type"] != "screen":
        raise RuntimeError("Legacy desktop type did not map to screen")
    if prepared_alias["enabled"] is not False:
        raise RuntimeError("Imported source did not start disabled")
    if prepared_alias["id"] == alias["id"]:
        raise RuntimeError("Imported source kept its old ID")

    portable_zip = make_zip(
        {
            "One.vicsource.json": json.dumps(
                {"vic_source_format": 1, "source": source("One")}
            ).encode("utf-8"),
            "Two.vicsource.json": json.dumps(
                {"vic_source_format": 1, "source": source("Two", "camera")}
            ).encode("utf-8"),
            "manifest.json": json.dumps(
                {
                    "vic_source_library_format": 1,
                    "sources": [
                        {"name": "One", "type": "screen", "file": "One.vicsource.json"},
                        {"name": "Two", "type": "camera", "file": "Two.vicsource.json"},
                    ],
                }
            ).encode("utf-8"),
        }
    )
    zip_items, zip_label = dashboard.decode_source_import(
        "VIC_Source_Library_test.zip",
        portable_zip,
    )
    if len(zip_items) != 2:
        raise RuntimeError("Export All ZIP was not imported directly")
    if zip_label != "VIC Source Library ZIP":
        raise RuntimeError("Export All ZIP format label is wrong")

    backup_zip = make_zip(
        {
            "config/sources.json": json.dumps(
                [source("Backup One"), source("Backup Two")]
            ).encode("utf-8")
        }
    )
    backup_items, backup_label = dashboard.decode_source_import(
        "VIC_Config_Backup.zip",
        backup_zip,
    )
    if len(backup_items) != 2:
        raise RuntimeError("Config backup ZIP was not accepted")
    if backup_label != "VIC configuration/backup ZIP":
        raise RuntimeError("Backup ZIP format label is wrong")

    manifest = {
        "vic_source_library_format": 1,
        "sources": [
            {
                "name": "One",
                "id": "one",
                "type": "screen",
                "file": "One.vicsource.json",
            }
        ],
    }
    try:
        dashboard.decode_source_import(
            "manifest.json",
            json.dumps(manifest).encode("utf-8"),
        )
    except Exception as exc:
        if "Select the original VIC Source Library ZIP directly" not in str(exc):
            raise RuntimeError(
                "Manifest-only error does not explain what to select"
            ) from exc
    else:
        raise RuntimeError("manifest.json was incorrectly imported as a source")

    print("VIC source-import regression tests passed:")
    print("- current .vicsource.json")
    print("- raw source JSON")
    print("- sources.json list")
    print("- legacy desktop type alias")
    print("- Export All Source Library ZIP selected directly")
    print("- config/backup ZIP with config/sources.json")
    print("- manifest.json gives a clear selection instruction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
