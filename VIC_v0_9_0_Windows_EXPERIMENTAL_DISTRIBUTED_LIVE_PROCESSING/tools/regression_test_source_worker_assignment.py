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

    request = SimpleNamespace(
        headers={},
        method="POST",
        path="/sources/set-worker-bulk",
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
    fake_flask.render_template_string = (
        lambda template, **context: template
    )
    fake_flask.request = request
    fake_flask.send_file = lambda *args, **kwargs: None
    fake_flask.send_from_directory = lambda *args, **kwargs: None
    fake_flask.url_for = lambda endpoint, **values: "/" + endpoint

    previous = sys.modules.get("flask")
    sys.modules["flask"] = fake_flask
    try:
        name = "vic_source_worker_assignment_regression"
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
        "Set worker after import",
        "Set Worker for Selected Group",
        "Imported sources — recommended",
        '@app.post("/sources/set-worker-bulk")',
        '@app.post("/sources/<source_id>/set-worker")',
        "def assign_worker_to_source_group",
        "def source_looks_imported",
        "Cleared incompatible recording storage",
        "Missing saved worker",
    ]
    for marker in required:
        if marker not in source:
            raise RuntimeError(
                f"Missing source-worker feature marker: {marker}"
            )

    dashboard = load_dashboard()

    with tempfile.TemporaryDirectory(
        prefix="vic-source-worker-test-"
    ) as temp:
        source_file = Path(temp) / "sources.json"
        storage_file = Path(temp) / "storage_locations.json"

        original = [
            {
                "id": "imported-one",
                "name": "Imported One (Imported)",
                "type": "website",
                "type_label": "Website video or livestream",
                "worker_id": "old-worker",
                "storage_id": "storage-old",
                "enabled": False,
                "archived": False,
            },
            {
                "id": "imported-active",
                "name": "Imported Active (Imported)",
                "type": "rtsp",
                "type_label": "RTSP / IP camera",
                "worker_id": "old-worker",
                "storage_id": "",
                "enabled": False,
                "archived": False,
            },
            {
                "id": "normal",
                "name": "Normal Source",
                "type": "screen",
                "type_label": "Desktop",
                "worker_id": "old-worker",
                "storage_id": "",
                "enabled": True,
                "archived": False,
            },
        ]
        source_file.write_text(
            json.dumps(original, indent=2),
            encoding="utf-8",
        )
        storage_file.write_text(
            json.dumps(
                [
                    {
                        "id": "storage-old",
                        "name": "Old storage",
                        "worker_id": "old-worker",
                        "path": "D:\\Recordings",
                        "enabled": True,
                    }
                ],
                indent=2,
            ),
            encoding="utf-8",
        )

        dashboard.SOURCES_FILE = source_file
        dashboard.STORAGES_FILE = storage_file
        dashboard.sources = lambda: json.loads(
            source_file.read_text(encoding="utf-8")
        )
        dashboard.storage_locations = lambda: json.loads(
            storage_file.read_text(encoding="utf-8")
        )
        dashboard.storage_by_id = lambda storage_id: next(
            (
                item
                for item in dashboard.storage_locations()
                if item["id"] == storage_id
            ),
            None,
        )
        dashboard.save_json = lambda path, data: path.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )
        dashboard.worker_by_id = lambda worker_id: (
            {
                "id": "new-worker",
                "display_name": "Second PC",
                "online": True,
            }
            if worker_id == "new-worker"
            else None
        )
        dashboard.active_job_for_source = lambda source_id: (
            {"id": "active-job"}
            if source_id == "imported-active"
            else None
        )

        changed, active, cleared, matched = (
            dashboard.assign_worker_to_source_group(
                "new-worker",
                scope="imported",
            )
        )

        if (changed, active, cleared, matched) != (1, 1, 1, 2):
            raise RuntimeError(
                "Unexpected imported assignment counts: "
                f"{(changed, active, cleared, matched)}"
            )

        after = {
            item["id"]: item
            for item in dashboard.sources()
        }

        if after["imported-one"]["worker_id"] != "new-worker":
            raise RuntimeError(
                "Imported source did not receive the new worker"
            )
        if after["imported-one"]["storage_id"] != "":
            raise RuntimeError(
                "Incompatible storage was not cleared"
            )
        if after["imported-one"]["enabled"] is not False:
            raise RuntimeError(
                "Worker assignment unexpectedly enabled the source"
            )
        if after["imported-active"]["worker_id"] != "old-worker":
            raise RuntimeError(
                "Active imported source was not safely skipped"
            )
        if after["normal"]["worker_id"] != "old-worker":
            raise RuntimeError(
                "Non-imported source was changed by imported scope"
            )

        changed, active, cleared, matched = (
            dashboard.assign_worker_to_source_group(
                "auto",
                source_id="normal",
            )
        )
        if (changed, active, cleared, matched) != (1, 0, 0, 1):
            raise RuntimeError(
                "Per-source Automatic assignment counts were wrong"
            )
        if {
            item["id"]: item
            for item in dashboard.sources()
        }["normal"]["worker_id"] != "auto":
            raise RuntimeError(
                "Per-source worker assignment did not save"
            )

    print("VIC source-worker assignment regression tests passed:")
    print("- imported sources can be assigned in one bulk action")
    print("- existing (Imported) names are recognised")
    print("- each source has a quick worker dropdown")
    print("- active sources are safely skipped")
    print("- incompatible storage is cleared")
    print("- worker assignment does not enable or start sources")
    print("- Automatic assignment is supported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
