from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
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

    fake_request = SimpleNamespace(
        headers={},
        method="POST",
        path="/sources/enable-all",
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
    fake_flask.request = fake_request
    fake_flask.send_file = lambda *args, **kwargs: None
    fake_flask.send_from_directory = lambda *args, **kwargs: None
    fake_flask.url_for = lambda endpoint, **values: "/" + endpoint

    previous = sys.modules.get("flask")
    sys.modules["flask"] = fake_flask
    try:
        name = "vic_bulk_source_control_regression"
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

    required = [
        "Enable All",
        "Disable All",
        '@app.post("/sources/enable-all")',
        '@app.post("/sources/disable-all")',
        "def set_all_sources_enabled",
        "active source(s) were safely left enabled",
        "archived source(s) remained archived",
    ]
    for marker in required:
        if marker not in source:
            raise RuntimeError(f"Missing bulk-control marker: {marker}")

    dashboard = load_dashboard()

    with tempfile.TemporaryDirectory(prefix="vic-bulk-source-test-") as temp:
        sources_file = Path(temp) / "sources.json"
        original = [
            {
                "id": "enabled",
                "name": "Enabled",
                "type": "screen",
                "enabled": True,
                "archived": False,
            },
            {
                "id": "disabled",
                "name": "Disabled",
                "type": "screen",
                "enabled": False,
                "archived": False,
            },
            {
                "id": "archived",
                "name": "Archived",
                "type": "screen",
                "enabled": False,
                "archived": True,
            },
            {
                "id": "active",
                "name": "Active",
                "type": "screen",
                "enabled": True,
                "archived": False,
            },
        ]
        sources_file.write_text(
            json.dumps(original, indent=2),
            encoding="utf-8",
        )

        dashboard.SOURCES_FILE = sources_file
        dashboard.sources = lambda: json.loads(
            sources_file.read_text(encoding="utf-8")
        )
        dashboard.save_json = lambda path, data: path.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )
        dashboard.active_job_for_source = (
            lambda source_id:
            {"id": "job"} if source_id == "active" else None
        )

        changed, active, archived = (
            dashboard.set_all_sources_enabled(True)
        )
        if (changed, active, archived) != (1, 0, 1):
            raise RuntimeError(
                "Enable All counts were wrong: "
                f"{(changed, active, archived)}"
            )
        after_enable = dashboard.sources()
        states = {
            item["id"]: item["enabled"]
            for item in after_enable
        }
        if states != {
            "enabled": True,
            "disabled": True,
            "archived": False,
            "active": True,
        }:
            raise RuntimeError(
                f"Enable All produced wrong states: {states}"
            )

        changed, active, archived = (
            dashboard.set_all_sources_enabled(False)
        )
        if (changed, active, archived) != (2, 1, 0):
            raise RuntimeError(
                "Disable All counts were wrong: "
                f"{(changed, active, archived)}"
            )
        after_disable = dashboard.sources()
        states = {
            item["id"]: item["enabled"]
            for item in after_disable
        }
        if states != {
            "enabled": False,
            "disabled": False,
            "archived": False,
            "active": True,
        }:
            raise RuntimeError(
                f"Disable All produced wrong states: {states}"
            )

    print("VIC bulk source-control regression tests passed:")
    print("- Enable All enables every non-archived source")
    print("- archived sources remain archived and disabled")
    print("- Disable All disables every inactive source")
    print("- active preview/recording sources are safely skipped")
    print("- both Source Library buttons and confirmations are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
